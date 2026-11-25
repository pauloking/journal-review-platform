"""Rule Registry：版本化审校规则注册表。"""

from .registry import (
    RuleRegistry,
    category_for_basis,
    check_regex_safety,
    maintainable_to_rule_spec,
    rule_spec_to_maintainable,
    validate_maintainable_rule,
)

__all__ = [
    "RuleRegistry",
    "category_for_basis",
    "check_regex_safety",
    "maintainable_to_rule_spec",
    "rule_spec_to_maintainable",
    "validate_maintainable_rule",
]
