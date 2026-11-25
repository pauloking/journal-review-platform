from __future__ import annotations

import io
import zipfile
from typing import Optional

from docx import Document


def make_docx_bytes(
    paragraphs: Optional[list[str | list[tuple[str, bool]]]] = None,
    tables: Optional[list[list[list[str]]]] = None,
) -> bytes:
    """构建内存中的合成 DOCX 字节流。paragraphs 支持纯文本或 (text, bold) 多 run 列表。"""
    doc = Document()
    for item in paragraphs or []:
        p = doc.add_paragraph()
        if isinstance(item, str):
            p.add_run(item)
        else:
            for txt, is_bold in item:
                r = p.add_run(txt)
                r.bold = is_bold

    for tbl_rows in tables or []:
        if not tbl_rows:
            continue
        tbl = doc.add_table(rows=len(tbl_rows), cols=len(tbl_rows[0]))
        for r_i, row in enumerate(tbl_rows):
            for c_i, val in enumerate(row):
                tbl.rows[r_i].cells[c_i].text = val

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def patch_docx_member(raw_docx: bytes, member: str, transform) -> bytes:
    buf_in = io.BytesIO(raw_docx)
    with zipfile.ZipFile(buf_in, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}
    entries[member] = transform(entries[member])
    buf_out = io.BytesIO()
    with zipfile.ZipFile(buf_out, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in entries.items():
            zout.writestr(name, data)
    return buf_out.getvalue()
