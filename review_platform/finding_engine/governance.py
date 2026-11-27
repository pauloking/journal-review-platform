"""审校结果门禁与治理：严禁无有效原文定位或无真实依据的意见进入正式报告。"""

from __future__ import annotations

from typing import Any

from review_platform.document_engine import validate_anchor
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import AnchorVerification, DocumentIR, Finding


def _within_sent_scope(anchor_block_id: str, start: int, end: int, sent_spans: dict[str, tuple[int, int]]) -> bool:
    if anchor_block_id not in sent_spans:
        return False
    s_start, s_end = sent_spans[anchor_block_id]
    return start >= s_start and end <= s_end


def govern_findings(
    document: DocumentIR,
    knowledge: KnowledgeEngine,
    candidates: list[Finding],
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """对候选 Finding 执行锚点门禁、送审范围边界门禁、关联证据核验、依据存在性门禁与同 ID 去重。"""
    verified: list[Finding] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for cand in candidates:
        ev = dict(cand.evidence or {})
        scope_err = ev.pop("_scope_error", None)
        sent_spans = ev.pop("_sent_spans", None)

        anchor_check = validate_anchor(document, cand.anchor)
        clause = knowledge.get_clause(cand.basis_id)
        basis_valid = clause is not None

        reasons: list[str] = []
        if scope_err:
            reasons.append(str(scope_err))
        elif sent_spans is not None and not _within_sent_scope(
            cand.anchor.block_id, cand.anchor.start, cand.anchor.end, sent_spans
        ):
            reasons.append(f"引文超出本次实际送审范围或截断边界: {cand.anchor.block_id}")

        if not anchor_check.anchor_valid:
            reasons.append(anchor_check.reason)
        if not basis_valid:
            reasons.append(f"知识依据不存在: {cand.basis_id}")

        for sec in cand.secondary_anchors:
            sec_chk = validate_anchor(document, sec)
            if not sec_chk.anchor_valid:
                reasons.append(f"关联证据定位无效({sec.block_id}): {sec_chk.reason}")
            elif sent_spans is not None and not _within_sent_scope(
                sec.block_id, sec.start, sec.end, sent_spans
            ):
                reasons.append(f"关联证据超出实际送审范围({sec.block_id})")

        if reasons:
            rejected.append({
                "candidate_id": cand.id,
                "rule_id": cand.rule_id,
                "basis_id": cand.basis_id,
                "source_type": cand.source_type,
                "quote": cand.anchor.quote,
                "reason": "; ".join(dict.fromkeys(reasons)),
            })
            continue

        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        updated = cand.model_copy(
            update={
                "basis_source": clause.source if clause else cand.basis_source,
                "evidence": ev,
                "verification": AnchorVerification(
                    anchor_valid=True,
                    basis_valid=True,
                    reason="verified",
                ),
            }
        )
        verified.append(updated)

    return verified, rejected
