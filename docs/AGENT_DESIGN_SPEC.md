# 中文科技期刊智能审校能力平台 — 智能体设计书

- **文档版本**：1.0（对应当前已交付基线版本 `V0.1`，代码基线提交 `4221702`）
- **适用工程**：[journal-review-platform](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform)
- **编写原则**：严格区分**当前版本已实现并经测试验证的能力**与**后续演进中的受控大模型协同规划**，不虚构未落地的线上模型调用或真实稿件准确率指标。

---

## 一、建设背景

### 1.1 行业与业务痛点
中文科技期刊（尤其是医学科技期刊）在初审、编辑加工与校对环节长期面临三大难题：
1. **规范标准繁杂且跨层叠加**：一篇医学稿件需同时符合国家标准（如 GB/T 15835-2011 数字用法、GB 3100~3102 量和单位、GB/T 15834-2011 标点符号、GB/T 7714-2025 参考文献）、行业规范（全国科技名词委审定医学名词、GB/T 3358 统计学符号）、期刊自身体例以及编辑经验规则，人工逐项核对耗时且易遗漏。
2. **传统纯提示词 AI 工具不可控**：将审校逻辑直接写入大模型提示词的早期应用普遍存在「黑箱运行、易漏检、幻觉引文、编造标准条款、无法精确回写 Word 原文位置、重复运行结果不一致」等工程缺陷，难以作为严肃出版生产系统长期演进。
3. **历史成果分散、缺少统一能力底座**：核查本机历史项目时发现两类成熟资产各自独立——Python 侧的 `medical-journal-copyediting-2.0.0` 沉淀了四层结构化规则与章节识别模型（25/25 用例通过），Web 侧的 `medical-journal-reviewer` 沉淀了原生 Word 批注注入防踩坑经验、综合审评报告导出、大模型 JSON 截断修复与 SQLite 统计口径（95 项测试通过），但缺乏统一的数据协议与任务编排中枢。

### 1.2 建设目标
基于上述背景，本项目确立核心建设原则：**建设可扩展的审校能力平台（Review Platform），而非一次性单点应用**。当前版本（V0.1）率先围绕首个应用领域——**医学科技期刊**，打通“DOCX 安全解析 → 规范文档装载 → Router DAG 编排 → 确定性规则与复算核验 → 锚点与依据门禁 → 编辑裁决与原生批注导出”的完整纵向闭环，同时在架构上为后续接入受控大模型语义审校预留可插拔执行器与门禁机制。

---

## 二、应用场景

当前平台面向中文科技期刊编辑部及相关审校角色，覆盖以下核心场景：

| 应用场景 | 目标用户 | 当前版本（V0.1）实际支撑方式 | 后续演进方向 |
|---|---|---|---|
| **1. 来稿自动化初筛与核验** | 期刊责编、专业审校人员 | 上传 `.docx` 稿件，按期刊配置自动检查数字、单位、标点、医学名词、参考文献、P 值闭区间、`n/N(pct%)` 复算与图表题注顺序 | 接入受控 LLM 执行摘要-正文一致性、方法学设计与图文表对应核查 |
| **2. 可视化审校过程审计** | 编辑、质控复核人员 | 提供 6 阶段执行步进条、按规范文档执行追踪、FactLedger 事实台账（含引号例外豁免原因）与全量 DAG 任务表，防止静默遗漏 | 增加跨版本稿件修改差异对齐与任务耗时监控 |
| **3. 规范知识与规则在线维护** | 编辑部规则管理员、资深校对 | 以「9 份独立 Markdown 规范文档」浏览、导出或在线编辑，支持上传 `.md/.txt/.docx/.json` 规范文件自动解析规则，**强制通过正反例校验后方可激活** | 增加多人审核流与灰度发布/回滚界面 |
| **4. 证据化裁决与 Word 批注退修** | 期刊编辑 | 在 Document IR 原文视图高亮定位引文，查看依据条款与复算证据，提交 `accept / reject / defer` 裁决（乐观并发防覆盖），一键导出仅含已采纳意见的原生 Word 批注与综合审评报告 `.docx` | 对接编辑部稿件采编系统、作者自查端与 Word 插件 |
| **5. 编校质量评估与规则迭代** | 质量管理员、教研人员 | 所有编辑裁决自动落入 `evaluation_feedback` 评估队列，支持按问题大类归并统计与黄金样本 Precision/Recall/F1 回归度量 | 建立双编辑盲标仲裁黄金测试集 |

