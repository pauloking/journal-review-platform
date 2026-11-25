"""FastAPI 平台统一服务接口（Review Platform API）。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from review_platform import PLATFORM_VERSION
from review_platform.document_engine import DocumentParseError
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
from review_platform.models import DecisionRequest, DocumentUpsertRequest, KnowledgeDocument, ReviewRun
from review_platform.pipeline import PlatformContext

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DB_PATH = Path(os.environ.get("REVIEW_DB", "var/review.sqlite3"))


def create_app(db_path: Optional[str | Path] = None) -> FastAPI:
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

    app.state.ctx = ctx
    app.state.store = store

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": PLATFORM_VERSION,
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
        }

    @app.get("/registry")
    def get_registry() -> dict:
        return {
            "version": PLATFORM_VERSION,
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
            "journals": [j.model_dump(mode="json") for j in ctx.knowledge.list_journals()],
            "clauses": [c.model_dump(mode="json") for c in ctx.knowledge.list_clauses()],
            "documents": [d.model_dump(mode="json") for d in ctx.list_documents()],
            "rules": [r.model_dump(mode="json") for r in ctx.registry.list_rules()],
        }

    @app.get("/knowledge/documents")
    def list_knowledge_documents() -> dict:
        return {
            "rule_snapshot_hash": ctx.registry.snapshot_hash,
            "documents": [d.model_dump(mode="json") for d in ctx.list_documents()],
        }

    @app.get("/knowledge/documents/{doc_id}", response_model=KnowledgeDocument)
    def get_knowledge_document(doc_id: str) -> KnowledgeDocument:
        try:
            return ctx.get_document(doc_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/knowledge/documents/{doc_id}/export")
    def export_knowledge_document(doc_id: str) -> PlainTextResponse:
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

    @app.post("/knowledge/documents", response_model=KnowledgeDocument)
    def create_knowledge_document(req: DocumentUpsertRequest) -> KnowledgeDocument:
        if req.doc_id:
            req = req.model_copy(update={"doc_id": None})
        try:
            doc = ctx.upsert_knowledge_document(req)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_knowledge_document(doc.doc_id, doc.model_dump_json(), doc.updated_at)
        return doc

    @app.put("/knowledge/documents/{doc_id}", response_model=KnowledgeDocument)
    def update_knowledge_document(doc_id: str, req: DocumentUpsertRequest) -> KnowledgeDocument:
        req = req.model_copy(update={"doc_id": doc_id})
        try:
            doc = ctx.upsert_knowledge_document(req)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store.save_knowledge_document(doc.doc_id, doc.model_dump_json(), doc.updated_at)
        return doc

    @app.post("/reviews", response_model=ReviewRun)
    async def create_review(
        file: UploadFile = File(...),
        journal: str = Query("general-medical-cn"),
    ) -> ReviewRun:
        raw_bytes = await file.read()
        try:
            ctx.knowledge.get_journal(journal)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            run = ctx.run_review(
                raw_docx=raw_bytes,
                filename=file.filename or "manuscript.docx",
                journal_id=journal,
            )
        except DocumentParseError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return store.save_run(run)

    @app.get("/reviews")
    def list_reviews(limit: int = Query(50, ge=1, le=200)) -> dict:
        return {"runs": store.list_runs(limit=limit)}

    @app.get("/reviews/{run_id}", response_model=ReviewRun)
    def get_review(run_id: str) -> ReviewRun:
        try:
            return store.get_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/reviews/{run_id}/decisions")
    def submit_decision(run_id: str, req: DecisionRequest) -> dict:
        try:
            dec = store.submit_decision(run_id, req)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"decision": dec.model_dump(mode="json")}

    @app.get("/reviews/{run_id}/export")
    def export_report(
        run_id: str,
        format: str = Query("json"),
    ) -> Response:
        try:
            run = store.get_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        fmt = format.lower()
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
        raise HTTPException(status_code=422, detail=f"不支持的导出格式: {format}")

    @app.post("/reviews/{run_id}/export/docx")
    async def export_word_comments(
        run_id: str,
        file: UploadFile = File(...),
    ) -> Response:
        try:
            run = store.get_run(run_id)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        raw_docx = await file.read()
        try:
            annotated_bytes, count = export_annotated_docx(raw_docx, run)
        except WordExportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        fname = quote(f"原生批注_{run_id}.docx")
        return Response(
            content=annotated_bytes,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{fname}",
                "X-Exported-Comments": str(count),
            },
        )

    @app.get("/evaluation/feedback")
    def get_feedback_summary() -> dict:
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
