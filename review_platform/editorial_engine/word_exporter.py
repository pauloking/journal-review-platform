"""原生 Word 批注导出器（融合 docxGenerator.ts 安全经验与精确 RunSpan 拆分）。

核心纪律：
1. 必须校验上传原稿 SHA-256 == run.document.source_sha256，防止旧定位写错文件。
2. 仅导出最新裁决为 accept 的意见，不修改正文字数与数值。
3. 使用 ElementTree 精确解析 <w:r>/<w:t>（绝不用正则扫 <w:t> 避免误吞 <w:topLinePunct/> 等标签），
   按 Unicode 区间精准拆分直接 Run 并保留 <w:rPr> 样式。
4. 目标段落已存在批注、区间重叠或引文落在超链接等非直接 Run 容器时，显式拒绝并说明原因，不静默丢弃或错位挂接。
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import io
import zipfile
import xml.etree.ElementTree as ET

from review_platform.models import Block, EditorialDecision, Finding, ReviewRun

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_COMMENTS_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

ET.register_namespace("w", W_NS)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")


class WordExportError(ValueError):
    """Word 批注导出校验或定位失败异常。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _locate_paragraph(body: ET.Element, block: Block) -> ET.Element:
    """根据 DocumentIR 记录的 meta 索引定位对应的 <w:p> 元素。"""
    meta = block.meta or {}
    if block.kind == "table_cell":
        t_idx = int(meta["table"]) - 1
        r_idx = int(meta["row"]) - 1
        c_idx = int(meta["col"]) - 1
        cp_idx = int(meta["cell_p"]) - 1
        tables = [el for el in list(body) if el.tag == _q(W_NS, "tbl")]
        if t_idx >= len(tables):
            raise WordExportError(f"找不到表格索引 {t_idx + 1}")
        trs = tables[t_idx].findall(_q(W_NS, "tr"))
        tcs = trs[r_idx].findall(_q(W_NS, "tc"))
        ps = tcs[c_idx].findall(_q(W_NS, "p"))
        return ps[cp_idx]
    p_idx = int(meta["body_p_index"]) - 1
    paras = [el for el in list(body) if el.tag == _q(W_NS, "p")]
    if p_idx >= len(paras):
        raise WordExportError(f"找不到正文段落索引 {p_idx + 1}")
    return paras[p_idx]


def _run_visible_len(run_elem: ET.Element) -> int:
    total = 0
    for child in run_elem:
        if child.tag == _q(W_NS, "t"):
            total += len(child.text or "")
        elif child.tag in (_q(W_NS, "tab"), _q(W_NS, "br"), _q(W_NS, "cr")):
            total += 1
    return total


def _run_is_simple_text_only(run_elem: ET.Element) -> bool:
    for child in run_elem:
        if child.tag not in (_q(W_NS, "rPr"), _q(W_NS, "t")):
            return False
    return True


def _make_run_with_text(template_run: ET.Element, text: str) -> ET.Element:
    clone = ET.Element(_q(W_NS, "r"), dict(template_run.attrib))
    rpr = template_run.find(_q(W_NS, "rPr"))
    if rpr is not None:
        clone.append(copy.deepcopy(rpr))
    t = ET.SubElement(clone, _q(W_NS, "t"))
    if text[:1].isspace() or text[-1:].isspace():
        t.set("{http://www.w3.org/XML/1998/namespace}space", "space" and "preserve")
    t.text = text
    return clone


