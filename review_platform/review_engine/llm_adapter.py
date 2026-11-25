"""可插拔大模型审校候选适配器（移植自 medical-journal-reviewer/src/services/aiService.ts）。

包含经过实战验证的三项核心防线：
1. 括号配平 JSON 提取 + 截断安全修复（extract_json_block / repair_json / parse_model_json）
2. 运行时动态时间基准（防止训练截止早于当前年份导致把 2026 等当期年份误判为“未来时间”）
3. AI 候选隔离协议：大模型仅能输出 ai_candidate，必须通过 FindingEngine 锚点与规范依据门禁。
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from review_platform.models import DocumentIR, Finding, JournalProfile

FUTURE_MISJUDGE_RE = re.compile(r"未来|尚未发生|还未发生|没有发生|不存在于|无法核实|疑似虚构|不可信")
TIME_REFERENCE_RE = re.compile(r"日期|时间|年份|时点|20\d{2}\s*年")


def build_time_anchored_system_prompt(now: datetime.datetime | None = None) -> str:
    """构建带运行时日期基准的系统提示词，消除当期年份误判。"""
    tz = ZoneInfo("Asia/Shanghai")
    current = now.astimezone(tz) if now else datetime.datetime.now(tz)
    today = f"{current.year}年{current.month}月{current.day}日"
    return (
        "你是一位专业的中文医学期刊审校专家，请用中文回复。"
        "需要引用原文时，字符串内一律使用「」，不要使用未转义的英文双引号。"
        "只输出合法 JSON，不要输出 Markdown 说明。\n\n"
        f"【时间基准】今天是 {today}。本次审校的稿件是当期投稿，正文、数据与参考文献中出现的"
        "近期年份（含今年及前一年）均为真实已发生时间。"
        "严禁以「日期在未来」「时间点尚未到达」「该年份不存在」等理由提出批注或扣分。"
    )


def is_future_date_misjudgment(item: dict[str, Any]) -> bool:
    """过滤模型无视时间基准仍将当期年份误判为未来的噪声条目。"""
    text = " ".join(
        str(item.get(k) or "")
        for k in ("problemType", "message", "description", "suggestion", "reason")
    )
    return bool(FUTURE_MISJUDGE_RE.search(text) and TIME_REFERENCE_RE.search(text))


def extract_json_block(text: str) -> str | None:
    """从纯 JSON、代码块包裹或夹杂文字的模型输出中以括号配平提取 JSON 主体。"""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    src = fenced.group(1) if fenced else text

    def balanced_from(start: int) -> str | None:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(src)):
            ch = src[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[start : i + 1]
        return None

    first_brace = src.find("{")
    if first_brace >= 0 and balanced_from(first_brace) is None:
        return src[first_brace:]

    candidates: list[str] = []
    pos = src.find("{")
    while pos >= 0:
        block = balanced_from(pos)
        if block:
            candidates.append(block)
        pos = src.find("{", pos + 1)

    if candidates:
        candidates.sort(key=len, reverse=True)
        for block in candidates:
            for probe in (block, repair_json(block)):
                try:
                    json.loads(probe)
                    return block
                except Exception:
                    continue
        return candidates[0]

    return src[first_brace:] if first_brace >= 0 else None


def repair_json(raw: str) -> str:
    """修复中文引号、尾逗号、对象漏逗号及 max_tokens 截断未闭合括号。"""
    out = raw.replace("\u201c", '"').replace("\u201d", '"')
    out = out.replace("\u2018", "'").replace("\u2019", "'")
    out = re.sub(r",\s*([}\]])", r"\1", out)
    out = re.sub(r"\}(\s*)\{", r"},\1{", out)
    out = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)\s*(?=\[|\{)', r'"\1": ', out)

    in_str = False
    escaped = False
    stack: list[str] = []
    for ch in out:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]") and stack:
            stack.pop()

    if in_str:
        out += '"'
    out = re.sub(r",\s*$", "", out)
    while stack:
        out += stack.pop()
    return out


def _scan_json(text: str) -> tuple[bool, list[int]]:
    commas: list[int] = []
    stack: list[str] = []
    in_str = False
    escaped = False
    well_formed = True

    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if not stack or stack.pop() != ch:
                well_formed = False
        elif ch == ",":
            commas.append(i)

    return well_formed and not stack and not in_str, commas


def parse_model_json(response: str) -> dict[str, Any]:
    """多级容错解析模型 JSON 响应。"""
    if not response.strip():
        raise ValueError("模型响应为空")
    block = extract_json_block(response)
    if not block:
        raise ValueError(f"无法从响应中提取 JSON（长度 {len(response)}）")

    try:
        parsed = json.loads(block)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    balanced, commas = _scan_json(block)
    attempts = [repair_json(block)]
    if not balanced:
        for idx in reversed(commas):
            if len(attempts) >= 60:
                break
            head = block[:idx]
            if not _scan_json(head)[0]:
                attempts.append(repair_json(head))

    for cand in attempts:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    raise ValueError("模型返回的 JSON 无法解析与修复")


class AICandidateExecutor(Protocol):
    """V0.4+ 可插拔大模型语义审校执行器接口（仅返回候选 Finding，须过门禁）。"""

    def generate_candidates(
        self, document: DocumentIR, journal: JournalProfile
    ) -> list[Finding]:
        ...
