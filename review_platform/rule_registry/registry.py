"""版本化规则注册、知识依据绑定、ReDoS 安全守卫与正反例自动校验。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import regex

from review_platform.document_engine.text_utils import is_reference_entry
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import MaintainableTermRule, RuleSpec, ScopeType

MAX_USER_REGEX_LEN = 500
MAX_RULE_EXAMPLE_LEN = 500
REGEX_MATCH_TIMEOUT_SEC = 0.2

# N1 修复：覆盖 + / * / ? / {m,n} / {m,} 等全部重复量词的嵌套与重叠分支结构
_QUANT = r"(?:[+*?]|\{\s*\d+\s*(?:,\s*\d*\s*)?\})"
_NESTED_QUANTIFIER_RE = regex.compile(
    rf"\((?:[^()\\]|\\.)*{_QUANT}(?:[^()\\]|\\.)*\)\s*{_QUANT}"
)
_OVERLAPPING_ALT_QUANT_RE = regex.compile(
    rf"\((?:[^()\\|]|\\.)*\|(?:[^()\\|]|\\.)*\)\s*{_QUANT}"
)

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

BASIS_CATEGORY_MAP: dict[str, str] = {
    "STD-GBT15835-2011": "numbers",
    "STD-GB3100-3102": "units",
    "STD-GBT15834-2011": "punct",
    "STD-GBT7714-2025": "references",
    "STD-CNTERM-COMM": "terminology",
    "STD-GB3358-STATS": "stats",
}


def category_for_basis(basis_id: str, fallback: str = "terminology") -> str:
    return BASIS_CATEGORY_MAP.get(basis_id, fallback)


def check_regex_safety(pattern: str) -> None:
    """静态校验用户自定义正则，拦截嵌套量词（含 {n,}）与带量词分支结构（ReDoS 防护）。"""
    if len(pattern) > MAX_USER_REGEX_LEN:
        raise ValueError(f"正则表达式过长（{len(pattern)} > {MAX_USER_REGEX_LEN}），已拒绝")
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise ValueError("检测到高危嵌套量词正则结构（如 (a+)+ 或 (a{1,}){1,}），存在 ReDoS 风险，已拒绝")
    if _OVERLAPPING_ALT_QUANT_RE.search(pattern):
        raise ValueError("检测到带重复量词的分组分支结构 (a|b)+，为防 ReDoS 请改写或拆分为字面规则")


def _validate_advanced_params(rule: MaintainableTermRule, *, is_builtin_trusted: bool) -> dict:
    """N6 修复：强校验 advanced_params 全部字段类型与 exclude_pattern 正则合法性。"""
    adv = rule.advanced_params or {}
    if not isinstance(adv, dict):
        raise ValueError(f"规则 {rule.rule_id} 的 advanced_params 必须为对象")

    excl_pat = adv.get("exclude_pattern")
    if excl_pat is not None:
        if not isinstance(excl_pat, str):
            raise ValueError(f"规则 {rule.rule_id} 的 exclude_pattern 必须为字符串")
        if excl_pat.strip():
            if not is_builtin_trusted:
                check_regex_safety(excl_pat)
            try:
                regex.compile(excl_pat)
            except regex.error as exc:
                raise ValueError(f"规则 {rule.rule_id} 的 exclude_pattern 正则语法错误: {exc}") from exc

    skip_ctx = adv.get("skip_context")
    if skip_ctx is not None:
        if not isinstance(skip_ctx, list) or not all(isinstance(x, str) for x in skip_ctx):
            raise ValueError(f"规则 {rule.rule_id} 的 skip_context 必须为字符串列表")

    for bool_key in ("whole_line", "ref_only", "skip_if_year", "auto"):
        if bool_key in adv and not isinstance(adv[bool_key], bool):
            raise ValueError(f"规则 {rule.rule_id} 的 {bool_key} 必须为布尔值")

    if "dedupe_key" in adv and not isinstance(adv["dedupe_key"], str):
        raise ValueError(f"规则 {rule.rule_id} 的 dedupe_key 必须为字符串")

    return adv


def validate_maintainable_rule(rule: MaintainableTermRule, *, is_builtin_trusted: bool = False) -> None:
    """对可维护规则条目执行安全性、参数类型与正反例门禁校验（带 C 级正则超时防护）。"""
    adv = _validate_advanced_params(rule, is_builtin_trusted=is_builtin_trusted)
    pattern = rule.nonstandard.strip()
    if not pattern:
        return

    if len(rule.example) > MAX_RULE_EXAMPLE_LEN or len(rule.counter_example) > MAX_RULE_EXAMPLE_LEN:
        raise ValueError(f"规则 {rule.rule_id} 的正反例长度不能超过 {MAX_RULE_EXAMPLE_LEN} 字符")

    if rule.is_regex and not is_builtin_trusted:
        check_regex_safety(pattern)

    try:
        compiled = regex.compile(pattern) if rule.is_regex else regex.compile(regex.escape(pattern))
    except regex.error as exc:
        raise ValueError(f"规则 {rule.rule_id} 正则表达式语法错误: {exc}") from exc

    if not rule.example.strip():
        raise ValueError(f"规则 {rule.rule_id} 必须提供至少 1 个正例（example）用于验证")

    excl_pat = adv.get("exclude_pattern")
    excl_compiled = regex.compile(excl_pat) if excl_pat else None
    whole_line = bool(adv.get("whole_line", False))
    ref_only = bool(adv.get("ref_only", False))
    skip_if_year = bool(adv.get("skip_if_year", False))
    skip_context = adv.get("skip_context") or []

    def _matches(text: str) -> bool:
        try:
            if ref_only and not is_reference_entry(text):
                return False
            if excl_compiled and excl_compiled.search(text, timeout=REGEX_MATCH_TIMEOUT_SEC):
                return False
            if whole_line:
                return bool(text.strip())
            for m in compiled.finditer(text, timeout=REGEX_MATCH_TIMEOUT_SEC):
                start, end = m.start(), m.end()
                if skip_if_year and m.lastindex and m.lastindex >= 2:
                    try:
                        lv, rv = int(float(m.group(1))), int(float(m.group(2)))
                        if 1900 <= lv <= 2099 and 1900 <= rv <= 2099:
                            continue
                    except ValueError:
                        pass
                if skip_context and start > 0:
                    prev_char = text[max(0, start - 3) : start]
                    if any(ctx in prev_char for ctx in skip_context):
                        continue
                after = text[end : end + 4]
                if any(after.startswith(exc) for exc in rule.exception_after):
                    continue
                return True
            return False
        except TimeoutError as exc:
            raise ValueError(f"规则 {rule.rule_id} 正则匹配超时（疑似 ReDoS），已拒绝") from exc

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
    merged_params = dict(item.advanced_params or {})
    merged_params.update(
        {
            "pattern": item.nonstandard,
            "is_regex": item.is_regex,
            "exception_after": list(item.exception_after),
        }
    )
    resolved_cat = item.category.strip() or category
    auto_flag = bool(merged_params.get("auto", has_pattern))
    executor_type = "pattern_rule" if (auto_flag and has_pattern) else "checklist"
    return RuleSpec(
        id=item.rule_id,
        version=version,
        title=item.description or f"[{resolved_cat}] {item.rule_id}",
        category=resolved_cat,
        scope=scope,
        basis_id=basis_id,
        fact_kind="pattern_match" if executor_type == "pattern_rule" else "manual_check",
        dependencies=["extract.facts"],
        applies_to=list(item.applies_to),
        severity=item.severity,
        executor_type=executor_type,
        auto=auto_flag and has_pattern,
        message_template=item.description
        or f"疑似非规范写法「{{found}}」，建议改为「{item.standard or '{suggestion}'}」",
        suggestion_template=item.standard,
        example=item.example,
        counter_example=item.counter_example,
        params=merged_params,
    )


def rule_spec_to_maintainable(spec: RuleSpec) -> MaintainableTermRule:
    adv = {
        k: v
        for k, v in (spec.params or {}).items()
        if k not in {"pattern", "is_regex", "exception_after"}
    }
    adv["auto"] = spec.auto
    return MaintainableTermRule(
        rule_id=spec.id,
        nonstandard=str(spec.params.get("pattern", "")),
        standard=spec.suggestion_template,
        category=spec.category,
        severity=spec.severity,
        is_regex=bool(spec.params.get("is_regex", False)),
        exception_after=list(spec.params.get("exception_after", [])),
        applies_to=list(spec.applies_to),
        example=spec.example,
        counter_example=spec.counter_example,
        description=spec.message_template,
        advanced_params=adv,
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
            RuleSpec(
                id="AI-ABSTRACT-CONSISTENCY",
                version="1.0.0",
                title="[AI 语义] 摘要与正文一致性核查",
                category="logic",
                scope="professional",
                basis_id="STD-AI-SEMANTIC-GUIDE",
                fact_kind="semantic_claim",
                dependencies=["extract.facts"],
                applies_to=["abstract", "results", "table", "body"],
                severity="warning",
                executor_type="ai_candidate",
                auto=True,
                message_template="核查结构式摘要与正文结果、表格关键数据的一致性",
                suggestion_template="请核对摘要与正文中的样本量、结局指标与统计学数值",
                example="摘要写有效率80%，正文结果写有效率75%",
                counter_example="摘要与正文结果段数据完全一致",
            ),
            RuleSpec(
                id="AI-METHOD-DESIGN",
                version="1.0.0",
                title="[AI 语义] 研究设计与统计学方法核查",
                category="methodology",
                scope="professional",
                basis_id="STD-AI-SEMANTIC-GUIDE",
                fact_kind="semantic_claim",
                dependencies=["extract.facts"],
                applies_to=["abstract", "methods", "results", "body"],
                severity="warning",
                executor_type="ai_candidate",
                auto=True,
                message_template="核查研究设计、随机盲法、样本量依据、检验方法匹配及伦理注册声明",
                suggestion_template="补充或核对研究设计关键要素与统计学依据",
                example="RCT 研究未说明随机序列生成方法或伦理批件号",
                counter_example="明确说明随机数字表法分组、伦理批件号及统计检验方法",
            ),
            RuleSpec(
                id="AI-DATA-LOGIC",
                version="1.0.0",
                title="[AI 语义] 图文表对应与论证逻辑核查",
                category="logic",
                scope="professional",
                basis_id="STD-AI-SEMANTIC-GUIDE",
                fact_kind="semantic_claim",
                dependencies=["extract.facts"],
                applies_to=["results", "discussion", "conclusion", "table", "body"],
                severity="warning",
                executor_type="ai_candidate",
                auto=True,
                message_template="核查图文表交叉对应关系及讨论/结论是否存在过度推断",
                suggestion_template="确保结论严格受限于本研究样本与数据范围",
                example="横断面调查结果在结论中推断明确因果关系",
                counter_example="结论客观陈述相关性并指出横断面设计局限性",
            ),
            RuleSpec(
                id="AI-LANGUAGE-STYLE",
                version="1.0.0",
                title="[AI 语义] 学术语言与表达逻辑润色",
                category="language",
                scope="professional",
                basis_id="STD-AI-SEMANTIC-GUIDE",
                fact_kind="semantic_claim",
                dependencies=["extract.facts"],
                applies_to=["title", "abstract", "introduction", "methods", "results", "discussion", "conclusion", "body"],
                severity="info",
                executor_type="ai_candidate",
                auto=True,
                message_template="识别句式杂糅、指代不明、搭配不当与逻辑衔接问题",
                suggestion_template="优化学术表达流畅度（不改变原文科研数据）",
                example="通过该治疗后使患者的预后得到了显著的提高改善",
                counter_example="该治疗显著改善了患者的临床预后",
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
        """原子替换某一规范依据对应的可编辑规则集合（保留 fact_validator / ai_candidate 内置规则）。"""
        to_delete = [
            rid
            for rid, r in self._rules.items()
            if r.basis_id == basis_id and r.executor_type not in {"fact_validator", "ai_candidate"}
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
