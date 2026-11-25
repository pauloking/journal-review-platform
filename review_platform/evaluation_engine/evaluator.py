"""质量评估与反馈统计（复用 medical-journal-reviewer/server/review_log_server.py 大类归并口径）。"""

from __future__ import annotations

from collections import Counter
from typing import Any

from review_platform.models import Finding

# 复用自 medical-journal-reviewer/server/review_log_server.py 的问题大类归并口径
PROBLEM_TYPE_GROUPS: list[tuple[str, list[str]]] = [
    ("统计学与数据", ["stats", "data", "统计问题", "统计方法", "统计设计", "统计功效", "多重比较", "效应量"]),
    ("格式与数字单位", ["numbers", "units", "punct", "structure", "格式问题", "标点符号", "错别字", "语法错误"]),
    ("医学术语", ["terminology", "术语问题"]),
    ("方法学", ["methodology", "方法学问题", "研究设计", "样本量问题"]),
    ("参考文献", ["references", "引用问题", "参考文献"]),
]

_CAT_TO_GROUP: dict[str, str] = {}
for _group, _items in PROBLEM_TYPE_GROUPS:
    for _item in _items:
        _CAT_TO_GROUP[_item] = _group


def group_of_category(category: str) -> str:
    return _CAT_TO_GROUP.get(category, "其他")


def evaluate_findings_against_gold(
    findings: list[Finding],
    expected_rules: list[dict[str, str]],
) -> dict[str, Any]:
    """根据黄金期待规则列表（含 rule_id + quote_substring）计算 TP/FP/FN 与 Precision/Recall/F1。"""
    matched_finding_idx: set[int] = set()
    tp = 0
    fn_items: list[dict[str, str]] = []

    for exp in expected_rules:
        exp_rule = exp["rule_id"]
        exp_sub = exp["quote_substring"]
        hit_idx = None
        for idx, f in enumerate(findings):
            if idx in matched_finding_idx:
                continue
            if f.rule_id == exp_rule and exp_sub in f.anchor.quote:
                hit_idx = idx
                break
        if hit_idx is not None:
            matched_finding_idx.add(hit_idx)
            tp += 1
        else:
            fn_items.append(exp)

    fp_items = [
        {"rule_id": f.rule_id, "quote": f.anchor.quote}
        for idx, f in enumerate(findings)
        if idx not in matched_finding_idx
    ]
    fp = len(fp_items)
    fn = len(fn_items)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "fp_items": fp_items,
        "fn_items": fn_items,
    }


def summarize_feedback_stats(feedback_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总编辑裁决反馈，生成按大类与规则维度的误报/采纳分析。"""
    action_counts = Counter(r["action"] for r in feedback_rows)
    group_counts = Counter(group_of_category(r["category"]) for r in feedback_rows)
    by_rule: dict[str, dict[str, int]] = {}
    for r in feedback_rows:
        bucket = by_rule.setdefault(r["rule_id"], {"accept": 0, "reject": 0, "defer": 0})
        act = r["action"]
        if act in bucket:
            bucket[act] += 1

    return {
        "total_feedback": len(feedback_rows),
        "actions": dict(action_counts),
        "by_group": dict(group_counts),
        "by_rule": by_rule,
    }
