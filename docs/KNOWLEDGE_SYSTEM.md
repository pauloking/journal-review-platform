# 知识库与规范管理体系（KNOWLEDGE_SYSTEM）

## 1. 核心原则

- **不将规范知识库简单等同于向量数据库**：审校依据必须是精确、结构化、可追溯版本与条款号的实体（`KnowledgeClause`），向量检索未来仅能作为辅助召回手段，不可替代结构化规范约束。
- **严禁 AI 编造规范依据**：每条规则必须绑定 `basis_id`；`basis_id` 不存在于 Knowledge Engine 时，系统启动/注册校验阶段即报错拒绝。

## 2. 四层规范体系（继承并结构化自 `medical-journal-copyediting-2.0.0`）

| 层级 (`scope`) | 名称 | 典型依据示例 |
|---|---|---|
| `national` | 国家标准 | GB/T 15835-2011（数字用法）、GB 3100~3102（量和单位）、GB/T 15834-2011（标点符号）、GB/T 7714-2025（参考文献） |
| `professional` | 行业规范 | 全国科学技术名词审定委员会规范名词、GB 3358 统计学符号规范、医学期刊数学与统计复算规范 |
| `journal` | 期刊体例 | 目标期刊投稿须知、图/表顺序与容差配置、结构式摘要等人工核对清单 |
| `experience` | 编辑经验 | 常见易混医学缩写与审校经验提示（默认提示级，不强制一刀切） |

## 3. 知识条目模型 (`KnowledgeClause`)

- `id`: 稳定依据标识（如 `STD-GBT15835-2011-RANGE`、`STD-GB3100-UNIT`、`STD-CNTERM-COMM`、`LOCAL-STAT-PRANGE`）
- `source`: 来源标准或规范全称
- `edition`: 版本号或发布年份（如 `2011`、`2025`）
- `clause`: 条款或章节说明
- `scope`: `national` | `professional` | `journal` | `experience`
- `summary`: 条款核心约束摘要
- `effective_date`: 生效日期或备注
- `license_note`: 本地内置摘要说明（不冒充未授权标准全文转载）

## 4. 期刊配置包 (`JournalProfile`)

期刊配置与知识库解耦，支持不同期刊按需配置：
- `id` / `name` / `version`
- `discipline`: 如 `medical`（未来可扩展 `engineering`、`chemistry` 等）
- `enabled_scopes`: 启用的规范层级列表
- `disabled_rules`: 显式禁用的 `rule_id` 列表（Router 仍会保留该任务并标注 `skipped` + 原因）
- `percentage_tolerance`: 百分比复算允许容差（默认 `0.5` 个百分点，实际取显示精度半单位与期刊容差的较大者）
- `caption_start`: 图表起始编号（默认 `1`）
- `custom_terms`: 期刊定制术语覆盖映射（不直接修改国家标准基础词库）

## 5. 文档化规范库与可维护发布流（`KnowledgeDocument`）

为保证规则库在用户端以**一份份可读、可维护的规范文档**呈现（而非扁平化代码清单），平台建立 `KnowledgeDocument` 模型与 `knowledge_packs/medical_cn_v1/documents/*.md` 文档包：

- 每个规范依据（`basis_id`）绑定一份完整的 Markdown 规范文档，说明适用范围、编校要求、正反例对照及例外保护条件；
- 支持通过工作台或 `POST /knowledge/documents`、`PUT /knowledge/documents/{doc_id}` 在线维护文档正文与可读规则条目，并支持导出 `.md` 文件；
- **强验证发布门禁**：任何新增或修订的规则条目在保存激活前，系统必须自动执行正例命中检查（`example` 必命中）与反例防误报检查（`counter_example` 必不误报）；校验不通过返回 `422` 拒绝修改正式注册表；
- 更新后原子递增文档版本并刷新 `rule_snapshot_hash`，已有的历史审校记录快照不受影响。
