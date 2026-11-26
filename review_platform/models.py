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


class MaintainableTermRule(BaseModel):
    """面向编辑与规则维护人员的可读规则条目（自动绑定正反例校验）。"""

    rule_id: str = Field(..., min_length=2, max_length=64)
    nonstandard: str = ""
    standard: str = ""
    category: str = ""
    severity: SeverityType = "warning"
    is_regex: bool = False
    exception_after: list[str] = Field(default_factory=list)
    applies_to: list[str] = Field(
        default_factory=lambda: [
            "title",
            "abstract",
            "keywords",
            "introduction",
            "methods",
            "results",
            "discussion",
            "conclusion",
            "body",
            "table",
        ]
    )
    example: str = ""
    counter_example: str = ""
    description: str = ""
    advanced_params: dict[str, Any] = Field(default_factory=dict)


class KnowledgeDocument(BaseModel):
    """用户端可阅读、可维护的规范文档（1 个文档对应 1 套规范依据及其可执行规则集）。"""

    doc_id: str
    basis_id: str
    basis_ids: list[str] = Field(default_factory=list)
    title: str
    source: str
    edition: str
    scope: ScopeType
    effective_date: str = ""
    summary: str
    body_markdown: str
    rule_ids: list[str] = Field(default_factory=list)
    maintainable_rules: list[MaintainableTermRule] = Field(default_factory=list)
    is_builtin: bool = True
    supports_rule_editing: bool = True
    version: str = "1.0.0"
    updated_at: str = ""


class DocumentUpsertRequest(BaseModel):
    """创建或更新规范文档的请求体（保存前必须通过正反例自动验证）。"""

    doc_id: Optional[str] = None
    title: str = Field(..., min_length=2, max_length=120)
    source: str = Field(..., min_length=2, max_length=200)
    edition: str = Field(default="2026", max_length=40)
    scope: ScopeType = "journal"
    summary: str = Field(..., min_length=2, max_length=500)
    body_markdown: str = Field(..., min_length=5)
    rules: list[MaintainableTermRule] = Field(default_factory=list)


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
    basis_id: str = ""
    doc_id: str = ""
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = "pending"
    reason: str = ""
    checked_count: int = 0
    excluded_count: int = 0
    finding_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionTraceStep(BaseModel):
    """平台运行时可视化中间过程追踪事件。"""

    step_index: int
    stage: Literal[
        "document_parse",
        "knowledge_bind",
        "router_plan",
        "fact_extract",
        "rule_execute",
        "finding_gate",
    ]
    title: str
    status: Literal["completed", "skipped", "failed", "blocked"] = "completed"
    doc_id: str = ""
    node_id: str = ""
    duration_ms: float = 0.0
    summary: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ParsedRuleDocumentPreview(BaseModel):
    """上传规则文档后的自动解析预览结果（未直接生效，待正反例校验后保存）。"""

    filename: str
    title: str
    source: str
    edition: str
    scope: ScopeType
    summary: str
    body_markdown: str
    rules: list[MaintainableTermRule] = Field(default_factory=list)
    parse_notes: list[str] = Field(default_factory=list)


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


LLMProviderType = Literal["openai", "claude", "custom"]


class LLMConfig(BaseModel):
    """用户自定义大模型配置（api_key 仅用于当次请求，绝不写入数据库或导出文件）。"""

    enabled: bool = False
    provider: LLMProviderType = "openai"
    endpoint: str = ""
    api_key: str = ""
    model: str = ""
    temperature: float = Field(default=0.2, ge=0.0, le=1.5)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    disable_thinking: bool = True
    timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    enabled_rules: list[str] = Field(default_factory=list)


class LLMTestResult(BaseModel):
    """大模型连接测试结果。"""

    ok: bool
    provider: LLMProviderType
    model: str
    latency_ms: float
    preview: str = ""
    message: str = ""


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
    trace: list[ExecutionTraceStep] = Field(default_factory=list)
