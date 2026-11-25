# 原有医学期刊审校项目核查与复用说明

核查日期：2026-11-25

根据研发移交要求，优先对本机已有医学期刊审校项目进行全面核查，识别可复用的成熟能力、明确技术边界与不直接照搬的部分，避免重复建设。

## 1. 核查对象与实际验证情况

### 1.1 `medical-journal-copyediting-2.0.0`
- 路径：`/Users/wangguoyang/Documents/Codex/2026-07-29/wo-ka/work/medical-journal-copyediting-2.0.0`
- 实际运行验证：`PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/run_tests.py`
  - 实测结果：`用例通过: 25/25 (100.0%)`，`规则级: TP=20 FP=0 FN=0 精确率=1.000 召回率=1.000 F1=1.000`。
- 核心资产评估：
  1. `scripts/manuscript_model.py`（纯标准库）：中文医学期刊章节识别（题名/摘要/关键词/引言/方法/结果/讨论/结论/参考文献/表格）、参考文献区段自动回溯与结构质量诊断。**可直接复用并适配为 Document Engine 的结构画像模块**。
  2. `scripts/rules/*.json`（`numbers.json`、`units.json`、`punct.json`、`stats.json`、`terms.json`、`references.json`、`journal.json`、`experience.json`）：结构化四层规则库（国家标准/行业规范/期刊体例/编辑经验），携带 `source`、`severity`、`example`、`counter_example`、`exception_after`、`skip_if_year`、`skip_context` 等元数据。**是建设 Knowledge Engine 与 Rule Registry 的高价值底座**。
  3. `scripts/check_manuscript.py` 中的规则过滤逻辑（年份范围豁免、文献行豁免、`exception_after` 上下文防误报、小数点前零保护等）。**可抽象复用为规则执行器**，但其原本按纯文本行裁剪上下文的方式无法满足新平台的精确 Unicode `Anchor` 契约，需在新架构中绑定到 `Block` 与 `start..end` 区间。
  4. `scripts/parse_docx.py` 存在域残余清理与空白压缩逻辑，会破坏原稿字符偏移，**不复用其文本清洗逻辑**，改由新平台的精确可见文本解析器替代。

### 1.2 `medical-journal-reviewer`
- 路径：`/Users/wangguoyang/Projects/medical-journal-reviewer`
- 实际运行验证：`npm test`（Vitest）**9 个测试文件、95 项测试全部通过**；`npm run build`（TypeScript + Vite）构建成功。
- 核心资产评估：
  1. `src/services/docxGenerator.ts`：原生 OOXML Word 批注注入方案，记录并修复了实际 Word 稿件中的关键踩坑点——限定 `<w:t(?:\s[^>]*)?>` 防止 `<w:topLinePunct/>`/`<w:tabs>`/`<w:textAlignment/>` 污染文本提取、安全插入 `commentRangeStart`/`commentRangeEnd`/`commentReference`、更新 `[Content_Types].xml` 与 `word/_rels/document.xml.rels`、显式标注无法定位的批注。**其 OOXML 工程经验完整继承到 Python 版 `word_exporter.py` 中，并升级为基于 Document IR `RunSpan` 的精确拆 Run 挂载**。
  2. `src/services/summaryReportGenerator.ts`：独立的「综合审评意见」Word 文档生成器，将评分、问题分布、按严重程度排序的问题清单生成标准排版表格与段落。**移植为平台 `summary_exporter.py`**。
  3. `src/services/aiService.ts`：括号配平 JSON 块提取（`extractJsonBlock`）、截断 JSON 修复（`repairJson` / `scanJson`）、运行时当前日期时间基准注入（防止模型将当期投稿年份误判为“未来时间”）。**移植为平台 `llm_adapter.py`，并加上严格的证据与规范依据门禁**。
  4. `server/review_log_server.py`：SQLite 落库与 `PROBLEM_TYPE_GROUPS` 大类归并统计口径。**移植为平台 `evaluation_engine` 与 `store` 统计接口**。

## 2. 独立仓库构建策略

新平台在 `/Users/wangguoyang/.gemini/antigravity/scratch/journal-review-platform` 从零建立干净仓库与工程协议，**不修改上述两处历史项目源码**：
- 通过 `review_platform/adapters/legacy_structure.py` 封装并溯源 `manuscript_model.py` 章节结构识别能力；
- 将 `rules/*.json` 规范整理并赋予标准依据 ID（`basis_id`）纳入 `review_platform/knowledge_packs/medical_cn_v1/`；
- 将 `medical-journal-reviewer` 的 Word 批注防踩坑规则、综合审评报告、JSON 容错与问题大类归并实现为平台后端标准化能力，并通过自动化测试逐项验证。
