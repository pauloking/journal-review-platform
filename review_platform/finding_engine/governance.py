"""审校结果门禁与治理：严禁无有效原文定位或无真实依据的意见进入正式报告。"""

from __future__ import annotations

from typing import Any

from review_platform.document_engine import validate_anchor
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import AnchorVerification, DocumentIR, Finding


def govern_findings(
    document: DocumentIR,
    knowledge: KnowledgeEngine,
    candidates: list[Finding],
) -> tuple[list[Finding], list[dict[str, Any]]]:
    """对候选 Finding 执行锚点门禁、依据存在性门禁与同 ID 去重。"""
    verified: list[Finding] = []
    rejected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for cand in candidates:
        anchor_check = validate_anchor(document, cand.anchor)
        clause = knowledge.get_clause(cand.basis_id)
        basis_valid = clause is not None

        if not anchor_check.anchor_valid or not basis_valid:
            reasons = []
            if not anchor_check.anchor_valid:
                reasons.append(anchor_check.reason)
            if not basis_valid:
                reasons.append(f"知识依据不存在: {cand.basis_id}")
            rejected.append({
                "candidate_id": cand.id,
                "rule_id": cand.rule_id,
                "basis_id": cand.basis_id,
                "source_type": cand.source_type,
                "reason": "; ".join(reasons),
            })
            continue

        if cand.id in seen_ids:
            continue
        seen_ids.add(cand.id)

        updated = cand.model_copy(
            update={
                "basis_source": clause.source if clause else cand.basis_source,
                "verification": AnchorVerification(
                    anchor_valid=True,
                    basis_valid=True,
                    reason="verified",
                ),
            }
        )
        verified.append(updated)

    return verified, rejected
