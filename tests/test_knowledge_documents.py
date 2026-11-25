from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from review_platform.api import create_app
from tests.conftest import make_docx_bytes


def test_builtin_knowledge_documents_and_markdown_export(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "docs.sqlite3"))
    res = client.get("/knowledge/documents")
    assert res.status_code == 200
    docs = res.json()["documents"]
    assert len(docs) == 10

    ids = {d["doc_id"] for d in docs}
    assert "DOC-GBT15835-2011" in ids
    assert "DOC-CNTERM-MEDICAL" in ids
    assert "DOC-MATH-STRUCTURE" in ids
    assert "DOC-AI-SEMANTIC" in ids

    num_doc = client.get("/knowledge/documents/DOC-GBT15835-2011").json()
    assert "NUM-RANGE" in num_doc["rule_ids"]
    assert len(num_doc["maintainable_rules"]) >= 7

    exp = client.get("/knowledge/documents/DOC-GBT15835-2011/export")
    assert exp.status_code == 200
    assert "GB/T 15835-2011" in exp.text


def test_document_upsert_validation_gate_and_live_review(tmp_path: Path):
    db_path = tmp_path / "custom_docs.sqlite3"
    client = TestClient(create_app(db_path=db_path))
    old_hash = client.get("/health").json()["rule_snapshot_hash"]

    # 1. 反例误报必须被 422 拒绝，不能直接进入正式规则
    bad_payload = {
        "title": "《测试反例拦截文档》",
        "source": "本刊规范",
        "edition": "2026",
        "scope": "journal",
        "summary": "验证反例门禁",
        "body_markdown": "# 测试文档\n\n正反例校验测试。",
        "rules": [
            {
                "rule_id": "CUST-BAD-01",
                "nonstandard": "心梗",
                "standard": "心肌梗死",
                "severity": "warning",
                "is_regex": False,
                "exception_after": [],
                "example": "患者发生急性心梗",
                "counter_example": "患者发生急性心梗后就诊",
                "description": "反例仍命中，应被拒绝",
            }
        ],
    }
    bad_res = client.post("/knowledge/documents", json=bad_payload)
    assert bad_res.status_code == 422
    assert "误报" in bad_res.json()["detail"]

    # 2. 正反例均通过的规范文档成功创建、刷新快照并持久化
    good_payload = {
        "title": "《本刊专科缩写规范补充》",
        "source": "本刊编辑部规范 v2026",
        "edition": "2026",
        "scope": "journal",
        "summary": "统一本刊特定专科缩略语写法",
        "body_markdown": "# 本刊专科缩写规范补充\n\n统一使用「冠状动脉旁路移植术」。",
        "rules": [
            {
                "rule_id": "CUST-CABG-01",
                "nonstandard": "心脏搭桥术",
                "standard": "冠状动脉旁路移植术",
                "severity": "warning",
                "is_regex": False,
                "exception_after": [],
                "example": "行心脏搭桥术治疗",
                "counter_example": "行冠状动脉旁路移植术治疗",
                "description": "「心脏搭桥术」建议规范为「冠状动脉旁路移植术」",
            }
        ],
    }
    ok_res = client.post("/knowledge/documents", json=good_payload)
    assert ok_res.status_code == 200
    created_doc = ok_res.json()
    assert created_doc["is_builtin"] is False
    assert "CUST-CABG-01" in created_doc["rule_ids"]
    assert client.get("/health").json()["rule_snapshot_hash"] != old_hash

    # 3. 重启新实例后文档与规则持久存在，且新上传稿件立刻生效
    client2 = TestClient(create_app(db_path=db_path))
    reloaded = client2.get(f"/knowledge/documents/{created_doc['doc_id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["title"] == "《本刊专科缩写规范补充》"

    raw = make_docx_bytes(["冠心病研究", "摘要：本研究患者行心脏搭桥术治疗。"])
    rev = client2.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("cabg.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    hit_ids = {f["rule_id"] for f in rev["findings"]}
    assert "CUST-CABG-01" in hit_ids

    # 4. 验证运行时完整可视化中间过程 trace 含新文档的执行记录
    stages = [s["stage"] for s in rev["trace"]]
    for expected_stage in ("document_parse", "knowledge_bind", "router_plan", "fact_extract", "rule_execute", "finding_gate"):
        assert expected_stage in stages
    custom_trace = next(s for s in rev["trace"] if s.get("doc_id") == created_doc["doc_id"])
    assert "命中 1 条" in custom_trace["summary"]


def test_upload_and_parse_markdown_and_docx_rule_documents(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "parse_docs.sqlite3"))

    # 1. 上传 .md 规则文档自动解析箭头与表格规则
    md_bytes = (
        "# 本刊心血管术语补充规范 2026\n"
        "来源：心血管编辑部体例\n\n"
        "- 心脏搭桥 -> 冠状动脉旁路移植术\n\n"
        "| 非规范 | 规范写法 | 说明 |\n"
        "| --- | --- | --- |\n"
        "| 支架内再狭窄率高 | 支架内再狭窄发生率升高 | 避免口语化 |\n"
    ).encode("utf-8")
    res_md = client.post(
        "/knowledge/documents/parse",
        files={"file": ("cardio-rules.md", md_bytes, "text/markdown")},
    )
    assert res_md.status_code == 200
    parsed_md = res_md.json()
    assert parsed_md["title"] == "本刊心血管术语补充规范 2026"
    assert len(parsed_md["rules"]) == 2
    assert parsed_md["rules"][0]["nonstandard"] == "心脏搭桥"
    assert parsed_md["rules"][0]["standard"] == "冠状动脉旁路移植术"

    # 2. 上传 .docx 规则文档（含表格）自动解析并保存激活后立即在审校中命中
    docx_bytes = make_docx_bytes(
        paragraphs=["《检验医学符号补充规范 2026》", "来源：检验编辑部"],
        tables=[[["非规范写法", "规范写法", "说明"], ["血沉", "红细胞沉降率", "全文统一使用规范中文全称"]]],
    )
    res_docx = client.post(
        "/knowledge/documents/parse",
        files={"file": ("lab-rules.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res_docx.status_code == 200
    parsed_docx = res_docx.json()
    assert len(parsed_docx["rules"]) == 1
    assert parsed_docx["rules"][0]["nonstandard"] == "血沉"

    save_res = client.post(
        "/knowledge/documents",
        json={
            "title": parsed_docx["title"],
            "source": parsed_docx["source"],
            "edition": parsed_docx["edition"],
            "scope": parsed_docx["scope"],
            "summary": parsed_docx["summary"],
            "body_markdown": parsed_docx["body_markdown"],
            "rules": parsed_docx["rules"],
        },
    )
    assert save_res.status_code == 200
    saved_doc = save_res.json()

    # 用带「血沉」的稿件跑一次审校，确认按解析后的新文档规则实际执行
    ms_bytes = make_docx_bytes(["检验研究", "摘要：检测患者血沉指标变化。"])
    rev = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("ms.docx", ms_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    assert any(f["basis_id"] == saved_doc["basis_id"] for f in rev["findings"])

