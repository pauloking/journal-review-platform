"""DOCX 安全解析器，生成不可变 Document IR 协议（document-ir/1.0）。"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from review_platform.adapters.legacy_structure import (
    SECTION_CN,
    build_map,
    structure_quality,
)
from review_platform.models import (
    Anchor,
    AnchorVerification,
    Block,
    DocumentIR,
    ManuscriptProfile,
    RunSpan,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
MAX_FILE_BYTES = 10 * 1024 * 1024       # 10 MiB
MAX_UNZIP_BYTES = 30 * 1024 * 1024      # 30 MiB
MAX_ZIP_ENTRIES = 1000
TRACKED_CHANGE_TAGS = ("ins", "del", "moveFrom", "moveTo")


class DocumentParseError(ValueError):
    """DOCX 输入校验或解析异常，附带明确状态码供 API 使用。"""

    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _q(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def _check_xml_safety(raw_xml: bytes, member_name: str) -> None:
    head = raw_xml[:8192]
    if b"<!DOCTYPE" in head or b"<!ENTITY" in raw_xml:
        raise DocumentParseError(f"检测到不安全的 XML 实体或 DTD 声明（{member_name}），已拒绝导入")


def _extract_run_text(run_elem: ET.Element) -> str:
    """精确提取单个 <w:r> 内可见文本（仅 w:t / w:tab / w:br / w:cr）。"""
    parts: list[str] = []
    for child in run_elem:
        if child.tag == _q("t"):
            parts.append(child.text or "")
        elif child.tag == _q("tab"):
            parts.append("\t")
        elif child.tag in (_q("br"), _q("cr")):
            parts.append("\n")
    return "".join(parts)


def _extract_paragraph_and_runs(p_elem: ET.Element) -> tuple[str, list[RunSpan]]:
    """提取段落可见文本与直接 <w:r> 区间，严禁压缩空白或篡改原字符。"""
    chunks: list[str] = []
    runs: list[RunSpan] = []
    cursor = 0
    direct_run_idx = 0

    for child in p_elem:
        if child.tag == _q("r"):
            r_text = _extract_run_text(child)
            start = cursor
            end = start + len(r_text)
            runs.append(RunSpan(run_index=direct_run_idx, start=start, end=end, text=r_text))
            chunks.append(r_text)
            cursor = end
            direct_run_idx += 1
        elif child.tag == _q("hyperlink"):
            # 超链接内部文本也纳入可见正文以防文本缺失，不记为直接 run（批注回写时会安全识别）
            sub_parts: list[str] = []
            for r_sub in child.findall(_q("r")):
                sub_parts.append(_extract_run_text(r_sub))
            h_text = "".join(sub_parts)
            if h_text:
                chunks.append(h_text)
                cursor += len(h_text)

    return "".join(chunks), runs


def _extract_paragraph_style(p_elem: ET.Element) -> str:
    ppr = p_elem.find(_q("pPr"))
    if ppr is None:
        return ""
    pstyle = ppr.find(_q("pStyle"))
    if pstyle is None:
        return ""
    return pstyle.attrib.get(_q("val"), "")


def _detect_tracked_changes(root: ET.Element) -> bool:
    for tag in TRACKED_CHANGE_TAGS:
        if root.find(f".//{_q(tag)}") is not None:
            return True
    return False


def parse_docx_bytes(raw_bytes: bytes, filename: str = "manuscript.docx") -> DocumentIR:
    """安全解析 DOCX 字节流为不可变 DocumentIR。"""
    if not raw_bytes:
        raise DocumentParseError("上传文件为空", status_code=422)
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise DocumentParseError(
            f"文件大小超过上限（最大 {MAX_FILE_BYTES // (1024 * 1024)} MiB）",
            status_code=413,
        )

    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise DocumentParseError("文件不是合法的 DOCX/ZIP 格式", status_code=422) from exc

    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise DocumentParseError("ZIP 成员数超出安全上限，已拒绝导入", status_code=422)

        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > MAX_UNZIP_BYTES:
            raise DocumentParseError("解压体积超出安全上限，已拒绝导入", status_code=422)

        names = {info.filename for info in infos}
        if "word/document.xml" not in names:
            raise DocumentParseError("DOCX 缺少核心组件 word/document.xml", status_code=422)

        doc_xml_bytes = zf.read("word/document.xml")
        _check_xml_safety(doc_xml_bytes, "word/document.xml")

        for extra_xml in ("word/comments.xml", "word/styles.xml", "[Content_Types].xml"):
            if extra_xml in names:
                _check_xml_safety(zf.read(extra_xml), extra_xml)

        try:
            root = ET.fromstring(doc_xml_bytes)
        except ET.ParseError as exc:
            raise DocumentParseError(f"word/document.xml 解析失败: {exc}", status_code=422) from exc

        if _detect_tracked_changes(root):
            raise DocumentParseError(
                "检测到 Word 修订痕迹（w:ins/w:del/w:move），请先在 Word 中接收或拒绝全部修订定稿后再上传",
                status_code=422,
            )

        body = root.find(_q("body"))
        if body is None:
            raise DocumentParseError("word/document.xml 缺少 w:body 节点", status_code=422)

        raw_items: list[dict[str, Any]] = []
        p_idx = 0
        tbl_idx = 0

        for child in list(body):
            if child.tag == _q("p"):
                p_idx += 1
                text, runs = _extract_paragraph_and_runs(child)
                if not text.strip():
                    continue
                raw_items.append({
                    "source": "正文",
                    "text": text,
                    "style": _extract_paragraph_style(child),
                    "xml_path": f"body/p[{p_idx}]",
                    "runs": runs,
                    "meta": {"body_p_index": p_idx},
                })
            elif child.tag == _q("tbl"):
                tbl_idx += 1
                for r_no, tr in enumerate(child.findall(_q("tr")), 1):
                    for c_no, tc in enumerate(tr.findall(_q("tc")), 1):
                        cell_paras = tc.findall(_q("p"))
                        for cp_no, cp in enumerate(cell_paras, 1):
                            text, runs = _extract_paragraph_and_runs(cp)
                            if not text.strip():
                                continue
                            raw_items.append({
                                "source": "表格",
                                "table": tbl_idx,
                                "row": r_no,
                                "col": c_no,
                                "cell_p": cp_no,
                                "text": text,
                                "style": _extract_paragraph_style(cp),
                                "xml_path": f"body/tbl[{tbl_idx}]/tr[{r_no}]/tc[{c_no}]/p[{cp_no}]",
                                "runs": runs,
                                "meta": {"table": tbl_idx, "row": r_no, "col": c_no, "cell_p": cp_no},
                            })

        if not raw_items:
            raise DocumentParseError("DOCX 正文与表格中未检测到有效可见文本", status_code=422)

        mapped = build_map(raw_items)
        qual = structure_quality(mapped)

        seen_ids: dict[str, int] = {}
        blocks: list[Block] = []
        para_count = 0
        table_cell_count = 0
        title_guess = ""

        for item in mapped:
            base_id = str(item["id"])
            count = seen_ids.get(base_id, 0) + 1
            seen_ids[base_id] = count
            block_id = base_id if count == 1 else f"{base_id}_{count}"

            kind = item.get("kind", "paragraph")
            if kind == "table_cell":
                table_cell_count += 1
            else:
                para_count += 1
            if item.get("section") == "title" and not title_guess:
                title_guess = item["text"].strip()[:120]

            blocks.append(
                Block(
                    id=block_id,
                    kind=kind if kind in {"title", "heading", "paragraph", "reference", "table_cell", "separator"} else "paragraph",
                    section=item.get("section", "body"),
                    section_cn=item.get("section_cn", SECTION_CN.get(item.get("section", "body"), "正文")),
                    heading=item.get("heading", ""),
                    text=item["text"],
                    ooxml_part="word/document.xml",
                    xml_path=item["xml_path"],
                    runs=item["runs"],
                    excluded_spans=[tuple(sp) for sp in item.get("excluded_spans", [])],
                    exclude_entire=bool(item.get("exclude_entire", False)),
                    meta=item.get("meta", {}),
                )
            )

        if not title_guess and blocks:
            title_guess = blocks[0].text.strip()[:120]

        sections_present = list(dict.fromkeys(b.section for b in blocks))
        char_count = sum(len(re.sub(r"\s+", "", b.text)) for b in blocks)

        limitations = [
            "仅提取 word/document.xml 中顶层正文段落与表格单元格可见文本；页眉页脚、脚注尾注、文本框与嵌入图片/公式不纳入自动定位。",
            "章节识别基于启发式标题与文献特征规则，存在误分类可能，仅供审校路由参考。",
        ]
        if "word/comments.xml" in names:
            limitations.append("原稿已包含既有 Word 批注，导出批注副本时将保留未冲突段落的既有批注。")

        profile = ManuscriptProfile(
            title_guess=title_guess,
            total_blocks=len(blocks),
            paragraph_count=para_count,
            table_cell_count=table_cell_count,
            char_count=char_count,
            sections_present=sections_present,
            section_counts=qual["counts"],
            structure_status=qual["status"],
            structure_warnings=qual["warnings"],
        )

        return DocumentIR(
            schema_version="document-ir/1.0",
            document_id=sha256,
            source_sha256=sha256,
            filename=filename,
            blocks=blocks,
            profile=profile,
            limitations=limitations,
        )


def validate_anchor(doc: DocumentIR, anchor: Anchor) -> AnchorVerification:
    """严格核验 Anchor 与 DocumentIR 的指纹、块、码点区间与原文引文。"""
    if anchor.document_id != doc.document_id:
        return AnchorVerification(
            anchor_valid=False,
            basis_valid=True,
            reason=f"文档指纹不匹配: anchor={anchor.document_id[:12]} doc={doc.document_id[:12]}",
        )
    block = next((b for b in doc.blocks if b.id == anchor.block_id), None)
    if block is None:
        return AnchorVerification(
            anchor_valid=False,
            basis_valid=True,
            reason=f"找不到指定块 ID: {anchor.block_id}",
        )
    if anchor.start < 0 or anchor.end <= anchor.start or anchor.end > len(block.text):
        return AnchorVerification(
            anchor_valid=False,
            basis_valid=True,
            reason=f"Unicode 区间越界: [{anchor.start}, {anchor.end}) 块长度 {len(block.text)}",
        )
    actual = block.text[anchor.start : anchor.end]
    if actual != anchor.quote:
        return AnchorVerification(
            anchor_valid=False,
            basis_valid=True,
            reason=f"引文失配: 预期 '{anchor.quote}' 实际 '{actual}'",
        )
    return AnchorVerification(anchor_valid=True, basis_valid=True, reason="ok")
