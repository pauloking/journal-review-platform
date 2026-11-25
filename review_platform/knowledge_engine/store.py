"""结构化规范依据与期刊配置加载器（不依赖向量库、禁止 AI 编造依据）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from review_platform.models import JournalProfile, KnowledgeClause

DEFAULT_PACK_DIR = Path(__file__).resolve().parent.parent / "knowledge_packs" / "medical_cn_v1"


class KnowledgeEngine:
    """管理结构化规范条款与期刊配置。"""

    def __init__(self, pack_dir: Optional[Path] = None) -> None:
        self.pack_dir = Path(pack_dir) if pack_dir else DEFAULT_PACK_DIR
        self._clauses: dict[str, KnowledgeClause] = {}
        self._journals: dict[str, JournalProfile] = {}
        self._load()

    def _load(self) -> None:
        clauses_path = self.pack_dir / "clauses.json"
        journals_path = self.pack_dir / "journals.json"

        raw_clauses = json.loads(clauses_path.read_text(encoding="utf-8"))
        for item in raw_clauses:
            clause = KnowledgeClause.model_validate(item)
            self._clauses[clause.id] = clause

        raw_journals = json.loads(journals_path.read_text(encoding="utf-8"))
        for item in raw_journals:
            journal = JournalProfile.model_validate(item)
            self._journals[journal.id] = journal

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
