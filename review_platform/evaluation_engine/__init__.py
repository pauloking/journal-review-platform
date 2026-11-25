"""Evaluation Engine：黄金测试度量、问题大类归并与编辑反馈统计。"""

from .evaluator import PROBLEM_TYPE_GROUPS, evaluate_findings_against_gold, summarize_feedback_stats

__all__ = ["PROBLEM_TYPE_GROUPS", "evaluate_findings_against_gold", "summarize_feedback_stats"]
