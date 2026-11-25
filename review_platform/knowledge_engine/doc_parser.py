"""规则文档上传解析器：支持 .md / .txt / .docx / .json 解析为可校验的规范文档预览。"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from docx import Document

from review_platform.models import (
    MaintainableTermRule,
    ParsedRuleDocumentPreview,
    ScopeType,
)

MAX_RULE_DOC_BYTES = 5 * 1024 * 1024       # 5 MiB
MAX_RULE_DOC_UNZIP_BYTES = 20 * 1024 * 1024  # 20 MiB
MAX_RULE_DOC_ZIP_ENTRIES = 1000


def _validate_rule_docx_safety(raw_bytes: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError("上传的规则文件不是合法的 DOCX/ZIP 格式") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_RULE_DOC_ZIP_ENTRIES:
            raise ValueError("规则 DOCX 成员数超出安全上限，已拒绝导入")
        if sum(info.file_size for info in infos) > MAX_RULE_DOC_UNZIP_BYTES:
            raise ValueError("规则 DOCX 解压体积超出安全上限（20 MiB），已拒绝导入")
        names = {info.filename for info in infos}
        if "word/document.xml" not in names:
            raise ValueError("规则 DOCX 缺少 word/document.xml")
        for xml_member in ("word/document.xml", "word/styles.xml", "[Content_Types].xml"):
            if xml_member in names:
                raw_xml = zf.read(xml_member)
                if b"<!DOCTYPE" in raw_xml[:8192] or b"<!ENTITY" in raw_xml:
                    raise ValueError(f"检测到不安全的 XML 实体或 DTD 声明（{xml_member}）")

ARROW_PAIR_RE = re.compile(
    r"^\s*(?:[-*•]\s*|\d+[.、)]\s*)?[「“\"']?([^」”\"'\n\t]{1,60}?)[」”\"']?\s*(?:->|→|=>|改为|应改为|替换为)\s*[「“\"']?([^」”\"'\n\t]{1,80}?)[」”\"']?(?:\s*[（(]([^）)]{1,80})[）)])?\s*$"
)
MD_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")


def _safe_rule_id(prefix: str, idx: int, seed: str) -> str:
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:6].upper()
    return f"{prefix}-{idx:02d}-{h}"


def _infer_scope(text: str) -> ScopeType:
    if re.search(r"GB/T|GB\s*\d+|国家标准", text):
        return "national"
    if re.search(r"名词委|行业规范|中华医学会|CONSORT|STROBE", text):
        return "professional"
    if re.search(r"广告法|经验|保密|隐私", text):
        return "experience"
    return "journal"


def _infer_edition(text: str) -> str:
    m = re.search(r"(20\d{2}|19\d{2})", text)
    return m.group(1) if m else "2026"


def _extract_docx_markdown_and_tables(raw_bytes: bytes) -> tuple[str, list[list[list[str]]]]:
    _validate_rule_docx_safety(raw_bytes)
    doc = Document(io.BytesIO(raw_bytes))
    lines: list[str] = []
    tables: list[list[list[str]]] = []

    for p in doc.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        style_name = (p.style.name if p.style else "") or ""
        if "Heading 1" in style_name or "标题 1" in style_name:
            lines.append(f"# {t}")
        elif "Heading 2" in style_name or "标题 2" in style_name:
            lines.append(f"## {t}")
        elif "Heading" in style_name or "标题" in style_name:
            lines.append(f"### {t}")
        else:
            lines.append(t)

    for tbl in doc.tables:
        rows_data: list[list[str]] = []
        for row in tbl.rows:
            rows_data.append([cell.text.strip().replace("\n", " ") for cell in row.cells])
        if rows_data:
            tables.append(rows_data)
            cols = len(rows_data[0])
            lines.append("")
            lines.append("| " + " | ".join(rows_data[0]) + " |")
            lines.append("| " + " | ".join(["---"] * cols) + " |")
            for r in rows_data[1:]:
                lines.append("| " + " | ".join(r) + " |")
            lines.append("")

    return "\n".join(lines).strip(), tables


def _rules_from_table(rows: list[list[str]], start_idx: int) -> list[MaintainableTermRule]:
    if len(rows) < 2:
        return []
    header = [c.lower() for c in rows[0]]
    # 跳过纯分隔行
    body_rows = [r for r in rows[1:] if not all(re.fullmatch(r"[-:\s]+", c or "") for c in r)]
    if not body_rows:
        return []

    nonstd_col = 0
    std_col = 1 if len(header) > 1 else 0
    ex_col = None
    counter_col = None
    desc_col = None

    for i, h in enumerate(header):
        if any(k in h for k in ("非规范", "错", "旧称", "禁用", "原文")):
            nonstd_col = i
        elif any(k in h for k in ("规范", "建议", "改为", "正确")):
            std_col = i
        elif "正例" in h or "例" == h:
            ex_col = i
        elif "反例" in h or "豁免" in h:
            counter_col = i
        elif any(k in h for k in ("说明", "描述", "理由")):
            desc_col = i

    if nonstd_col == std_col:
        return []

    parsed: list[MaintainableTermRule] = []
    for offset, row in enumerate(body_rows):
        if len(row) <= max(nonstd_col, std_col):
            continue
        nonstd = row[nonstd_col].strip().strip("`")
        std = row[std_col].strip().strip("`")
        if not nonstd or not std or nonstd == std:
            continue
        ex = row[ex_col].strip() if ex_col is not None and ex_col < len(row) and row[ex_col].strip() else f"研究观察到{nonstd}现象。"
        cex = (
            row[counter_col].strip()
            if counter_col is not None and counter_col < len(row) and row[counter_col].strip()
            else f"研究观察到{std}现象。"
        )
        desc = (
            row[desc_col].strip()
            if desc_col is not None and desc_col < len(row) and row[desc_col].strip()
            else f"疑似非规范写法「{nonstd}」，建议改为「{std}」"
        )
        # 确保自动生成反例不会意外包含非规范词
        if nonstd in cex and cex == f"研究观察到{std}现象。":
            cex = ""
        parsed.append(
            MaintainableTermRule(
                rule_id=_safe_rule_id("DOC", start_idx + len(parsed) + 1, f"{nonstd}->{std}"),
                nonstandard=nonstd,
                standard=std,
                severity="warning",
                is_regex=False,
                exception_after=[],
                example=ex,
                counter_example=cex,
                description=desc,
            )
        )
    return parsed


def _parse_rules_from_markdown(md_text: str, extra_tables: list[list[list[str]]] | None = None) -> list[MaintainableTermRule]:
    rules: list[MaintainableTermRule] = []
    seen_patterns: set[str] = set()

    # 1. 解析文本中的 "A -> B" / "A 改为 B" 对照规则
    for line in md_text.splitlines():
        m = ARROW_PAIR_RE.match(line)
        if not m:
            continue
        nonstd = m.group(1).strip().strip("`")
        std = m.group(2).strip().strip("`")
        note = (m.group(3) or "").strip()
        if not nonstd or not std or nonstd == std or len(nonstd) > 40:
            continue
        if nonstd in seen_patterns:
            continue
        seen_patterns.add(nonstd)
        cex = f"本研究采用{std}评估。" if nonstd not in std else ""
        rules.append(
            MaintainableTermRule(
                rule_id=_safe_rule_id("DOC", len(rules) + 1, f"{nonstd}->{std}"),
                nonstandard=nonstd,
                standard=std,
                severity="warning",
                is_regex=False,
                exception_after=[],
                example=f"本研究采用{nonstd}评估。",
                counter_example=cex,
                description=note or f"疑似非规范写法「{nonstd}」，建议改为「{std}」",
            )
        )

    # 2. 解析 Markdown 表格
    md_lines = md_text.splitlines()
    idx = 0
    while idx < len(md_lines):
        if MD_TABLE_ROW_RE.match(md_lines[idx]):
            tbl_rows: list[list[str]] = []
            while idx < len(md_lines) and MD_TABLE_ROW_RE.match(md_lines[idx]):
                cells = [c.strip() for c in md_lines[idx].strip().strip("|").split("|")]
                tbl_rows.append(cells)
                idx += 1
            for r in _rules_from_table(tbl_rows, len(rules)):
                if r.nonstandard not in seen_patterns:
                    seen_patterns.add(r.nonstandard)
                    rules.append(r)
        else:
            idx += 1

    # 3. 解析 DOCX 原生表格
    for tbl_rows in extra_tables or []:
        for r in _rules_from_table(tbl_rows, len(rules)):
            if r.nonstandard not in seen_patterns:
                seen_patterns.add(r.nonstandard)
                rules.append(r)

    return rules


def parse_rule_document_bytes(raw_bytes: bytes, filename: str) -> ParsedRuleDocumentPreview:
    """解析上传的 .md / .txt / .docx / .json 规则文档，提取正文与候选可执行规则。"""
    if not raw_bytes:
        raise ValueError("上传的规则文档为空")
    if len(raw_bytes) > MAX_RULE_DOC_BYTES:
        raise ValueError("规则文档大小不能超过 5 MiB")

    ext = Path(filename).suffix.lower()
    stem = Path(filename).stem or "新规范文档"
    notes: list[str] = []

    if ext == ".json":
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"JSON 规则文档解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("JSON 规则文档顶层必须为对象")

        title = str(data.get("title") or data.get("layer_name") or stem)
        source = str(data.get("source") or title)
        edition = str(data.get("edition") or _infer_edition(source))
        scope_val = data.get("scope", "journal")
        scope: ScopeType = scope_val if scope_val in {"national", "professional", "journal", "experience"} else "journal"
        summary = str(data.get("summary") or data.get("note") or f"从 {filename} 导入的规则文档")
        raw_rules = data.get("rules") or []
        rules: list[MaintainableTermRule] = []
        for idx, r in enumerate(raw_rules, 1):
            if isinstance(r, dict):
                pat = str(r.get("nonstandard") or r.get("pattern") or "")
                std = str(r.get("standard") or r.get("suggestion") or "")
                raw_exc = r.get("exception_after", [])
                if raw_exc is None:
                    raw_exc = []
                if not isinstance(raw_exc, list) or not all(isinstance(x, str) for x in raw_exc):
                    raise ValueError(f"第 {idx} 条规则的 exception_after 必须为字符串列表")
                rules.append(
                    MaintainableTermRule(
                        rule_id=str(r.get("rule_id") or r.get("id") or _safe_rule_id("JSON", idx, pat)),
                        nonstandard=pat,
                        standard=std,
                        severity=r.get("severity", "warning"),
                        is_regex=bool(r.get("is_regex", False)),
                        exception_after=list(raw_exc),
                        example=str(r.get("example") or (f"示例：{pat}" if pat else "")),
                        counter_example=str(r.get("counter_example") or (f"示例：{std}" if std else "")),
                        description=str(r.get("description") or r.get("message") or ""),
                    )
                )
        body_md = str(
            data.get("body_markdown")
            or f"# {title}\n\n> 来源：{source} ｜ 版本：{edition}\n\n{summary}\n"
        )
        notes.append(f"已从 JSON 规则包解析 {len(rules)} 条规则，请核对正反例后保存激活。")
        return ParsedRuleDocumentPreview(
            filename=filename,
            title=title,
            source=source,
            edition=edition,
            scope=scope,
            summary=summary,
            body_markdown=body_md,
            rules=rules,
            parse_notes=notes,
        )

    if ext == ".docx":
        try:
            body_md, tables = _extract_docx_markdown_and_tables(raw_bytes)
        except Exception as exc:
            raise ValueError(f"DOCX 规则文档解析失败: {exc}") from exc
        if not body_md.strip():
            raise ValueError("DOCX 文档中未检测到有效文本或表格内容")
        rules = _parse_rules_from_markdown(body_md, extra_tables=tables)
        notes.append(f"已从 DOCX 提取正文与 {len(tables)} 个表格，自动识别 {len(rules)} 条规则。")
    elif ext in {".md", ".txt", ""}:
        try:
            body_md = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body_md = raw_bytes.decode("gb18030", errors="replace")
        if not body_md.strip():
            raise ValueError("文本/Markdown 规则文档内容为空")
        rules = _parse_rules_from_markdown(body_md)
        notes.append(f"已从文本/Markdown 自动解析 {len(rules)} 条可执行规则。")
    else:
        raise ValueError(f"不支持的规则文档格式 {ext}，支持 .md / .txt / .docx / .json")

    lines = [ln.strip() for ln in body_md.splitlines() if ln.strip()]
    title = stem
    for ln in lines[:5]:
        if ln.startswith("#"):
            title = ln.lstrip("#").strip()
            break
    else:
        if lines and len(lines[0]) <= 60:
            title = lines[0]

    source_match = re.search(r"(?:来源|依据|标准)[：:]\s*([^\n｜|]+)", body_md)
    source = source_match.group(1).strip() if source_match else f"上传规范文档（{filename}）"
    edition = _infer_edition(body_md)
    scope = _infer_scope(body_md)

    summary_candidates = [
        ln for ln in lines if not ln.startswith("#") and not ln.startswith(">") and not ln.startswith("|")
    ]
    summary = (summary_candidates[0][:200] if summary_candidates else f"由 {filename} 导入的规范文档")

    if not rules:
        notes.append("未在文档中检测到「非规范 -> 规范」或对照表格模式，将作为纯文档/人工核对依据保存，您也可手动添加可执行规则条目。")

    return ParsedRuleDocumentPreview(
        filename=filename,
        title=title,
        source=source,
        edition=edition,
        scope=scope,
        summary=summary,
        body_markdown=body_md,
        rules=rules,
        parse_notes=notes,
    )