def _inject_comment_range_into_paragraph(
    p_elem: ET.Element,
    block: Block,
    finding: Finding,
    comment_id: int,
) -> None:
    """按精确 Unicode 区间拆分直接 <w:r> 并插入 commentRangeStart/End/Reference。"""
    if (
        p_elem.find(f".//{_q(W_NS, 'commentRangeStart')}") is not None
        or p_elem.find(f".//{_q(W_NS, 'commentReference')}") is not None
    ):
        raise WordExportError(
            f"段落 {block.id} 已包含既有批注或同一段落存在多条批注重叠，已安全拒绝修改原段落 XML"
        )

    start, end = finding.anchor.start, finding.anchor.end
    direct_runs = [(idx, child) for idx, child in enumerate(list(p_elem)) if child.tag == _q(W_NS, "r")]
    if not direct_runs or len(direct_runs) != len(block.runs):
        raise WordExportError(f"段落 {block.id} 的 Run 结构与解析结果不一致（可能含超链接或复杂域）")

    # 校验目标区间必须完整落在连续直接 <w:r> 覆盖范围内
    covered = False
    for r_span in block.runs:
        if r_span.start < end and r_span.end > start:
            covered = True
    if not covered:
        raise WordExportError(f"意见 {finding.id} 的引文落在非直接文本容器（如超链接）内，无法安全写回批注")

    new_children: list[ET.Element] = []
    run_ptr = 0
    range_started = False
    range_ended = False

    for child in list(p_elem):
        if child.tag != _q(W_NS, "r"):
            new_children.append(child)
            continue

        r_span = block.runs[run_ptr]
        run_ptr += 1
        r_start, r_end = r_span.start, r_span.end

        # 完全在目标区间外
        if r_end <= start or r_start >= end:
            new_children.append(child)
            continue

        # 命中当前 run，检查是否为可安全拆分的纯文本 run
        r_text = "".join(t.text or "" for t in child.findall(_q(W_NS, "t")))
        if not _run_is_simple_text_only(child) or len(r_text) != (r_end - r_start):
            if r_start >= start and r_end <= end:
                # 整 run 被覆盖，无需拆分内部元素
                if not range_started:
                    cs = ET.Element(_q(W_NS, "commentRangeStart"))
                    cs.set(_q(W_NS, "id"), str(comment_id))
                    new_children.append(cs)
                    range_started = True
                new_children.append(child)
                if r_end >= end and not range_ended:
                    ce = ET.Element(_q(W_NS, "commentRangeEnd"))
                    ce.set(_q(W_NS, "id"), str(comment_id))
                    new_children.append(ce)
                    ref_r = ET.Element(_q(W_NS, "r"))
                    ref = ET.SubElement(ref_r, _q(W_NS, "commentReference"))
                    ref.set(_q(W_NS, "id"), str(comment_id))
                    new_children.append(ref_r)
                    range_ended = True
                continue
            raise WordExportError(f"段落 {block.id} 目标区间跨越不可拆分制表符/换行符，已拒绝导出")

        rel_s = max(0, start - r_start)
        rel_e = min(len(r_text), end - r_start)

        prefix = r_text[:rel_s]
        middle = r_text[rel_s:rel_e]
        suffix = r_text[rel_e:]

        if prefix:
            new_children.append(_make_run_with_text(child, prefix))

        if not range_started:
            cs = ET.Element(_q(W_NS, "commentRangeStart"))
            cs.set(_q(W_NS, "id"), str(comment_id))
            new_children.append(cs)
            range_started = True

        if middle:
            new_children.append(_make_run_with_text(child, middle))

        if r_end >= end and not range_ended:
            ce = ET.Element(_q(W_NS, "commentRangeEnd"))
            ce.set(_q(W_NS, "id"), str(comment_id))
            new_children.append(ce)
            ref_r = ET.Element(_q(W_NS, "r"))
            ref = ET.SubElement(ref_r, _q(W_NS, "commentReference"))
            ref.set(_q(W_NS, "id"), str(comment_id))
            new_children.append(ref_r)
            range_ended = True

        if suffix:
            new_children.append(_make_run_with_text(child, suffix))

    if not range_started or not range_ended:
        raise WordExportError(f"段落 {block.id} 批注区间闭合失败")

    p_elem[:] = new_children


def _format_comment_lines(finding: Finding, dec: EditorialDecision) -> list[str]:
    return [
        f"【{finding.rule_id}｜{finding.category}｜{finding.severity}】{finding.title}",
        f"问题：{finding.message}",
        f"建议：{finding.suggestion}",
        f"依据：{finding.basis_id}（{finding.basis_source}）",
        f"裁决：{dec.editor} 已采纳（v{dec.revision}）— {dec.reason}",
    ]


