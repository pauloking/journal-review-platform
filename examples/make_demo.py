"""生成用于演示与端到端验证的合成中文医学期刊 DOCX 稿件。"""

from __future__ import annotations

from pathlib import Path
from docx import Document


def build_demo_docx(output_path: str | Path) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    doc.add_paragraph("急性心肌梗塞患者经皮冠状动脉介入术后预后分析")
    doc.add_paragraph(
        "摘要：目的 探讨急性心肌梗死患者行经皮冠状动脉介入（PCI）治疗的临床疗效。"
        "方法 纳入2020-2025年住院患者100例，血清肌钙蛋白检测以3000rpm离心分离，"
        "有效率30/100（40%），对照组与观察组差异显著（p<0.05，实测P=1.25）。"
        "注意引号示例不应误报：“例如错例P=1.5和20/100（90%）”。"
    )
    doc.add_paragraph("关键词：心肌梗死；经皮冠状动脉介入；预后")
    doc.add_paragraph("1 资料与方法")
    doc.add_paragraph(
        "1.1 一般资料 患者年龄30-65岁，收缩压高于140 mmHg（注：血压高于140 mmHg属正常语境）。"
        "对照组有效例数见图9（正文交叉引用不计为题注），其余结果如下......"
    )
    doc.add_paragraph("图1 患者入组筛选流程图")
    doc.add_paragraph("2 结果")
    doc.add_paragraph("表1 两组患者基线资料比较")

    tbl = doc.add_table(rows=2, cols=3)
    tbl.rows[0].cells[0].text = "组别"
    tbl.rows[0].cells[1].text = "例数/总数（比例）"
    tbl.rows[0].cells[2].text = "统计学检验"
    tbl.rows[1].cells[0].text = "观察组"
    tbl.rows[1].cells[1].text = "45/90（50.0%）"
    tbl.rows[1].cells[2].text = "P=0.012"

    doc.add_paragraph("图3 术后6个月生存曲线（此处故意跳号，缺失图2）")
    doc.add_paragraph("参考文献")
    doc.add_paragraph("[1] 张三, 李四. 急性心肌梗死诊疗进展[J]. 中华心血管病杂志, 2024, 52(3): 120～125.")

    doc.save(out)
    return out


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "demo.docx"
    build_demo_docx(target)
    print(f"Generated: {target}")