---

## 三、设计框架

平台采用**协议驱动的分层解耦架构**，确保任何一层能力均可独立测试、替换与升级，各层职责与红线如下：

```
+-----------------------------------------------------------------------+
| 应用与交互层 (Application Layer)                                      |
|  · 编辑工作台 Web UI (review_platform/static/)                        |
|  · FastAPI 服务接口 (review_platform/api.py)                          |
|  · 命令行工具 CLI (python -m review_platform)                         |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
| 核心能力编排层 (Review Platform Core Engines)                         |
|  1. Document Engine   : DOCX 安全解析 -> DocumentIR + ManuscriptProfile|
|  2. Knowledge Engine  : 结构化条款 + 9份Markdown规范文档 + 期刊配置   |
|  3. Rule Registry     : 版本化 RuleSpec 注册 + basis_id 校验 + 正反例 |
|  4. Router            : Kahn 确定性 DAG 编排（零静默遗漏、环/缺失拦截）|
|  5. Review Engine     : FactLedger抽取 + 确定性规则/复算 + AI适配器底座|
|  6. Finding Engine    : 锚点逐字核验 + 依据真实性门禁 + 稳定ID去重    |
|  7. Editorial Engine  : SQLite 裁决事件 + 原生批注DOCX + 综合报告DOCX |
|  8. Evaluation Engine : 黄金集度量 + 问题大类归并 + 编辑反馈评估队列  |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
| 可插拔执行层 & 基础设施层                                             |
|  · 已运行：Deterministic Pattern Executor / Decimal Math Validators    |
|  · 已预留接口：AICandidateExecutor (JSON修复 + 动态时间基准防误判)     |
|  · 数据存储：SQLite (WAL模式、运行快照、不可变裁决事件、维护文档持久化)|
+-----------------------------------------------------------------------+
```

### 核心设计约束（工程红线）
1. **逻辑不硬编码**：规则与规范条款存于结构化知识包与注册表，不写死在前端或提示词中；
2. **知识库不等同于向量库**：以精确条款编号、版本、适用章节与例外规则为核心契约；
3. **任务不静默遗漏**：`RuleRegistry` 中每条规则必在 `ExecutionPlan` 留痕（`passed / findings / skipped / failed / blocked`）；
4. **AI 不越权、不编造**：禁止自动修改科研数据，禁止引用未登记的 `basis_id`，AI 候选仅作待裁决建议且须过门禁。

---

## 四、技术路线

围绕「**确定性规则 + 专业计算工具 + 受控 AI 语义审校**」三轨协同路线，当前版本已落地的主体技术链路与预留扩展路线如下：

### 4.1 稿件中间表示协议（`document-ir/1.0`）
- **零清洗保真提取**：直接解析 OOXML `word/document.xml` 顶层 `<w:p>` 与 `<w:tbl>` 单元格，精确提取 `<w:t>`、`<w:tab/>`、`<w:br/>`、`<w:cr/>` 可见文本与直接 `<w:r>` 的 Unicode 码点区间（`RunSpan`），避免正误替换导致坐标错位。
- **确定性指纹与安全防线**：以 DOCX 原始字节 `SHA-256` 作为 `document_id`；强制拦截空文件、超 10 MiB 文件、ZIP 炸弹（30 MiB / 1000 成员）、XXE 外部实体（`<!DOCTYPE`/`<!ENTITY`）及未定稿 Word 修订（`<w:ins>`/`<w:del>`/`<w:moveFrom>`/`<w:moveTo>`）。
- **结构画像复用**：通过 [legacy_structure.py](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform/review_platform/adapters/legacy_structure.py) 复用 `manuscript_model.py` 实现中文医学论著章节分类与引号示例上下文区间识别。

