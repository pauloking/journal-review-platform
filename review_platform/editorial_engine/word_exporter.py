"""原生 Word 批注导出器（支持同段多批注、局部 lxml 命名空间、SHA-256 指纹校验）。

核心纪律：
1. 必须校验上传原稿 SHA-256 == run.document.source_sha256，防止旧定位写错文件。
2. 仅导出最新裁决为 accept 的意见，不修改正文字数与数值。
3. 使用 lxml.etree 精确解析 <w:r>/<w:t>，支持同段多条非重叠批注按 Unicode 区间拆分并保留 <w:rPr> 样式。
4. 目标段落已包含原稿既有批注、意见区间重叠或落在非直接 Run 容器时，显式跳过并列明原因，不再因同段两条意见而整体 422 失败。
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import io
import zipfile
from collections import defaultdict

from lxml import etree

from review_platform.models import Block, EditorialDecision, Finding, ReviewRun

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_COMMENTS_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PR_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE_ATTR = "{http://www.w3.org/XML/1998/namespace}space"


class WordExportError(ValueError):
    """Word 批注导出校验或定位失败异常。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _locate_paragraph(body: etree._Element, block: Block) -> etree._Element:
    meta = block.meta or {}
    if block.kind == "table_cell":
        t_idx = int(meta["table"]) - 1
        r_idx = int(meta["row"]) - 1
        c_idx = int(meta["col"]) - 1
        cp_idx = int(meta["cell_p"]) - 1
        tables = [el for el in body if el.tag == _q(W_NS, "tbl")]
        if t_idx >= len(tables):
            raise WordExportError(f"找不到表格索引 {t_idx + 1}")
        trs = tables[t_idx].findall(_q(W_NS, "tr"))
        tcs = trs[r_idx].findall(_q(W_NS, "tc"))
        ps = tcs[c_idx].findall(_q(W_NS, "p"))
        return ps[cp_idx]
    p_idx = int(meta["body_p_index"]) - 1
    paras = [el for el in body if el.tag == _q(W_NS, "p")]
    if p_idx >= len(paras):
        raise WordExportError(f"找不到正文段落索引 {p_idx + 1}")
    return paras[p_idx]


def _run_is_simple_text_only(run_elem: etree._Element) -> bool:
    return all(child.tag in (_q(W_NS, "rPr"), _q(W_NS, "t")) for child in run_elem)


def _make_run_with_text(template_run: etree._Element, text: str) -> etree._Element:
    clone = etree.Element(_q(W_NS, "r"), attrib=dict(template_run.attrib))
    rpr = template_run.find(_q(W_NS, "rPr"))
    if rpr is not None:
        clone.append(copy.deepcopy(rpr))
    t = etree.SubElement(clone, _q(W_NS, "t"))
    if text[:1].isspace() or text[-1:].isspace():
        t.set(XML_SPACE_ATTR, "preserve")
    t.text = text
    return clone


