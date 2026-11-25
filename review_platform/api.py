"""FastAPI 平台统一服务接口（含流式上传限额、线程池卸载、可选 Bearer RBAC 鉴权）。"""

from __future__ import annotations

import hmac
import json
import os
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from review_platform import PLATFORM_VERSION
from review_platform.document_engine import DocumentParseError
from review_platform.document_engine.parser import MAX_FILE_BYTES
from review_platform.editorial_engine import (
    ConflictError,
    NotFoundError,
    ReviewStore,
    WordExportError,
    build_summary_docx,
    export_annotated_docx,
    export_json,
    export_markdown,
)
from review_platform.evaluation_engine import summarize_feedback_stats
from review_platform.knowledge_engine import parse_rule_document_bytes
from review_platform.knowledge_engine.doc_parser import MAX_RULE_DOC_BYTES
from review_platform.models import (
    DecisionRequest,
    DocumentUpsertRequest,
    KnowledgeDocument,
    LLMConfig,
    LLMTestResult,
    ParsedRuleDocumentPreview,
    ReviewRun,
)
from review_platform.pipeline import PlatformContext
from review_platform.review_engine import get_llm_env_defaults_public, test_llm_connection

STATIC_DIR = Path(__file__).resolve().parent / "static"
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = Path(os.environ.get("REVIEW_DB") or str(PACKAGE_ROOT / "var" / "review.sqlite3"))

ROLE_LEVEL = {"viewer": 1, "editor": 2, "rule_admin": 3, "admin": 4}


