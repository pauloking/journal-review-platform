# 期刊稿件智能审校报告（run-07607e098d3d96）

## 一、稿件与运行摘要
- 稿件文件：`demo.docx`
- 文档指纹 (`SHA-256`)：`82569af187c2e9bdb9822adcbe7c5f6bb56b4a068223e929cd94884a9dc30652`
- 推断标题：急性心肌梗塞患者经皮冠状动脉介入术后预后分析
- 正文字数：438（正文块 11，表格单元 6）
- 结构质量：reliable
- 期刊配置：`general-medical-cn` v1.0.0（中文综合医学期刊（默认配置））
- 运行状态：`complete_with_limitations` | 规则快照：`13241b1a82837e8b`

## 二、覆盖范围与局限声明
- 仅提取 word/document.xml 中顶层正文段落与表格单元格可见文本；页眉页脚、脚注尾注、文本框与嵌入图片/公式不纳入自动定位。
- 章节识别基于启发式标题与文献特征规则，存在误分类可能，仅供审校路由参考。

## 三、Router DAG 任务执行摘要（零静默遗漏）
| 任务节点 | 类别 | 状态 | 检查数 | 排除数 | 命中数 | 说明 |
|---|---|---|---:|---:|---:|---|
| `extract.facts` | extraction | `passed` | 10 | 2 | 0 | facts_extracted |
| `DATA-PCT-RECALC` | data | `findings` | 2 | 1 | 1 | findings:1 |
| `EXP-AD` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `EXP-POL` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `EXP-SEC` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-ABS` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-AUTH` | references | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-CAP` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-DOI` | references | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-ETHIC` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-FIG` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-HEAD` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-JNAME` | references | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-KEY` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-PAGE` | references | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-STAT` | stats | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `JOU-TAB` | terminology | `skipped` | 0 | 0 | 0 | manual_checklist_only |
| `NUM-CENTURY` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `NUM-LEADZERO` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `NUM-PCT` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `NUM-RANGE` | numbers | `findings` | 2 | 1 | 1 | findings:1 |
| `NUM-THOUSAND` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `NUM-TIMES` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `NUM-YUE` | numbers | `passed` | 0 | 0 | 0 | all_checked_ok |
| `PUN-ELLIPSIS` | punct | `findings` | 1 | 0 | 1 | findings:1 |
| `PUN-HALF` | punct | `passed` | 0 | 0 | 0 | all_checked_ok |
| `REF-PAGE` | references | `findings` | 1 | 0 | 1 | findings:1 |
| `REF-TYPE` | references | `passed` | 2 | 1 | 0 | all_checked_ok |
| `STA-CHI` | stats | `passed` | 0 | 0 | 0 | all_checked_ok |
| `STA-LOWERP` | stats | `findings` | 1 | 0 | 1 | findings:1 |
| `STA-METHOD` | stats | `findings` | 3 | 1 | 2 | findings:2 |
| `STA-SE` | stats | `passed` | 0 | 0 | 0 | all_checked_ok |
| `STA-SIGNIF` | stats | `findings` | 1 | 0 | 1 | findings:1 |
| `STAT-PRANGE` | stats | `findings` | 3 | 1 | 1 | findings:1 |
| `STRUCT-CAPTION-SEQ` | structure | `findings` | 3 | 0 | 1 | findings:1 |
| `TERM-ABO` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-ABX` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-ADR` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-AUT` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-BBB` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-BC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-BP` | terminology | `passed` | 1 | 1 | 0 | all_checked_ok |
| `TERM-CH` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-CI` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-CON` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-DIA` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-DIAM` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-EDE` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-EPI` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-EPI2` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-ESO` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-FEN` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-FUNC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-FUNCX` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-GAL` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-HB` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-HOD` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-HOR` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-IMG` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-IND` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-LC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-LINK` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-MEC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-MEN` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-MENS` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-MI` | terminology | `findings` | 1 | 0 | 1 | findings:1 |
| `TERM-OTH` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-PRE` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-PVC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-RBC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-REP` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-RES` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-STR` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-SYG` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-SYN` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-VIT` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-WBC` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `TERM-ZB` | terminology | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-CAL` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-CMH2O` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-EQ` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-KGS` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-M` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-MMHG` | units | `findings` | 2 | 1 | 1 | findings:1 |
| `UNIT-MUM` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-N` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-PPB` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-PPM` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-PPT` | units | `passed` | 0 | 0 | 0 | all_checked_ok |
| `UNIT-RPM` | units | `findings` | 1 | 0 | 1 | findings:1 |

