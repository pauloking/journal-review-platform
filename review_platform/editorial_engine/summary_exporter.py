"""综合审评意见 Word 报告生成器（移植自 medical-journal-reviewer/src/services/summaryReportGenerator.ts）。"""

from __future__ import annotations

import io
from collections import Counter

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from review_platform.models import EditorialDecision, ReviewRun


def build_summary_docx(run: ReviewRun) -> bytes:
    """将审校任务执行覆盖、问题分布、证据清单及裁决状态生成标准 Word 汇总报告。"""
    doc = Document()

    latest: dict[str, EditorialDecision] = {}
    for dec in run.decisions:
        prev = latest.get(dec.finding_id)
        if prev is None or dec.revision > prev.revision:
            latest[dec.finding_id] = dec

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title_p.add_run("中文科技期刊智能审校综合意见报告")
    tr.bold = True
    tr.font.size = Pt(18)

    sub_p = doc.add_paragraph()
    sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sr = sub_p.add_run(run.document.profile.title_guess or run.document.filename)
    sr.font.size = Pt(11)
    sr.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    doc.add_heading("一、稿件与审校平台信息", level=1)
    info_rows = [
        ("运行编号", run.run_id),
        ("稿件文件", run.document.filename),
        ("文件指纹", run.document.document_id[:24] + "…"),
        ("正文字数", f"{run.document.profile.char_count} 字"),
        ("期刊配置", f"{run.journal.name} ({run.journal.id} v{run.journal.version})"),
        ("规则快照", run.plan.rule_snapshot_hash),
        ("运行状态", run.status),
    ]
    tbl = doc.add_table(rows=1, cols=2)
    tbl.style = "Table Grid"
    tbl.rows[0].cells[0].text = "项目"
    tbl.rows[0].cells[1].text = "内容"
    for k, v in info_rows:
        row = tbl.add_row().cells
        row[0].text = k
        row[1].text = v

    doc.add_heading("二、任务执行覆盖（无静默遗漏）", level=1)
    status_counts = Counter(t.status for t in run.plan.tasks)
    st_tbl = doc.add_table(rows=1, cols=2)
    st_tbl.style = "Table Grid"
    st_tbl.rows[0].cells[0].text = "任务状态"
    st_tbl.rows[0].cells[1].text = "数量"
    for st, cnt in sorted(status_counts.items()):
        r = st_tbl.add_row().cells
        r[0].text = st
        r[1].text = str(cnt)

    doc.add_heading(f"三、审校意见与证据清单（共 {len(run.findings)} 条）", level=1)
    action_cn = {"accept": "已采纳", "reject": "已驳回", "defer": "暂缓"}
    if not run.findings:
        doc.add_paragraph("本次确定性审校未发现需处理问题。")
    else:
        for idx, f in enumerate(run.findings, 1):
            dec = latest.get(f.id)
            dec_label = f"{action_cn[dec.action]}（{dec.editor}）" if dec else "待裁决"
            p = doc.add_paragraph()
            r1 = p.add_run(f"{idx}. [{f.severity.upper()}] {f.rule_id} — {f.title}（{dec_label}）")
            r1.bold = True
            doc.add_paragraph(f"定位：块 {f.anchor.block_id} [{f.anchor.start}:{f.anchor.end}] 原文「{f.anchor.quote}」")
            doc.add_paragraph(f"问题：{f.message}")
            doc.add_paragraph(f"建议：{f.suggestion}")
            doc.add_paragraph(f"依据：{f.basis_id}（{f.basis_source}）")

    doc.add_heading("四、覆范围与局限声明", level=1)
    for lim in run.document.limitations:
        doc.add_paragraph(f"• {lim}")

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
