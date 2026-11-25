# 系统架构与模块边界（ARCHITECTURE）

## 1. 设计目标

本系统定位为**可扩展的中文科技期刊智能审校能力平台**，而非单一审校应用。所有核心能力以数据协议与模块化引擎方式组织，各引擎之间通过 Pydantic 强类型契约交互，支持独立单元测试、替换与版本升级。

## 2. 分层架构

```
+-------------------------------------------------------------------+
| 应用层 (Application Layer)                                        |
|  - 编辑工作台 Web UI (static/)                                    |
|  - HTTP API (FastAPI: review_platform/api.py)                     |
|  - CLI 工具 (python -m review_platform)                           |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 平台编排与核心引擎层 (Review Platform Core Engines)               |
|                                                                   |
|  [Document Engine]       DOCX安全解析 -> Document IR -> 稿件画像  |
|  [Knowledge Engine]      规范条款 + 专业词库 + 期刊配置包         |
|  [Rule Registry]         版本化规则注册 + 依据绑定 + 正反例       |
|  [Router]                DAG 依赖规划 + 环/缺失校验 + 全量任务表  |
|  [Review Engine]         FactLedger抽取 + 确定性规则 + 可插拔执行 |
|  [Finding Engine]        锚点核验 + 证据链 + 冲突/去重 + 裁决治理 |
|  [Editorial Engine]      裁决工作流 + 原生批注DOCX + 综合报告DOCX |
|  [Evaluation Engine]     黄金测试评估 + 大类归并 + 编辑反馈队列   |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 可插拔能力层 (Pluggable Capabilities)                             |
|  - 确定性规则执行器 (DeterministicRuleExecutor)                   |
|  - 数学/统计核验器 (P值/百分比Decimal复算/题注序列)               |
|  - 受控大模型候选适配器 (LLMCandidateExecutor, JSON修复+时间锚点) |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
| 数据与基础设施层 (Infrastructure & Storage)                       |
|  - SQLite 持久化与追加式裁决事件/评估反馈表 (store.py)            |
|  - 本地文件指纹索引与结构化配置包 (knowledge_packs/)              |
+-------------------------------------------------------------------+
```

## 3. 严格模块边界

| 模块 | 职责输入 | 职责输出 | 禁止越权事项 |
|---|---|---|---|
| `document_engine` | DOCX 字节流 | `DocumentIR`、`ManuscriptProfile`、解析限制清单 | 不执行审校规则、不修改原稿文本 |
| `knowledge_engine` | 知识包 JSON、期刊配置 | `KnowledgeClause`、`JournalProfile`、词库 | 不调用模型、不写死在前端 |
| `rule_registry` | 规则定义与知识引用 | `RuleSpec` 注册表（含版本与依赖） | 不允许引用不存在的 `basis_id` |
| `router` | `ManuscriptProfile`、`JournalProfile`、`RuleRegistry` | 确定性 `ExecutionPlan`（DAG 任务列表） | 不静默丢弃禁用或无候选规则 |
| `review_engine` | 只读 `DocumentIR`、`ExecutionPlan`、`JournalProfile` | `FactLedger`、`TaskResult`、原始候选 `Finding` | 禁止直接修改 `DocumentIR` 数值或文本 |
| `finding_engine` | 原始候选 `Finding`、`DocumentIR`、`KnowledgeEngine` | 经核验 `Finding`（含失败拦截） | 禁止无有效锚点或无规范依据的候选直接晋升为正式意见 |
| `editorial_engine` | `ReviewRun`、最新裁决、原稿字节流 | 裁决事件、原生批注 DOCX、综合审评 DOCX、MD/JSON | 禁止绕过原稿 SHA-256 指纹注入批注 |
| `evaluation_engine` | 黄金测试集 / 裁决反馈事件 | Precision/Recall/F1、问题大类分布、回归报告 | 禁止未经审核直接改写线上正式规则 |
