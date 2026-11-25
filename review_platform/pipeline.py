"""平台统一编排流水线：串联 Document -> Knowledge -> Registry -> Router -> Review -> Finding。"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Optional

from review_platform import PLATFORM_VERSION
from review_platform.document_engine import parse_docx_bytes
from review_platform.finding_engine import govern_findings
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import (
    DocumentUpsertRequest,
    KnowledgeClause,
    KnowledgeDocument,
    ReviewRun,
    RuleSpec,
)
from review_platform.review_engine import execute_plan
from review_platform.router import build_execution_plan
from review_platform.rule_registry import (
    RuleRegistry,
    maintainable_to_rule_spec,
    rule_spec_to_maintainable,
    validate_maintainable_rule,
)


def _bump_patch_version(ver: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)$", ver.strip())
    if not m:
        return "1.0.1"
    return f"{m.group(1)}.{m.group(2)}.{int(m.group(3)) + 1}"


class PlatformContext:
    """聚合平台无状态核心引擎，支持依赖注入与独立测试。"""

    def __init__(self, pack_dir: Optional[Path] = None) -> None:
        self.knowledge = KnowledgeEngine(pack_dir=pack_dir)
        self.registry = RuleRegistry(knowledge=self.knowledge)

    def hydrate_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        """把当前 RuleRegistry 中真实生效的规则关联回规范文档，确保文档与引擎一致。"""
        rules = self.registry.rules_for_basis_ids(doc.basis_ids or [doc.basis_id])
        return doc.model_copy(
            update={
                "rule_ids": [r.id for r in rules],
                "maintainable_rules": [rule_spec_to_maintainable(r) for r in rules],
            }
        )

    def list_documents(self) -> list[KnowledgeDocument]:
        return [self.hydrate_document(d) for d in self.knowledge.list_documents()]

    def get_document(self, doc_id: str) -> KnowledgeDocument:
        return self.hydrate_document(self.knowledge.get_document(doc_id))

    def upsert_knowledge_document(self, req: DocumentUpsertRequest) -> KnowledgeDocument:
        """验证正反例后创建或更新规范文档，并原子刷新 KnowledgeEngine 与 RuleRegistry。"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        is_update = bool(req.doc_id)

        if is_update:
            existing = self.knowledge.get_document(req.doc_id or "")
            doc_id = existing.doc_id
            basis_id = existing.basis_id
            basis_ids = list(existing.basis_ids or [basis_id])
            is_builtin = existing.is_builtin
            supports_rule_editing = existing.supports_rule_editing
            new_version = _bump_patch_version(existing.version)
            if not supports_rule_editing and req.rules:
                # 仅数学/结构 Python 验证器不可在线改写逻辑，保留原规则，允许维护文档正文
                pass
        else:
            slug = re.sub(r"[^A-Za-z0-9]+", "-", req.title).strip("-").upper()
            if not slug:
                slug = hashlib.sha256(req.title.encode("utf-8")).hexdigest()[:8].upper()
            doc_id = f"DOC-CUSTOM-{slug[:24]}"
            while any(d.doc_id == doc_id for d in self.knowledge.list_documents()):
                doc_id = f"{doc_id}-{hashlib.sha256(now.encode()).hexdigest()[:4].upper()}"
            basis_id = f"STD-CUSTOM-{doc_id.removeprefix('DOC-CUSTOM-')}"
            basis_ids = [basis_id]
            is_builtin = False
            supports_rule_editing = True
            new_version = "1.0.0"

        # 先做全量规则正反例与冲突校验，任何一条不通过即中断并保持原注册表干净
        seen_ids: set[str] = set()
        new_specs: list[RuleSpec] = []
        if supports_rule_editing:
            for item in req.rules:
                if item.rule_id in seen_ids:
                    raise ValueError(f"文档内规则 ID 重复: {item.rule_id}")
                seen_ids.add(item.rule_id)
                if self.registry.has(item.rule_id):
                    old_rule = self.registry.get(item.rule_id)
                    if old_rule.basis_id != basis_id:
                        raise ValueError(
                            f"规则 ID {item.rule_id} 已被其他规范依据 {old_rule.basis_id} 占用"
                        )
                validate_maintainable_rule(item)
                category = "terminology"
                if basis_id == "STD-GBT15835-2011":
                    category = "numbers"
                elif basis_id == "STD-GB3100-3102":
                    category = "units"
                elif basis_id == "STD-GBT15834-2011":
                    category = "punct"
                elif basis_id == "STD-GBT7714-2025":
                    category = "references"
                elif basis_id == "STD-GB3358-STATS":
                    category = "stats"
                new_specs.append(
                    maintainable_to_rule_spec(
                        item,
                        basis_id=basis_id,
                        scope=req.scope,
                        category=category,
                        version=new_version,
                    )
                )

        # 更新结构化知识条款
        existing_clause = self.knowledge.get_clause(basis_id)
        clause = KnowledgeClause(
            id=basis_id,
            source=req.source.strip(),
            edition=req.edition.strip(),
            clause=existing_clause.clause if existing_clause else req.title.strip(),
            scope=req.scope,
            summary=req.summary.strip(),
            effective_date=existing_clause.effective_date if existing_clause else now[:10],
            license_note=existing_clause.license_note if existing_clause else "编辑部维护规范文档",
        )
        self.knowledge.register_clause(clause)

        if supports_rule_editing:
            self.registry.replace_rules_for_basis(basis_id, new_specs)

        doc = KnowledgeDocument(
            doc_id=doc_id,
            basis_id=basis_id,
            basis_ids=basis_ids,
            title=req.title.strip(),
            source=req.source.strip(),
            edition=req.edition.strip(),
            scope=req.scope,
            effective_date=clause.effective_date,
            summary=req.summary.strip(),
            body_markdown=req.body_markdown,
            is_builtin=is_builtin,
            supports_rule_editing=supports_rule_editing,
            version=new_version,
            updated_at=now,
        )
        self.knowledge.upsert_document(doc)
        return self.hydrate_document(doc)

    def load_persisted_documents(self, raw_payloads: list[str]) -> None:
        """启动时从 SQLite 恢复用户维护过的规范文档与规则。"""
        for payload in raw_payloads:
            doc = KnowledgeDocument.model_validate_json(payload)
            clause = KnowledgeClause(
                id=doc.basis_id,
                source=doc.source,
                edition=doc.edition,
                clause=doc.title,
                scope=doc.scope,
                summary=doc.summary,
                effective_date=doc.effective_date,
                license_note="编辑部维护规范文档",
            )
            self.knowledge.register_clause(clause)
            if doc.supports_rule_editing:
                specs: list[RuleSpec] = []
                for item in doc.maintainable_rules:
                    validate_maintainable_rule(item)
                    specs.append(
                        maintainable_to_rule_spec(
                            item,
                            basis_id=doc.basis_id,
                            scope=doc.scope,
                            category="terminology",
                            version=doc.version,
                        )
                    )
                self.registry.replace_rules_for_basis(doc.basis_id, specs)
            self.knowledge.upsert_document(doc)

    def run_review(
        self,
        raw_docx: bytes,
        filename: str = "manuscript.docx",
        journal_id: str = "general-medical-cn",
        fail_nodes: Optional[set[str]] = None,
    ) -> ReviewRun:
        doc_ir = parse_docx_bytes(raw_docx, filename=filename)
        journal = self.knowledge.get_journal(journal_id)
        plan = build_execution_plan(doc_ir, journal, self.registry)
        final_plan, facts, raw_findings, has_failure = execute_plan(
            doc_ir,
            journal,
            plan,
            self.registry,
            self.knowledge,
            fail_nodes=fail_nodes,
        )
        verified_findings, rejected = govern_findings(doc_ir, self.knowledge, raw_findings)

        run_seed = f"{doc_ir.document_id}:{journal.id}:{journal.version}:{final_plan.rule_snapshot_hash}:{PLATFORM_VERSION}"
        run_id = f"run-{hashlib.sha256(run_seed.encode('utf-8')).hexdigest()[:14]}"

        if has_failure:
            status = "complete_with_limitations"
        elif doc_ir.limitations:
            status = "complete_with_limitations"
        else:
            status = "completed"

        return ReviewRun(
            run_id=run_id,
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            engine_version=PLATFORM_VERSION,
            status=status,
            document=doc_ir,
            journal=journal,
            plan=final_plan,
            facts=facts,
            findings=verified_findings,
            rejected_candidates=rejected,
            decisions=[],
        )