## 四、审校意见与证据清单（共 13 条）
### 1. [严重] `DATA-PCT-RECALC` — 频数百分比 n/N(pct%) 精确复算校验
- **Finding ID**：`f-4ed01ad96589` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[86, 97)` 引文：「`30/100（40%）`」
- **问题描述**：报告百分比 40% 与 30/100 复算结果 30.0% 不一致（差值 10.00 个百分点，超出容差 0.5）
- **修改建议**：请编辑核实原始分子分母或四舍五入后百分比，严禁未核实直接修改数据
- **规范依据**：`LOCAL-MATH-PERCENTAGE` — 医学期刊数值核对基线约定
- **结构化证据**：`{"n": "30", "N": "100", "reported_pct": "40", "expected_pct": "30.0", "diff": "10.0000", "tolerance": "0.5"}`

### 2. [严重] `NUM-RANGE` — [numbers] NUM-RANGE
- **Finding ID**：`f-16f0e6f4bbe0` | **当前裁决**：待裁决
- **原文定位**：块 `P0005` 区间 `[13, 18)` 引文：「`30-65`」
- **问题描述**：数值范围应使用浪纹线「～」而非连字符「-」（命中：30-65）
- **修改建议**：改为「30～65」
- **规范依据**：`STD-GBT15835-2011` — GB/T 15835-2011《出版物上数字用法》
- **结构化证据**：`{"match_mode": "regex", "section": "methods", "matched": "30-65"}`

### 3. [警告] `PUN-ELLIPSIS` — [punct] PUN-ELLIPSIS
- **Finding ID**：`f-d5bfc8c55782` | **当前裁决**：待裁决
- **原文定位**：块 `P0005` 区间 `[85, 91)` 引文：「`......`」
- **问题描述**：省略号应使用「……」而非三个点或更多点（命中：......）
- **修改建议**：改为「……」（六点，占两字）
- **规范依据**：`STD-GBT15834-2011` — GB/T 15834-2011《标点符号用法》
- **结构化证据**：`{"match_mode": "regex", "section": "methods", "matched": "......"}`

### 4. [严重] `REF-PAGE` — [references] REF-PAGE
- **Finding ID**：`f-9631a322924e` | **当前裁决**：待裁决
- **原文定位**：块 `P0011` 区间 `[50, 57)` 引文：「`120～125`」
- **问题描述**：参考文献页码范围应使用半字线「-」而非「～」（命中：120～125）
- **修改建议**：将页码范围改为半字线「-」连接
- **规范依据**：`STD-GBT7714-2025` — GB/T 7714-2025《信息与文献 参考文献著录规则》
- **结构化证据**：`{"match_mode": "regex", "section": "references", "matched": "120～125"}`

### 5. [警告] `STA-LOWERP` — [stats] STA-LOWERP
- **Finding ID**：`f-86158c804815` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[110, 112)` 引文：「`p<`」
- **问题描述**：P 值应大写斜体 P（命中：p<）
- **修改建议**：改为大写斜体 P
- **规范依据**：`STD-GB3358-STATS` — GB/T 3358《统计学词汇及符号》及中华医学会系列杂志统计学表述规范
- **结构化证据**：`{"match_mode": "regex", "section": "abstract", "matched": "p<"}`

