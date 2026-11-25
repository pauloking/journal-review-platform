#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把稿件转换为编辑可使用的结构地图（纯标准库）。"""

import re


BRACKET = r"[\[［【〔]?"
END_BRACKET = r"[\]］】〕]?"

SECTION_PATTERNS = [
    ("references", r"^\s*(?:主要)?参考文献\s*[:：]?\s*$"),
    ("abstract", rf"^\s*{BRACKET}(?:中英文摘要|英文摘要|摘\s*要|摘要|Abstract){END_BRACKET}\s*[:：]?\s*"),
    ("keywords", rf"^\s*{BRACKET}(?:关键词|Key\s*words?){END_BRACKET}\s*[:：]?"),
    ("methods", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?(?:资料与方法|材料与方法|对象与方法|研究对象与方法|方法)\s*$"),
    ("results", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?结果\s*$"),
    ("discussion", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?讨论\s*$"),
    ("conclusion", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?结论\s*$"),
    ("introduction", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?(?:引言|前言)\s*$"),
]

SECTION_PREFIX_PATTERNS = [
    ("abstract", rf"^\s*{BRACKET}(?:中英文摘要|英文摘要|摘\s*要|摘要|Abstract){END_BRACKET}\s*[:：]\s*\S"),
    ("keywords", rf"^\s*{BRACKET}(?:关键词|Key\s*words?){END_BRACKET}\s*[:：]?\s*\S"),
    ("methods", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?(?:资料与方法|材料与方法|对象与方法|研究对象与方法|方法)[　\s:：]+\S"),
    ("results", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?结果[　\s:：]+\S"),
    ("discussion", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?讨论[　\s:：]+\S"),
    ("conclusion", r"^\s*(?:\d+(?:\.\d+)*[　\s、.]*)?结论[　\s:：]+\S"),
]

SECTION_CN = {
    "front": "文前", "title": "题名", "abstract": "摘要", "keywords": "关键词",
    "introduction": "引言", "methods": "方法", "results": "结果",
    "discussion": "讨论", "conclusion": "结论", "references": "参考文献",
    "body": "正文", "table": "表格",
}


def _clean(text):
    return text.strip().strip("#").strip()


def _heading_section(text):
    clean = _clean(text)
    for name, pattern in SECTION_PATTERNS:
        if re.match(pattern, clean, re.I):
            return name
    return None


def _prefix_section(text):
    clean = _clean(text)
    for name, pattern in SECTION_PREFIX_PATTERNS:
        if re.match(pattern, clean, re.I):
            return name
    return None


def _numbered_major_section(text):
    clean = _clean(text)
    m = re.match(r"^\s*(\d+)\s*[.．、]?\s+(.{1,30})$", clean)
    if not m:
        return None
    label = re.sub(r"\s+", "", m.group(2))
    if re.search(r"(?:资料与方法|材料与方法|对象与方法|研究对象与方法|研究方法|方法)$", label):
        return "methods"
    if re.search(r"结果$", label):
        return "results"
    if re.search(r"讨论$", label):
        return "discussion"
    if re.search(r"结论$", label):
        return "conclusion"
    return None


def _looks_heading(text, style=""):
    clean = _clean(text)
    if not clean or len(clean) > 80:
        return False
    if _heading_section(clean):
        return True
    if style and ("heading" in style.lower() or "标题" in style):
        return True
    return bool(re.match(r"^(?:\d+(?:\.\d+){0,3}|[一二三四五六七八九十]+)[　\s、.]\s*\S+", clean))


def _is_separator(text):
    clean = re.sub(r"\s", "", text)
    return bool(clean and len(clean) >= 4 and re.fullmatch(r"[*＊_—－-]+", clean))


def _is_reference_entry(text):
    """识别带编号或不带编号的中英文文后条目。"""
    clean = text.strip()
    if len(clean) < 12:
        return False
    numbered = bool(re.match(r"^[\[［〔]?\d+[\]］〕．.、)]\s*", clean))
    doc_type = bool(re.search(r"\[[A-Za-z]/?[A-Za-z]?\]", clean))
    year = bool(re.search(r"(?:19|20)\d{2}", clean))
    journal_tail = bool(re.search(
        r"(?:19|20)\d{2}\s*[,，]\s*\d+(?:\s*[（(]\d+[）)])?\s*[:：]\s*\d+", clean))
    doi = bool(re.search(r"(?:doi|10\.\d{4,}|https?://)", clean, re.I))
    authorish = bool(re.match(
        r"^(?:[\u4e00-\u9fff]{2,4}[,，、]|[A-Z][A-Za-z'’-]+(?:\s+[A-Z]{1,3})?[,，])", clean))
    bracket_numbered = bool(re.match(r"^[\[［〔]\d+[\]］〕]", clean))
    return (numbered and (doc_type or year or journal_tail or doi or bracket_numbered)) or (
        authorish and (doc_type or journal_tail or doi))


def _find_reference_start(lines):
    """用连续条目回溯无标题参考文献的起点。"""
    flags = [_is_reference_entry((x if isinstance(x, str) else x.get("text", "")))
             for x in lines]
    for i in range(len(flags)):
        if flags[i] and len(flags) == 1:
            return i
        if not flags[i]:
            continue
        window = flags[i:i + 5]
        if len(window) >= 3 and sum(window) >= 3:
            tail = flags[i:]
            if sum(tail) >= 5 and sum(tail) / max(1, len(tail)) >= 0.55:
                return i
    return None


def _front_section(text):
    clean = _clean(text)
    if re.match(rf"^{BRACKET}(?:摘要|Abstract){END_BRACKET}\s*[:：]?", clean, re.I):
        return "abstract"
    if re.match(rf"^{BRACKET}(?:关键词|Key\s*words?){END_BRACKET}\s*[:：]?", clean, re.I):
        return "keywords"
    return None


def build_map(lines):
    """为解析后的行/段落增加稳定位置、章节、标题路径和语境属性。"""
    raw_lines = [dict(x) if isinstance(x, dict) else {"text": str(x)} for x in lines]
    reference_start = _find_reference_start(raw_lines)
    blocks = []
    section = "front"
    heading = ""
    title_seen = False
    body_started = False
    paragraph_no = 0
    table_counts = {}

    for raw_index, item in enumerate(raw_lines):
        text = item.get("text", "")
        if not text.strip():
            continue
        source = item.get("source", "正文")
        if source == "表格":
            table_no = int(item.get("table", 1) or 1)
            table_counts[table_no] = table_counts.get(table_no, 0) + 1
            row, col = item.get("row"), item.get("col")
            location = (f"表{table_no}—第{row}行第{col}列" if row and col
                        else f"表{table_no}—单元格{table_counts[table_no]}")
            stable_id = f"T{table_no:02d}R{int(row or table_counts[table_no]):03d}C{int(col or 1):02d}"
            block_section, kind = "table", "table_cell"
        else:
            paragraph_no += 1
            stable_id = f"P{paragraph_no:04d}"
            style = item.get("style", "")
            detected, prefixed = _heading_section(text), _prefix_section(text)
            detected = detected or _numbered_major_section(text)
            front = _front_section(text)
            if reference_start is not None and raw_index >= reference_start:
                section, heading, kind = "references", "参考文献", "reference"
            elif detected:
                section, heading, kind = detected, _clean(text), "heading"
                body_started = body_started or detected in {
                    "introduction", "methods", "results", "discussion", "conclusion"}
            elif prefixed:
                section, heading, kind = prefixed, SECTION_CN[prefixed], "paragraph"
                body_started = body_started or prefixed in {
                    "methods", "results", "discussion", "conclusion"}
            elif not title_seen and not _is_separator(text):
                section, heading, kind, title_seen = "title", text.strip(), "title", True
            elif _is_separator(text):
                kind = "separator"
            elif front and not body_started:
                section, heading, kind = front, SECTION_CN[front], "paragraph"
            elif (section == "abstract" and not body_started and
                  len(text) >= 80 and re.search(
                      r"^(?:近年来|目前|随着|近年|在[^。]{0,20}中|[\u4e00-\u9fff]{2,20}是)",
                      text.strip())):
                section, heading, kind, body_started = "body", "正文", "paragraph", True
            elif _looks_heading(text, style):
                # 数字小标题是正文边界；可使摘要状态正确结束，也支持综述章节。
                if re.match(r"^\s*(?:\d+(?:\.\d+){0,3}|[一二三四五六七八九十]+)[　\s、.]", _clean(text)):
                    section = section if section in {"methods", "results", "discussion", "conclusion"} else "body"
                    body_started = True
                elif section in {"title", "abstract", "keywords", "front"}:
                    section, body_started = "body", True
                heading, kind = _clean(text), "heading"
            else:
                if section in {"title", "keywords"} and title_seen:
                    section, body_started = "body", True
                elif section == "abstract" and body_started:
                    section = "body"
                elif section == "front":
                    section, body_started = "body", True
                kind = "paragraph"
            block_section = section
            location = f"{SECTION_CN.get(block_section, block_section)}—{stable_id}"
            if heading and kind not in {"heading", "title", "separator"} and block_section not in {"title", "front"}:
                location = f"{heading}—{stable_id}"

        example_cue = bool(re.search(r"(?:例如|如写作|错例|反例|示例)", text))
        excluded_spans = []
        if example_cue:
            for pat in (r"“[^”]{1,160}”", r"『[^』]{1,160}』", r"‘[^’]{1,160}’", r'"[^"\n]{1,160}"'):
                excluded_spans.extend((m.start(), m.end()) for m in re.finditer(pat, text))
        exclude_entire = False
        item.update({
            "index": len(blocks) + 1, "id": stable_id, "section": block_section,
            "section_cn": SECTION_CN.get(block_section, block_section), "heading": heading,
            "kind": kind, "location": location, "quoted_or_example": example_cue or exclude_entire,
            "excluded_spans": sorted(excluded_spans), "exclude_entire": exclude_entire,
        })
        blocks.append(item)
    return blocks


def map_summary(blocks):
    counts = {}
    for b in blocks:
        counts[b["section"]] = counts.get(b["section"], 0) + 1
    return counts


def structure_quality(blocks):
    """返回结构质量信号，用于报告门禁和模块降级。"""
    counts = map_summary(blocks)
    text_blocks = [b for b in blocks if b.get("source") != "表格"]
    total = max(1, len(text_blocks))
    title_ratio = counts.get("title", 0) / total
    abstract_ratio = counts.get("abstract", 0) / total
    reference_ratio = counts.get("references", 0) / total
    warnings = []
    if title_ratio > 0.25:
        warnings.append("题名区域异常扩大")
    if abstract_ratio > 0.35:
        warnings.append("摘要区域异常扩大")
    if counts.get("references", 0) == 0 and total >= 30:
        warnings.append("未识别参考文献区域")
    if reference_ratio > 0.75:
        warnings.append("参考文献区域异常扩大")
    status = "reliable" if not warnings else ("limited" if len(warnings) == 1 else "unreliable")
    return {"status": status, "warnings": warnings, "counts": counts}
