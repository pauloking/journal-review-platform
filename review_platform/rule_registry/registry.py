"""版本化规则注册与知识依据绑定校验。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import RuleSpec, ScopeType

DEFAULT_RULES_DIR = (
    Path(__file__).resolve().parent.parent / "knowledge_packs" / "medical_cn_v1" / "rules"
)

# 将 copyediting-2.0.0 规则文件映射到对应结构化知识依据 ID（严禁伪造标准）
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
        self.snapshot_hash = self._compute_snapshot_hash()

    def _load_builtin_validators(self) -> None:
        """注册核心数学与结构事实验证规则。"""
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
        """从结构化规则包（复用自 copyediting-2.0.0）加载声明式规则。"""
        if not self.rules_dir.exists():
            return
        for path in sorted(self.rules_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            scope: ScopeType = data.get("scope", "professional")
            auto = bool(data.get("auto", True))
            applies_to = list(data.get("applies_to", ["abstract", "introduction", "methods", "results", "discussion", "conclusion", "body", "table"]))
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

    def register(self, rule: RuleSpec) -> None:
        if rule.id in self._rules:
            raise ValueError(f"重复注册规则 ID: {rule.id}")
        if not self.knowledge.has_clause(rule.basis_id):
            raise ValueError(f"规则 {rule.id} 引用的规范依据 basis_id={rule.basis_id} 不存在于知识引擎")
        self._rules[rule.id] = rule

    def get(self, rule_id: str) -> RuleSpec:
        if rule_id not in self._rules:
            raise KeyError(f"未知规则 ID: {rule_id}")
        return self._rules[rule_id]

    def has(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def list_rules(self) -> list[RuleSpec]:
        return list(self._rules.values())

    def _compute_snapshot_hash(self) -> str:
        payload = [r.model_dump(mode="json") for r in sorted(self._rules.values(), key=lambda x: x.id)]
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]
