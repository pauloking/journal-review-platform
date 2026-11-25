# 核心数据模型与对象关系（DATA_MODEL）

所有跨模块传输对象均定义在 `review_platform/models.py`，基于 Pydantic v2 实现，支持自动导出 JSON Schema。

## 1. 核心对象关系

```
Manuscript (document_id = sha256)
  ├── 1 : N ── Block (id, kind, section, text, runs, xml_path)
  └── 1 : 1 ── ManuscriptProfile (sections, counts, limitations)

JournalProfile (id, version, enabled_rules, scopes, tolerance)
  └── references ── RuleSpec (id, version, basis_id, dependencies)
                        └── references ── KnowledgeClause (basis_id, source, clause)

ReviewRun (run_id, document_id, journal_id, journal_version, rule_snapshot_hash)
  ├── 1 : 1 ── ExecutionPlan (tasks: list[ReviewTask])
  ├── 1 : N ── Fact (fact_id, kind, anchor, values, status)
  ├── 1 : N ── Finding (finding_id, rule_id, basis_id, anchor, fact_ids, evidence, verification)
  └── 1 : N ── EditorialDecision (decision_id, finding_id, action, editor, reason, revision)
```

## 2. 关键实体字段定义

### 2.1 `Anchor`（原文定位锚点）
- `document_id`: `str`（必须等于当前 `DocumentIR.document_id`）
- `block_id`: `str`（对应 `Block.id`）
- `start`: `int`（Python Unicode 码点起止左闭右开区间 `[start, end)`）
- `end`: `int`
- `quote`: `str`（必须与 `block.text[start:end]` 逐字相等）

### 2.2 `Fact`（事实台账条目）
- `id`: `str`（基于规则/类别+位置哈希的稳定标识）
- `kind`: `str`（如 `p_value` / `percentage` / `caption` / `pattern_match` / `reference_entry`）
- `anchor`: `Anchor`
- `values`: `dict[str, Any]`（保留原始字符串及精确数值表达，如 `Decimal` 字符串）
- `scope`: `str`（所在章节及语境）
- `extractor`: `str`（抽取器名称与版本）
- `status`: `verified` | `excluded` | `candidate`
- `exclusion_reason`: `Optional[str]`（被例外规则排除时必须记录原因）

### 2.3 `ReviewTask`（Router 生成的任务节点）
- `rule_id`: `str`
- `rule_version`: `str`
- `dependencies`: `list[str]`
- `status`: `pending` | `passed` | `findings` | `skipped` | `failed` | `blocked`
- `reason`: `str`（禁用原因、跳过原因或失败诊断）
- `checked_count`: `int`
- `excluded_count`: `int`
- `finding_count`: `int`

### 2.4 `Finding`（审校结果）
- `id`: `str`（稳定唯一 ID，由 `rule_id` + `anchor` + 关键特征生成）
- `rule_id`: `str`
- `rule_version`: `str`
- `basis_id`: `str`（必须可在 Knowledge Engine 中检索）
- `category`: `str`
- `scope`: `national` | `professional` | `journal` | `experience`
- `severity`: `error` | `warning` | `info`
- `anchor`: `Anchor`
- `fact_ids`: `list[str]`
- `message`: `str`
- `suggestion`: `str`
- `evidence`: `dict[str, Any]`
- `source_type`: `deterministic` | `tool` | `ai_candidate`
- `verification`: `AnchorVerification`（记录锚点与依据校验状态）

### 2.5 `EditorialDecision`（编辑裁决）
- `id`: `int`
- `run_id`: `str`
- `finding_id`: `str`
- `action`: `accept` | `reject` | `defer`
- `editor`: `str`
- `reason`: `str`
- `revision`: `int`（从 1 递增，用于乐观并发控制）
- `created_at`: `str`（ISO-8601 UTC 时间）
