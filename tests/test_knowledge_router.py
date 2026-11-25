from __future__ import annotations

import pytest

from review_platform.document_engine import parse_docx_bytes
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import RuleSpec
from review_platform.review_engine import execute_plan
from review_platform.router import RouterError, build_execution_plan
from review_platform.rule_registry import RuleRegistry
from tests.conftest import make_docx_bytes


def test_registry_rejects_fabricated_basis_id():
    knowledge = KnowledgeEngine()
    bad_rule = RuleSpec(
        id="FAKE-01",
        title="伪造依据测试",
        category="stats",
        scope="national",
        basis_id="STD-NONEXISTENT-9999",
    )
    with pytest.raises(ValueError, match="不存在于知识引擎"):
        RuleRegistry(knowledge=knowledge, extra_rules=[bad_rule])


def test_router_zero_silent_omission_and_failure_blocking():
    knowledge = KnowledgeEngine()
    registry = RuleRegistry(knowledge=knowledge)
    raw = make_docx_bytes(["题名测试", "摘要：观察组 30/100（40%），P=1.25。"])
    doc = parse_docx_bytes(raw)
    journal = knowledge.get_journal("core-math-only")

    plan = build_execution_plan(doc, journal, registry)
    node_ids = {t.node_id for t in plan.tasks}
    # 每个已注册规则必须在计划中显式出现
    for r in registry.list_rules():
        assert r.id in node_ids

    # 被禁用的规则必须显式为 skipped 且带理由
    signif_task = next(t for t in plan.tasks if t.node_id == "STA-SIGNIF")
    assert signif_task.status == "skipped"
    assert "journal_disabled" in signif_task.reason

    # 注入 extract.facts 失败，下游 fact_validator 必须显式 blocked
    failed_plan, _, _, has_failure = execute_plan(
        doc, journal, plan, registry, knowledge, fail_nodes={"extract.facts"}
    )
    assert has_failure is True
    extract_task = next(t for t in failed_plan.tasks if t.node_id == "extract.facts")
    p_task = next(t for t in failed_plan.tasks if t.node_id == "STAT-PRANGE")
    assert extract_task.status == "failed"
    assert p_task.status == "blocked"


def test_router_rejects_cycle_and_unknown_disabled_rule():
    knowledge = KnowledgeEngine()
    r1 = RuleSpec(
        id="CYC-A",
        title="环A",
        category="stats",
        scope="professional",
        basis_id="LOCAL-MATH-PRANGE",
        dependencies=["CYC-B"],
    )
    r2 = RuleSpec(
        id="CYC-B",
        title="环B",
        category="stats",
        scope="professional",
        basis_id="LOCAL-MATH-PRANGE",
        dependencies=["CYC-A"],
    )
    registry = RuleRegistry(knowledge=knowledge, extra_rules=[r1, r2])
    doc = parse_docx_bytes(make_docx_bytes(["标题", "摘要：正文内容。"]))
    journal = knowledge.get_journal("general-medical-cn")

    with pytest.raises(RouterError, match="环依赖"):
        build_execution_plan(doc, journal, registry)

    clean_reg = RuleRegistry(knowledge=knowledge)
    bad_journal = journal.model_copy(update={"disabled_rules": ["NOT-A-REAL-RULE"]})
    with pytest.raises(RouterError, match="不存在的规则"):
        build_execution_plan(doc, bad_journal, clean_reg)
