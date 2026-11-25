# 审校规则定义及执行协议（RULE_SPEC）

## 1. 规则与平台解耦设计

规则通过声明式元数据注册在 `RuleRegistry` 中，包含版本、所属领域、适用章节、知识依据、前置依赖、执行器类型以及正反例测试用例。前端和 LLM 提示词中均不允许硬编码规则逻辑。

## 2. `RuleSpec` 协议字段

- `id`: 唯一规则编号（如 `NUM-RANGE`、`UNIT-RPM`、`TERM-MI`、`STAT-PRANGE`、`DATA-PCT-RECALC`、`STRUCT-CAPTION-SEQ`）
- `version`: 语义化版本号（如 `"1.0.0"`）
- `title`: 规则名称
- `category`: `numbers` | `units` | `punct` | `terminology` | `stats` | `data` | `structure` | `references`
- `scope`: `national` | `professional` | `journal` | `experience`
- `basis_id`: 关联 `KnowledgeClause.id`（强制校验存在）
- `fact_kind`: 该规则依赖或产出的事实类型
- `dependencies`: 前置规则或抽取阶段 ID 列表（如 `["extract.facts"]`）
- `applies_to`: 适用章节集合（如 `["abstract", "methods", "results", "body", "table"]`）
- `severity`: `error` | `warning` | `info`
- `executor_type`: `fact_validator` | `pattern_rule` | `checklist` | `ai_candidate`
- `auto`: `bool`（是否为自动执行规则；`false` 为人工核对清单）
- `examples`: 至少 1 个正例（应命中）与 1 个反例（不应误报）

## 3. 首轮内建规则能力矩阵

1. **事实与计算类规则（Fact Validators）**：
   - `STAT-PRANGE`：显式 `P` 值与阈值范围 `[0, 1]` 检查（兼容全角 `Ｐ`、`=<>≤≥`，排除引号/示例句区间，记录排除原因）。
   - `DATA-PCT-RECALC`：显式 `n/N(pct%)` 百分比 `Decimal` 精确复算与容差核验（零分母、分子>分母异常单独提示，排除引号示例）。
   - `STRUCT-CAPTION-SEQ`：正文段首 `图X`/`表X` 题注序列连续性与重复号检查（图、表独立序列；正文“见图9”不误判为题注）。
2. **继承自 `medical-journal-copyediting-2.0.0` 的确定性规则族（Pattern Rules）**：
   - **数字用法 (`numbers`)**：范围波浪线 `NUM-RANGE`（跳过年份区间与图表编号）、百分数双端符号 `NUM-PCT`、世纪 `NUM-CENTURY`、约十几 `NUM-YUE`、降低倍数 `NUM-TIMES`、小数前零 `NUM-LEADZERO`、千分位逗号 `NUM-THOUSAND`。
   - **量和单位 (`units`)**：`ppm/ppb/ppt/rpm/cal/M/Eq/当量浓度/mμm/kgs/mmHg/cmH2O` 等 12 条规范规则。
   - **标点符号 (`punct`)**：多点省略号 `PUN-ELLIPSIS`、中文上下文半角标点 `PUN-HALF`。
   - **统计学表述 (`stats`)**：`显著性差异` 旧表述 `STA-SIGNIF`、小写 `p<` `STA-LOWERP`、卡方写法 `STA-CHI`、`±SE` 提醒 `STA-SE`、未注明统计方法 `STA-METHOD`。
   - **规范医学术语 (`terminology`)**：复用名词委 43 条高频医学错用术语对照规则及 `exception_after` 防误报机制。
   - **参考文献著录 (`references`)**：`REF-TYPE`（缺文献类型标志）、`REF-PAGE`（页码范围连接号误用），仅作用于 `references` 章节及已识别文献条目。
