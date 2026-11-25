# 路由与任务编排机制（ROUTER_SPEC）

## 1. 核心目标

Router 负责根据 `ManuscriptProfile`、`JournalProfile` 与 `RuleRegistry` 动态构建确定性有向无环图（DAG）执行计划 `ExecutionPlan`，确保：
- **零静默遗漏**：`RuleRegistry` 中每一条已注册规则都在 `ExecutionPlan.tasks` 中有一一对应的 `ReviewTask` 记录；
- **依赖严格有序**：前置抽取节点（如 `extract.facts`）或前置规则执行失败时，下游依赖节点必须显式标记为 `blocked`，不得静默继续；
- **可重复性**：同一输入文档 + 同一期刊配置 + 同一规则注册表快照，必产生完全一致的任务列表与拓扑执行顺序。

## 2. DAG 校验与拓扑排序

1. **配置校验**：若 `JournalProfile.disabled_rules` 引用了未注册的 `rule_id`，立即抛出配置错误（防止静默拼写错误）。
2. **图校验**：
   - 检查所有依赖项是否存在；
   - 使用 Kahn 算法进行确定性拓扑排序（同层按 `node_id` 字典序排列）；
   - 若存在环依赖，拒绝生成计划并报错。
3. **动态路由判定**：
   - 若规则 `id` 在 `disabled_rules` 中 → 任务标记为 `skipped`，`reason = "journal_disabled"`；
   - 若规则 `scope` 不在 `journal.enabled_scopes` 中 → 标记为 `skipped`，`reason = "scope_disabled"`；
   - 若规则 `auto == False`（仅人工核对清单）→ 标记为 `skipped`，`reason = "manual_checklist_only"`；
   - 若 `ManuscriptProfile` 中无规则 `applies_to` 覆盖的章节块 → 标记为 `skipped`，`reason = "no_applicable_sections"`；
   - 否则进入可执行队列，等待依赖满足后执行。

## 4. 任务状态机

- `pending`：等待前置依赖；
- `passed`：已执行且检查候选数 ≥ 0，未发现违规；
- `findings`：已执行且产出 ≥ 1 条 Finding；
- `skipped`：因配置禁用、人工清单或无适用章节跳过（必带 `reason`）；
- `failed`：执行期发生异常（保留错误诊断）；
- `blocked`：至少一个前置依赖状态为 `failed` 或 `blocked`，被安全阻断。
