"""可插拔大模型审校候选适配器（移植并升级自 medical-journal-reviewer/src/services/aiService.ts）。

包含经过实战验证的核心防线：
1. 支持 OpenAI / Claude / 自定义 OpenAI 兼容接口（如 DeepSeek、GLM、Qwen、Ollama、vLLM 等）
2. 括号配平 JSON 提取 + 截断安全修复（extract_json_block / repair_json / parse_model_json）
3. 运行时动态时间基准（防止把 2026 等当期年份误判为“未来时间”）
4. 推理思考关闭开关（disable_thinking -> enable_thinking=False，防止超长推理截断）
5. 原文逐字锚点定位与 AI 候选隔离协议（绝不自动改写科研数据，定位失败进入拦截清单）
"""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import json
import os
import re
import socket
import time
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from review_platform.models import (
    Anchor,
    AnchorVerification,
    DocumentIR,
    Finding,
    JournalProfile,
    LLMConfig,
    LLMTestResult,
    RuleSpec,
)

FUTURE_MISJUDGE_RE = re.compile(r"未来|尚未发生|还未发生|没有发生|不存在于|无法核实|疑似虚构|不可信")
TIME_REFERENCE_RE = re.compile(r"日期|时间|年份|时点|20\d{2}\s*年")

DEFAULT_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "claude": "https://api.anthropic.com/v1/messages",
    "custom": "",
}
DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "claude": "claude-3-5-sonnet-20241022",
    "custom": "gpt-4o",
}

RULE_INSTRUCTIONS: dict[str, str] = {
    "AI-ABSTRACT-CONSISTENCY": (
        "请专项核查【摘要与正文一致性】：\n"
        "1. 摘要是否具备「目的、方法、结果、结论」四要素；\n"
        "2. 摘要中报告的样本量、分组数、主要结局指标、P 值与置信区间是否与正文方法/结果/表格数据一致；\n"
        "3. 摘要结论是否有正文数据支撑。"
    ),
    "AI-METHOD-DESIGN": (
        "请专项核查【研究设计与统计学方法】:\n"
        "1. 研究设计类型（RCT/队列/病例对照/横断面/Meta分析）与研究目的是否匹配；\n"
        "2. 纳入/排除标准、随机化与盲法、样本量估算依据是否完整；\n"
        "3. 统计学检验方法（t检验、χ²、ANOVA、非参数检验、多因素回归）与数据类型是否适配，是否遗漏伦理批件号与知情同意声明。"
    ),
    "AI-DATA-LOGIC": (
        "请专项核查【图文表对应与论证逻辑】：\n"
        "1. 正文对图表结果的解读是否与表格实际数据吻合；\n"
        "2. 讨论与结论是否存在超出本研究设计（如横断面推断因果）的过度推断；\n"
        "3. 是否充分客观讨论研究局限性与偏倚风险。"
    ),
    "AI-LANGUAGE-STYLE": (
        "请专项核查【学术语言与表达逻辑】：\n"
        "1. 识别长难句句式杂糅、主谓搭配不当、成分残缺、指代不明；\n"
        "2. 识别前后段落逻辑衔接断层或非学术口语化表达；\n"
        "3. 仅提出语言表达修改建议，严禁改动任何科研数据与统计结果。"
    ),
}


def get_llm_env_defaults_public() -> dict[str, Any]:
    """返回脱敏后的环境变量默认 LLM 配置状态（绝不返回 api_key）。"""
    env_key = os.environ.get("REVIEW_LLM_API_KEY", "").strip()
    env_provider = os.environ.get("REVIEW_LLM_PROVIDER", "openai").strip()
    if env_provider not in ("openai", "claude", "custom"):
        env_provider = "openai"
    return {
        "has_env_default": bool(env_key or os.environ.get("REVIEW_LLM_ENDPOINT", "").strip()),
        "provider": env_provider,
        "endpoint": os.environ.get("REVIEW_LLM_ENDPOINT", "").strip(),
        "model": os.environ.get("REVIEW_LLM_MODEL", "").strip(),
    }


