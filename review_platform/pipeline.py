"""平台统一编排流水线：串联 Document -> Knowledge -> Registry -> Router -> Review -> Finding。"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from review_platform import PLATFORM_VERSION
from review_platform.document_engine import parse_docx_bytes
from review_platform.finding_engine import govern_findings
from review_platform.knowledge_engine import KnowledgeEngine
from review_platform.models import (
    DocumentUpsertRequest,
    ExecutionTraceStep,
    KnowledgeClause,
    KnowledgeDocument,
    LLMConfig,
    ReviewRun,
    RuleSpec,
)
from review_platform.review_engine import execute_plan, resolve_llm_config
from review_platform.router import build_execution_plan
from review_platform.rule_registry import (
    RuleRegistry,
    category_for_basis,
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
            base_doc_id = f"DOC-CUSTOM-{slug[:24]}"
            doc_id = base_doc_id
            suffix_n = 2
            existing_doc_ids = {d.doc_id for d in self.knowledge.list_documents()}
            while doc_id in existing_doc_ids:
                doc_id = f"{base_doc_id}-{suffix_n}"
                suffix_n += 1
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

                merged_item = item
                trusted_builtin_pattern = False
                if self.registry.has(item.rule_id):
                    old_rule = self.registry.get(item.rule_id)
                    if old_rule.basis_id != basis_id:
                        raise ValueError(
                            f"规则 ID {item.rule_id} 已被其他规范依据 {old_rule.basis_id} 占用"
                        )
                    # C1 无损往返：继承原注册规则高级参数（ref_only / whole_line / exclude_pattern / skip_if_year / skip_context / dedupe_key / auto）
                    preserved_adv = {
                        k: v
                        for k, v in (old_rule.params or {}).items()
                        if k not in {"pattern", "is_regex", "exception_after"}
                    }
                    preserved_adv["auto"] = old_rule.auto
                    preserved_adv.update(item.advanced_params or {})
                    merged_item = item.model_copy(
                        update={
                            "category": item.category or old_rule.category,
                            "advanced_params": preserved_adv,
                        }
                    )
                    if is_builtin and old_rule.params.get("pattern") == item.nonstandard:
                        trusted_builtin_pattern = True

                validate_maintainable_rule(merged_item, is_builtin_trusted=trusted_builtin_pattern)
                category = category_for_basis(basis_id, fallback=merged_item.category or "terminology")
                new_specs.append(
                    maintainable_to_rule_spec(
                        merged_item,
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
                    trusted = doc.is_builtin and self.registry.has(item.rule_id) and (
                        self.registry.get(item.rule_id).params.get("pattern") == item.nonstandard
                    )
                    validate_maintainable_rule(item, is_builtin_trusted=trusted)
                    cat = category_for_basis(doc.basis_id, fallback=item.category or "terminology")
                    specs.append(
                        maintainable_to_rule_spec(
                            item,
                            basis_id=doc.basis_id,
                            scope=doc.scope,
                            category=cat,
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
        llm_config: Optional[LLMConfig] = None,
        llm_client_factory: Optional[Callable[[float], httpx.Client]] = None,
    ) -> ReviewRun:
        trace: list[ExecutionTraceStep] = []
        resolved_llm = resolve_llm_config(llm_config)

        t0 = time.perf_counter()
        doc_ir = parse_docx_bytes(raw_docx, filename=filename)
        dt_parse = round((time.perf_counter() - t0) * 1000, 2)
        trace.append(
            ExecutionTraceStep(
                step_index=1,
                stage="document_parse",
                title="DOCX 安全解析 → Document IR",
                status="completed",
                duration_ms=dt_parse,
                summary=f"提取 {doc_ir.profile.total_blocks} 个文本块（{doc_ir.profile.char_count} 字），结构状态：{doc_ir.profile.structure_status}",
                details={
                    "document_id": doc_ir.document_id,
                    "sections": doc_ir.profile.sections_present,
                    "paragraph_count": doc_ir.profile.paragraph_count,
                    "table_cell_count": doc_ir.profile.table_cell_count,
                },
            )
        )

        t1 = time.perf_counter()
        journal = self.knowledge.get_journal(journal_id)
        active_docs = self.list_documents()
        dt_know = round((time.perf_counter() - t1) * 1000, 2)
        trace.append(
            ExecutionTraceStep(
                step_index=2,
                stage="knowledge_bind",
                title="装载规范文档与期刊配置",
                status="completed",
                duration_ms=dt_know,
                summary=f"期刊配置「{journal.name}」v{journal.version}，已装载 {len(active_docs)} 份规范文档（快照 {self.registry.snapshot_hash}）",
                details={
                    "journal_id": journal.id,
                    "enabled_scopes": journal.enabled_scopes,
                    "documents": [
                        {"doc_id": d.doc_id, "title": d.title, "rule_count": len(d.rule_ids)}
                        for d in active_docs
                    ],
                },
            )
        )

        t2 = time.perf_counter()
        plan = build_execution_plan(doc_ir, journal, self.registry, knowledge=self.knowledge)
        dt_router = round((time.perf_counter() - t2) * 1000, 2)
        ready_cnt = sum(1 for t in plan.tasks if t.status == "pending")
        skip_cnt = sum(1 for t in plan.tasks if t.status == "skipped")
        trace.append(
            ExecutionTraceStep(
                step_index=3,
                stage="router_plan",
                title="Router DAG 拓扑编排（零静默遗漏）",
                status="completed",
                duration_ms=dt_router,
                summary=f"共编排 {len(plan.tasks)} 个任务节点：待执行 {ready_cnt}，配置/适用性跳过 {skip_cnt}",
                details={"plan_hash": plan.plan_hash, "ready": ready_cnt, "skipped": skip_cnt},
            )
        )

        t3 = time.perf_counter()
        final_plan, facts, raw_findings, has_failure = execute_plan(
            doc_ir,
            journal,
            plan,
            self.registry,
            self.knowledge,
            fail_nodes=fail_nodes,
            llm_config=resolved_llm,
            llm_client_factory=llm_client_factory,
        )
        dt_exec = round((time.perf_counter() - t3) * 1000, 2)

        extract_task = next((t for t in final_plan.tasks if t.node_id == "extract.facts"), None)
        trace.append(
            ExecutionTraceStep(
                step_index=4,
                stage="fact_extract",
                title="FactLedger 事实台账抽取",
                status="failed" if (extract_task and extract_task.status == "failed") else "completed",
                node_id="extract.facts",
                duration_ms=dt_exec,
                summary=f"抽取显式事实 {len(facts)} 条（其中引号/示例豁免 {sum(1 for f in facts if f.status == 'excluded')} 条）",
                details={
                    "p_value_count": sum(1 for f in facts if f.kind == "p_value"),
                    "percentage_count": sum(1 for f in facts if f.kind == "percentage"),
                    "caption_count": sum(1 for f in facts if f.kind == "caption"),
                    "excluded_count": sum(1 for f in facts if f.status == "excluded"),
                },
            )
        )

        # 按规范文档汇总执行轨迹，直观呈现每份规范文档的实际检查情况
        for doc_obj in active_docs:
            doc_tasks = [t for t in final_plan.tasks if t.doc_id == doc_obj.doc_id]
            if not doc_tasks:
                continue
            exec_cnt = sum(1 for t in doc_tasks if t.status in {"passed", "findings"})
            chk_cnt = sum(t.checked_count for t in doc_tasks)
            exc_cnt = sum(t.excluded_count for t in doc_tasks)
            hit_cnt = sum(t.finding_count for t in doc_tasks)
            blocked_cnt = sum(1 for t in doc_tasks if t.status == "blocked")
            failed_cnt = sum(1 for t in doc_tasks if t.status == "failed")
            st = "failed" if failed_cnt else ("blocked" if blocked_cnt == len(doc_tasks) else ("skipped" if exec_cnt == 0 else "completed"))
            trace.append(
                ExecutionTraceStep(
                    step_index=len(trace) + 1,
                    stage="rule_execute",
                    title=f"执行规范文档：{doc_obj.title}",
                    status=st,
                    doc_id=doc_obj.doc_id,
                    summary=(
                        f"共 {len(doc_tasks)} 条规则（实际运行 {exec_cnt}），检查 {chk_cnt} 处，"
                        f"豁免 {exc_cnt} 处，命中 {hit_cnt} 条"
                    ),
                    details={
                        "rules": [
                            {
                                "rule_id": t.rule_id,
                                "status": t.status,
                                "checked": t.checked_count,
                                "excluded": t.excluded_count,
                                "findings": t.finding_count,
                                "reason": t.reason,
                            }
                            for t in doc_tasks
                        ]
                    },
                )
            )

        t4 = time.perf_counter()
        verified_findings, rejected = govern_findings(doc_ir, self.knowledge, raw_findings)
        dt_gate = round((time.perf_counter() - t4) * 1000, 2)
        trace.append(
            ExecutionTraceStep(
                step_index=len(trace) + 1,
                stage="finding_gate",
                title="FindingEngine 锚点与依据门禁",
                status="completed",
                duration_ms=dt_gate,
                summary=f"门禁核验通过 {len(verified_findings)} 条正式意见，拦截 {len(rejected)} 条不合格候选",
                details={"verified": len(verified_findings), "rejected": len(rejected)},
            )
        )

        # N4 修复：run_id 种子纳入全部影响输出的 LLM 配置维度（严格排除 api_key）
        if resolved_llm.enabled:
            llm_cfg_payload = {
                "enabled": True,
                "provider": resolved_llm.provider,
                "endpoint": resolved_llm.endpoint,
                "model": resolved_llm.model,
                "temperature": resolved_llm.temperature,
                "max_tokens": resolved_llm.max_tokens,
                "disable_thinking": resolved_llm.disable_thinking,
                "enabled_rules": sorted(resolved_llm.enabled_rules),
            }
            llm_tag = hashlib.sha256(
                json.dumps(llm_cfg_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:12]
        else:
            llm_tag = "off"

        ai_truncated = any("context_truncated_24k" in (t.reason or "") for t in final_plan.tasks)
        if ai_truncated:
            lim_msg = "AI 语义审校单次上下文仅覆盖前 24,000 字符，超出部分已截断、未纳入本次语义核查。"
            if lim_msg not in doc_ir.limitations:
                doc_ir = doc_ir.model_copy(update={"limitations": [*doc_ir.limitations, lim_msg]})

        run_seed = f"{doc_ir.document_id}:{journal.id}:{journal.version}:{final_plan.rule_snapshot_hash}:{llm_tag}:{PLATFORM_VERSION}"
        run_id = f"run-{hashlib.sha256(run_seed.encode('utf-8')).hexdigest()[:14]}"

        if has_failure or doc_ir.profile.structure_status != "reliable" or len(rejected) > 0 or ai_truncated:
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
            trace=trace,
        )
