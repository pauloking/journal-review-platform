"""版本化规则注册、知识依据绑定与正反例自动校验。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import MaintainableTermRule, RuleSpec, ScopeType

DEFAULT_RULES_DIR = (
    Path(__file__).resolve().parent.parent / "knowledge_packs" / "medical_cn_v1" / "rules"
)

FILE_BASIS_MAP: dict[str, str] = {
    "numbers.json": "STD-GBT15835-2011",
    "units.json": "STD-GB3100-3102",
    "punct.json": "STD-GBT15834-2011",
    "references.json": "STD-GBT7714-2025",
    "terms.json": "STD-CNTERM-COMM",
    "stats.json": "STD-GB3358-STATS",
    "journal.json": "JOU-STYLE-GUIDE",
    "experience.json": "EXP-EDITORIAL-GUIDE",
}


def validate_maintainable_rule(rule: MaintainableTermRule) -> None:
    """对可维护规则条目执行正反例门禁校验，严禁未经验证的规则进入正式注册表。"""
    pattern = rule.nonstandard.strip()
    if not pattern:
        return

    try:
        compiled = re.compile(pattern) if rule.is_regex else re.compile(re.escape(pattern))
    except re.error as exc:
        raise ValueError(f"规则 {rule.rule_id} 正则表达式语法错误: {exc}") from exc

    if not rule.example.strip():
        raise ValueError(f"规则 {rule.rule_id} 必须提供至少 1 个正例（example）用于验证")

    def _matches(text: str) -> bool:
        for m in compiled.finditer(text):
            after = text[m.end() : m.end() + 4]
            if any(after.startswith(exc) for exc in rule.exception_after):
                continue
            return True
        return False

    if not _matches(rule.example):
        raise ValueError(
            f"规则 {rule.rule_id} 未能命中其提供的正例「{rule.example}」，请检查匹配模式"
        )

    if rule.counter_example.strip() and _matches(rule.counter_example):
        raise ValueError(
            f"规则 {rule.rule_id} 对反例「{rule.counter_example}」产生了误报，已拒绝发布正式规则"
        )


def maintainable_to_rule_spec(
    item: MaintainableTermRule,
    *,
    basis_id: str,
    scope: ScopeType,
    category: str,
    version: str = "1.0.0",
) -> RuleSpec:
    has_pattern = bool(item.nonstandard.strip())
    return RuleSpec(
        id=item.rule_id,
        version=version,
        title=item.description or f"[{category}] {item.rule_id}",
        category=category,
        scope=scope,
        basis_id=basis_id,
        fact_kind="pattern_match" if has_pattern else "manual_check",
        dependencies=["extract.facts"],
        applies_to=list(item.applies_to),
        severity=item.severity,
        executor_type="pattern_rule" if has_pattern else "checklist",
        auto=has_pattern,
        message_template=item.description
        or f"疑似非规范写法「{{found}}」，建议改为「{item.standard or '{suggestion}'}」",
        suggestion_template=item.standard,
        example=item.example,
        counter_example=item.counter_example,
        params={
            "pattern": item.nonstandard,
            "is_regex": item.is_regex,
            "exception_after": list(item.exception_after),
        },
    )


def rule_spec_to_maintainable(spec: RuleSpec) -> MaintainableTermRule:
    return MaintainableTermRule(
        rule_id=spec.id,
        nonstandard=str(spec.params.get("pattern", "")),
        standard=spec.suggestion_template,
        severity=spec.severity,
        is_regex=bool(spec.params.get("is_regex", False)),
        exception_after=list(spec.params.get("exception_after", [])),
        applies_to=list(spec.applies_to),
        example=spec.example,
        counter_example=spec.counter_example,
        description=spec.message_template,
    )


class RuleRegistry:
    """集中管理所有审校规则及其版本与知识依据。"""

    def __init__(
        self,
        knowledge: KnowledgeEngine,
        rules_dir: Optional[Path] = None,
        extra_rules: Optional[list[RuleSpec]] = None,
    ) -> None:
        self.knowledge = knowledge
        self.rules_dir = Path(rules_dir) if rules_dir else DEFAULT_RULES_DIR
        self._rules: dict[str, RuleSpec] = {}
        self._load_builtin_validators()
        self._load_pack_rules()
        if extra_rules:
            for rule in extra_rules:
                self.register(rule)
        self.refresh_snapshot()

    def _load_builtin_validators(self) -> None:
        validators = [
            RuleSpec(
                id="STAT-PRANGE",
                version="1.0.0",
                title="P 值与检验水准数值范围校验",
                category="stats",
                scope="professional",
                basis_id="LOCAL-MATH-PRANGE",
                fact_kind="p_value",
                dependencies=["extract.facts"],
                applies_to=["abstract", "methods", "results", "discussion", "conclusion", "body", "table"],
                severity="error",
                executor_type="fact_validator",
                auto=True,
                message_template="显式报告的 P 值超出合法概率区间 [0, 1]",
                suggestion_template="请编辑核实原始统计输出与小数点位置，禁止自动改写数值",
                example="差异有统计学意义（P=1.25）",
                counter_example="差异有统计学意义（P=0.032；P=0；P=1；P=1.2e-5）",
            ),
            RuleSpec(
                id="DATA-PCT-RECALC",
                version="1.0.0",
                title="频数百分比 n/N(pct%) 精确复算校验",
                category="data",
                scope="professional",
                basis_id="LOCAL-MATH-PERCENTAGE",
                fact_kind="percentage",
                dependencies=["extract.facts"],
                applies_to=["abstract", "methods", "results", "discussion", "conclusion", "body", "table"],
                severity="error",
                executor_type="fact_validator",
                auto=True,
                message_template="报告百分比与 n/N 复算结果存在偏差或分母异常",
                suggestion_template="请编辑核实原始病例数 n、总例数 N 与百分比计算过程",
                example="有效例数 30/100（40%）",
                counter_example="有效例数 30/100（30.0%）",
            ),
            RuleSpec(
                id="STRUCT-CAPTION-SEQ",
                version="1.0.0",
                title="图表题注顺序连续性校验",
                category="structure",
                scope="journal",
                basis_id="LOCAL-STRUCT-CAPTION",
                fact_kind="caption",
                dependencies=["extract.facts"],
                applies_to=["title", "abstract", "introduction", "methods", "results", "discussion", "conclusion", "body"],
                severity="warning",
                executor_type="fact_validator",
                auto=True,
                message_template="图或表题注编号出现跳号或重复",
                suggestion_template="请按正文出现顺序核对并重新编排图序/表序",
                example="图1 …… 随后直接出现 图3 ……",
                counter_example="图1 …… 随后出现 图2 ……（正文「见图9」不计为题注）",
            ),
        ]
        for rule in validators:
            self.register(rule)

    def _load_pack_rules(self) -> None:
        if not self.rules_dir.exists():
            return
        for path in sorted(self.rules_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            scope: ScopeType = data.get("scope", "professional")
            auto = bool(data.get("auto", True))
            applies_to = list(
                data.get(
                    "applies_to",
                    ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "body", "table"],
                )
            )
            basis_id = FILE_BASIS_MAP.get(path.name)
            if not basis_id:
                raise ValueError(f"规则文件 {path.name} 未映射合法 basis_id")

            for raw in data.get("rules", []):
                rule_id = raw["id"]
                category = raw.get("category", "terminology")
                executor_type = "pattern_rule" if auto and raw.get("pattern") else "checklist"
                params = {
                    k: v
                    for k, v in raw.items()
                    if k not in {"id", "category", "severity", "suggestion", "message", "example", "counter_example"}
                }
                spec = RuleSpec(
                    id=rule_id,
                    version="2.0.0",
                    title=f"[{category}] {rule_id}",
                    category=category,
                    scope=scope,
                    basis_id=basis_id,
                    fact_kind="pattern_match" if executor_type == "pattern_rule" else "manual_check",
                    dependencies=["extract.facts"],
                    applies_to=applies_to,
                    severity=raw.get("severity", "warning"),
                    executor_type=executor_type,
                    auto=auto and bool(raw.get("pattern")),
                    message_template=raw.get("message", ""),
                    suggestion_template=raw.get("suggestion", ""),
                    example=raw.get("example", ""),
                    counter_example=raw.get("counter_example", ""),
                    params=params,
                )
                self.register(spec)

    def register(self, rule: RuleSpec, *, allow_replace: bool = False) -> None:
        if not allow_replace and rule.id in self._rules:
            raise ValueError(f"重复注册规则 ID: {rule.id}")
        if not self.knowledge.has_clause(rule.basis_id):
            raise ValueError(f"规则 {rule.id} 引用的规范依据 basis_id={rule.basis_id} 不存在于知识引擎")
        self._rules[rule.id] = rule

    def replace_rules_for_basis(self, basis_id: str, new_rules: list[RuleSpec]) -> None:
        """原子替换某一规范依据对应的可编辑规则集合（保留 fact_validator 内置规则）。"""
        to_delete = [
            rid
            for rid, r in self._rules.items()
            if r.basis_id == basis_id and r.executor_type != "fact_validator"
        ]
        for rid in to_delete:
            del self._rules[rid]
        for rule in new_rules:
            self.register(rule, allow_replace=True)
        self.refresh_snapshot()

    def get(self, rule_id: str) -> RuleSpec:
        if rule_id not in self._rules:
            raise KeyError(f"未知规则 ID: {rule_id}")
        return self._rules[rule_id]

    def has(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def list_rules(self) -> list[RuleSpec]:
        return list(self._rules.values())

    def rules_for_basis_ids(self, basis_ids: list[str]) -> list[RuleSpec]:
        bset = set(basis_ids)
        return [r for r in self._rules.values() if r.basis_id in bset]

    def refresh_snapshot(self) -> None:
        payload = [r.model_dump(mode="json") for r in sorted(self._rules.values(), key=lambda x: x.id)]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.snapshot_hash = hashlib.sha256(raw).hexdigest()[:16]
