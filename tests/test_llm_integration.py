from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from review_platform.api import create_app
from tests.conftest import make_docx_bytes


def _stub_public_dns(monkeypatch, public_ip: str = "93.184.216.34") -> None:
    """R07 修复：在注入 MockTransport 的传输层测试中固定公网解析结果，避免依赖本机/公网 DNS。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (public_ip, int(port or 443)))
        ],
    )


def _mock_client_factory(captured_requests: list[dict], response_builder):
    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append(
            {
                "url": str(request.url),
                "headers": dict(request.headers),
                "body": body,
            }
        )
        status, payload = response_builder(request, body)
        return httpx.Response(status, json=payload)

    transport = httpx.MockTransport(_handler)
    return lambda timeout: httpx.Client(transport=transport, timeout=timeout)


def test_llm_defaults_and_disabled_skip_trace(tmp_path: Path):
    client = TestClient(create_app(db_path=tmp_path / "llm_off.sqlite3"))
    defs = client.get("/llm/defaults").json()
    assert defs["has_env_default"] is False
    assert len(defs["ai_rules"]) == 4

    raw = make_docx_bytes(["医学临床研究", "摘要：观察组与对照组对比分析。", "1 方法", "采用随机对照研究。", "2 结果", "随访结束。"])
    rev = client.post(
        "/reviews?journal=general-medical-cn",
        files={"file": ("off.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    ai_tasks = [t for t in rev["plan"]["tasks"] if t["node_id"].startswith("AI-")]
    assert len(ai_tasks) == 4
    assert all(t["status"] == "skipped" and t["reason"] == "llm_not_configured" for t in ai_tasks)


def test_llm_test_connection_and_custom_endpoint_normalization(tmp_path: Path, monkeypatch):
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []

    def _resp(req: httpx.Request, body: dict):
        if "anthropic" in str(req.url):
            return 200, {"content": [{"type": "text", "text": "连接成功"}]}
        return 200, {"choices": [{"message": {"content": "连接成功"}}]}

    factory = _mock_client_factory(captured, _resp)
    client = TestClient(create_app(db_path=tmp_path / "llm_test.sqlite3", llm_client_factory=factory))

    # 1. Custom endpoint 自动补全 /v1/chat/completions 且注入 enable_thinking=False
    r1 = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "https://api.deepseek.com",
            "api_key": "sk-secret-test-key",
            "model": "deepseek-chat",
            "disable_thinking": True,
        },
    )
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True
    assert captured[0]["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "sk-secret-test-key" not in json.dumps(d1)

    # 2. Claude 接口走 x-api-key + /v1/messages
    r2 = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "claude",
            "api_key": "sk-ant-test",
            "model": "claude-3-5-sonnet-20241022",
        },
    )
    assert r2.status_code == 200
    assert r2.json()["ok"] is True
    assert captured[1]["headers"].get("x-api-key") == "sk-ant-test"


def test_llm_semantic_review_with_anchor_gate_and_key_isolation(tmp_path: Path, monkeypatch):
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []

    def _resp(req: httpx.Request, body: dict):
        # 构造混合输出：
        # 1) 真实可逐字定位的问题（应通过门禁成为 ai_candidate Finding）
        # 2) 模型幻觉捏造的原文（应被 FindingEngine 拦截进入 rejected_candidates）
        # 3) 当期年份误判噪声（应被 is_future_date_misjudgment 过滤）
        model_json = {
            "findings": [
                {
                    "blockId": "P0002",
                    "originalText": "有效率达80%",
                    "description": "摘要报告有效率80%，但结果段报告有效率为65%，前后数据不一致",
                    "suggestion": "请核对摘要与正文结果段的原始有效率计算结果",
                    "reason": "摘要与正文核心结局指标必须一致",
                    "severity": "error",
                },
                {
                    "blockId": "P0003",
                    "originalText": "这段文字在原稿里根本不存在",
                    "description": "幻觉引文测试",
                    "suggestion": "修改",
                    "reason": "幻觉测试",
                    "severity": "warning",
                },
                {
                    "blockId": "P0002",
                    "originalText": "2026年收治患者",
                    "description": "2026年尚未到来，日期在未来",
                    "suggestion": "修改年份",
                    "reason": "时间点尚未到达",
                    "severity": "warning",
                },
            ]
        }
        return 200, {"choices": [{"message": {"content": f"```json\n{json.dumps(model_json, ensure_ascii=False)}\n```"}}]}

    factory = _mock_client_factory(captured, _resp)
    db_path = tmp_path / "llm_e2e.sqlite3"
    client = TestClient(create_app(db_path=db_path, llm_client_factory=factory))

    raw = make_docx_bytes(
        [
            "冠心病介入治疗临床分析",
            "摘要：2026年收治患者100例，有效率达80%。",
            "1 资料与方法",
            "纳入患者100例，行标准介入治疗。",
            "2 结果",
            "最终观察组有效率为65%（65/100）。",
        ]
    )
    llm_cfg = {
        "enabled": True,
        "provider": "openai",
        "api_key": "sk-never-persist-this-secret",
        "model": "gpt-4o-mini",
        "enabled_rules": ["AI-ABSTRACT-CONSISTENCY"],
    }
    res = client.post(
        "/reviews?journal=general-medical-cn",
        data={"llm_config": json.dumps(llm_cfg)},
        files={"file": ("llm_ms.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    run = res.json()

    # 1. 验证真实可定位的 AI 候选通过门禁
    ai_findings = [f for f in run["findings"] if f["source_type"] == "ai_candidate"]
    assert len(ai_findings) == 1
    assert ai_findings[0]["rule_id"] == "AI-ABSTRACT-CONSISTENCY"
    assert ai_findings[0]["anchor"]["quote"] == "有效率达80%"
    assert ai_findings[0]["verification"]["anchor_valid"] is True

    # 2. 验证幻觉原文被拦截入 rejected_candidates，且状态反映局限
    assert len(run["rejected_candidates"]) == 1
    assert run["status"] == "complete_with_limitations"

    # 3. 验证 API Key 绝不落入响应或 SQLite
    assert "sk-never-persist-this-secret" not in json.dumps(run)
    stored_raw = client.get(f"/reviews/{run['run_id']}").text
    assert "sk-never-persist-this-secret" not in stored_raw


def test_n2_server_api_key_isolated_from_custom_endpoint(tmp_path: Path, monkeypatch):
    """N2 回归：服务端 REVIEW_LLM_API_KEY 严禁发送至用户自定义的非同源 endpoint。"""
    monkeypatch.setenv("REVIEW_LLM_API_KEY", "sk-SERVER-SECRET-NEVER-LEAK")
    monkeypatch.setenv("REVIEW_LLM_ENDPOINT", "https://api.openai.com/v1")

    captured: list[dict] = []
    factory = _mock_client_factory(captured, lambda r, b: (200, {"choices": [{"message": {"content": "ok"}}]}))
    client = TestClient(create_app(db_path=tmp_path / "n2.sqlite3", llm_client_factory=factory))

    # 用户指定第三方自定义 endpoint 但未传 api_key：不得回落使用服务端 key
    res = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "https://attacker.example.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert captured == []
    assert "sk-SERVER-SECRET-NEVER-LEAK" not in json.dumps(data)


def test_n3_ssrf_targets_blocked_and_upstream_body_sanitized(tmp_path: Path, monkeypatch):
    """N3 回归：阻断元数据/回环/带查询参数的 SSRF 地址，且不回显上游原始错误体。"""
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []

    def _err_resp(req: httpx.Request, body: dict):
        return 500, {"error": {"message": "SECRET_INTERNAL_STACK_TRACE_LEAK", "code": "upstream_err"}}

    factory = _mock_client_factory(captured, _err_resp)
    client = TestClient(create_app(db_path=tmp_path / "n3.sqlite3", llm_client_factory=factory))

    for bad_ep in [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:8080/v1",
        "https://api.deepseek.com/v1?leak=1",
        "https://api.deepseek.com/v1#frag",
    ]:
        r = client.post(
            "/llm/test",
            json={
                "enabled": True,
                "provider": "custom",
                "endpoint": bad_ep,
                "api_key": "sk-user",
                "model": "deepseek-chat",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False
    assert captured == []

    # 合法公网地址返回上游 500 时，仅摘要状态码与错误码，不泄露内部堆栈文本
    r_err = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "https://api.deepseek.com",
            "api_key": "sk-user",
            "model": "deepseek-chat",
        },
    )
    assert r_err.status_code == 200
    err_data = r_err.json()
    assert err_data["ok"] is False
    assert "500" in err_data["message"]
    assert "SECRET_INTERNAL_STACK_TRACE_LEAK" not in err_data["message"]


def test_n4_and_i3_run_seed_includes_llm_dims_and_truncation_disclosure(tmp_path: Path, monkeypatch):
    """N4 & i3 回归：不同 enabled_rules/temperature 生成不同 run_id；超过 24,000 字时显式披露截断并降级状态。"""
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []

    def _resp(req: httpx.Request, body: dict):
        return 200, {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "findings": [
                                    {
                                        "blockId": "P0002",
                                        "originalText": "观察组纳入50例",
                                        "description": "语境示例",
                                        "suggestion": "保持一致",
                                        "reason": "语义检查",
                                        "severity": "info",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    factory = _mock_client_factory(captured, _resp)
    client = TestClient(create_app(db_path=tmp_path / "n4_i3.sqlite3", llm_client_factory=factory))

    long_para = "观察组纳入50例患者并进行常规治疗与统计学评估。" * 700  # > 17,000 chars
    raw = make_docx_bytes(["长稿截断与幂等键测试", "摘要：观察组纳入50例。", "1 方法", long_para, long_para])
    files = {"file": ("long.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}

    r1 = client.post(
        "/reviews?journal=general-medical-cn",
        data={
            "llm_config": json.dumps(
                {
                    "enabled": True,
                    "provider": "openai",
                    "api_key": "sk-k1",
                    "model": "gpt-4o-mini",
                    "enabled_rules": ["AI-LANGUAGE-STYLE"],
                    "temperature": 0.1,
                }
            )
        },
        files=files,
    ).json()

    r2 = client.post(
        "/reviews?journal=general-medical-cn",
        data={
            "llm_config": json.dumps(
                {
                    "enabled": True,
                    "provider": "openai",
                    "api_key": "sk-k1",
                    "model": "gpt-4o-mini",
                    "enabled_rules": ["AI-METHOD-DESIGN"],
                    "temperature": 0.5,
                }
            )
        },
        files=files,
    ).json()

    # N4: 不同 AI 维度或采样参数必须产出不同 run_id
    assert r1["run_id"] != r2["run_id"]

    # i3: 超 24k 字稿件须在 limitations 披露并标记 complete_with_limitations
    assert r1["status"] == "complete_with_limitations"
    assert any("24,000" in msg or "截断" in msg for msg in r1["document"]["limitations"])
    ai_done = [t for t in r1["plan"]["tasks"] if t["node_id"] == "AI-LANGUAGE-STYLE"][0]
    assert "context_truncated_24k" in ai_done["reason"]


def test_n7_env_key_strictly_bound_to_server_endpoint_not_official_default(tmp_path: Path, monkeypatch):
    """N7 回归：服务端配置自定义厂商时，用户切到 OpenAI 官方默认地址严禁注入自定义厂商环境变量密钥。"""
    monkeypatch.setenv("REVIEW_LLM_PROVIDER", "custom")
    monkeypatch.setenv("REVIEW_LLM_ENDPOINT", "https://custom-vendor.example.com/v1")
    monkeypatch.setenv("REVIEW_LLM_API_KEY", "sk-CUSTOM-VENDOR-SECRET")

    captured: list[dict] = []
    factory = _mock_client_factory(captured, lambda r, b: (200, {"choices": [{"message": {"content": "ok"}}]}))
    client = TestClient(create_app(db_path=tmp_path / "n7.sqlite3", llm_client_factory=factory))

    res = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "openai",
            "endpoint": "https://api.openai.com/v1/chat/completions",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert captured == []
    assert "sk-CUSTOM-VENDOR-SECRET" not in json.dumps(data)


def test_n8_ssrf_cgnat_allowlist_and_dns_rebinding_pinning(tmp_path: Path, monkeypatch):
    """N8 回归：拦截 CGNAT、失效 REVIEW_LLM_ALLOW_LOCAL、支持精确白名单并固定 DNS 解析防重绑定。"""
    import socket
    import httpcore
    from review_platform.models import LLMConfig
    from review_platform.review_engine import llm_adapter

    captured: list[dict] = []
    factory = _mock_client_factory(captured, lambda r, b: (200, {"choices": [{"message": {"content": "ok"}}]}))
    client = TestClient(create_app(db_path=tmp_path / "n8.sqlite3", llm_client_factory=factory))

    # 1. CGNAT 与 IPv4-mapped IPv6 环回地址必须被拦截
    for bad_ep in ["http://100.64.0.1/v1", "http://[::ffff:127.0.0.1]:8080/v1"]:
        r = client.post(
            "/llm/test",
            json={
                "enabled": True,
                "provider": "custom",
                "endpoint": bad_ep,
                "api_key": "sk-user",
                "model": "test",
            },
        )
        assert r.status_code == 200
        assert r.json()["ok"] is False

    # 2. 旧的 REVIEW_LLM_ALLOW_LOCAL=1 布尔开关不再放行 169.254.169.254 或未登记私网
    monkeypatch.setenv("REVIEW_LLM_ALLOW_LOCAL", "1")
    r_meta = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "http://169.254.169.254/latest/meta-data",
            "api_key": "sk-user",
            "model": "test",
        },
    )
    assert r_meta.status_code == 200
    assert r_meta.json()["ok"] is False
    assert captured == []

    # 3. 精确白名单 REVIEW_LLM_LOCAL_ALLOWLIST 仅放行已登记的 host+port+path（本地可免 key）
    monkeypatch.delenv("REVIEW_LLM_ALLOW_LOCAL", raising=False)
    monkeypatch.setenv("REVIEW_LLM_LOCAL_ALLOWLIST", "http://127.0.0.1:11434/v1")
    r_ollama = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "http://127.0.0.1:11434/v1",
            "api_key": "",
            "model": "qwen2.5",
        },
    )
    assert r_ollama.status_code == 200
    assert r_ollama.json()["ok"] is True
    assert len(captured) == 1

    # 未在白名单的 127.0.0.1:8080 仍拦截
    r_other_local = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "custom",
            "endpoint": "http://127.0.0.1:8080/v1",
            "api_key": "sk-user",
            "model": "test",
        },
    )
    assert r_other_local.status_code == 200
    assert r_other_local.json()["ok"] is False

    # 4. DNS 重绑定：首次 getaddrinfo 返回公网 IP，后续再调用会返回 127.0.0.1；
    #    验证默认客户端使用固定 IP 直连且绝不触发二次解析到 127.0.0.1
    monkeypatch.delenv("REVIEW_LLM_LOCAL_ALLOWLIST", raising=False)
    calls = {"n": 0}

    def _rebind_getaddrinfo(host, port, *args, **kwargs):
        calls["n"] += 1
        ip = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, int(port or 80)))]

    monkeypatch.setattr(socket, "getaddrinfo", _rebind_getaddrinfo)

    dialed_hosts: list[str] = []

    class _FakeStream(httpcore.NetworkStream):
        def __init__(self) -> None:
            body = b'{"choices":[{"message":{"content":"ok"}}]}'
            self._buf = (
                f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
                f"Content-Type: application/json\r\n\r\n"
            ).encode("ascii") + body
            self._pos = 0

        def read(self, max_bytes, timeout=None):
            chunk = self._buf[self._pos : self._pos + max_bytes]
            self._pos += len(chunk)
            return chunk

        def write(self, buffer, timeout=None):
            pass

        def close(self):
            pass

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return self

        def get_extra_info(self, info):
            return None

    def _fake_super_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        dialed_hosts.append(host)
        return _FakeStream()

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", _fake_super_connect)
    reply = llm_adapter.call_llm_completion(
        LLMConfig(
            enabled=True,
            provider="custom",
            endpoint="http://rebind.example.com/v1",
            api_key="sk-test",
            model="test",
        ),
        "sys",
        "usr",
    )
    assert reply == "ok"
    assert dialed_hosts == ["93.184.216.34"]
    assert calls["n"] == 1


def test_n9_short_paragraphs_truncation_disclosed_even_with_zero_findings(tmp_path: Path, monkeypatch):
    """N9 回归：大量短段落致带前缀提示词超 24k 但原文不足 24k 且 AI 返回零意见时，仍须披露截断。"""
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []
    factory = _mock_client_factory(
        captured,
        lambda r, b: (200, {"choices": [{"message": {"content": json.dumps({"findings": []})}}]}),
    )
    client = TestClient(create_app(db_path=tmp_path / "n9.sqlite3", llm_client_factory=factory))

    # 1,500 个 3 字短段：原文仅 ~4,500 字（<24,000），但带 "[Pxxxx | 方法] " 前缀超过 24,000 字
    paras = ["短句。", "摘要：短句测试。", "1 方法"] + ["短样本" for _ in range(1500)]
    raw = make_docx_bytes(paras)
    res = client.post(
        "/reviews?journal=general-medical-cn",
        data={
            "llm_config": json.dumps(
                {
                    "enabled": True,
                    "provider": "openai",
                    "api_key": "sk-k1",
                    "model": "gpt-4o-mini",
                    "enabled_rules": ["AI-LANGUAGE-STYLE"],
                }
            )
        },
        files={"file": ("short_many.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    run = res.json()
    assert run["document"]["profile"]["char_count"] < 24000
    ai_task = [t for t in run["plan"]["tasks"] if t["node_id"] == "AI-LANGUAGE-STYLE"][0]
    assert ai_task["finding_count"] == 0
    assert ai_task["metadata"]["context_truncated"] is True
    assert ai_task["metadata"]["input_chars"] > 24000
    assert "context_truncated_24k" in ai_task["reason"]
    assert run["status"] == "complete_with_limitations"
    assert any("24,000" in msg or "截断" in msg for msg in run["document"]["limitations"])


def test_r01_retry_after_failure_returns_new_run_preserves_failure_and_decisions(tmp_path: Path, monkeypatch):
    """R01 回归：首次 AI 失败后按同配置重试返回最新成功结果，保留首轮失败记录与既有裁决，健康后再次提交幂等复用。"""
    _stub_public_dns(monkeypatch)
    state = {"call": 0}
    captured: list[dict] = []

    def _resp(req: httpx.Request, body: dict):
        state["call"] += 1
        if state["call"] == 1:
            return 503, {"error": {"message": "upstream temporary unavailable", "code": "temp_503"}}
        return 200, {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "findings": [
                                    {
                                        "blockId": "P0002",
                                        "originalText": "有效率达80%",
                                        "description": "摘要与结果数值核对提示",
                                        "suggestion": "核对有效率百分比",
                                        "reason": "摘要核验",
                                        "severity": "warning",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }

    factory = _mock_client_factory(captured, _resp)
    client = TestClient(create_app(db_path=tmp_path / "r01.sqlite3", llm_client_factory=factory))
    raw = make_docx_bytes(["冠心病研究", "摘要：有效率达80%，同时实测P=1.25。", "1 结果", "有效率为65%。"])
    llm_cfg = json.dumps(
        {
            "enabled": True,
            "provider": "openai",
            "api_key": "sk-k1",
            "model": "gpt-4o-mini",
            "enabled_rules": ["AI-ABSTRACT-CONSISTENCY"],
        }
    )
    files = {"file": ("retry.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}

    # 1. 首次运行：503 导致 AI 任务 failed
    r1 = client.post("/reviews?journal=general-medical-cn", data={"llm_config": llm_cfg}, files=files).json()
    ai_t1 = [t for t in r1["plan"]["tasks"] if t["node_id"] == "AI-ABSTRACT-CONSISTENCY"][0]
    assert ai_t1["status"] == "failed"

    # 对首轮确定性意见做一条裁决，确认不会被后续重试冲掉
    det_f = next(f for f in r1["findings"] if f["source_type"] == "deterministic")
    d_res = client.post(
        f"/reviews/{r1['run_id']}/decisions",
        json={
            "finding_id": det_f["id"],
            "action": "accept",
            "editor": "张编辑",
            "reason": "保留首轮裁决记录",
            "expected_revision": 0,
        },
    )
    assert d_res.status_code == 200

    # 2. 原参数重试：上游恢复 200，客户端必须拿到新的成功结果
    r2 = client.post("/reviews?journal=general-medical-cn", data={"llm_config": llm_cfg}, files=files).json()
    assert r2["run_id"] != r1["run_id"]
    assert r2["retry_of"] == r1["run_id"]
    ai_t2 = [t for t in r2["plan"]["tasks"] if t["node_id"] == "AI-ABSTRACT-CONSISTENCY"][0]
    assert ai_t2["status"] == "findings"
    assert any(f["rule_id"] == "AI-ABSTRACT-CONSISTENCY" for f in r2["findings"])

    # 首轮失败记录和其既有裁决依然可追溯且完整保留
    stored_r1 = client.get(f"/reviews/{r1['run_id']}").json()
    assert [t for t in stored_r1["plan"]["tasks"] if t["node_id"] == "AI-ABSTRACT-CONSISTENCY"][0]["status"] == "failed"
    assert len(stored_r1["decisions"]) == 1

    # 3. 已成功后再次同配置提交：保持幂等复用 r2
    r3 = client.post("/reviews?journal=general-medical-cn", data={"llm_config": llm_cfg}, files=files).json()
    assert r3["run_id"] == r2["run_id"]


def test_r02_ai_citation_scope_truncation_mismatch_ambiguity_and_cross_evidence(tmp_path: Path, monkeypatch):
    """R02 回归：越过送审章节、截断边界外、指定段落不匹配、位置歧义均拦截；跨摘要与正文多处证据均核验并保存。"""
    _stub_public_dns(monkeypatch)
    captured: list[dict] = []

    long_tail_prefix = "观测结果平稳。" * 3500  # 使长块尾段超过 24,000 字符截断阈值

    def _resp(req: httpx.Request, body: dict):
        items = [
            # 1) 章节外（讨论段未纳入 AI-ABSTRACT-CONSISTENCY 的 applies_to）
            {
                "blockId": "P0006",
                "originalText": "讨论段独有句子不应过检",
                "description": "越界章节引文",
                "suggestion": "修改",
                "severity": "warning",
            },
            # 2) 截断边界外（位于 P0004 的 24k 截断线之后）
            {
                "blockId": "P0004",
                "originalText": "超截断尾句不可见",
                "description": "截断边界外引文",
                "suggestion": "修改",
                "severity": "warning",
            },
            # 3) 指定段落不匹配（引文在结果段 P0003，但 model 声称在 P0002）
            {
                "blockId": "P0002",
                "originalText": "结果独有指标数值",
                "description": "指定段落张冠李戴",
                "suggestion": "修改",
                "severity": "warning",
            },
            # 4) 位置歧义（未指定段落，短语在送审范围出现两次）
            {
                "originalText": "重复患者百例",
                "description": "歧义匹配测试",
                "suggestion": "修改",
                "severity": "warning",
            },
            # 5) 合法跨摘要与正文对照证据（主锚点在摘要 P0002，次证据在结果 P0003）
            {
                "blockId": "P0002",
                "originalText": "摘要有效率80%",
                "evidenceAnchors": [{"blockId": "P0003", "originalText": "结果有效率65%"}],
                "description": "摘要与结果段有效率不一致",
                "suggestion": "核对前后有效率",
                "severity": "error",
            },
        ]
        return 200, {"choices": [{"message": {"content": json.dumps({"findings": items}, ensure_ascii=False)}}]}

    factory = _mock_client_factory(captured, _resp)
    client = TestClient(create_app(db_path=tmp_path / "r02.sqlite3", llm_client_factory=factory))

    raw = make_docx_bytes(
        [
            "R02 送审范围门禁测试",
            "摘要：纳入重复患者百例，摘要有效率80%。",
            "1 结果\n结果独有指标数值，结果有效率65%，纳入重复患者百例。",
            f"2 结果详述：{long_tail_prefix}超截断尾句不可见。",
            "3 讨论",
            "讨论段独有句子不应过检。",
        ]
    )
    res = client.post(
        "/reviews?journal=general-medical-cn",
        data={
            "llm_config": json.dumps(
                {
                    "enabled": True,
                    "provider": "openai",
                    "api_key": "sk-k1",
                    "model": "gpt-4o-mini",
                    "enabled_rules": ["AI-ABSTRACT-CONSISTENCY"],
                }
            )
        },
        files={"file": ("r02.docx", raw, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert res.status_code == 200
    run = res.json()

    # 检查发往模型的 prompt 确实不包含讨论段与超截断尾句
    sent_prompt = captured[0]["body"]["messages"][1]["content"]
    assert "讨论段独有句子不应过检" not in sent_prompt
    assert "超截断尾句不可见" not in sent_prompt

    # 仅有跨摘要-结果合法意见通过门禁，且保留经核验的 secondary_anchors
    ai_ok = [f for f in run["findings"] if f["source_type"] == "ai_candidate"]
    assert len(ai_ok) == 1
    assert ai_ok[0]["anchor"]["quote"] == "摘要有效率80%"
    assert len(ai_ok[0]["secondary_anchors"]) == 1
    assert ai_ok[0]["secondary_anchors"][0]["quote"] == "结果有效率65%"
    assert "_sent_spans" not in ai_ok[0]["evidence"]

    # 4 条不合格响应均进入带明确原因的 rejected_candidates
    rej = run["rejected_candidates"]
    assert len(rej) == 4
    reasons_joined = "\n".join(item["reason"] for item in rej)
    assert "超出" in reasons_joined or "送审" in reasons_joined
    assert "截断" in reasons_joined
    assert "不匹配" in reasons_joined
    assert "歧义" in reasons_joined


def test_r07_production_endpoint_dns_ssrf_guard(tmp_path: Path, monkeypatch):
    """R07 回归：真实或恶意非公网 DNS 解析（如 198.18.0.1 Fake-IP 或内网 IP）在端点预检阶段被阻断。"""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port, *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", int(port or 443)))
        ],
    )
    client = TestClient(create_app(db_path=tmp_path / "r07.sqlite3"))
    res = client.post(
        "/llm/test",
        json={
            "enabled": True,
            "provider": "openai",
            "api_key": "sk-user",
            "model": "gpt-4o-mini",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert "SSRF" in data["message"] or "非公网" in data["message"]