def resolve_llm_config(user_cfg: Optional[LLMConfig]) -> LLMConfig:
    """合并用户提交配置与可选服务端环境变量默认配置（严格同源隔离：自定义地址绝不复用服务端密钥）。"""
    if user_cfg is None:
        env_enabled = os.environ.get("REVIEW_LLM_ENABLED", "").strip().lower() in ("1", "true", "yes")
        if not env_enabled:
            return LLMConfig(enabled=False)
        user_cfg = LLMConfig(enabled=True)

    if not user_cfg.enabled:
        return user_cfg

    provider = user_cfg.provider
    user_endpoint = user_cfg.endpoint.strip()
    env_endpoint = os.environ.get("REVIEW_LLM_ENDPOINT", "").strip()
    endpoint = user_endpoint or env_endpoint
    model = user_cfg.model.strip() or os.environ.get("REVIEW_LLM_MODEL", "").strip() or DEFAULT_MODELS[provider]

    # N2 修复：仅当 endpoint 未被用户自定义替换（即仍为服务端默认或官方默认）时，才允许回退服务端环境变量密钥
    official_default = DEFAULT_ENDPOINTS.get(provider, "")
    same_origin_as_server = (
        not user_endpoint
        or (env_endpoint and user_endpoint.rstrip("/") == env_endpoint.rstrip("/"))
        or (official_default and user_endpoint.rstrip("/") == official_default.rstrip("/"))
    )
    user_key = user_cfg.api_key.strip()
    if user_key:
        api_key = user_key
    elif same_origin_as_server:
        api_key = os.environ.get("REVIEW_LLM_API_KEY", "").strip()
    else:
        api_key = ""

    return user_cfg.model_copy(
        update={
            "endpoint": endpoint,
            "model": model,
            "api_key": api_key,
        }
    )


def build_time_anchored_system_prompt(now: datetime.datetime | None = None) -> str:
    """构建带运行时日期基准的系统提示词，消除当期年份误判。"""
    tz = ZoneInfo("Asia/Shanghai")
    current = now.astimezone(tz) if now else datetime.datetime.now(tz)
    today = f"{current.year}年{current.month}月{current.day}日"
    return (
        "你是一位专业的中文医学期刊审校专家，请用中文回复。"
        "需要引用原文时，字符串内一律使用「」，不要使用未转义的英文双引号。"
        "只输出合法 JSON，不要输出 Markdown 说明。\n\n"
        f"【时间基准】今天是 {today}。本次审校的稿件是当期投稿，正文、数据与参考文献中出现的"
        "近期年份（含今年及前一年）均为真实已发生时间。"
        "严禁以「日期在未来」「时间点尚未到达」「该年份不存在」等理由提出批注或扣分。"
    )


def is_future_date_misjudgment(item: dict[str, Any]) -> bool:
    """过滤模型无视时间基准仍将当期年份误判为未来的噪声条目。"""
    text = " ".join(
        str(item.get(k) or "")
        for k in ("problemType", "message", "description", "suggestion", "reason")
    )
    return bool(FUTURE_MISJUDGE_RE.search(text) and TIME_REFERENCE_RE.search(text))


def extract_json_block(text: str) -> str | None:
    """从纯 JSON、代码块包裹或夹杂文字的模型输出中以括号配平提取 JSON 主体。"""
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.I)
    src = fenced.group(1) if fenced else text

    def balanced_from(start: int) -> str | None:
        depth = 0
        in_str = False
        escaped = False
        for i in range(start, len(src)):
            ch = src[i]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[start : i + 1]
        return None

    first_brace = src.find("{")
    if first_brace >= 0 and balanced_from(first_brace) is None:
        return src[first_brace:]

    candidates: list[str] = []
    pos = src.find("{")
    while pos >= 0:
        block = balanced_from(pos)
        if block:
            candidates.append(block)
        pos = src.find("{", pos + 1)

    if candidates:
        candidates.sort(key=len, reverse=True)
        for block in candidates:
            for probe in (block, repair_json(block)):
                try:
                    json.loads(probe)
                    return block
                except Exception:
                    continue
        return candidates[0]

    return src[first_brace:] if first_brace >= 0 else None