async def _read_upload_limited(file: UploadFile, max_bytes: int, label: str) -> bytes:
    """流式分段读取上传体，避免恶意超大请求直接撑爆内存。"""
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label}大小超过上限（最大 {max_bytes // (1024 * 1024)} MiB）",
        )
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{label}大小超过上限（最大 {max_bytes // (1024 * 1024)} MiB）",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _load_token_map(explicit_tokens: Optional[dict[str, tuple[str, str]]]) -> dict[str, tuple[str, str]]:
    if explicit_tokens is not None:
        return dict(explicit_tokens)
    raw_json = os.environ.get("REVIEW_API_TOKENS", "").strip()
    if raw_json:
        parsed = json.loads(raw_json)
        return {
            tok: (str(info.get("user", "user")), str(info.get("role", "editor")))
            for tok, info in parsed.items()
        }
    single = os.environ.get("REVIEW_API_TOKEN", "").strip()
    if single:
        return {single: ("admin", "admin")}
    return {}


def create_app(
    db_path: Optional[str | Path] = None,
    api_tokens: Optional[dict[str, tuple[str, str]]] = None,
    llm_client_factory: Optional[Callable[[float], httpx.Client]] = None,
) -> FastAPI:
    app = FastAPI(
        title="中文科技期刊智能审校能力平台",
        version=PLATFORM_VERSION,
        description="可扩展的审校能力平台（Document IR · Knowledge Engine · Router DAG · Finding Governance · Editorial Workbench）",
    )
    ctx = PlatformContext()
    store = ReviewStore(db_path or DEFAULT_DB_PATH)
    persisted_docs = store.list_knowledge_documents()
    if persisted_docs:
        ctx.load_persisted_documents(persisted_docs)

    token_map = _load_token_map(api_tokens)
    app.state.ctx = ctx
    app.state.store = store
    app.state.auth_enabled = bool(token_map)

    def require_role(min_role: str):
        def _dep(request: Request) -> Optional[tuple[str, str]]:
            if not token_map:
                return None
            auth_hdr = request.headers.get("Authorization", "")
            if not auth_hdr.startswith("Bearer "):
                raise HTTPException(status_code=401, detail="缺少 Bearer 认证令牌")
            token = auth_hdr[7:].strip()
            matched: Optional[tuple[str, str]] = None
            for k, v in token_map.items():
                if hmac.compare_digest(token, k):
                    matched = v
                    break
            if matched is None:
                raise HTTPException(status_code=401, detail="认证令牌无效")
            user, role = matched
            if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL.get(min_role, 99):
                raise HTTPException(status_code=403, detail=f"当前角色 {role} 无权执行此操作（需 {min_role}）")
            return user, role

        return _dep

    @app.get("/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": PLATFORM_VERSION,
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
            "auth_enabled": bool(token_map),
        }

    @app.get("/registry")
    def get_registry(_auth=Depends(require_role("viewer"))) -> dict:
        return {
            "version": PLATFORM_VERSION,
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
            "journals": [j.model_dump(mode="json") for j in ctx.knowledge.list_journals()],
            "clauses": [c.model_dump(mode="json") for c in ctx.knowledge.list_clauses()],
            "documents": [d.model_dump(mode="json") for d in ctx.list_documents()],
            "rules": [r.model_dump(mode="json") for r in ctx.registry.list_rules()],
        }

    @app.get("/knowledge/documents")
    def list_knowledge_documents(_auth=Depends(require_role("viewer"))) -> dict:
        return {
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
            "documents": [d.model_dump(mode="json") for d in ctx.list_documents()],
        }

    @app.get("/knowledge/documents/{doc_id}", response_model=KnowledgeDocument)
    def get_knowledge_document(doc_id: str, _auth=Depends(require_role("viewer"))) -> KnowledgeDocument:
        try:
            return ctx.get_document(doc_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/knowledge/documents/{doc_id}/export")
    def export_knowledge_document(doc_id: str, _auth=Depends(require_role("viewer"))) -> PlainTextResponse:
        try:
            doc = ctx.get_document(doc_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        fname = quote(f"{doc.doc_id}.md")
        return PlainTextResponse(
            content=doc.body_markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
        )

    @app.post("/knowledge/documents/parse", response_model=ParsedRuleDocumentPreview)
    async def parse_uploaded_rule_document(
        file: UploadFile = File(...),
        _auth=Depends(require_role("rule_admin")),
    ) -> ParsedRuleDocumentPreview:
        raw_bytes = await _read_upload_limited(file, MAX_RULE_DOC_BYTES, "规则文档")
        try:
            return await run_in_threadpool(
                parse_rule_document_bytes, raw_bytes, file.filename or "rules.md"
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/knowledge/documents", response_model=KnowledgeDocument)
    def create_knowledge_document(
        req: DocumentUpsertRequest,
        _auth=Depends(require_role("rule_admin")),
    ) -> KnowledgeDocument:
        if req.doc_id:
            req = req.model_copy(update={"doc_id": None})
        try:
            doc = ctx.upsert_knowledge_document(req)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_knowledge_document(doc.doc_id, doc.model_dump_json(), doc.updated_at)
        return doc

    @app.put("/knowledge/documents/{doc_id}", response_model=KnowledgeDocument)
    def update_knowledge_document(
        doc_id: str,
        req: DocumentUpsertRequest,
        _auth=Depends(require_role("rule_admin")),
    ) -> KnowledgeDocument:
        req = req.model_copy(update={"doc_id": doc_id})
        try:
            doc = ctx.upsert_knowledge_document(req)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_knowledge_document(doc.doc_id, doc.model_dump_json(), doc.updated_at)
        return doc

    @app.get("/llm/defaults")
    def get_llm_defaults(_auth=Depends(require_role("viewer"))) -> dict:
        ai_rules = [
            {"id": r.id, "title": r.title, "category": r.category}
            for r in ctx.registry.list_rules()
            if r.executor_type == "ai_candidate"
        ]
        return {
            **get_llm_env_defaults_public(),
            "ai_rules": ai_rules,
        }

    @app.post("/llm/test", response_model=LLMTestResult)
    async def test_llm_endpoint(
        req: LLMConfig,
        _auth=Depends(require_role("editor")),
    ) -> LLMTestResult:
        return await run_in_threadpool(
            test_llm_connection, req, client_factory=llm_client_factory
        )

    @app.post("/reviews", response_model=ReviewRun)
    async def create_review(
        file: UploadFile = File(...),
        journal: str = Query("general-medical-cn"),
        llm_config: Optional[str] = Form(None),
        _auth=Depends(require_role("editor")),
    ) -> ReviewRun:
        raw_bytes = await _read_upload_limited(file, MAX_FILE_BYTES, "DOCX 稿件")
        try:
            ctx.knowledge.get_journal(journal)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        parsed_llm: Optional[LLMConfig] = None
        if llm_config and llm_config.strip():
            try:
                parsed_llm = LLMConfig.model_validate_json(llm_config)
            except Exception as exc:
                raise HTTPException(status_code=422, detail=f"大模型配置 JSON 无效: {exc}") from exc

        try:
            run = await run_in_threadpool(
                ctx.run_review,
                raw_bytes,
                file.filename or "manuscript.docx",
                journal,
                None,
                parsed_llm,
                llm_client_factory,
            )
        except DocumentParseError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return await run_in_threadpool(store.save_run, run)

    @app.get("/reviews")
    def list_reviews(
        limit: int = Query(50, ge=1, le=200),
        _auth=Depends(require_role("viewer")),
    ) -> dict:
        return {"runs": store.list_runs(limit=limit)}

    @app.get("/reviews/{run_id}", response_model=ReviewRun)
    def get_review(run_id: str, _auth=Depends(require_role("viewer"))) -> ReviewRun:
        try:
            return store.get_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/reviews/{run_id}/decisions")
    def submit_decision(
        run_id: str,
        req: DecisionRequest,
        auth=Depends(require_role("editor")),
    ) -> dict:
        effective_req = req
        if auth is not None:
            auth_user, _ = auth
            effective_req = req.model_copy(update={"editor": auth_user})
        try:
            dec = store.submit_decision(run_id, effective_req)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"decision": dec.model_dump(mode="json")}

    @app.get("/reviews/{run_id}/export")
    def export_report(
        run_id: str,
        export_format: str = Query("json", alias="format"),
        _auth=Depends(require_role("viewer")),
    ) -> Response:
        try:
            run = store.get_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        fmt = export_format.lower()
        if fmt == "json":
            return Response(
                content=export_json(run),
                media_type="application/json; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
            )
        if fmt == "markdown":
            return PlainTextResponse(
                content=export_markdown(run),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'},
            )
        if fmt == "summary_docx":
            doc_bytes = build_summary_docx(run)
            fname = quote(f"综合审评意见_{run_id}.docx")
            return Response(
                content=doc_bytes,
                media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
            )
        raise HTTPException(status_code=422, detail=f"不支持的导出格式: {export_format}")

    @app.post("/reviews/{run_id}/export/docx")
    async def export_word_comments(
        run_id: str,
        file: UploadFile = File(...),
        _auth=Depends(require_role("editor")),
    ) -> Response:
        try:
            run = await run_in_threadpool(store.get_run, run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        raw_docx = await _read_upload_limited(file, MAX_FILE_BYTES, "DOCX 稿件")
        try:
            annotated_bytes, count, skipped = await run_in_threadpool(
                export_annotated_docx, raw_docx, run, return_skipped=True
            )
        except WordExportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        fname = quote(f"原生批注_{run_id}.docx")
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{fname}",
            "X-Exported-Comments": str(count),
            "X-Skipped-Comments": str(len(skipped)),
        }
        return Response(
            content=annotated_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers=headers,
        )

    @app.get("/evaluation/feedback")
    def get_feedback_summary(_auth=Depends(require_role("viewer"))) -> dict:
        rows = store.list_feedback(limit=500)
        return {
            "summary": summarize_feedback_stats(rows),
            "recent": rows[:50],
        }

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        def workbench_index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app
