"""平台核心强类型数据协议（Pydantic v2）。"""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

ScopeType = Literal["national", "professional", "journal", "experience"]
SeverityType = Literal["error", "warning", "info"]
TaskStatus = Literal["pending", "passed", "findings", "skipped", "failed", "blocked"]
DecisionAction = Literal["accept", "reject", "defer"]
SourceType = Literal["deterministic", "tool", "ai_candidate"]
FactStatus = Literal["verified", "excluded", "candidate"]


class RunSpan(BaseModel):
    """段落内直接 <w:r> 在 Block.text 中对应的 Unicode 区间。"""

    run_index: int
    start: int
    end: int
    text: str


class Block(BaseModel):
    """Document IR 中的最小文本块单元（段落或表格单元格）。"""

    id: str
    kind: Literal["title", "heading", "paragraph", "reference", "table_cell", "separator"]
    section: str
    section_cn: str
    heading: str = ""
    text: str
    ooxml_part: str = "word/document.xml"
    xml_path: str
    runs: list[RunSpan] = Field(default_factory=list)
    excluded_spans: list[tuple[int, int]] = Field(default_factory=list)
    exclude_entire: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class ManuscriptProfile(BaseModel):
    """稿件特征与结构质量画像。"""

    title_guess: str = ""
    total_blocks: int
    paragraph_count: int
    table_cell_count: int
    char_count: int
    sections_present: list[str]
    section_counts: dict[str, int]
    structure_status: Literal["reliable", "limited", "unreliable"]
    structure_warnings: list[str] = Field(default_factory=list)


class DocumentIR(BaseModel):
    """不可变稿件中间表示协议。"""

    schema_version: str = "document-ir/1.0"
    document_id: str
    source_sha256: str
    filename: str
    blocks: list[Block]
    profile: ManuscriptProfile
    limitations: list[str] = Field(default_factory=list)


class Anchor(BaseModel):
    """绑定文档指纹与 Unicode 码点区间的原文定位锚点。"""

    document_id: str
    block_id: str
    start: int
    end: int
    quote: str


class AnchorVerification(BaseModel):
    """Finding 的锚点与依据门禁核验结果。"""

    anchor_valid: bool
    basis_valid: bool
    reason: str = "ok"


class KnowledgeClause(BaseModel):
    """结构化规范知识条目（严禁等同于向量库或由 AI 编造）。"""

    id: str
    source: str
    edition: str
    clause: str
    scope: ScopeType
    summary: str
    effective_date: str = ""
    license_note: str = "结构化规范元数据摘要"


class JournalProfile(BaseModel):
    """期刊配置包：控制启用层级、禁用规则与数学容差。"""

    id: str
    name: str
    version: str
    discipline: str = "medical"
    enabled_scopes: list[ScopeType] = Field(
        default_factory=lambda: ["national", "professional", "journal", "experience"]
    )
    disabled_rules: list[str] = Field(default_factory=list)
    percentage_tolerance: float = 0.5
    caption_start: int = 1
    custom_terms: dict[str, str] = Field(default_factory=dict)
    description: str = ""


class RuleSpec(BaseModel):
    """版本化审校规则注册契约。"""

    id: str
    version: str = "1.0.0"
    title: str
    category: str
    scope: ScopeType
    basis_id: str
    fact_kind: str = ""
    dependencies: list[str] = Field(default_factory=lambda: ["extract.facts"])
    applies_to: list[str] = Field(default_factory=list)
    severity: SeverityType = "warning"
    executor_type: Literal["fact_validator", "pattern_rule", "checklist", "ai_candidate"] = "pattern_rule"
    auto: bool = True
    message_template: str = ""
    suggestion_template: str = ""
    example: str = ""
    counter_example: str = ""
    params: dict[str, Any] = Field(default_factory=dict)


class Fact(BaseModel):
    """事实台账条目：记录稿件显式陈述或规则候选，保留排除原因。"""

    id: str
    kind: str
    anchor: Anchor
    values: dict[str, Any] = Field(default_factory=dict)
    scope: str
    extractor: str
    status: FactStatus = "verified"
    exclusion_reason: Optional[str] = None


class ReviewTask(BaseModel):
    """Router DAG 规划产生的任务节点（禁止静默遗漏任何规则）。"""

    node_id: str
    rule_id: str
    rule_version: str
    title: str
    category: str
    scope: ScopeType
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = "pending"
    reason: str = ""
    checked_count: int = 0
    excluded_count: int = 0
    finding_count: int = 0


class ExecutionPlan(BaseModel):
    """确定性 DAG 执行计划。"""

    plan_hash: str
    document_id: str
    journal_id: str
    journal_version: str
    rule_snapshot_hash: str
    tasks: list[ReviewTask]


class Finding(BaseModel):
    """经 Finding Engine 门禁核验后的正式审校意见。"""

    id: str
    rule_id: str
    rule_version: str
    basis_id: str
    basis_source: str = ""
    title: str
    category: str
    scope: ScopeType
    severity: SeverityType
    anchor: Anchor
    fact_ids: list[str] = Field(default_factory=list)
    message: str
    suggestion: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    source_type: SourceType = "deterministic"
    verification: AnchorVerification


class EditorialDecision(BaseModel):
    """不可变追加式编辑裁决记录。"""

    id: int
    run_id: str
    finding_id: str
    action: DecisionAction
    editor: str
    reason: str
    revision: int
    created_at: str


class DecisionRequest(BaseModel):
    """提交裁决请求体。"""

    finding_id: str
    action: DecisionAction
    editor: str = Field(..., min_length=1, max_length=64)
    reason: str = Field(..., min_length=1, max_length=1000)
    expected_revision: int = Field(ge=0)


class ReviewRun(BaseModel):
    """单次审校运行的完整可追溯快照。"""

    run_id: str
    created_at: str
    engine_version: str
    status: Literal["completed", "complete_with_limitations", "failed"]
    document: DocumentIR
    journal: JournalProfile
    plan: ExecutionPlan
    facts: list[Fact]
    findings: list[Finding]
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    decisions: list[EditorialDecision] = Field(default_factory=list)
