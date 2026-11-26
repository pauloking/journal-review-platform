from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from review_platform.api import create_app
from tests.conftest import make_docx_bytes


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


def test_llm_test_connection_and_custom_endpoint_normalization(tmp_path: Path):
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


def test_llm_semantic_review_with_anchor_gate_and_key_isolation(tmp_path: Path):
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


def test_n3_ssrf_targets_blocked_and_upstream_body_sanitized(tmp_path: Path):
    """N3 回归：阻断元数据/回环/带查询参数的 SSRF 地址，且不回显上游原始错误体。"""
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


def test_n4_and_i3_run_seed_includes_llm_dims_and_truncation_disclosure(tmp_path: Path):
    """N4 & i3 回归：不同 enabled_rules/temperature 生成不同 run_id；超过 24,000 字时显式披露截断并降级状态。"""
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


def test_n9_short_paragraphs_truncation_disclosed_even_with_zero_findings(tmp_path: Path):
    """N9 回归：大量短段落致带前缀提示词超 24k 但原文不足 24k 且 AI 返回零意见时，仍须披露截断。"""
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
