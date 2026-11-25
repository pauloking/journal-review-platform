"""Review Engine 核心执行器：按 Router DAG 顺序执行事实抽取与确定性规则。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

import httpx

from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import (
    Anchor,
    AnchorVerification,
    DocumentIR,
    ExecutionPlan,
    Fact,
    Finding,
    JournalProfile,
    LLMConfig,
    ReviewTask,
)
from review_platform.review_engine.fact_extractor import extract_facts
from review_platform.review_engine.llm_adapter import resolve_llm_config, run_semantic_rule_with_llm
from review_platform.review_engine.pattern_executor import execute_pattern_rule
from review_platform.rule_registry import RuleRegistry


def _stable_finding_id(rule_id: str, anchor: Anchor, tag: str = "") -> str:
    raw = f"{rule_id}:{anchor.block_id}:{anchor.start}:{anchor.end}:{anchor.quote}:{tag}".encode(
        "utf-8"
    )
    return f"f-{hashlib.sha256(raw).hexdigest()[:12]}"


def _validate_p_range(
    facts: list[Fact],
    knowledge: KnowledgeEngine,
) -> tuple[list[Finding], int, int]:
    rule_id = "STAT-PRANGE"
    basis_id = "LOCAL-MATH-PRANGE"
    clause = knowledge.get_clause(basis_id)
    basis_source = clause.source if clause else ""

    findings: list[Finding] = []
    checked = 0
    excluded = 0

    for fact in facts:
        if fact.kind != "p_value":
            continue
        if fact.status == "excluded":
            excluded += 1
            continue
        checked += 1
        raw_val = str(fact.values.get("raw_value", ""))
        try:
            val = Decimal(raw_val)
        except InvalidOperation:
            continue
        if val < Decimal("0") or val > Decimal("1"):
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, raw_val),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="P 值与检验水准数值范围校验",
                    category="stats",
                    scope="professional",
                    severity="error",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=f"报告数值「{fact.anchor.quote}」对应的 P={raw_val} 超出概率区间 [0, 1]",
                    suggestion="请编辑核实原始统计输出与小数点位置（严禁自动篡改数据）",
                    evidence={
                        "raw_value": raw_val,
                        "valid_interval": "[0, 1]",
                        "section": fact.scope,
                    },
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )
    return findings, checked, excluded


def _validate_percentage(
    facts: list[Fact],
    journal: JournalProfile,
    knowledge: KnowledgeEngine,
) -> tuple[list[Finding], int, int]:
    rule_id = "DATA-PCT-RECALC"
    basis_id = "LOCAL-MATH-PERCENTAGE"
    clause = knowledge.get_clause(basis_id)
    basis_source = clause.source if clause else ""

    findings: list[Finding] = []
    checked = 0
    excluded = 0
    journal_tol = Decimal(str(journal.percentage_tolerance))

    for fact in facts:
        if fact.kind != "percentage":
            continue
        if fact.status == "excluded":
            excluded += 1
            continue
        checked += 1
        n_str = str(fact.values.get("numerator", "0"))
        N_str = str(fact.values.get("denominator", "0"))
        rep_str = str(fact.values.get("reported_pct", "0"))

        try:
            n_val = Decimal(n_str)
            N_val = Decimal(N_str)
            rep_val = Decimal(rep_str)
        except InvalidOperation:
            continue

        if N_val <= 0:
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, "zero_denom"),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="频数百分比 n/N(pct%) 精确复算校验",
                    category="data",
                    scope="professional",
                    severity="error",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=f"百分比分母 N={N_str} 无效（不能为 0 或负数）：「{fact.anchor.quote}」",
                    suggestion="请编辑核实总例数 N 与原始数据记录",
                    evidence={"n": n_str, "N": N_str, "reported_pct": rep_str},
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )
            continue

        if n_val > N_val:
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, "num_gt_denom"),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="频数百分比 n/N(pct%) 精确复算校验",
                    category="data",
                    scope="professional",
                    severity="error",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=f"分子 n={n_str} 大于分母 N={N_str}：「{fact.anchor.quote}」",
                    suggestion="请编辑核实分子与分母是否颠倒或统计口径不一致",
                    evidence={"n": n_str, "N": N_str, "reported_pct": rep_str},
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )
            continue

        expected = (n_val / N_val) * Decimal("100")
        decimals = len(rep_str.split(".")[1]) if "." in rep_str else 0
        half_unit = Decimal("0.5") * (Decimal("10") ** (-decimals))
        tolerance = max(half_unit, journal_tol)
        diff = abs(rep_val - expected)

        if diff > tolerance:
            expected_round = expected.quantize(Decimal("10") ** (-max(1, decimals)))
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, f"{rep_str}!={expected_round}"),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="频数百分比 n/N(pct%) 精确复算校验",
                    category="data",
                    scope="professional",
                    severity="error",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=(
                        f"报告百分比 {rep_str}% 与 {n_str}/{N_str} 复算结果 {expected_round}% 不一致"
                        f"（差值 {diff:.2f} 个百分点，超出容差 {tolerance}）"
                    ),
                    suggestion="请编辑核实原始分子分母或四舍五入后百分比，严禁未核实直接修改数据",
                    evidence={
                        "n": n_str,
                        "N": N_str,
                        "reported_pct": rep_str,
                        "expected_pct": str(expected_round),
                        "diff": f"{diff:.4f}",
                        "tolerance": str(tolerance),
                    },
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )

    return findings, checked, excluded


def _validate_captions(
    facts: list[Fact],
    journal: JournalProfile,
    knowledge: KnowledgeEngine,
) -> tuple[list[Finding], int, int]:
    rule_id = "STRUCT-CAPTION-SEQ"
    basis_id = "LOCAL-STRUCT-CAPTION"
    clause = knowledge.get_clause(basis_id)
    basis_source = clause.source if clause else ""

    findings: list[Finding] = []
    checked = 0
    excluded = 0
    expected_num: dict[str, int] = {"图": journal.caption_start, "表": journal.caption_start}
    seen_nums: dict[str, set[int]] = {"图": set(), "表": set()}

    for fact in facts:
        if fact.kind != "caption":
            continue
        if fact.status == "excluded":
            excluded += 1
            continue
        checked += 1
        ltype = str(fact.values.get("label_type", "图"))
        num = int(fact.values.get("number", 0))
        exp = expected_num.get(ltype, journal.caption_start)

        if num in seen_nums[ltype]:
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, f"dup_{ltype}{num}"),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="图表题注顺序连续性校验",
                    category="structure",
                    scope="journal",
                    severity="warning",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=f"题注「{fact.anchor.quote}」重复出现（前文已出现过 {ltype}{num}）",
                    suggestion=f"请核对全文{ltype}题注编号顺序并消除重复编号",
                    evidence={"label_type": ltype, "actual": num, "expected": exp, "issue": "duplicate"},
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )
            continue

        if num != exp:
            findings.append(
                Finding(
                    id=_stable_finding_id(rule_id, fact.anchor, f"jump_{ltype}{num}_exp{exp}"),
                    rule_id=rule_id,
                    rule_version="1.0.0",
                    basis_id=basis_id,
                    basis_source=basis_source,
                    title="图表题注顺序连续性校验",
                    category="structure",
                    scope="journal",
                    severity="warning",
                    anchor=fact.anchor,
                    fact_ids=[fact.id],
                    message=f"题注「{fact.anchor.quote}」编号跳号：期望 {ltype}{exp}，实际 {ltype}{num}",
                    suggestion=f"请核对是否遗漏 {ltype}{exp} 或按出现顺序重新编号",
                    evidence={"label_type": ltype, "actual": num, "expected": exp, "issue": "sequence_gap"},
                    source_type="deterministic",
                    verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
                )
            )
        seen_nums[ltype].add(num)
        expected_num[ltype] = num + 1

    return findings, checked, excluded


def execute_plan(
    document: DocumentIR,
    journal: JournalProfile,
    plan: ExecutionPlan,
    registry: RuleRegistry,
    knowledge: KnowledgeEngine,
    *,
    fact_extractor_fn: Callable[[DocumentIR], list[Fact]] = extract_facts,
    fail_nodes: Optional[set[str]] = None,
    llm_config: Optional[LLMConfig] = None,
    llm_client_factory: Optional[Callable[[float], httpx.Client]] = None,
) -> tuple[ExecutionPlan, list[Fact], list[Finding], bool]:
    """按 ExecutionPlan 顺序执行任务，任何节点失败时严格阻断下游依赖节点；支持并发执行受控 AI 语义规则。"""
    fail_set = fail_nodes or set()
    resolved_llm = resolve_llm_config(llm_config)
    facts: list[Fact] = []
    raw_findings: list[Finding] = []
    task_status: dict[str, str] = {}
    has_failure = False

    updated_tasks: list[ReviewTask] = []
    pending_ai_indices: list[tuple[int, ReviewTask]] = []

    for task in plan.tasks:
        t = task.model_copy(deep=True)
        blocked_by = [
            dep for dep in t.dependencies if task_status.get(dep) in {"failed", "blocked"}
        ]
        if blocked_by:
            t.status = "blocked"
            t.reason = f"blocked_by:{','.join(blocked_by)}"
            task_status[t.node_id] = "blocked"
            updated_tasks.append(t)
            continue

        if t.status == "skipped":
            task_status[t.node_id] = "skipped"
            updated_tasks.append(t)
            continue

        try:
            if t.node_id in fail_set:
                raise RuntimeError(f"injected_failure:{t.node_id}")

            if t.node_id == "extract.facts":
                facts = fact_extractor_fn(document)
                t.checked_count = len(facts)
                t.excluded_count = sum(1 for f in facts if f.status == "excluded")
                t.status = "passed"
                t.reason = "facts_extracted"
                task_status[t.node_id] = "passed"
                updated_tasks.append(t)
                continue

            rule = registry.get(t.rule_id)
            clause = knowledge.get_clause(rule.basis_id)
            basis_source = clause.source if clause else ""

            if rule.executor_type == "ai_candidate":
                if not resolved_llm.enabled:
                    t.status = "skipped"
                    t.reason = "llm_not_configured"
                    task_status[t.node_id] = "skipped"
                    updated_tasks.append(t)
                    continue
                if resolved_llm.enabled_rules and rule.id not in resolved_llm.enabled_rules:
                    t.status = "skipped"
                    t.reason = "llm_rule_unselected"
                    task_status[t.node_id] = "skipped"
                    updated_tasks.append(t)
                    continue
                idx = len(updated_tasks)
                updated_tasks.append(t)
                pending_ai_indices.append((idx, t))
                continue

            if rule.executor_type == "fact_validator":
                if rule.id == "STAT-PRANGE":
                    f_list, chk, exc = _validate_p_range(facts, knowledge)
                elif rule.id == "DATA-PCT-RECALC":
                    f_list, chk, exc = _validate_percentage(facts, journal, knowledge)
                elif rule.id == "STRUCT-CAPTION-SEQ":
                    f_list, chk, exc = _validate_captions(facts, journal, knowledge)
                else:
                    raise RuntimeError(f"未实现的 fact_validator: {rule.id}")
            elif rule.executor_type == "pattern_rule":
                f_list, chk, exc = execute_pattern_rule(rule, document, journal, basis_source)
            else:
                f_list, chk, exc = [], 0, 0

            raw_findings.extend(f_list)
            t.checked_count = chk
            t.excluded_count = exc
            t.finding_count = len(f_list)
            t.status = "findings" if f_list else "passed"
            t.reason = f"findings:{len(f_list)}" if f_list else "all_checked_ok"
            task_status[t.node_id] = t.status

        except Exception as exc:
            has_failure = True
            t.status = "failed"
            t.reason = f"error:{exc}"
            task_status[t.node_id] = "failed"

        updated_tasks.append(t)

    # 并发执行就绪的 AI 语义审校任务，显著缩短端到端等待时间
    if pending_ai_indices:
        def _run_one(task_obj: ReviewTask) -> tuple[list[Finding], int, int]:
            r = registry.get(task_obj.rule_id)
            c = knowledge.get_clause(r.basis_id)
            return run_semantic_rule_with_llm(
                r,
                document,
                journal,
                resolved_llm,
                c.source if c else "",
                client_factory=llm_client_factory,
            )

        with ThreadPoolExecutor(max_workers=min(4, len(pending_ai_indices))) as pool:
            futures = [(idx, t, pool.submit(_run_one, t)) for idx, t in pending_ai_indices]
            for idx, t, fut in futures:
                try:
                    f_list, chk, exc = fut.result()
                    raw_findings.extend(f_list)
                    t.checked_count = chk
                    t.excluded_count = exc
                    t.finding_count = len(f_list)
                    t.status = "findings" if f_list else "passed"
                    trunc_note = ""
                    if any(bool(f.evidence.get("context_truncated")) for f in f_list) or sum(len(b.text) for b in document.blocks) > 24000:
                        trunc_note = ", context_truncated_24k"
                    t.reason = (
                        f"ai_findings:{len(f_list)} ({resolved_llm.provider}/{resolved_llm.model}{trunc_note})"
                        if f_list
                        else f"ai_checked_ok ({resolved_llm.provider}/{resolved_llm.model}{trunc_note})"
                    )
                except Exception as exc:
                    has_failure = True
                    t.status = "failed"
                    t.reason = f"error:{exc}"
                task_status[t.node_id] = t.status
                updated_tasks[idx] = t

    final_plan = plan.model_copy(update={"tasks": updated_tasks})
    return final_plan, facts, raw_findings, has_failure
