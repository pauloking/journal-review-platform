# 审校结果与证据协议（FINDING_SPEC）

## 1. Finding 准入门禁

任何执行器（规则、工具或 AI 候选）产出的问题条目，在进入 `ReviewRun.findings` 前必须通过 `FindingEngine` 门禁：
1. **锚点有效门禁**：调用 `validate_anchor(doc_ir, finding.anchor)` 核验 `document_id`、`block_id`、`[start, end)` 与 `quote` 逐字一致；校验不通过禁止伪造偏移。
2. **依据真实门禁**：检查 `finding.basis_id` 在 `KnowledgeEngine` 中存在；禁止编造国家标准号或条款。
3. **事实关联**：结构化计算/序列规则必须引用对应的 `Fact.id` 与复算输入/输出证据（如 `n=30, N=100, reported=40%, expected=30.0%, diff=10.0%`）。
4. **AI 候选隔离**：`source_type == "ai_candidate"` 的条目即使通过锚点与依据校验，也必须标明来源模型与待编辑人工复核标识，不得越权自动采纳。

## 2. 稳定 ID 与去重

- `finding.id` 由 `(rule_id, block_id, start, end, quote)` 计算确定性短哈希生成。
- 同一位置、同一规则重复提交审校时，`finding_id` 保持稳定，从而允许跨重复运行保留已有编辑裁决历史。

## 3. 编辑裁决协议 (`accept` / `reject` / `defer`)

- 每次裁决必须显式指定：`finding_id`、`action`、`editor`、`reason`、`expected_revision`。
- **乐观并发控制**：若服务端当前最新版本与 `expected_revision` 不一致，返回 `409 Conflict`，提示刷新最新状态。
- **事件追加**：裁决以不可变事件追加记录至 SQLite `decisions` 表，不覆盖或删除原始 `Finding`，且自动同步写入 `evaluation_feedback` 评估队列供误报/漏报分析（不自动改变正式规则）。
