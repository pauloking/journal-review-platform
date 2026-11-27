"""FactLedger 抽取器：提取显式 P 值、百分比 n/N(pct%) 与段首图表题注。"""

from __future__ import annotations

import hashlib
import re

from review_platform.models import Anchor, Block, DocumentIR, Fact

P_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])([PpＰｐ])(?:\s*值)?\s*([=＝<＜>＞≤≥])\s*([+\-−－]?[0-9０-９]*(?:[.．][0-9０-９]+|[0-9０-９]+)(?:[eEｅＥ][+\-−－]?[0-9０-９]+)?)"
)

PCT_RE = re.compile(
    r"(?<![0-9０-９.])([+\-−－]?[0-9０-９]+)\s*/\s*([0-9０-９]+)(?:\s*(?:例(?:患者)?|人|名|次|个|份|株))?\s*[（(]\s*([+\-−－]?[0-9０-９]+(?:[.．][0-9０-９]+)?)\s*[%％]\s*[)）]"
)

CAPTION_RE = re.compile(r"^\s*(图|表)\s*(\d+)(?=\s|[:：.．]|$)")
_CAPTION_STYLE_RE = re.compile(r"caption|题注|图题|表题", re.I)
_PROSE_CAPTION_TAIL_RE = re.compile(
    r"^(?:显示|表明|提示|可见|说明|示意|示|反映|呈现|证实|证明|列出|比较了|所示|如下|为|是|中|内)"
)


def _in_excluded_span(block: Block, start: int, end: int) -> str | None:
    if block.exclude_entire:
        return "excluded_entire_example_block"
    for ex_s, ex_e in block.excluded_spans:
        if start >= ex_s and end <= ex_e:
            return "quoted_example_context"
    return None


def _fact_id(kind: str, block_id: str, start: int, end: int, quote: str) -> str:
    raw = f"{kind}:{block_id}:{start}:{end}:{quote}".encode("utf-8")
    return f"fact-{hashlib.sha256(raw).hexdigest()[:12]}"


def extract_facts(doc: DocumentIR) -> list[Fact]:
    """从 DocumentIR 中确定性抽取事实台账（保留所有被例外排除的痕迹）。"""
    facts: list[Fact] = []

    for block in doc.blocks:
        text = block.text

        # 1. P 值抽取
        for m in P_VALUE_RE.finditer(text):
            start, end = m.start(), m.end()
            quote = text[start:end]
            excl = _in_excluded_span(block, start, end)
            facts.append(
                Fact(
                    id=_fact_id("p_value", block.id, start, end, quote),
                    kind="p_value",
                    anchor=Anchor(
                        document_id=doc.document_id,
                        block_id=block.id,
                        start=start,
                        end=end,
                        quote=quote,
                    ),
                    values={
                        "symbol": m.group(1),
                        "operator": m.group(2),
                        "raw_value": m.group(3),
                    },
                    scope=block.section,
                    extractor="fact_extractor.p_value/1.0.0",
                    status="excluded" if excl else "verified",
                    exclusion_reason=excl,
                )
            )

        # 2. n/N(pct%) 百分比抽取
        for m in PCT_RE.finditer(text):
            start, end = m.start(), m.end()
            quote = text[start:end]
            excl = _in_excluded_span(block, start, end)
            facts.append(
                Fact(
                    id=_fact_id("percentage", block.id, start, end, quote),
                    kind="percentage",
                    anchor=Anchor(
                        document_id=doc.document_id,
                        block_id=block.id,
                        start=start,
                        end=end,
                        quote=quote,
                    ),
                    values={
                        "numerator": m.group(1),
                        "denominator": m.group(2),
                        "reported_pct": m.group(3),
                    },
                    scope=block.section,
                    extractor="fact_extractor.percentage/1.0.0",
                    status="excluded" if excl else "verified",
                    exclusion_reason=excl,
                )
            )

        # 3. 段首图表题注抽取（表格单元格与参考文献区不计为题注；无题注样式的正文语境保留排除痕迹）
        if block.kind not in {"table_cell", "reference", "separator"}:
            cm = CAPTION_RE.match(text)
            if cm:
                start, end = cm.start(1), cm.end(2)
                quote = text[start:end]
                excl = _in_excluded_span(block, start, end)
                if excl is None:
                    style = str((block.meta or {}).get("style", ""))
                    has_caption_style = bool(_CAPTION_STYLE_RE.search(style))
                    tail = text[cm.end(2) :].strip()
                    if not has_caption_style and tail and (
                        _PROSE_CAPTION_TAIL_RE.match(tail) or tail.endswith(("。", "！", "？", "；"))
                    ):
                        excl = "body_prose_caption_reference"
                facts.append(
                    Fact(
                        id=_fact_id("caption", block.id, start, end, quote),
                        kind="caption",
                        anchor=Anchor(
                            document_id=doc.document_id,
                            block_id=block.id,
                            start=start,
                            end=end,
                            quote=quote,
                        ),
                        values={
                            "label_type": cm.group(1),
                            "number": int(cm.group(2)),
                        },
                        scope=block.section,
                        extractor="fact_extractor.caption/1.0.0",
                        status="excluded" if excl else "verified",
                        exclusion_reason=excl,
                    )
                )

    return facts