### 4.2 文档化知识体系与规则门禁
- **规范文档与条款绑定**：将四层规范整理为 9 份结构化 Markdown 文档与 `clauses.json`，支持通过 `parse_rule_document_bytes`（[doc_parser.py](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform/review_platform/knowledge_engine/doc_parser.py)）上传 `.md/.txt/.docx/.json` 文件自动抽取对照表格与规则条目。
- **正反例自动校验发布**：新增或修订规则保存前，经 `validate_maintainable_rule` 验证「必命中正例 + 不误报反例」，通过后刷新 `rule_snapshot_hash` 并持久化到 SQLite。

### 4.3 确定性 Router DAG 与可视化执行追踪
- **拓扑规划**：采用 Kahn 算法（字典序堆）对所有已注册规则与前置 `extract.facts` 节点进行确定性拓扑排序；检测环依赖、缺失依赖或未知禁用规则；上游节点失败时显式将下游置为 `blocked`。
- **6 阶段过程追踪**：每次运行产出 `ReviewRun.trace`（`document_parse` → `knowledge_bind` → `router_plan` → `fact_extract` → 按规范文档汇总的 `rule_execute` → `finding_gate`），并在工作台可视化呈现。

### 4.4 专业计算复算与原生 Word 批注写回
- **零浮点漂移复算**：使用 Python `Decimal` 复算 `n/N(pct%)` 与显示精度半单位/期刊容差比较，检验 P 值 `[0, 1]` 边界及独立图/表题注序列。
- **原生 OOXML 批注导出**：[word_exporter.py](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform/review_platform/editorial_engine/word_exporter.py) 在核验原稿 `SHA-256` 一致且意见最新裁决为 `accept` 后，基于 `RunSpan` 精确拆分 `<w:r>` 并保留原 `<w:rPr>` 格式注入批注标记；对含既有批注冲突段落或非直接文本容器显式拒绝并报错。另提供 [summary_exporter.py](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform/review_platform/editorial_engine/summary_exporter.py) 导出综合审评 Word 报告。

