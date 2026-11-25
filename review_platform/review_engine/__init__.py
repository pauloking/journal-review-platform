"""Review Engine：事实台账抽取、确定性规则执行与可插拔 AI 候选适配。"""

from .executor import execute_plan
from .fact_extractor import extract_facts
from .llm_adapter import (
    AICandidateExecutor,
    build_time_anchored_system_prompt,
    extract_json_block,
    is_future_date_misjudgment,
    parse_model_json,
    repair_json,
)

__all__ = [
    "AICandidateExecutor",
    "build_time_anchored_system_prompt",
    "execute_plan",
    "extract_facts",
    "extract_json_block",
    "is_future_date_misjudgment",
    "parse_model_json",
    "repair_json",
]
