# 质量评价与回归测试体系（EVALUATION）

## 1. 评价维度与指标

本平台禁止用“模型自我感觉良好”代替质量评估，所有新增或变更审校能力必须由客观测试与黄金样本集度量：

| 指标 | 定义 | 首轮目标/门禁 |
|---|---|---|
| 定位准确率 | 产出 Finding 的 `Anchor` 通过 `validate_anchor` 且与黄金标注区间匹配比例 | 确定性规则 **100%** |
| 规则精确率 (Precision) | `TP / (TP + FP)` | 合成/黄金基线按规则逐条统计 |
| 规则召回率 (Recall) | `TP / (TP + FN)` | 合成/黄金基线逐条统计 |
| 任务覆盖无静默遗漏率 | `ExecutionPlan.tasks` 覆盖 `RuleRegistry` 全部规则比例 | **100%** |
| 问题大类归并口径 | 沿用 `medical-journal-reviewer` 的 `PROBLEM_TYPE_GROUPS` 归并视图 | 细分类与大类双重可查 |

## 2. 最小测试配置要求

每条新增规则必须至少配套 4 类样本测试：
1. **正例 (Positive)**：确认问题能被精准检出且定位正确；
2. **反例 (Negative)**：确认正确写法不误报；
3. **边界 (Boundary)**：如 `P=0` / `P=1`、四舍五入容差边界、`2020-2025` 年份豁免；
4. **例外 (Exception)**：如引号内示例句、“血压高于”“人流量”上下文豁免、正文提及“见图9”不误判为题注。

## 3. 历史回归与当前实测基线（2026-11-25）

- **本平台自动化测试（`.venv/bin/python -m pytest -v`，Python 3.12.13）**：覆盖 `DocumentIR` 安全解析与锚点、Knowledge/Registry/Router DAG、P 值/百分比/题注/复用规则正反边界例外、规则文档无损往返与重启保持、ReDoS 静态与执行预算拦截、流式上传限额与 ZIP 炸弹拦截、Bearer RBAC 鉴权、同段多批注 Word 导出、受控大模型（OpenAI/Claude/自定义兼容接口）并行执行与锚点/幻觉拦截门禁、乐观并发裁决、综合报告导出及 CLI 全流程（最新实测见下一次运行记录）。
- **历史基线核对（未改动原仓库）**：
  - `medical-journal-copyediting-2.0.0`：`scripts/tests/run_tests.py` 实测 `25/25` 通过（`F1=1.000`）。
  - `medical-journal-reviewer`：`npm test`（Vitest）实测 9 个文件 `95/95` 通过，`npm run build` 构建通过。
- 所有编辑 `reject`（误报反馈）或 `accept` 裁决记入 SQLite `evaluation_feedback` 评估队列，供人工标注黄金集并驱动规则版本迭代。
