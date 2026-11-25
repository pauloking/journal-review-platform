"""Rule Registry：版本化审校规则注册表。"""

from .registry import (
    RuleRegistry,
    maintainable_to_rule_spec,
    rule_spec_to_maintainable,
    validate_maintainable_rule,
)

__all__ = [
    "RuleRegistry",
    "maintainable_to_rule_spec",
    "rule_spec_to_maintainable",
    "validate_maintainable_rule",
]
