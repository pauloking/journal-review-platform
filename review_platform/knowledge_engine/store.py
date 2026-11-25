"""结构化规范依据、规范文档（Markdown）与期刊配置加载器。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from review_platform.models import JournalProfile, KnowledgeClause, KnowledgeDocument

DEFAULT_PACK_DIR = Path(__file__).resolve().parent.parent / "knowledge_packs" / "medical_cn_v1"

BUILTIN_DOC_MANIFEST: list[dict] = [
    {
        "doc_id": "DOC-GBT15835-2011",
        "file": "DOC-GBT15835-2011.md",
        "title": "《GB/T 15835-2011 出版物上数字用法》",
        "basis_id": "STD-GBT15835-2011",
        "basis_ids": ["STD-GBT15835-2011"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-GBT3100-UNITS",
        "file": "DOC-GBT3100-UNITS.md",
        "title": "《GB 3100~3102-1993 量和单位》",
        "basis_id": "STD-GB3100-3102",
        "basis_ids": ["STD-GB3100-3102"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-GBT15834-PUNCT",
        "file": "DOC-GBT15834-PUNCT.md",
        "title": "《GB/T 15834-2011 标点符号用法》",
        "basis_id": "STD-GBT15834-2011",
        "basis_ids": ["STD-GBT15834-2011"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-GBT7714-REFS",
        "file": "DOC-GBT7714-REFS.md",
        "title": "《GB/T 7714-2025 信息与文献 参考文献著录规则》",
        "basis_id": "STD-GBT7714-2025",
        "basis_ids": ["STD-GBT7714-2025"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-CNTERM-MEDICAL",
        "file": "DOC-CNTERM-MEDICAL.md",
        "title": "《全国科学技术名词审定委员会 规范医学名词》",
        "basis_id": "STD-CNTERM-COMM",
        "basis_ids": ["STD-CNTERM-COMM"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-GB3358-STATS",
        "file": "DOC-GB3358-STATS.md",
        "title": "《GB/T 3358 统计学词汇及符号与医学统计表述规范》",
        "basis_id": "STD-GB3358-STATS",
        "basis_ids": ["STD-GB3358-STATS"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-MATH-STRUCTURE",
        "file": "DOC-MATH-STRUCTURE.md",
        "title": "《医学期刊数学复算与图表题注基线规范》",
        "basis_id": "LOCAL-MATH-PRANGE",
        "basis_ids": ["LOCAL-MATH-PRANGE", "LOCAL-MATH-PERCENTAGE", "LOCAL-STRUCT-CAPTION"],
        "supports_rule_editing": False,
    },
    {
        "doc_id": "DOC-JOURNAL-CHECKLIST",
        "file": "DOC-JOURNAL-CHECKLIST.md",
        "title": "《医学期刊投稿须知与编辑部体例核对清单》",
        "basis_id": "JOU-STYLE-GUIDE",
        "basis_ids": ["JOU-STYLE-GUIDE"],
        "supports_rule_editing": True,
    },
    {
        "doc_id": "DOC-EDITORIAL-EXP",
        "file": "DOC-EDITORIAL-EXP.md",
        "title": "《医学期刊编校经验与风险提示手册》",
        "basis_id": "EXP-EDITORIAL-GUIDE",
        "basis_ids": ["EXP-EDITORIAL-GUIDE"],
        "supports_rule_editing": True,
    },
]


class KnowledgeEngine:
    """管理结构化规范条款、规范文档与期刊配置。"""

    def __init__(self, pack_dir: Optional[Path] = None) -> None:
        self.pack_dir = Path(pack_dir) if pack_dir else DEFAULT_PACK_DIR
        self._clauses: dict[str, KnowledgeClause] = {}
        self._journals: dict[str, JournalProfile] = {}
        self._documents: dict[str, KnowledgeDocument] = {}
        self._load()

    def _load(self) -> None:
        clauses_path = self.pack_dir / "clauses.json"
        journals_path = self.pack_dir / "journals.json"
        docs_dir = self.pack_dir / "documents"

        raw_clauses = json.loads(clauses_path.read_text(encoding="utf-8"))
        for item in raw_clauses:
            clause = KnowledgeClause.model_validate(item)
            self._clauses[clause.id] = clause

        raw_journals = json.loads(journals_path.read_text(encoding="utf-8"))
        for item in raw_journals:
            journal = JournalProfile.model_validate(item)
            self._journals[journal.id] = journal

        for entry in BUILTIN_DOC_MANIFEST:
            md_path = docs_dir / entry["file"]
            body_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
            primary_clause = self._clauses[entry["basis_id"]]
            doc = KnowledgeDocument(
                doc_id=entry["doc_id"],
                basis_id=entry["basis_id"],
                basis_ids=list(entry["basis_ids"]),
                title=entry["title"],
                source=primary_clause.source,
                edition=primary_clause.edition,
                scope=primary_clause.scope,
                effective_date=primary_clause.effective_date,
                summary=primary_clause.summary,
                body_markdown=body_md,
                is_builtin=True,
                supports_rule_editing=bool(entry["supports_rule_editing"]),
                version="1.0.0",
            )
            self._documents[doc.doc_id] = doc

    def register_clause(self, clause: KnowledgeClause) -> None:
        self._clauses[clause.id] = clause

    def upsert_document(self, doc: KnowledgeDocument) -> None:
        self._documents[doc.doc_id] = doc

    def get_document(self, doc_id: str) -> KnowledgeDocument:
        if doc_id not in self._documents:
            raise KeyError(f"未知规范文档: {doc_id}")
        return self._documents[doc_id]

    def list_documents(self) -> list[KnowledgeDocument]:
        return list(self._documents.values())

    def get_clause(self, basis_id: str) -> Optional[KnowledgeClause]:
        return self._clauses.get(basis_id)

    def has_clause(self, basis_id: str) -> bool:
        return basis_id in self._clauses

    def list_clauses(self) -> list[KnowledgeClause]:
        return list(self._clauses.values())

    def get_journal(self, journal_id: str) -> JournalProfile:
        if journal_id not in self._journals:
            raise KeyError(f"未知期刊配置: {journal_id}")
        return self._journals[journal_id]

    def list_journals(self) -> list[JournalProfile]:
        return list(self._journals.values())
