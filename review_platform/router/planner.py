"""确定性 DAG Router：不允许静默遗漏任何规则，强校验环与缺失依赖。"""

from __future__ import annotations

import hashlib
import heapq
import json

from review_platform.models import (
    DocumentIR,
    ExecutionPlan,
    JournalProfile,
    ReviewTask,
    RuleSpec,
)
from review_platform.rule_registry import RuleRegistry


class RouterError(ValueError):
    """Router 配置或 DAG 依赖校验异常。"""


def build_execution_plan(
    document: DocumentIR,
    journal: JournalProfile,
    registry: RuleRegistry,
) -> ExecutionPlan:
    """根据稿件画像、期刊配置和规则注册表生成确定性 DAG 计划。"""
    for disabled_id in journal.disabled_rules:
        if not registry.has(disabled_id):
            raise RouterError(f"期刊配置 {journal.id} 禁用了不存在的规则: {disabled_id}")

    rules: list[RuleSpec] = registry.list_rules()
    rule_ids = {r.id for r in rules}
    builtin_nodes = {"extract.facts"}

    # 校验依赖与构建拓扑图
    indegree: dict[str, int] = {r.id: 0 for r in rules}
    children: dict[str, list[str]] = {r.id: [] for r in rules}

    for rule in rules:
        for dep in rule.dependencies:
            if dep == rule.id:
                raise RouterError(f"规则 {rule.id} 存在自环依赖")
            if dep in builtin_nodes:
                continue
            if dep not in rule_ids:
                raise RouterError(f"规则 {rule.id} 引用了不存在的前置依赖: {dep}")
            indegree[rule.id] += 1
            children[dep].append(rule.id)

    # Kahn 拓扑排序（字典序堆保证完全确定性）
    heap: list[str] = [rid for rid, deg in indegree.items() if deg == 0]
    heapq.heapify(heap)
    ordered_ids: list[str] = []

    while heap:
        curr = heapq.heappop(heap)
        ordered_ids.append(curr)
        for nxt in sorted(children[curr]):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, nxt)

    if len(ordered_ids) != len(rules):
        raise RouterError("规则依赖图存在环依赖，已拒绝生成执行计划")

    sections_present = set(document.profile.sections_present)
    disabled_set = set(journal.disabled_rules)
    enabled_scopes = set(journal.enabled_scopes)

    tasks: list[ReviewTask] = [
        ReviewTask(
            node_id="extract.facts",
            rule_id="extract.facts",
            rule_version="1.0.0",
            title="事实台账抽取（P值/百分比/题注）",
            category="extraction",
            scope="professional",
            dependencies=[],
            status="pending",
            reason="",
        )
    ]

    for rid in ordered_ids:
        rule = registry.get(rid)
        status = "pending"
        reason = ""

        if rid in disabled_set:
            status = "skipped"
            reason = f"journal_disabled:{journal.id}"
        elif rule.scope not in enabled_scopes:
            status = "skipped"
            reason = f"scope_disabled:{rule.scope}"
        elif not rule.auto:
            status = "skipped"
            reason = "manual_checklist_only"
        elif rule.applies_to and not (sections_present & set(rule.applies_to)):
            status = "skipped"
            reason = "no_applicable_sections"

        tasks.append(
            ReviewTask(
                node_id=rule.id,
                rule_id=rule.id,
                rule_version=rule.version,
                title=rule.title,
                category=rule.category,
                scope=rule.scope,
                dependencies=list(rule.dependencies),
                status=status,
                reason=reason,
            )
        )

    plan_payload = {
        "document_id": document.document_id,
        "journal_id": journal.id,
        "journal_version": journal.version,
        "rule_snapshot_hash": registry.snapshot_hash,
        "nodes": [(t.node_id, t.status, t.reason) for t in tasks],
    }
    plan_hash = hashlib.sha256(
        json.dumps(plan_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    return ExecutionPlan(
        plan_hash=plan_hash,
        document_id=document.document_id,
        journal_id=journal.id,
        journal_version=journal.version,
        rule_snapshot_hash=registry.snapshot_hash,
        tasks=tasks,
    )