def _inject_multiple_comments_into_paragraph(
    p_elem: etree._Element,
    block: Block,
    items: list[tuple[Finding, EditorialDecision, int]],
) -> tuple[list[tuple[Finding, EditorialDecision, int]], list[str]]:
    """支持在同一个段落内注入多条非重叠批注，对原稿既有批注或重叠项显式跳过并说明原因。"""
    skipped: list[str] = []
    if (
        p_elem.find(f".//{_q(W_NS, 'commentRangeStart')}") is not None
        or p_elem.find(f".//{_q(W_NS, 'commentReference')}") is not None
    ):
        for f, _, _ in items:
            skipped.append(f"{f.id}:段落 {block.id} 已含原稿既有批注")
        return [], skipped

    direct_runs = [child for child in p_elem if child.tag == _q(W_NS, "r")]
    if not direct_runs or len(direct_runs) != len(block.runs):
        for f, _, _ in items:
            skipped.append(f"{f.id}:段落 {block.id} 含超链接或复杂域，无法安全拆分 Run")
        return [], skipped

    # 筛选非重叠、区间有效的项（按 start, end 升序）
    ordered = sorted(items, key=lambda x: (x[0].anchor.start, x[0].anchor.end))
    active: list[tuple[Finding, EditorialDecision, int]] = []
    last_end = -1

    for f, dec, cid in ordered:
        s, e = f.anchor.start, f.anchor.end
        if s < last_end:
            skipped.append(f"{f.id}:与同段前一条批注区间重叠")
            continue
        # 检查是否可完整拆分覆盖
        can_apply = True
        covered = False
        for r_idx, r_span in enumerate(block.runs):
            if r_span.end <= s or r_span.start >= e:
                continue
            covered = True
            run_el = direct_runs[r_idx]
            r_text = "".join(t.text or "" for t in run_el.findall(_q(W_NS, "t")))
            if not _run_is_simple_text_only(run_el) or len(r_text) != (r_span.end - r_span.start):
                if not (r_span.start >= s and r_span.end <= e):
                    can_apply = False
                    break
        if not covered or not can_apply:
            skipped.append(f"{f.id}:引文落在非直接文本容器或跨不可拆分制表符")
            continue
        active.append((f, dec, cid))
        last_end = e

    if not active:
        return [], skipped

    new_children: list[etree._Element] = []
    run_ptr = 0
    started_ids: set[int] = set()
    ended_ids: set[int] = set()

    for child in list(p_elem):
        if child.tag != _q(W_NS, "r"):
            new_children.append(child)
            continue

        r_span = block.runs[run_ptr]
        run_ptr += 1
        r_start, r_end = r_span.start, r_span.end

        intersecting = [
            (f, cid) for f, _, cid in active if r_start < f.anchor.end and r_end > f.anchor.start
        ]
        if not intersecting:
            new_children.append(child)
            continue

        r_text = "".join(t.text or "" for t in child.findall(_q(W_NS, "t")))
        if not _run_is_simple_text_only(child) or len(r_text) != (r_end - r_start):
            # 整 run 被单一批注覆盖
            f, cid = intersecting[0]
            if f.anchor.start >= r_start and cid not in started_ids:
                cs = etree.Element(_q(W_NS, "commentRangeStart"))
                cs.set(_q(W_NS, "id"), str(cid))
                new_children.append(cs)
                started_ids.add(cid)
            new_children.append(child)
            if f.anchor.end <= r_end and cid not in ended_ids:
                ce = etree.Element(_q(W_NS, "commentRangeEnd"))
                ce.set(_q(W_NS, "id"), str(cid))
                new_children.append(ce)
                ref_r = etree.Element(_q(W_NS, "r"))
                ref = etree.SubElement(ref_r, _q(W_NS, "commentReference"))
                ref.set(_q(W_NS, "id"), str(cid))
                new_children.append(ref_r)
                ended_ids.add(cid)
            continue

        cuts = {0, len(r_text)}
        for f, _ in intersecting:
            cuts.add(max(0, f.anchor.start - r_start))
            cuts.add(min(len(r_text), f.anchor.end - r_start))
        sorted_cuts = sorted(cuts)

        for a, b in zip(sorted_cuts[:-1], sorted_cuts[1:]):
            abs_a = r_start + a
            abs_b = r_start + b
            for f, cid in intersecting:
                if f.anchor.start == abs_a and cid not in started_ids:
                    cs = etree.Element(_q(W_NS, "commentRangeStart"))
                    cs.set(_q(W_NS, "id"), str(cid))
                    new_children.append(cs)
                    started_ids.add(cid)

            new_children.append(_make_run_with_text(child, r_text[a:b]))

            for f, cid in intersecting:
                if f.anchor.end == abs_b and cid not in ended_ids:
                    ce = etree.Element(_q(W_NS, "commentRangeEnd"))
                    ce.set(_q(W_NS, "id"), str(cid))
                    new_children.append(ce)
                    ref_r = etree.Element(_q(W_NS, "r"))
                    ref = etree.SubElement(ref_r, _q(W_NS, "commentReference"))
                    ref.set(_q(W_NS, "id"), str(cid))
                    new_children.append(ref_r)
                    ended_ids.add(cid)

    for elem in list(p_elem):
        p_elem.remove(elem)
    for elem in new_children:
        p_elem.append(elem)

    return active, skipped


def _format_comment_lines(finding: Finding, dec: EditorialDecision) -> list[str]:
    return [
        f"【{finding.rule_id}｜{finding.category}｜{finding.severity}】{finding.title}",
        f"问题：{finding.message}",
        f"建议：{finding.suggestion}",
        f"依据：{finding.basis_id}（{finding.basis_source}）",
        f"裁决：{dec.editor} 已采纳（v{dec.revision}）— {dec.reason}",
    ]


