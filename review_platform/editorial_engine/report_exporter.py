"""JSON 与 Markdown 审校结果导出器。"""

from __future__ import annotations

import json
from typing import Any

from review_platform.models import EditorialDecision, ReviewRun


def latest_decisions(run: ReviewRun) -> dict[str, EditorialDecision]:
    out: dict[str, EditorialDecision] = {}
    for dec in run.decisions:
        prev = out.get(dec.finding_id)
        if prev is None or dec.revision > prev.revision:
            out[dec.finding_id] = dec
    return out


def export_json(run: ReviewRun) -> str:
    payload: dict[str, Any] = run.model_dump(mode="json")
    payload["latest_decisions"] = {
        fid: dec.model_dump(mode="json") for fid, dec in latest_decisions(run).items()
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def export_markdown(run: ReviewRun) -> str:
    latest = latest_decisions(run)
    action_cn = {"accept": "已采纳", "reject": "已驳回", "defer": "暂缓"}
    sev_cn = {"error": "严重", "warning": "警告", "info": "提示"}

    lines: list[str] = [
        f"# 期刊稿件智能审校报告（{run.run_id}）",
        "",
        "## 一、稿件与运行摘要",
        f"- 稿件文件：`{run.document.filename}`",
        f"- 文档指纹 (`SHA-256`)：`{run.document.document_id}`",
        f"- 推断标题：{run.document.profile.title_guess or '（未识别）'}",
        f"- 正文字数：{run.document.profile.char_count}（正文块 {run.document.profile.paragraph_count}，表格单元 {run.document.profile.table_cell_count}）",
        f"- 结构质量：{run.document.profile.structure_status}"
        + (f"（提示：{'；'.join(run.document.profile.structure_warnings)}）" if run.document.profile.structure_warnings else ""),
        f"- 期刊配置：`{run.journal.id}` v{run.journal.version}（{run.journal.name}）",
        f"- 运行状态：`{run.status}` | 规则快照：`{run.plan.rule_snapshot_hash}`",
        "",
        "## 二、覆盖范围与局限声明",
    ]
    for lim in run.document.limitations:
        lines.append(f"- {lim}")

    lines.extend([
        "",
        "## 三、Router DAG 任务执行摘要（零静默遗漏）",
        "| 任务节点 | 类别 | 状态 | 检查数 | 排除数 | 命中数 | 说明 |",
        "|---|---|---|---:|---:|---:|---|",
    ])
    for task in run.plan.tasks:
        lines.append(
            f"| `{task.node_id}` | {task.category} | `{task.status}` | "
            f"{task.checked_count} | {task.excluded_count} | {task.finding_count} | {task.reason} |"
        )

    lines.extend(["", f"## 四、审校意见与证据清单（共 {len(run.findings)} 条）"])
    if not run.findings:
        lines.append("- 未命中需要处理的确定性问题。")
    else:
        for idx, f in enumerate(run.findings, 1):
            dec = latest.get(f.id)
            dec_text = (
                f"{action_cn.get(dec.action, dec.action)}（v{dec.revision} · {dec.editor}：{dec.reason}）"
                if dec
                else "待裁决"
            )
            lines.extend([
                f"### {idx}. [{sev_cn.get(f.severity, f.severity)}] `{f.rule_id}` — {f.title}",
                f"- **Finding ID**：`{f.id}` | **当前裁决**：{dec_text}",
                f"- **原文定位**：块 `{f.anchor.block_id}` 区间 `[{f.anchor.start}, {f.anchor.end})` 引文：「`{f.anchor.quote}`」",
                f"- **问题描述**：{f.message}",
                f"- **修改建议**：{f.suggestion}",
                f"- **规范依据**：`{f.basis_id}` — {f.basis_source}",
                f"- **结构化证据**：`{json.dumps(f.evidence, ensure_ascii=False)}`",
                "",
            ])

    lines.extend(["## 五、编辑裁决完整历史"])
    if not run.decisions:
        lines.append("- 暂无裁决记录。")
    else:
        for d in run.decisions:
            lines.append(
                f"- `{d.created_at}` | `{d.finding_id}` | v{d.revision} | **{action_cn.get(d.action, d.action)}** | {d.editor}：{d.reason}"
            )

    return "\n".join(lines) + "\n"
