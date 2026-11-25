# 中文科技期刊智能审校能力平台（Journal Review Platform）

定位：**可扩展的中文科技期刊智能审校能力平台**（首个应用领域：医学科技期刊）。

本仓库严格按能力平台架构建设，优先审计并复用了本机已有两项成熟医学审校资产（详见 [现有项目核查](docs/EXISTING_PROJECT_AUDIT.md)）：
- `medical-journal-copyediting-2.0.0`：章节结构识别（`legacy_structure.py`）、四层规范规则库（`knowledge_packs/medical_cn_v1/rules/*.json`）与上下文/年份豁免逻辑；
- `medical-journal-reviewer`：原生 OOXML Word 批注安全防踩坑经验、综合审评报告 DOCX 导出、OpenAI/Claude/自定义大模型接入与 JSON 括号配平修复、动态时间基准、以及问题大类归并统计口径。

## 快速开始

```bash
# 1. 进入项目并激活虚拟环境
cd journal-review-platform
source .venv/bin/activate

# 2. 运行全部自动化测试
python -m pytest -q

# 3. 生成合成医学稿件示例并执行 CLI 审校
python examples/make_demo.py
python -m review_platform examples/demo.docx --format markdown --output var/demo-report.md

# 4. 启动本地编辑工作台与 API 服务（仅监听本机）
python -m uvicorn review_platform.api:create_app --factory --host 127.0.0.1 --port 8000

# 可选：部署鉴权（通过环境变量启用 Bearer Token + RBAC）
# export REVIEW_API_TOKENS='{"tok-editor":{"user":"王编辑","role":"editor"},"tok-admin":{"user":"规则管理员","role":"admin"}}'

# 可选：服务端默认大模型配置（也可直接在网页工作台自定义配置，API Key 不落库）
# export REVIEW_LLM_ENABLED=1
# export REVIEW_LLM_PROVIDER=custom
# export REVIEW_LLM_ENDPOINT=https://api.deepseek.com/v1
# export REVIEW_LLM_MODEL=deepseek-chat
# export REVIEW_LLM_API_KEY=sk-...
```

打开浏览器访问 `http://127.0.0.1:8000/` 即可体验完整纵向闭环（上传 DOCX → Document IR → Router DAG → 带原文高亮与依据的意见 → 编辑裁决 → 导出原生 Word 批注与综合报告）。

## 工程设计文档

- [PRD.md](PRD.md) — 产品需求与首轮纵向闭环验收标准
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 分层架构与严格模块边界
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — 核心数据模型与对象关系
- [docs/DOCUMENT_IR.md](docs/DOCUMENT_IR.md) — `document-ir/1.0` 中间表示与锚点协议
- [docs/KNOWLEDGE_SYSTEM.md](docs/KNOWLEDGE_SYSTEM.md) — 四层规范知识库与期刊配置包
- [docs/RULE_SPEC.md](docs/RULE_SPEC.md) — 版本化审校规则与执行协议
- [docs/ROUTER_SPEC.md](docs/ROUTER_SPEC.md) — 确定性 Router DAG 与任务状态机
- [docs/FINDING_SPEC.md](docs/FINDING_SPEC.md) — 意见门禁、证据链与乐观并发裁决
- [docs/EVALUATION.md](docs/EVALUATION.md) — 质量评估指标与回归测试体系
- [docs/ROADMAP.md](docs/ROADMAP.md) — V0.1 ~ V1.0 演进路线
- [docs/EXISTING_PROJECT_AUDIT.md](docs/EXISTING_PROJECT_AUDIT.md) — 原有医学审校项目核查与复用记录
- [docs/AGENT_DESIGN_SPEC.md](docs/AGENT_DESIGN_SPEC.md) — 当前版本智能体设计书（背景/场景/框架/路线/资源/效果/伦理合规）
