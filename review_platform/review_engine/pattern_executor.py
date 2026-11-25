"""复用并适配 medical-journal-copyediting-2.0.0 的确定性模式规则执行器。"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import regex

from review_platform.document_engine.text_utils import is_reference_entry as _is_reference_entry
from review_platform.models import (
    Anchor,
    AnchorVerification,
    Block,
    DocumentIR,
    Finding,
    JournalProfile,
    RuleSpec,
)

RULE_EXEC_BUDGET_MS = 250.0
REGEX_MATCH_TIMEOUT_SEC = 0.2


def _in_excluded_span(block: Block, start: int, end: int) -> bool:
    if block.exclude_entire:
        return True
    for ex_s, ex_e in block.excluded_spans:
        if start >= ex_s and end <= ex_e:
            return True
    return False


def _format_template(tpl: str, values: dict[str, Any]) -> str:
    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    try:
        return tpl.format_map(_SafeDict(values))
    except Exception:
        return tpl


def _should_skip_match(
    rule: RuleSpec,
    block: Block,
    m: re.Match[str],
    line_text: str,
) -> str | None:
    params = rule.params
    start, end = m.start(), m.end()
    found = m.group(0)

    if _in_excluded_span(block, start, end):
        return "quoted_example_context"

    # 参考文献条目特殊保护：常规数字/标点/单位规则不误报参考文献条目
    is_ref_block = block.section == "references" or _is_reference_entry(line_text)
    if params.get("ref_only"):
        if not (block.section == "references" and _is_reference_entry(line_text)):
            return "not_reference_entry"
    elif is_ref_block and rule.category in {"numbers", "punct", "units"}:
        return "protected_reference_entry"

    # 年份范围豁免（如 2020-2025）
    if params.get("skip_if_year") and m.lastindex and m.lastindex >= 2:
        try:
            left_v = int(float(m.group(1)))
            right_v = int(float(m.group(2)))
            if 1900 <= left_v <= 2099 and 1900 <= right_v <= 2099:
                return "year_range_exempt"
        except ValueError:
            pass

    # 上下文前缀豁免（如 "图1-2"、"表2-1"）
    skip_ctx = params.get("skip_context") or []
    if skip_ctx and start > 0:
        prev_char = line_text[max(0, start - 3) : start]
        if any(ctx in prev_char for ctx in skip_ctx):
            return "skip_context_match"

    # exception_after 豁免（如 "血压高" 后接 "于"/"升" -> "血压高于"）
    exc_after = params.get("exception_after") or []
    if exc_after:
        after_text = line_text[end : end + 4]
        if any(after_text.startswith(tok) for tok in exc_after):
            return "exception_after_match"

    # exclude_pattern（整段含排除模式则跳过，如 STA-METHOD 已含检验方法名）
    excl_pat = params.get("exclude_pattern")
    if excl_pat and regex.search(excl_pat, line_text, timeout=REGEX_MATCH_TIMEOUT_SEC):
        return "exclude_pattern_matched"

    # NUM-LEADZERO 额外保护：避免把 "第1.2节" 或 "1.2.3" 或小数后版本误判
    if rule.id == "NUM-LEADZERO":
        if start > 0 and line_text[start - 1].isdigit():
            return "preceded_by_digit"

    # NUM-RANGE 额外保护：连字符左右若紧邻 ASCII 字母或正负号/斜杠（如 COVID-19 或 P-01）跳过
    if rule.id == "NUM-RANGE":
        before = line_text[start - 1] if start > 0 else ""
        after = line_text[end] if end < len(line_text) else ""
        if (before.isascii() and before.isalpha()) or (after.isascii() and after.isalpha()) or before in ("-", "－", "/"):
            return "identifier_hyphen_exempt"

    return None


def execute_pattern_rule(
    rule: RuleSpec,
    doc: DocumentIR,
    journal: JournalProfile,
    basis_source: str,
) -> tuple[list[Finding], int, int]:
    """执行声明式模式规则，返回 (findings, checked_count, excluded_count)。"""
    params = rule.params
    pattern = params.get("pattern", "")
    if not pattern:
        return [], 0, 0

    is_regex = bool(params.get("is_regex", False))
    whole_line = bool(params.get("whole_line", False))
    applies_to = set(rule.applies_to) if rule.applies_to else None

    compiled = regex.compile(pattern) if is_regex else regex.compile(regex.escape(pattern))
    t_start = time.perf_counter()
    findings: list[Finding] = []
    checked = 0
    excluded = 0
    dedupe_seen: set[str] = set()
    dedupe_key = params.get("dedupe_key") or ""

    for block in doc.blocks:
        if (time.perf_counter() - t_start) * 1000.0 > RULE_EXEC_BUDGET_MS:
            raise TimeoutError(f"规则 {rule.id} 执行耗时超出预算（>{RULE_EXEC_BUDGET_MS}ms），已安全熔断")
        if applies_to and block.section not in applies_to:
            continue
        if block.kind == "separator":
            continue

        text = block.text
        if whole_line:
            checked += 1
            if _in_excluded_span(block, 0, len(text)):
                excluded += 1
                continue
            if params.get("ref_only") and not (
                block.section == "references" and _is_reference_entry(text)
            ):
                excluded += 1
                continue
            excl_pat = params.get("exclude_pattern")
            if excl_pat and regex.search(excl_pat, text, timeout=REGEX_MATCH_TIMEOUT_SEC):
                continue
            if not text.strip():
                continue
            quote = text.strip()
            start = text.find(quote)
            end = start + len(quote)
            findings.append(
                _make_finding(
                    rule=rule,
                    doc=doc,
                    block=block,
                    start=start,
                    end=end,
                    quote=quote,
                    basis_source=basis_source,
                    fmt_vals={"found": quote, "suggestion": rule.suggestion_template},
                    evidence={"match_mode": "whole_line", "section": block.section},
                )
            )
            continue

        for m in compiled.finditer(text, timeout=REGEX_MATCH_TIMEOUT_SEC):
            checked += 1
            reason = _should_skip_match(rule, block, m, text)
            if reason:
                excluded += 1
                continue

            if dedupe_key:
                if dedupe_key in dedupe_seen:
                    excluded += 1
                    continue
                dedupe_seen.add(dedupe_key)

            start, end = m.start(), m.end()
            quote = text[start:end]
            fmt_vals: dict[str, Any] = {
                "found": quote,
                "suggestion": rule.suggestion_template,
            }
            if m.lastindex and m.lastindex >= 2:
                fmt_vals["left"] = m.group(1)
                fmt_vals["right"] = m.group(2)

            suggestion = _format_template(rule.suggestion_template, fmt_vals)
            fmt_vals["suggestion"] = suggestion

            findings.append(
                _make_finding(
                    rule=rule,
                    doc=doc,
                    block=block,
                    start=start,
                    end=end,
                    quote=quote,
                    basis_source=basis_source,
                    fmt_vals=fmt_vals,
                    evidence={
                        "match_mode": "regex" if is_regex else "literal",
                        "section": block.section,
                        "matched": quote,
                    },
                )
            )

    # 期刊定制术语补充（仅在 TERM-* 家族之外由 JournalProfile.custom_terms 驱动时触发）
    return findings, checked, excluded


def _make_finding(
    *,
    rule: RuleSpec,
    doc: DocumentIR,
    block: Block,
    start: int,
    end: int,
    quote: str,
    basis_source: str,
    fmt_vals: dict[str, Any],
    evidence: dict[str, Any],
) -> Finding:
    raw_id = f"{rule.id}:{block.id}:{start}:{end}:{quote}".encode("utf-8")
    fid = f"f-{hashlib.sha256(raw_id).hexdigest()[:12]}"
    message = _format_template(rule.message_template, fmt_vals)
    suggestion = _format_template(rule.suggestion_template, fmt_vals)

    return Finding(
        id=fid,
        rule_id=rule.id,
        rule_version=rule.version,
        basis_id=rule.basis_id,
        basis_source=basis_source,
        title=rule.title,
        category=rule.category,
        scope=rule.scope,
        severity=rule.severity,
        anchor=Anchor(
            document_id=doc.document_id,
            block_id=block.id,
            start=start,
            end=end,
            quote=quote,
        ),
        fact_ids=[],
        message=message,
        suggestion=suggestion,
        evidence=evidence,
        source_type="deterministic",
        verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
    )
