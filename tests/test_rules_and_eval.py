from __future__ import annotations

import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from examples.make_demo import build_demo_docx
from review_platform.document_engine import parse_docx_bytes
from review_platform.evaluation_engine import evaluate_findings_against_gold
from review_platform.finding_engine import govern_findings
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import Anchor, AnchorVerification, Finding
from review_platform.pipeline import PlatformContext
from review_platform.review_engine import (
    build_time_anchored_system_prompt,
    extract_json_block,
    is_future_date_misjudgment,
    parse_model_json,
)
from tests.conftest import make_docx_bytes


def test_p_value_and_percentage_and_caption_positive_negative_exceptions():
    ctx = PlatformContext()
    raw = make_docx_bytes(
        paragraphs=[
            "医学测试稿件",
            (
                "摘要：正确值 P=0，P=1，P=1.2e-5，1/3（33.3%）。"
                "错误值 P=1.25，P=-0.02，30/100（40%），5/0（0%），12/10（120%）。"
                "引号示例不报错：“例如错例 P=1.9 和 10/100（50%）”。"
            ),
            "1 资料与方法",
            "正文引用见图9，不应视为题注。",
            "图1 正常起始图",
            "表1 独立表序列",
            "图3 跳号图题注",
            "图1 重复图题注",
        ]
    )
    run = ctx.run_review(raw, "cases.docx", "general-medical-cn")
    by_rule = {}
    for f in run.findings:
        by_rule.setdefault(f.rule_id, []).append(f)

    # P 值只命中 1.25 与 -0.02，不误报 0 / 1 / 1.2e-5 / 引号内 1.9
    p_quotes = [f.anchor.quote for f in by_rule.get("STAT-PRANGE", [])]
    assert len(p_quotes) == 2
    assert any("1.25" in q for q in p_quotes)
    assert any("-0.02" in q for q in p_quotes)

    # 百分比命中 30/100（40%）、5/0（0%）、12/10（120%），不误报 1/3（33.3%）和引号内 10/100（50%）
    pct_quotes = [f.anchor.quote for f in by_rule.get("DATA-PCT-RECALC", [])]
    assert len(pct_quotes) == 3

    # 题注命中 图3 跳号 与 图1 重复，不误报「见图9」
    cap_quotes = [f.anchor.quote for f in by_rule.get("STRUCT-CAPTION-SEQ", [])]
    assert cap_quotes == ["图3", "图1"]


def test_legacy_copyediting_rules_with_exceptions():
    ctx = PlatformContext()
    raw = make_docx_bytes(
        paragraphs=[
            "急性心肌梗塞临床研究",
            "摘要：纳入2020-2025年患者，年龄30-65岁，以3000rpm离心。",
            "1 方法 患者收缩压高于140 mmHg，其余结果如下......",
            "参考文献",
            "[1] 王五. 心肌梗死研究[J]. 中华医学杂志, 2024, 100(1): 10～15.",
        ]
    )
    run = ctx.run_review(raw, "legacy.docx", "general-medical-cn")
    rule_ids = {f.rule_id for f in run.findings}

    assert "TERM-MI" in rule_ids       # 心肌梗塞 -> 心肌梗死
    assert "TERM-BP" not in rule_ids   # 血压高于 受 exception_after=["升","于"] 豁免
    assert "UNIT-RPM" in rule_ids      # 3000rpm
    assert "PUN-ELLIPSIS" in rule_ids  # ......
    assert "REF-PAGE" in rule_ids      # 10～15 in references

    # NUM-RANGE 仅命中 30-65，跳过 2020-2025 年份区间及参考文献行
    num_range_quotes = [f.anchor.quote for f in run.findings if f.rule_id == "NUM-RANGE"]
    assert num_range_quotes == ["30-65"]


def test_llm_adapter_json_repair_and_finding_gate():
    sys_prompt = build_time_anchored_system_prompt(
        datetime.datetime(2026, 11, 25, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    )
    assert "2026年11月25日" in sys_prompt
    assert is_future_date_misjudgment({"description": "2026年尚未发生，时间点在未来"}) is True
    assert is_future_date_misjudgment({"description": "对照组样本量未说明"}) is False

    truncated = '说明文字 ```json\n{"overallScore": 85, "annotations": [{"description": "问题A"}, {"description": "截断'
    block = extract_json_block(truncated)
    assert block is not None
    parsed = parse_model_json(truncated)
    assert parsed["overallScore"] == 85
    assert len(parsed["annotations"]) >= 1

    # FindingEngine 拦截伪造依据与无效锚点的 AI 候选
    doc = parse_docx_bytes(make_docx_bytes(["标题", "摘要：样本量 50 例。"]))
    knowledge = KnowledgeEngine()
    fake_ai = Finding(
        id="ai-1",
        rule_id="AI-SEM-01",
        rule_version="0.1.0",
        basis_id="FAKE-BASIS",
        title="AI 候选",
        category="logic",
        scope="professional",
        severity="info",
        anchor=Anchor(
            document_id=doc.document_id,
            block_id=doc.blocks[1].id,
            start=0,
            end=2,
            quote="摘要",
        ),
        message="AI 发现",
        suggestion="修改",
        source_type="ai_candidate",
        verification=AnchorVerification(anchor_valid=False, basis_valid=False, reason="pending"),
    )
    verified, rejected = govern_findings(doc, knowledge, [fake_ai])
    assert verified == []
    assert len(rejected) == 1
    assert "不存在" in rejected[0]["reason"]


def test_demo_gold_evaluation(tmp_path: Path):
    demo_path = build_demo_docx(tmp_path / "demo.docx")
    ctx = PlatformContext()
    run = ctx.run_review(demo_path.read_bytes(), "demo.docx", "general-medical-cn")
    gold = [
        {"rule_id": "TERM-MI", "quote_substring": "心肌梗塞"},
        {"rule_id": "UNIT-RPM", "quote_substring": "3000rpm"},
        {"rule_id": "DATA-PCT-RECALC", "quote_substring": "30/100"},
        {"rule_id": "STAT-PRANGE", "quote_substring": "1.25"},
        {"rule_id": "STRUCT-CAPTION-SEQ", "quote_substring": "图3"},
        {"rule_id": "REF-PAGE", "quote_substring": "120～125"},
    ]
    metrics = evaluate_findings_against_gold(run.findings, gold)
    assert metrics["tp"] == len(gold)
    assert metrics["recall"] == 1.0