### 6. [提示] `STA-METHOD` — [stats] STA-METHOD
- **Finding ID**：`f-4bc0b774abee` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[119, 121)` 引文：「`P=`」
- **问题描述**：出现 P 值但未说明所用统计学检验方法，建议补充（若方法已在上下文说明，本提示可忽略）（命中：P=）
- **修改建议**：注明检验方法、统计量及自由度
- **规范依据**：`STD-GB3358-STATS` — GB/T 3358《统计学词汇及符号》及中华医学会系列杂志统计学表述规范
- **结构化证据**：`{"match_mode": "regex", "section": "abstract", "matched": "P="}`

### 7. [提示] `STA-METHOD` — [stats] STA-METHOD
- **Finding ID**：`f-5ad9b8ad40c6` | **当前裁决**：待裁决
- **原文定位**：块 `T01R002C03` 区间 `[0, 2)` 引文：「`P=`」
- **问题描述**：出现 P 值但未说明所用统计学检验方法，建议补充（若方法已在上下文说明，本提示可忽略）（命中：P=）
- **修改建议**：注明检验方法、统计量及自由度
- **规范依据**：`STD-GB3358-STATS` — GB/T 3358《统计学词汇及符号》及中华医学会系列杂志统计学表述规范
- **结构化证据**：`{"match_mode": "regex", "section": "table", "matched": "P="}`

### 8. [警告] `STA-SIGNIF` — [stats] STA-SIGNIF
- **Finding ID**：`f-2e506225454c` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[105, 109)` 引文：「`差异显著`」
- **问题描述**：统计学结论建议用「差异有（无）统计学意义」，避免「显著」旧表述（命中：差异显著）
- **修改建议**：改为「差异有（无）统计学意义（P<0.05）」
- **规范依据**：`STD-GB3358-STATS` — GB/T 3358《统计学词汇及符号》及中华医学会系列杂志统计学表述规范
- **结构化证据**：`{"match_mode": "regex", "section": "abstract", "matched": "差异显著"}`

### 9. [严重] `STAT-PRANGE` — P 值与检验水准数值范围校验
- **Finding ID**：`f-bf20c087db96` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[119, 125)` 引文：「`P=1.25`」
- **问题描述**：报告数值「P=1.25」对应的 P=1.25 超出概率区间 [0, 1]
- **修改建议**：请编辑核实原始统计输出与小数点位置（严禁自动篡改数据）
- **规范依据**：`LOCAL-MATH-PRANGE` — 医学期刊统计学数学一致性基线约定
- **结构化证据**：`{"raw_value": "1.25", "valid_interval": "[0, 1]", "section": "abstract"}`

### 10. [警告] `STRUCT-CAPTION-SEQ` — 图表题注顺序连续性校验
- **Finding ID**：`f-f9908cfaeb35` | **当前裁决**：待裁决
- **原文定位**：块 `P0009` 区间 `[0, 2)` 引文：「`图3`」
- **问题描述**：题注「图3」编号跳号：期望 图2，实际 图3
- **修改建议**：请核对是否遗漏 图2 或按出现顺序重新编号
- **规范依据**：`LOCAL-STRUCT-CAPTION` — 中文科技期刊图表编排体例约定
- **结构化证据**：`{"label_type": "图", "actual": 3, "expected": 2, "issue": "sequence_gap"}`

### 11. [警告] `TERM-MI` — [terminology] TERM-MI
- **Finding ID**：`f-bceba7e2e92e` | **当前裁决**：待裁决
- **原文定位**：块 `P0001` 区间 `[2, 6)` 引文：「`心肌梗塞`」
- **问题描述**：疑似非规范医学名词「心肌梗塞」，建议改为「心肌梗死」
- **修改建议**：心肌梗死
- **规范依据**：`STD-CNTERM-COMM` — 全国科学技术名词审定委员会《医学名词》
- **结构化证据**：`{"match_mode": "literal", "section": "title", "matched": "心肌梗塞"}`

### 12. [警告] `UNIT-MMHG` — [units] UNIT-MMHG
- **Finding ID**：`f-8a6bf2a7b0d9` | **当前裁决**：待裁决
- **原文定位**：块 `P0005` 区间 `[29, 33)` 引文：「`mmHg`」
- **问题描述**：mmHg 可保留，但首次出现注明 1 mmHg ≈ 0.133 kPa（SI 单位 kPa 优先）
- **修改建议**：首次出现注明 1 mmHg ≈ 0.133 kPa（SI 单位 kPa 优先）
- **规范依据**：`STD-GB3100-3102` — GB 3100~3102-1993《量和单位》
- **结构化证据**：`{"match_mode": "regex", "section": "methods", "matched": "mmHg"}`

### 13. [警告] `UNIT-RPM` — [units] UNIT-RPM
- **Finding ID**：`f-14b49f282208` | **当前裁决**：待裁决
- **原文定位**：块 `P0002` 区间 `[71, 78)` 引文：「`3000rpm`」
- **问题描述**：rpm 应改为 r/min（转每分）
- **修改建议**：r/min（转每分）
- **规范依据**：`STD-GB3100-3102` — GB 3100~3102-1993《量和单位》
- **结构化证据**：`{"match_mode": "regex", "section": "abstract", "matched": "3000rpm"}`

## 五、编辑裁决完整历史
- 暂无裁决记录。
