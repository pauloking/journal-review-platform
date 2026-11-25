from __future__ import annotations

from pathlib import Path

import pytest

from review_platform.document_engine import (
    DocumentParseError,
    parse_docx_bytes,
    validate_anchor,
)
from review_platform.models import Anchor
from tests.conftest import make_docx_bytes, patch_docx_member


def test_deterministic_ir_and_split_runs():
    raw = make_docx_bytes(
        paragraphs=[
            "心血管介入治疗临床研究",
            [("观察组有效率", False), ("30/100（40%）", True), ("，差异显著。", False)],
        ],
        tables=[[["组别", "比例"], ["观察组", "15/30（50.0%）"]]],
    )
    ir1 = parse_docx_bytes(raw, "case.docx")
    ir2 = parse_docx_bytes(raw, "case.docx")

    assert ir1.document_id == ir2.document_id
    assert [b.id for b in ir1.blocks] == [b.id for b in ir2.blocks]
    assert ir1.blocks[1].text == "观察组有效率30/100（40%），差异显著。"
    assert len(ir1.blocks[1].runs) == 3
    assert ir1.blocks[1].runs[1].start == 6
    assert ir1.blocks[1].runs[1].end == 17

    # 表格单元格文本提取准确
    table_texts = [b.text for b in ir1.blocks if b.kind == "table_cell"]
    assert "15/30（50.0%）" in table_texts

    # 锚点精确校验与篡改拒绝
    good = Anchor(
        document_id=ir1.document_id,
        block_id=ir1.blocks[1].id,
        start=6,
        end=17,
        quote="30/100（40%）",
    )
    assert validate_anchor(ir1, good).anchor_valid is True

    bad_fp = good.model_copy(update={"document_id": "0" * 64})
    assert validate_anchor(ir1, bad_fp).anchor_valid is False

    bad_quote = good.model_copy(update={"quote": "30/100（30%）"})
    assert validate_anchor(ir1, bad_quote).anchor_valid is False


def test_security_rejections():
    with pytest.raises(DocumentParseError):
        parse_docx_bytes(b"")

    with pytest.raises(DocumentParseError):
        parse_docx_bytes(b"not-a-zip")

    with pytest.raises(DocumentParseError) as exc_big:
        parse_docx_bytes(b"x" * (10 * 1024 * 1024 + 1))
    assert exc_big.value.status_code == 413

    base = make_docx_bytes(["正常测试段落"])

    xxe_docx = patch_docx_member(
        base,
        "word/document.xml",
        lambda b: b.replace(b"<w:document", b"<!DOCTYPE foo [<!ENTITY xxe 'bar'>]><w:document"),
    )
    with pytest.raises(DocumentParseError):
        parse_docx_bytes(xxe_docx)

    tracked_docx = patch_docx_member(
        base,
        "word/document.xml",
        lambda b: b.replace(b"</w:p>", b'<w:ins w:id="1" w:author="a"/></w:p>'),
    )
    with pytest.raises(DocumentParseError):
        parse_docx_bytes(tracked_docx)


def test_real_legacy_manuscript_parses_cleanly():
    fixture = Path(
        "/Users/wangguoyang/Projects/medical-journal-reviewer/src/services/__fixtures__/real-case-manuscript.txt"
    )
    if not fixture.exists():
        pytest.skip("legacy fixture missing")
    lines = [ln for ln in fixture.read_text(encoding="utf-8").splitlines() if ln.strip()][:60]
    raw = make_docx_bytes(paragraphs=lines)
    ir = parse_docx_bytes(raw, "real-case.docx")
    assert ir.profile.total_blocks >= 20
    assert "abstract" in ir.profile.sections_present or "body" in ir.profile.sections_present
