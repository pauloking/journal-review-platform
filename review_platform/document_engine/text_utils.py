"""纯文本特征辅助工具（供解析、规则校验与执行器共享，避免跨层反向依赖）。"""

from __future__ import annotations

import re


_NUM_TRANS = str.maketrans("０１２３４５６７８９．＋－−ｅＥ", "0123456789.+--eE")


def normalize_numeric_text(text: str) -> str:
    """将全角数字/小数点/正负号/Unicode减号规范化为 ASCII 数值字符串（仅用于计算，不改动原文坐标）。"""
    return text.translate(_NUM_TRANS)


def is_reference_entry(text: str) -> bool:
    """识别带编号或不带编号的中英文文后参考文献条目。"""
    clean = text.strip()
    if len(clean) < 12:
        return False
    numbered = bool(re.match(r"^[\[［〔]?\d+[\]］〕．.、)]\s*", clean))
    doc_type = bool(re.search(r"\[[A-Za-z]/?[A-Za-z]?\]", clean))
    year = bool(re.search(r"(?:19|20)\d{2}", clean))
    journal_tail = bool(
        re.search(r"(?:19|20)\d{2}\s*[,，]\s*\d+(?:\s*[（(]\d+[）)])?\s*[:：]\s*\d+", clean)
    )
    doi = bool(re.search(r"(?:doi|10\.\d{4,}|https?://)", clean, re.I))
    authorish = bool(
        re.match(r"^(?:[\u4e00-\u9fff]{2,4}[,，、]|[A-Z][A-Za-z'’-]+(?:\s+[A-Z]{1,3})?[,，])", clean)
    )
    bracket_numbered = bool(re.match(r"^[\[［〔]\d+[\]］〕]", clean))
    return (numbered and (doc_type or year or journal_tail or doi or bracket_numbered)) or (
        authorish and (doc_type or journal_tail or doi)
    )
