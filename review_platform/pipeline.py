"""平台统一编排流水线：串联 Document -> Knowledge -> Registry -> Router -> Review -> Finding。"""

from __future__ import annotations

import datetime
import hashlib
from pathlib import Path
from typing import Optional

from review_platform import PLATFORM_VERSION
from review_platform.document_engine import parse_docx_bytes
from review_platform.finding_engine import govern_findings
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import ReviewRun
from review_platform.review_engine import execute_plan
from review_platform.router import build_execution_plan
from review_platform.rule_registry import RuleRegistry


class PlatformContext:
    """聚合平台无状态核心引擎，支持依赖注入与独立测试。"""

    def __init__(self, pack_dir: Optional[Path] = None) -> None:
        self.knowledge = KnowledgeEngine(pack_dir=pack_dir)
        self.registry = RuleRegistry(knowledge=self.knowledge)

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

        # 确定性 run_id：同文档、同期刊版本、同规则快照产生同一 run_id
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