def export_annotated_docx(
    raw_docx: bytes, run: ReviewRun, *, return_skipped: bool = False
) -> tuple[bytes, int] | tuple[bytes, int, list[str]]:
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

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    doc_root = etree.fromstring(entries["word/document.xml"], parser=parser)
    body = doc_root.find(_q(W_NS, "body"))
    if body is None:
        raise WordExportError("DOCX 缺少 w:body 节点", status_code=422)

    if "word/comments.xml" in entries:
        comments_root = etree.fromstring(entries["word/comments.xml"], parser=parser)
        existing_ids = [
            int(c.attrib.get(_q(W_NS, "id"), "0"))
            for c in comments_root.findall(_q(W_NS, "comment"))
            if c.attrib.get(_q(W_NS, "id"), "").isdigit()
        ]
        next_cid = (max(existing_ids) + 1) if existing_ids else 0
    else:
        comments_root = etree.Element(_q(W_NS, "comments"), nsmap={"w": W_NS, "r": R_NS})
        next_cid = 0

    now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 按段落聚合，原生支持同段多条非重叠批注
    by_block: dict[str, list[tuple[Finding, EditorialDecision, int]]] = defaultdict(list)
    for finding, dec in accepted_pairs:
        cid = next_cid
        next_cid += 1
        by_block[finding.anchor.block_id].append((finding, dec, cid))

    exported_items: list[tuple[Finding, EditorialDecision, int]] = []
    skipped_reasons: list[str] = []

    for block_id, items in by_block.items():
        block = block_map.get(block_id)
        if block is None:
            for f, _, _ in items:
                skipped_reasons.append(f"{f.id}:找不到对应块 {block_id}")
            continue
        p_elem = _locate_paragraph(body, block)
        applied, skipped = _inject_multiple_comments_into_paragraph(p_elem, block, items)
        exported_items.extend(applied)
        skipped_reasons.extend(skipped)

    if not exported_items:
        raise WordExportError(
            "所有已采纳意见均无法安全注入 Word 批注：" + "；".join(skipped_reasons),
            status_code=422,
        )

    for finding, dec, cid in exported_items:
        c_elem = etree.SubElement(comments_root, _q(W_NS, "comment"))
        c_elem.set(_q(W_NS, "id"), str(cid))
        c_elem.set(_q(W_NS, "author"), f"审校平台·{dec.editor}")
        c_elem.set(_q(W_NS, "date"), now_iso)
        c_elem.set(_q(W_NS, "initials"), "RP")
        for line in _format_comment_lines(finding, dec):
            cp = etree.SubElement(c_elem, _q(W_NS, "p"))
            cr = etree.SubElement(cp, _q(W_NS, "r"))
            ct = etree.SubElement(cr, _q(W_NS, "t"))
            ct.text = line

    entries["word/document.xml"] = etree.tostring(doc_root, encoding="utf-8", xml_declaration=True)
    entries["word/comments.xml"] = etree.tostring(comments_root, encoding="utf-8", xml_declaration=True)

    # 局部命名空间处理，绝不污染进程级全局表
    if "[Content_Types].xml" in entries:
        ct_root = etree.fromstring(entries["[Content_Types].xml"], parser=parser)
        has_override = any(
            el.attrib.get("PartName") == "/word/comments.xml"
            for el in ct_root.findall(_q(CT_NS, "Override"))
        )
        if not has_override:
            ov = etree.SubElement(ct_root, _q(CT_NS, "Override"))
            ov.set("PartName", "/word/comments.xml")
            ov.set("ContentType", CT_COMMENTS)
            entries["[Content_Types].xml"] = etree.tostring(
                ct_root, encoding="utf-8", xml_declaration=True
            )

    rels_path = "word/_rels/document.xml.rels"
    if rels_path in entries:
        rel_root = etree.fromstring(entries[rels_path], parser=parser)
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
            rel = etree.SubElement(rel_root, _q(PR_NS, "Relationship"))
            rel.set("Id", new_rid)
            rel.set("Type", REL_COMMENTS_TYPE)
            rel.set("Target", "comments.xml")
            entries[rels_path] = etree.tostring(rel_root, encoding="utf-8", xml_declaration=True)

    out_buf = io.BytesIO()
    with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)

    if return_skipped:
        return out_buf.getvalue(), len(exported_items), skipped_reasons
    return out_buf.getvalue(), len(exported_items)