def repair_json(raw: str) -> str:
    """修复中文引号、尾逗号、对象漏逗号及 max_tokens 截断未闭合括号。"""
    out = raw.replace("\u201c", '"').replace("\u201d", '"')
    out = out.replace("\u2018", "'").replace("\u2019", "'")
    out = re.sub(r",\s*([}\]])", r"\1", out)
    out = re.sub(r"\}(\s*)\{", r"},\1{", out)
    out = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)\s*(?=\[|\{)', r'"\1": ', out)

    in_str = False
    escaped = False
    stack: list[str] = []
    for ch in out:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]") and stack:
            stack.pop()

    if in_str:
        out += '"'
    out = re.sub(r",\s*$", "", out)
    while stack:
        out += stack.pop()
    return out


def _scan_json(text: str) -> tuple[bool, list[int]]:
    commas: list[int] = []
    stack: list[str] = []
    in_str = False
    escaped = False
    well_formed = True

    for i, ch in enumerate(text):
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if not stack or stack.pop() != ch:
                well_formed = False
        elif ch == ",":
            commas.append(i)

    return well_formed and not stack and not in_str, commas


def parse_model_json(response: str) -> dict[str, Any]:
    """多级容错解析模型 JSON 响应。"""
    if not response.strip():
        raise ValueError("模型响应为空")
    block = extract_json_block(response)
    if not block:
        raise ValueError(f"无法从响应中提取 JSON（长度 {len(response)}）")

    try:
        parsed = json.loads(block)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    balanced, commas = _scan_json(block)
    attempts = [repair_json(block)]
    if not balanced:
        for idx in reversed(commas):
            if len(attempts) >= 60:
                break
            head = block[:idx]
            if not _scan_json(head)[0]:
                attempts.append(repair_json(head))

    for cand in attempts:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue

    raise ValueError("模型返回的 JSON 无法解析与修复")


def _is_disallowed_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def _validate_ssrf_target(endpoint: str) -> None:
    """N3 修复：拒绝内网/环回/链路本地（云元数据）地址及 query/fragment 注入。"""
    parsed = urlparse(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.hostname:
        raise ValueError("接口地址必须为合法的 http:// 或 https:// URL")
    if parsed.query or parsed.fragment:
        raise ValueError("接口地址不允许包含查询参数 (?) 或锚点 (#)")

    allow_local = os.environ.get("REVIEW_LLM_ALLOW_LOCAL", "").strip().lower() in ("1", "true", "yes")
    if allow_local:
        return

    host = parsed.hostname.lower()
    if host in ("localhost", "ip6-localhost") or host.endswith((".local", ".internal")):
        raise ValueError("默认禁止访问本机或内网域名（如需本机 Ollama 请设置 REVIEW_LLM_ALLOW_LOCAL=1）")

    try:
        ip_lit = ipaddress.ip_address(host)
    except ValueError:
        ip_lit = None

    if ip_lit is not None:
        if _is_disallowed_ip(ip_lit):
            raise ValueError("默认禁止访问私有网段、环回或链路本地地址（SSRF 防护）")
        return

    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                resolved_ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
                if _is_disallowed_ip(resolved_ip):
                    raise ValueError("目标域名解析到受限内网或链路本地地址（SSRF 防护）")
    except socket.gaierror:
        # 离线/测试环境允许继续交给传输层或 MockTransport 处理
        pass


def _resolve_endpoint(config: LLMConfig) -> str:
    raw = config.endpoint.strip()
    if config.provider == "openai":
        ep = raw or DEFAULT_ENDPOINTS["openai"]
    elif config.provider == "claude":
        ep = raw or DEFAULT_ENDPOINTS["claude"]
    else:
        if not raw:
            raise ValueError("自定义接口必须提供 Base URL / Endpoint")
        parsed_raw = urlparse(raw)
        if parsed_raw.query or parsed_raw.fragment:
            raise ValueError("自定义接口地址不允许包含查询参数 (?) 或锚点 (#)")
        ep = raw.rstrip("/")
        if not (ep.endswith("/v1/chat/completions") or ep.endswith("/chat/completions")):
            ep = f"{ep}/chat/completions" if ep.endswith("/v1") else f"{ep}/v1/chat/completions"

    _validate_ssrf_target(ep)
    return ep


def _is_local_endpoint(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def call_llm_completion(
    config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens_override: Optional[int] = None,
    client_factory: Optional[Callable[[float], httpx.Client]] = None,
) -> str:
    """调用用户配置的大模型（OpenAI / Claude / 自定义兼容接口）。"""
    endpoint = _resolve_endpoint(config)
    model = config.model.strip() or DEFAULT_MODELS[config.provider]
    api_key = config.api_key.strip()

    if not api_key and not (config.provider == "custom" and _is_local_endpoint(endpoint)):
        raise ValueError("请提供有效的 API Key")

    max_tokens = max_tokens_override or config.max_tokens
    make_client = client_factory or (lambda t: httpx.Client(timeout=t, follow_redirects=False))

    with make_client(config.timeout_seconds) as client:
        if config.provider == "claude":
            headers = {
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            payload: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            resp = client.post(endpoint, headers=headers, json=payload)
        else:
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": config.temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
            if config.disable_thinking:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            resp = client.post(endpoint, headers=headers, json=payload)

    if resp.status_code >= 400:
        # N3 修复：脱敏错误提示，不回显任意上游原始响应体，防止半盲 SSRF 读取
        err_code = ""
        try:
            err_json = resp.json()
            if isinstance(err_json, dict) and isinstance(err_json.get("error"), dict):
                err_code = str(err_json["error"].get("code") or err_json["error"].get("type") or "")[:40]
        except Exception:
            pass
        suffix = f" [{err_code}]" if err_code else ""
        raise RuntimeError(f"大模型接口返回错误（HTTP {resp.status_code}{suffix}）")

    data = resp.json()
    if config.provider == "claude":
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    else:
        choices = data.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        text = msg.get("content") or (choices[0].get("text") if choices else "") or ""

    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("大模型返回内容为空（若使用推理模型，建议开启「关闭思考链」或增大 max_tokens）")
    return text


def test_llm_connection(
    config: LLMConfig,
    *,
    client_factory: Optional[Callable[[float], httpx.Client]] = None,
) -> LLMTestResult:
    """验证模型接口连通性（绝不在返回值中回显 API Key）。"""
    resolved = resolve_llm_config(config.model_copy(update={"enabled": True}))
    t0 = time.perf_counter()
    try:
        reply = call_llm_completion(
            resolved,
            "你是连接测试助手，请简短回复。",
            "请回复：连接成功",
            max_tokens_override=64,
            client_factory=client_factory,
        )
        dt = round((time.perf_counter() - t0) * 1000, 1)
        return LLMTestResult(
            ok=True,
            provider=resolved.provider,
            model=resolved.model,
            latency_ms=dt,
            preview=reply.strip()[:80],
            message="模型连接测试成功",
        )
    except Exception as exc:
        dt = round((time.perf_counter() - t0) * 1000, 1)
        return LLMTestResult(
            ok=False,
            provider=resolved.provider,
            model=resolved.model,
            latency_ms=dt,
            message=str(exc),
        )


def _locate_quote_anchor(
    document: DocumentIR,
    block_hint: str,
    quote: str,
) -> Anchor:
    """在 DocumentIR 中逐字定位 AI 返回的 originalText；找不到则返回未命中锚点供门禁拦截。"""
    clean_q = quote.strip()
    if clean_q and len(clean_q) >= 2:
        blocks = list(document.blocks)
        if block_hint:
            hinted = [b for b in blocks if b.id == block_hint]
            blocks = hinted + [b for b in blocks if b.id != block_hint]
        for blk in blocks:
            idx = blk.text.find(clean_q)
            if idx >= 0:
                return Anchor(
                    document_id=document.document_id,
                    block_id=blk.id,
                    start=idx,
                    end=idx + len(clean_q),
                    quote=clean_q,
                )
        # 去除模型外层可能自加的引号后再次精确匹配（不压缩内部空白）
        stripped = clean_q.strip("“”‘’\"'「」")
        if len(stripped) >= 2 and stripped != clean_q:
            for blk in blocks:
                idx = blk.text.find(stripped)
                if idx >= 0:
                    return Anchor(
                        document_id=document.document_id,
                        block_id=blk.id,
                        start=idx,
                        end=idx + len(stripped),
                        quote=stripped,
                    )

    fallback_block = document.blocks[0].id if document.blocks else "P0001"
    return Anchor(
        document_id=document.document_id,
        block_id=block_hint or fallback_block,
        start=0,
        end=0,
        quote=clean_q or "(未提供原文引文)",
    )


def run_semantic_rule_with_llm(
    rule: RuleSpec,
    document: DocumentIR,
    journal: JournalProfile,
    config: LLMConfig,
    basis_source: str,
    *,
    client_factory: Optional[Callable[[float], httpx.Client]] = None,
) -> tuple[list[Finding], int, int]:
    """执行单条受控 AI 语义规则，返回待 FindingEngine 门禁核验的候选意见。"""
    applies_to = set(rule.applies_to) if rule.applies_to else None
    target_blocks = [
        b
        for b in document.blocks
        if b.kind != "separator" and (not applies_to or b.section in applies_to)
    ]
    if not target_blocks:
        return [], 0, 0

    manuscript_lines = [f"[{b.id} | {b.section_cn}] {b.text}" for b in target_blocks]
    manuscript_text = "\n".join(manuscript_lines)
    context_truncated = len(manuscript_text) > 24000
    # 防止超长稿件撑爆单次上下文，保守截断到约 24000 字符并显式标记
    if context_truncated:
        manuscript_text = manuscript_text[:24000] + "\n…（后续内容超出单次模型上下文预算已截断）"

    dimension_guide = RULE_INSTRUCTIONS.get(rule.id, rule.message_template)
    user_prompt = (
        f"目标期刊：{journal.name}（{journal.id}）\n"
        f"当前审校维度：{rule.title}（规则编号：{rule.id}）\n"
        f"{dimension_guide}\n\n"
        "## 严格输出要求\n"
        "1. 严格输出如下 JSON 格式，不要输出任何额外解释：\n"
        "{\n"
        '  "findings": [\n'
        "    {\n"
        '      "blockId": "段落编号，如 P0002",\n'
        '      "originalText": "必须从下方文本块中逐字连续复制的有问题原文片段（不要包含[P0002|...]前缀，不要改写）",\n'
        '      "description": "具体问题描述",\n'
        '      "suggestion": "修改建议（严禁擅自捏造或篡改科研数据）",\n'
        '      "reason": "审校理由",\n'
        '      "severity": "warning"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        '2. 若未发现该维度下的明确问题，返回 {"findings": []}，宁缺毋滥。\n'
        "3. severity 仅允许 error / warning / info。\n\n"
        f"## 稿件文本块\n{manuscript_text}"
    )

    raw_reply = call_llm_completion(
        config,
        build_time_anchored_system_prompt(),
        user_prompt,
        client_factory=client_factory,
    )
    parsed = parse_model_json(raw_reply)
    raw_items = parsed.get("findings") or parsed.get("annotations") or []
    if not isinstance(raw_items, list):
        raw_items = []

    findings: list[Finding] = []
    excluded = 0

    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        if is_future_date_misjudgment(item):
            excluded += 1
            continue
        desc = str(item.get("description") or item.get("message") or "").strip()
        sugg = str(item.get("suggestion") or "").strip()
        if not desc and not sugg:
            continue

        orig_text = str(item.get("originalText") or item.get("quote") or "").strip()
        block_hint = str(item.get("blockId") or item.get("block_id") or "").strip()
        anchor = _locate_quote_anchor(document, block_hint, orig_text)

        sev = str(item.get("severity") or rule.severity).lower()
        if sev not in ("error", "warning", "info"):
            sev = rule.severity

        raw_id = f"{rule.id}:{anchor.block_id}:{anchor.start}:{anchor.end}:{anchor.quote}:{idx}".encode("utf-8")
        fid = f"ai-{hashlib.sha256(raw_id).hexdigest()[:12]}"

        findings.append(
            Finding(
                id=fid,
                rule_id=rule.id,
                rule_version=rule.version,
                basis_id=rule.basis_id,
                basis_source=basis_source,
                title=rule.title,
                category=rule.category,
                scope=rule.scope,
                severity=sev,  # type: ignore[arg-type]
                anchor=anchor,
                fact_ids=[],
                message=desc or rule.message_template,
                suggestion=sugg or rule.suggestion_template,
                evidence={
                    "provider": config.provider,
                    "model": config.model,
                    "reason": str(item.get("reason") or "").strip(),
                    "block_hint": block_hint,
                    "context_truncated": context_truncated,
                },
                source_type="ai_candidate",
                verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
            )
        )

    return findings, len(target_blocks), excluded


class AICandidateExecutor(Protocol):
    """可插拔大模型语义审校执行器接口（仅返回候选 Finding，须过门禁）。"""

    def generate_candidates(
        self, document: DocumentIR, journal: JournalProfile
    ) -> list[Finding]:
        ...