### 4.5 受控大模型语义协同路线（当前已预留底座，待接入在线调用）
- 当前 [llm_adapter.py](file:///Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform/review_platform/review_engine/llm_adapter.py) 已完成移植自 `medical-journal-reviewer` 的三项防御基础设施：括号配平 JSON 提取与截断修复（`extract_json_block` / `repair_json` / `parse_model_json`）、运行时动态时间基准注入（`build_time_anchored_system_prompt`）以及当期年份误判过滤（`is_future_date_misjudgment`），并定义 `AICandidateExecutor` 协议。
- 后续 V0.4 接入真实 LLM HTTP 客户端时，所有语义审校产出必须经过 `govern_findings`（原文 `Anchor` 逐字匹配 + `basis_id` 存在性校验）方能以 `ai_candidate` 身份呈送编辑裁决。

---

## 五、资源需求

### 5.1 当前版本（V0.1 离线确定性基线）资源需求
当前版本不依赖外部大模型或独立向量数据库，单机即可低成本稳定运行：
- **计算与内存**：2 vCPU / 1–2 GB RAM（单篇万字级 DOCX 解析与全套确定性规则执行耗时在百毫秒级）。
- **运行环境**：Python 3.11+（已在 Python 3.12.13 验证），核心依赖为 `fastapi`、`uvicorn`、`pydantic`、`python-docx`、`lxml`、`python-multipart`；测试依赖 `pytest`、`httpx`。
- **存储需求**：本地 SQLite（WAL 模式）存放运行快照、裁决历史、评估反馈与自定义规范文档，单机磁盘占用极小（MB 级起步）；原始 DOCX 字节不默认落盘存储。

### 5.2 后续接入受控 LLM 与编辑部试点（V0.4–V1.0）新增资源需求
- **模型算力/API 配额**：支持 OpenAI 兼容接口或私有化部署推理服务（需支持上下文 >= 32K tokens、输出 >= 8K tokens，推荐支持关闭思考链 `enable_thinking: false` 以避免并发超时）。
- **基础设施升级**：试点阶段需引入 PostgreSQL、异步任务队列（处理多稿并发长耗时语义审校）、HTTPS 网关、加密存储与对象存储。
- **领域与标注人力**：需要 2 名以上资深医学期刊编辑参与脱敏黄金样本双盲标注与仲裁，构建规则级精度/召回基线；涉及国家标准全文授权与医学术语库更新时需配合相应版权与专家评审资源。

---

## 六、预期效果

### 6.1 当前版本已可实测验证的效果
1. **自动化验证全通过**：当前工程套件 `.venv/bin/python -m pytest -v` **16 项自动化测试全部通过**（覆盖 DOCX 安全防线、精确锚点、DAG 失败阻断、P 值/百分比/题注/复用规则正反边界例外、文档上传解析与 422 拦截、乐观并发 409、原生 Word 批注写回与综合报告导出）。
2. **复用基线可追溯**：继承的 `medical-journal-copyediting-2.0.0` 在其 25 例基准测试中达成 `TP=20, FP=0, FN=0 (F1=1.000)`；`medical-journal-reviewer` 原有 95 项前端与导出测试全部通过。
3. **100% 确定性定位与零静默遗漏**：所有已通过门禁的确定性 Finding 均保证原文区间逐字可验证，Router DAG 中每条规则执行或跳过状态均有明确追踪。

### 6.2 演进与试点阶段预期目标（门禁指标，待真实脱敏样本验证）
- 确定性规则定位准确率保持 **100%**；
- 核心高风险确定性规则在双编辑仲裁黄金集上实现 Precision >= 95%、Recall >= 90% 与重大退化为 0；
- 结合受控 LLM 语义审校后，进一步覆盖“摘要与结果数字一致、方法与结果指标呼应、图文表数据交叉核对”等跨位置语义审校场景，显著降低编辑初筛与格式加工机械耗时，同时保持全部裁决可审计。

---

## 七、可能的风险与伦理合规考虑

| 风险类别 | 具体风险描述 | 平台当前与演进防控措施 |
|---|---|---|
| **1. 科研数据篡改风险** | 审校工具或 AI 误改稿件中的样本量、P 值、效应量或实验结论，引发严重科研诚信问题 | 平台硬性规定 `DocumentIR` 只读；Word 批注导出**只写批注不改正文**；数值复算仅提示「请编辑核实原始数据」 |
| **2. AI 幻觉与伪造依据** | 后续接入大模型后可能编造不存在的国家标准条款、虚构文献或将当期投稿年份误判为“未来时间” | 强制 `basis_id` 必须存在于 `KnowledgeEngine`；未通过原文 `Anchor` 逐字核验的候选全部拦截入 `rejected_candidates`；内置运行时日期基准注入与误判过滤 |
| **3. 隐性漏检与误导性通过** | 系统因解析失败、作用域不匹配或静默跳过规则而输出虚假的“全稿无错”结论 | Router 禁止静默丢弃任务；解析限制（页眉页脚/脚注/图片/公式未纳入定位）强制写入 `limitations` 与导出报告；修订稿明确拒绝导入 |
| **4. 规则污染与误报扩散** | 未经检验的编辑反馈或临时规则直接覆盖正式规则，导致全量稿件误报激增 | 编辑裁决仅进入 `evaluation_feedback` 队列；在线新增/修改规则文档必须通过正反例自动校验门禁方可保存 |
| **5. 稿件保密与患者隐私** | 未发表医学手稿含科研机密与潜在患者敏感信息，外发第三方模型或泄露本地库存在保密风险 | 当前 V0.1 完全本地离线运行，稿件数据不出本机；数据库明文提示敏感性且 `.gitignore` 排除入库；V1.0 上线前须补齐认证授权、脱敏网关、HTTPS、静态加密与数据删除策略 |
| **6. 标准版权与责任主体** | 未经授权转载国家标准全文引发版权纠纷；编辑过度依赖自动化结果导致审稿把关失责 | 知识库当前仅存储结构化标准编号、条款摘要与核查逻辑，不非法分发标准全文；平台定位为**编辑辅助审校**，最终学术与出版裁决权及签名责任始终归属期刊编辑 |
