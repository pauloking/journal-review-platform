from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from review_platform.api import create_app
from tests.conftest import make_docx_bytes, patch_docx_member


def test_c1_m1_builtin_documents_lossless_roundtrip_and_restart_category(tmp_path: Path):
    """C1 & M1 回归：原样保存内置文档（数字/单位/参考文献）不丢失高级参数且重启保持 category。"""
    db_path = tmp_path / "c1_m1.sqlite3"
    client = TestClient(create_app(db_path=db_path))

    # 1. 保存《GB/T 15835 数字用法》仅修改摘要，不改动规则
    num_doc = client.get("/knowledge/documents/DOC-GBT15835-2011").json()
    res_num = client.put(
        "/knowledge/documents/DOC-GBT15835-2011",
        json={
            "title": num_doc["title"],
            "source": num_doc["source"],
            "edition": num_doc["edition"],
            "scope": num_doc["scope"],
            "summary": num_doc["summary"] + "（维护注释）",
            "body_markdown": num_doc["body_markdown"] + "\n\n维护注释。",
            "rules": num_doc["maintainable_rules"],
        },
    )
    assert res_num.status_code == 200

    # 年份 2020-2025 与 图1-2 豁免仍生效
    raw_ok = make_docx_bytes(["研究标题", "摘要：纳入2020-2025年患者，详见图1-2。"])
    rev_ok = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("ok.docx", raw_ok, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    assert not [f for f in rev_ok["findings"] if f["rule_id"] == "NUM-RANGE"]

    # 2. 保存《GB/T 7714 参考文献》不得触发 REF-TYPE 422（exclude_pattern 无损保留）
    ref_doc = client.get("/knowledge/documents/DOC-GBT7714-REFS").json()
    res_ref = client.put(
        "/knowledge/documents/DOC-GBT7714-REFS",
        json={
            "title": ref_doc["title"],
            "source": ref_doc["source"],
            "edition": ref_doc["edition"],
            "scope": ref_doc["scope"],
            "summary": ref_doc["summary"],
            "body_markdown": ref_doc["body_markdown"] + "\n\n维护注释。",
            "rules": ref_doc["maintainable_rules"],
        },
    )
    assert res_ref.status_code == 200

    # 3. 编辑保存《量和单位》后重启应用，UNIT-PPM 的 category 必须保持为 units（而非 terminology）
    unit_doc = client.get("/knowledge/documents/DOC-GBT3100-UNITS").json()
    res_unit = client.put(
        "/knowledge/documents/DOC-GBT3100-UNITS",
        json={
            "title": unit_doc["title"],
            "source": unit_doc["source"],
            "edition": unit_doc["edition"],
            "scope": unit_doc["scope"],
            "summary": unit_doc["summary"],
            "body_markdown": unit_doc["body_markdown"] + "\n\n维护注释。",
            "rules": unit_doc["maintainable_rules"],
        },
    )
    assert res_unit.status_code == 200

    client2 = TestClient(create_app(db_path=db_path))
    reg2 = client2.get("/registry").json()
    unit_ppm = next(r for r in reg2["rules"] if r["id"] == "UNIT-PPM")
    assert unit_ppm["category"] == "units"


def test_c2_no_silent_skip_on_normal_medical_phrases(tmp_path: Path):
    """C2 回归：真实方法段出现「检查项包括…」时，P=1.25 与 30/100（40%）必须被检出。"""
    client = TestClient(create_app(db_path=tmp_path / "c2.sqlite3"))
    raw = make_docx_bytes(
        [
            "临床观察研究",
            "摘要：本研究回顾性分析住院患者临床资料。",
            "1 资料与方法",
            "检查项包括血常规与生化，异常率30/100（40%），实测P=1.25。",
            "2 结果",
            "全部患者均完成随访观察。",
        ]
    )
    rev = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("c2.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    hit_ids = {f["rule_id"] for f in rev["findings"]}
    assert "STAT-PRANGE" in hit_ids
    assert "DATA-PCT-RECALC" in hit_ids
    # 正常可信结构稿件状态应为 completed（M6）
    assert rev["status"] == "completed"


def test_c3_redos_regex_rejected_at_registration(tmp_path: Path):
    """C3 回归：高危嵌套量词正则 (a+)+$ 必须在保存门禁时被 422 拒绝。"""
    client = TestClient(create_app(db_path=tmp_path / "c3.sqlite3"))
    res = client.post(
        "/knowledge/documents",
        json={
            "title": "《恶意正则测试》",
            "source": "测试来源",
            "edition": "2026",
            "scope": "journal",
            "summary": "ReDoS 拦截测试",
            "body_markdown": "# 恶意正则测试文档",
            "rules": [
                {
                    "rule_id": "CUST-REDOS-1",
                    "nonstandard": "(a+)+$",
                    "standard": "safe",
                    "severity": "warning",
                    "is_regex": True,
                    "example": "aaaaaaaa",
                    "counter_example": "bbbb",
                    "description": "ReDoS test",
                }
            ],
        },
    )
    assert res.status_code == 422
    assert "ReDoS" in res.json()["detail"]


def test_m2_rule_docx_zip_bomb_and_m3_json_exception_after_type(tmp_path: Path):
    """M2 & m3 回归：拦截高膨胀比规则 DOCX 与非法 JSON exception_after 类型。"""
    client = TestClient(create_app(db_path=tmp_path / "m2.sqlite3"))

    # 构造解压超过 20MiB 的 DOCX 压缩包（高重复空格高度压缩）
    base = make_docx_bytes(["规则标题"])
    big_xml = ("<w:p><w:r><w:t>" + ("A" * 500_000) + "</w:t></w:r></w:p>") * 45
    bomb = patch_docx_member(
        base,
        "word/document.xml",
        lambda b: b.replace(b"</w:body>", big_xml.encode("utf-8") + b"</w:body>"),
    )
    res_bomb = client.post(
        "/knowledge/documents/parse",
        files={"file": ("bomb.docx", bomb, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res_bomb.status_code == 422
    assert "20 MiB" in res_bomb.json()["detail"]

    # m3: exception_after 非 list[str] 应返回 422 而非 500 或拆成单字
    bad_json = b'{"title":"t","rules":[{"id":"R1","pattern":"x","suggestion":"y","exception_after":123}]}'
    res_json = client.post(
        "/knowledge/documents/parse",
        files={"file": ("bad.json", bad_json, "application/json")},
    )
    assert res_json.status_code == 422


def test_m3_bearer_rbac_and_editor_identity_binding(tmp_path: Path):
    """M3 回归：启用令牌后鉴权拦截、角色权限区分以及裁决署名强制绑定认证身份。"""
    tokens = {
        "tok-editor": ("王编辑", "editor"),
        "tok-admin": ("张规则管理员", "rule_admin"),
    }
    client = TestClient(create_app(db_path=tmp_path / "auth.sqlite3", api_tokens=tokens))

    assert client.get("/health").status_code == 200
    assert client.get("/registry").status_code == 401

    # editor 可提交审校但无权新建规则文档（403）
    raw = make_docx_bytes(["急性心肌梗塞临床研究", "摘要：观察组30/100（40%）。"])
    rev_res = client.post(
        "/reviews?journal=general-medical-cn",
        headers={"Authorization": "Bearer tok-editor"},
        files={"file": ("auth.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert rev_res.status_code == 200
    run_id = rev_res.json()["run_id"]
    f_id = rev_res.json()["findings"][0]["id"]

    # editor 伪造署名，服务端强制采用认证身份 "王编辑"
    dec_res = client.post(
        f"/reviews/{run_id}/decisions",
        headers={"Authorization": "Bearer tok-editor"},
        json={
            "finding_id": f_id,
            "action": "accept",
            "editor": "假冒管理员",
            "reason": "核实通过",
            "expected_revision": 0,
        },
    )
    assert dec_res.status_code == 200
    assert dec_res.json()["decision"]["editor"] == "王编辑"

    rule_try = client.post(
        "/knowledge/documents",
        headers={"Authorization": "Bearer tok-editor"},
        json={
            "title": "无权限测试",
            "source": "测试来源",
            "edition": "2026",
            "scope": "journal",
            "summary": "权限拦截测试",
            "body_markdown": "# 无权限测试文档",
            "rules": [],
        },
    )
    assert rule_try.status_code == 403

    rule_ok = client.post(
        "/knowledge/documents",
        headers={"Authorization": "Bearer tok-admin"},
        json={
            "title": "管理员权限测试",
            "source": "测试来源",
            "edition": "2026",
            "scope": "journal",
            "summary": "管理员权限测试",
            "body_markdown": "# 管理员权限测试文档",
            "rules": [],
        },
    )
    assert rule_ok.status_code == 200


def test_m5_multiple_accepted_comments_in_same_paragraph(tmp_path: Path):
    """M5 回归：同一段落内两条非重叠已采纳意见可同时成功写入原生 Word 批注。"""
    client = TestClient(create_app(db_path=tmp_path / "m5.sqlite3"))
    raw = make_docx_bytes(["急性心肌梗塞合并脑梗塞患者观察"])
    rev = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("multi.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()

    mi = next(f for f in rev["findings"] if f["rule_id"] == "TERM-MI")
    ci = next(f for f in rev["findings"] if f["rule_id"] == "TERM-CI")
    assert mi["anchor"]["block_id"] == ci["anchor"]["block_id"]

    for f in (mi, ci):
        res_d = client.post(
            f"/reviews/{rev['run_id']}/decisions",
            json={
                "finding_id": f["id"],
                "action": "accept",
                "editor": "王编辑",
                "reason": "术语规范",
                "expected_revision": 0,
            },
        )
        assert res_d.status_code == 200

    exp = client.post(
        f"/reviews/{rev['run_id']}/export/docx",
        files={"file": ("multi.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert exp.status_code == 200
    assert exp.headers.get("X-Exported-Comments") == "2"

    with zipfile.ZipFile(io.BytesIO(exp.content), "r") as zf:
        comments_xml = zf.read("word/comments.xml").decode("utf-8")
        assert "TERM-MI" in comments_xml
        assert "TERM-CI" in comments_xml


def test_n1_redos_brace_quantifier_long_example_and_runtime_timeout(tmp_path: Path, monkeypatch):
    """N1 回归：{n,} 嵌套量词拦截、超长 example 拦截、以及逃逸静态检查的回溯型正则超时熔断。"""
    import time
    from review_platform.rule_registry import registry as reg_mod

    client = TestClient(create_app(db_path=tmp_path / "n1.sqlite3"))

    # 1. {1,} 等价于 +，静态守卫应 422 拒绝
    res_brace = client.post(
        "/knowledge/documents",
        json={
            "title": "N1 花括号量词测试",
            "source": "安全测试",
            "edition": "2026",
            "scope": "journal",
            "summary": "拦截嵌套 {n,} 量词",
            "body_markdown": "# N1 测试",
            "rules": [
                {
                    "rule_id": "N1-BRACE-01",
                    "nonstandard": r"(a{1,}){1,}$",
                    "standard": "修正",
                    "severity": "warning",
                    "is_regex": True,
                    "example": "aaa",
                    "counter_example": "bbb",
                    "description": "花括号嵌套量词",
                }
            ],
        },
    )
    assert res_brace.status_code == 422
    assert "ReDoS" in res_brace.json()["detail"]

    # 2. 超长 example 超过 500 字上限应被拒绝
    res_long = client.post(
        "/knowledge/documents",
        json={
            "title": "N1 超长示例测试",
            "source": "安全测试",
            "edition": "2026",
            "scope": "journal",
            "summary": "拦截超长 example",
            "body_markdown": "# N1 超长示例",
            "rules": [
                {
                    "rule_id": "N1-LONG-01",
                    "nonstandard": "foo",
                    "standard": "bar",
                    "severity": "warning",
                    "is_regex": False,
                    "example": "a" * 501,
                    "counter_example": "bbb",
                    "description": "超长正例",
                }
            ],
        },
    )
    assert res_long.status_code == 422
    assert "长度" in res_long.json()["detail"]

    # 3. 即使绕过静态守卫，regex 引擎超时机制也能在 <1s 内中止灾难性回溯而不是卡死
    monkeypatch.setattr(reg_mod, "check_regex_safety", lambda pattern: None)
    evasion_doc = {
        "title": "N1 运行时超时保护",
        "source": "安全测试",
        "edition": "2026",
        "scope": "journal",
        "summary": "运行时超时测试",
        "body_markdown": "# N1 运行时超时",
        "rules": [
            {
                "rule_id": "N1-EVADE-01",
                "nonstandard": r"(a+a+)+$",
                "standard": "修改",
                "severity": "warning",
                "is_regex": True,
                "example": "aa",
                "counter_example": "b",
                "description": "隐式回溯正则",
            }
        ],
    }
    res_save = client.post("/knowledge/documents", json=evasion_doc)
    assert res_save.status_code == 200

    evil_raw = make_docx_bytes(["医学临床分析", "a" * 38 + "!"])
    t0 = time.monotonic()
    res_rev = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("evil.docx", evil_raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    elapsed = time.monotonic() - t0
    assert res_rev.status_code == 200
    assert elapsed < 3.0


def test_n5_frontend_auth_bar_and_health_flag(tmp_path: Path):
    """N5 回归：启用鉴权时 / 与 /health 可访问、auth_enabled=True，且前端包含令牌输入栏。"""
    client = TestClient(
        create_app(
            db_path=tmp_path / "n5.sqlite3",
            api_tokens={"tok-edit": ("李编辑", "editor")},
        )
    )
    h = client.get("/health")
    assert h.status_code == 200
    assert h.json()["auth_enabled"] is True

    home = client.get("/")
    assert home.status_code == 200
    assert 'id="auth-bar"' in home.text
    app_js = client.get("/static/app.js")
    assert app_js.status_code == 200
    assert "sessionStorage" in app_js.text
    assert "localStorage" not in app_js.text


def test_n6_advanced_params_type_and_regex_validation(tmp_path: Path):
    """N6 回归：advanced_params 非法类型或语法错误正则应返回 422，而非 500。"""
    client = TestClient(create_app(db_path=tmp_path / "n6.sqlite3"))

    bad_regex_res = client.post(
        "/knowledge/documents",
        json={
            "title": "N6 非法正则参数",
            "source": "安全测试",
            "edition": "2026",
            "scope": "journal",
            "summary": "N6 测试",
            "body_markdown": "# N6 测试",
            "rules": [
                {
                    "rule_id": "N6-REGEX-01",
                    "nonstandard": "abc",
                    "standard": "def",
                    "severity": "warning",
                    "is_regex": False,
                    "example": "abc",
                    "counter_example": "xyz",
                    "description": "非法 exclude_pattern",
                    "advanced_params": {"exclude_pattern": "("},
                }
            ],
        },
    )
    assert bad_regex_res.status_code == 422

    bad_type_res = client.post(
        "/knowledge/documents",
        json={
            "title": "N6 非法参数类型",
            "source": "安全测试",
            "edition": "2026",
            "scope": "journal",
            "summary": "N6 类型测试",
            "body_markdown": "# N6 类型测试",
            "rules": [
                {
                    "rule_id": "N6-TYPE-01",
                    "nonstandard": "abc",
                    "standard": "def",
                    "severity": "warning",
                    "is_regex": False,
                    "example": "abc",
                    "counter_example": "xyz",
                    "description": "非法 skip_context",
                    "advanced_params": {"skip_context": "not-a-list"},
                }
            ],
        },
    )
    assert bad_type_res.status_code == 422


def test_n10_shared_reference_entry_helper_used():
    """N10 回归：结构适配器不再保留遮蔽共享工具的重复实现。"""
    from review_platform.adapters import legacy_structure
    from review_platform.document_engine import text_utils

    assert legacy_structure._is_reference_entry is text_utils.is_reference_entry


def test_r03_knowledge_pack_markdown_complete_and_missing_raises(tmp_path: Path):
    """R03 回归：全部内置规范文档正文非空，导出包含正文；缺失知识包文档文件显式报错。"""
    import pytest
    import shutil
    from review_platform.knowledge_engine.store import DEFAULT_PACK_DIR, KnowledgeEngine

    client = TestClient(create_app(db_path=tmp_path / "r03.sqlite3"))
    docs = client.get("/registry").json()["documents"]
    assert len(docs) == 10
    for d in docs:
        assert d["body_markdown"].strip(), f"{d['doc_id']} 正文为空"
        exp = client.get(f"/knowledge/documents/{d['doc_id']}/export")
        assert exp.status_code == 200
        assert d["body_markdown"].strip().splitlines()[0] in exp.text

    broken_pack = tmp_path / "broken_pack"
    shutil.copytree(DEFAULT_PACK_DIR, broken_pack)
    next((broken_pack / "documents").glob("*.md")).unlink()
    with pytest.raises(RuntimeError, match="内置规范文档资源缺失"):
        KnowledgeEngine(pack_dir=broken_pack)


def test_r04_basic_rules_fullwidth_strict_ineq_pct_unit_neg_and_prose_caption(tmp_path: Path):
    """R04 回归：全角 P 值、严格不等式 P<0、P=.03、带量词百分比、负分子与无样式正文图引用识别与排除原因。"""
    from docx import Document

    buf = io.BytesIO()
    doc = Document()
    doc.add_paragraph("R04 基础规则回归稿件")
    doc.add_paragraph(
        "摘要：全角异常 Ｐ值＝１．２，无解不等式 P<0 与 P>1，合法边界 P=.03、P=0、P=1。"
        "百分比异常 30/100 例（40%），负分子 −1/3(33%)。"
    )
    doc.add_paragraph("1 结果")
    p_cap1 = doc.add_paragraph("图1 基线特征图")
    p_cap1.style = "Caption"
    doc.add_paragraph("图2 显示组间变化。")
    doc.add_paragraph("图2 后续完整随访曲线")
    doc.save(buf)

    client = TestClient(create_app(db_path=tmp_path / "r04.sqlite3"))
    run = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("r04.docx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()

    p_facts = [f for f in run["facts"] if f["kind"] == "p_value"]
    p_fact_quotes = {f["anchor"]["quote"] for f in p_facts}
    assert "Ｐ值＝１．２" in p_fact_quotes
    assert "P=.03" in p_fact_quotes

    p_findings = [f for f in run["findings"] if f["rule_id"] == "STAT-PRANGE"]
    p_hit_quotes = {f["anchor"]["quote"] for f in p_findings}
    assert {"Ｐ值＝１．２", "P<0", "P>1"}.issubset(p_hit_quotes)
    assert "P=.03" not in p_hit_quotes
    assert "P=0" not in p_hit_quotes
    assert "P=1" not in p_hit_quotes

    # 原文切片锚点保留全角原文
    fw = next(f for f in p_findings if f["anchor"]["quote"] == "Ｐ值＝１．２")
    blk_text = next(b["text"] for b in run["document"]["blocks"] if b["id"] == fw["anchor"]["block_id"])
    assert blk_text[fw["anchor"]["start"] : fw["anchor"]["end"]] == "Ｐ值＝１．２"

    pct_findings = [f for f in run["findings"] if f["rule_id"] == "DATA-PCT-RECALC"]
    pct_quotes = {f["anchor"]["quote"] for f in pct_findings}
    assert "30/100 例（40%）" in pct_quotes
    assert "−1/3(33%)" in pct_quotes

    # 「图2 显示组间变化。」被排除并保留排除原因，因此真题注「图2 后续完整随访曲线」不会误报跳号/重复
    cap_facts = [f for f in run["facts"] if f["kind"] == "caption"]
    excluded_prose = [f for f in cap_facts if f["exclusion_reason"] == "body_prose_caption_reference"]
    assert len(excluded_prose) == 1
    assert [f for f in run["findings"] if f["rule_id"] == "STRUCT-CAPTION-SEQ"] == []


def test_r05_whole_line_rule_respects_pattern_and_exemptions(tmp_path: Path):
    """R05 回归：整段模式先校验匹配条件与例外再以整段输出引文，注册与运行时行为一致。"""
    client = TestClient(create_app(db_path=tmp_path / "r05.sqlite3"))

    save_res = client.post(
        "/knowledge/documents",
        json={
            "title": "R05 整段模式测试规范",
            "source": "本刊体例",
            "edition": "2026",
            "scope": "journal",
            "summary": "测试 whole_line 模式确实校验 pattern",
            "body_markdown": "# R05 整段模式规范",
            "rules": [
                {
                    "rule_id": "R05-WHOLE-01",
                    "nonstandard": "绝对有效",
                    "standard": "改为客观统计学描述",
                    "severity": "warning",
                    "is_regex": False,
                    "example": "本方案宣称绝对有效。",
                    "counter_example": "本方案观察组缓解率高于对照组。",
                    "description": "禁用绝对化表述（整段标记）",
                    "advanced_params": {"whole_line": True},
                }
            ],
        },
    )
    assert save_res.status_code == 200

    paras = [f"正常医学段落第{i}段。" for i in range(1, 8)] + ["结论：该疗法绝对有效。"]
    raw = make_docx_bytes(paras)
    run = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("r05.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()

    hits = [f for f in run["findings"] if f["rule_id"] == "R05-WHOLE-01"]
    assert len(hits) == 1
    assert hits[0]["anchor"]["quote"] == "结论：该疗法绝对有效。"


def test_r06_codepoint_anchor_and_frontend_utf16_slicing(tmp_path: Path):
    """R06 回归：前置 emoji/扩展汉字场景下前后端 Unicode 码点坐标一致。"""
    client = TestClient(create_app(db_path=tmp_path / "r06.sqlite3"))
    raw = make_docx_bytes(["🙂P=1.2。", "𠮷P=1.5。"])
    run = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("r06.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()

    p_hits = [f for f in run["findings"] if f["rule_id"] == "STAT-PRANGE"]
    assert len(p_hits) == 2
    assert p_hits[0]["anchor"]["start"] == 1
    assert p_hits[0]["anchor"]["end"] == 6
    assert p_hits[0]["anchor"]["quote"] == "P=1.2"
    assert p_hits[1]["anchor"]["start"] == 1
    assert p_hits[1]["anchor"]["end"] == 6
    assert p_hits[1]["anchor"]["quote"] == "P=1.5"

    app_js = client.get("/static/app.js").text
    assert "sliceByCodePoints" in app_js
    assert "Array.from(String(text" in app_js