def export_annotated_docx(raw_docx: bytes, run: ReviewRun) -> tuple[bytes, int]:
    """校验原稿 SHA-256 后，将已采纳的审校意见写回原生 Word 批注副本。"""
    sha = hashlib.sha256(raw_docx).hexdigest()
    if sha != run.document.source_sha256:
        raise WordExportError(
            "上传的 DOCX 文件指纹与该次审校原稿不一致，请上传同一份原稿后再导出批注",
            status_code=422,
        )

    latest: dict[str, EditorialDecision] = {}
    for dec in run.decisions:
        prev = latest.get(dec.finding_id)
        if prev is None or dec.revision > prev.revision:
            latest[dec.finding_id] = dec

    accepted_pairs: list[tuple[Finding, EditorialDecision]] = [
        (f, latest[f.id])
        for f in run.findings
        if f.id in latest and latest[f.id].action == "accept"
    ]
    if not accepted_pairs:
        raise WordExportError("当前没有已采纳（accept）的审校意见可导出为 Word 批注", status_code=422)

    block_map = {b.id: b for b in run.document.blocks}

    buf = io.BytesIO(raw_docx)
    with zipfile.ZipFile(buf, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}

    doc_root = ET.fromstring(entries["word/document.xml"])
    body = doc_root.find(_q(W_NS, "body"))
    if body is None:
        raise WordExportError("DOCX 缺少 w:body 节点", status_code=422)

    if "word/comments.xml" in entries:
        comments_root = ET.fromstring(entries["word/comments.xml"])
        existing_ids = [
            int(c.attrib.get(_q(W_NS, "id"), "0"))
            for c in comments_root.findall(_q(W_NS, "comment"))
            if c.attrib.get(_q(W_NS, "id"), "").isdigit()
        ]
        next_cid = (max(existing_ids) + 1) if existing_ids else 0
    else:
        comments_root = ET.Element(_q(W_NS, "comments"))
        next_cid = 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for finding, dec in accepted_pairs:
        block = block_map.get(finding.anchor.block_id)
        if block is None:
            raise WordExportError(f"找不到对应块: {finding.anchor.block_id}", status_code=422)
        p_elem = _locate_paragraph(body, block)
        cid = next_cid
        next_cid += 1
        _inject_comment_range_into_paragraph(p_elem, block, finding, cid)

        c_elem = ET.SubElement(comments_root, _q(W_NS, "comment"))
        c_elem.set(_q(W_NS, "id"), str(cid))
        c_elem.set(_q(W_NS, "author"), f"审校平台·{dec.editor}")
        c_elem.set(_q(W_NS, "date"), now_iso)
        c_elem.set(_q(W_NS, "initials"), "RP")
        for line in _format_comment_lines(finding, dec):
            cp = ET.SubElement(c_elem, _q(W_NS, "p"))
            cr = ET.SubElement(cp, _q(W_NS, "r"))
            ct = ET.SubElement(cr, _q(W_NS, "t"))
            ct.text = line

    entries["word/document.xml"] = ET.tostring(doc_root, encoding="utf-8", xml_declaration=True)
    entries["word/comments.xml"] = ET.tostring(comments_root, encoding="utf-8", xml_declaration=True)

    # 更新 [Content_Types].xml
    if "[Content_Types].xml" in entries:
        ct_root = ET.fromstring(entries["[Content_Types].xml"])
        ET.register_namespace("", CT_NS)
        has_override = any(
            el.attrib.get("PartName") == "/word/comments.xml"
            for el in ct_root.findall(_q(CT_NS, "Override"))
        )
        if not has_override:
            ov = ET.SubElement(ct_root, _q(CT_NS, "Override"))
            ov.set("PartName", "/word/comments.xml")
            ov.set("ContentType", CT_COMMENTS)
            entries["[Content_Types].xml"] = ET.tostring(
                ct_root, encoding="utf-8", xml_declaration=True
            )

    # 更新 word/_rels/document.xml.rels
    rels_path = "word/_rels/document.xml.rels"
    if rels_path in entries:
        rel_root = ET.fromstring(entries[rels_path])
        ET.register_namespace("", PR_NS)
        has_rel = any(
            el.attrib.get("Type") == REL_COMMENTS_TYPE
            for el in rel_root.findall(_q(PR_NS, "Relationship"))
        )
        if not has_rel:
            existing_rids = [
                int(el.attrib.get("Id", "rId0")[3:])
                for el in rel_root.findall(_q(PR_NS, "Relationship"))
                if el.attrib.get("Id", "").startswith("rId") and el.attrib["Id"][3:].isdigit()
            ]
            new_rid = f"rId{(max(existing_rids) + 1) if existing_rids else 1}"
            rel = ET.SubElement(rel_root, _q(PR_NS, "Relationship"))
            rel.set("Id", new_rid)
            rel.set("Type", REL_COMMENTS_TYPE)
            rel.set("Target", "comments.xml")
            entries[rels_path] = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)

    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)

    return out_buf.getvalue(), len(accepted_pairs)
