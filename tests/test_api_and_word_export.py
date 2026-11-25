from __future__ import annotations

import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from examples.make_demo import build_demo_docx
from review_platform.__main__ import main as cli_main
from review_platform.api import create_app
from tests.conftest import make_docx_bytes, patch_docx_member

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def test_full_01_to_09_vertical_loop_and_word_export(tmp_path: Path):
    db_path = tmp_path / "review.sqlite3"
    demo_path = build_demo_docx(tmp_path / "demo.docx")
    demo_bytes = demo_path.read_bytes()

    app = create_app(db_path=db_path)
    client = TestClient(app)

    # 0. 健康检查、规则注册接口与工作台首页
    assert client.get("/health").status_code == 200
    reg = client.get("/registry").json()
    assert len(reg["rules"]) > 10
    assert client.get("/").status_code == 200

    # 1-6. 上传真实 DOCX -> 解析 IR -> 识别特征 -> Router DAG -> 规则执行 -> 生成带定位和证据的意见
    res = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("demo.docx", demo_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    run_data = res.json()
    run_id = run_data["run_id"]
    findings = run_data["findings"]
    assert len(findings) >= 6

    # 7. 编辑裁决 + 乐观并发冲突检测（409）
    target_f = next(f for f in findings if f["rule_id"] == "DATA-PCT-RECALC")
    bad_rev = client.post(
        f"/reviews/{run_id}/decisions",
        json={
            "finding_id": target_f["id"],
            "action": "accept",
            "editor": "王编辑",
            "reason": "版本冲突测试",
            "expected_revision": 1,
        },
    )
    assert bad_rev.status_code == 409

    d1 = client.post(
        f"/reviews/{run_id}/decisions",
        json={
            "finding_id": target_f["id"],
            "action": "defer",
            "editor": "王编辑",
            "reason": "待作者核实原始病例数",
            "expected_revision": 0,
        },
    )
    assert d1.status_code == 200
    assert d1.json()["decision"]["revision"] == 1

    d2 = client.post(
        f"/reviews/{run_id}/decisions",
        json={
            "finding_id": target_f["id"],
            "action": "accept",
            "editor": "王编辑",
            "reason": "复核确认30/100应为30%",
            "expected_revision": 1,
        },
    )
    assert d2.status_code == 200
    assert d2.json()["decision"]["revision"] == 2

    # 重复提交同一份 DOCX + 配置：run_id 稳定且不清空既有裁决历史
    res_dup = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("demo.docx", demo_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res_dup.json()["run_id"] == run_id
    assert len(res_dup.json()["decisions"]) == 2

    # 新建应用实例重启恢复，裁决与反馈队列持久存在
    client2 = TestClient(create_app(db_path=db_path))
    restored = client2.get(f"/reviews/{run_id}").json()
    assert len(restored["decisions"]) == 2
    fb = client2.get("/evaluation/feedback").json()
    assert fb["summary"]["total_feedback"] == 2

    # 8. 导出 JSON / Markdown / 综合报告 DOCX / 原生批注 DOCX
    assert client2.get(f"/reviews/{run_id}/export?format=json").status_code == 200
    md_res = client2.get(f"/reviews/{run_id}/export?format=markdown")
    assert md_res.status_code == 200
    assert "30/100（40%）" in md_res.text

    sum_res = client2.get(f"/reviews/{run_id}/export?format=summary_docx")
    assert sum_res.status_code == 200
    sum_doc = Document(io.BytesIO(sum_res.content))
    assert any("审校意见与证据清单" in p.text for p in sum_doc.paragraphs)

    # 指纹不一致的原稿必须被拒绝导出批注
    wrong_docx = make_docx_bytes(["另一份不同的稿件"])
    mismatch_res = client2.post(
        f"/reviews/{run_id}/export/docx",
        files={"file": ("wrong.docx", wrong_docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert mismatch_res.status_code == 422

    # 原稿导出原生 Word 批注：验证 comments.xml 与 commentRangeStart/End/Reference
    word_res = client2.post(
        f"/reviews/{run_id}/export/docx",
        files={"file": ("demo.docx", demo_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert word_res.status_code == 200
    assert word_res.headers.get("X-Exported-Comments") == "1"

    with zipfile.ZipFile(io.BytesIO(word_res.content), "r") as zf:
        assert "word/comments.xml" in zf.namelist()
        comments_xml = zf.read("word/comments.xml").decode("utf-8")
        assert "复核确认30/100应为30%" in comments_xml
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert "commentRangeStart" in doc_xml
        assert "commentReference" in doc_xml


def test_word_export_resilient_to_toplinepunct_and_tabs_tags(tmp_path: Path):
    """回归：Word 段落含 <w:topLinePunct/> 与 <w:tabs> 时不破坏文本提取与批注定位。"""
    base = make_docx_bytes(["第一段", "观察组有效率30/100（40%）。"])
    patched = patch_docx_member(
        base,
        "word/document.xml",
        lambda b: b.replace(
            b"<w:p>",
            b'<w:p><w:pPr><w:topLinePunct/><w:tabs><w:tab w:val="left" w:pos="420"/></w:tabs></w:pPr>',
        ),
    )

    client = TestClient(create_app(db_path=tmp_path / "tp.sqlite3"))
    run = client.post(
        "/reviews?journal=core-math-only",
        files={"file": ("tp.docx", patched, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    f = next(x for x in run["findings"] if x["rule_id"] == "DATA-PCT-RECALC")
    assert f["anchor"]["quote"] == "30/100（40%）"

    client.post(
        f"/reviews/{run['run_id']}/decisions",
        json={
            "finding_id": f["id"],
            "action": "accept",
            "editor": "校对员",
            "reason": "确认批注挂载",
            "expected_revision": 0,
        },
    )
    out = client.post(
        f"/reviews/{run['run_id']}/export/docx",
        files={"file": ("tp.docx", patched, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert out.status_code == 200
    with zipfile.ZipFile(io.BytesIO(out.content), "r") as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
        assert root.find(f".//{{{W_NS}}}commentRangeStart") is not None


def test_cli_execution(tmp_path: Path):
    demo_path = build_demo_docx(tmp_path / "cli_demo.docx")
    out_md = tmp_path / "report.md"
    rc = cli_main([str(demo_path), "--format", "markdown", "--output", str(out_md)])
    assert rc == 0
    assert out_md.exists()
    assert "Router DAG" in out_md.read_text(encoding="utf-8")
