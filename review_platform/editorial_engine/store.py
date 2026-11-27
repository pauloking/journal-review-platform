"""SQLite 持久化存储：审校运行快照、乐观并发裁决事件与评估反馈队列。"""

from __future__ import annotations

import contextlib
import datetime
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from review_platform.models import DecisionRequest, EditorialDecision, ReviewRun


class NotFoundError(LookupError):
    """目标运行记录或意见不存在。"""


class ConflictError(RuntimeError):
    """乐观并发修订号冲突（409）。"""


class ValidationError(ValueError):
    """业务请求参数校验失败（422）。"""


SCHEMA = """
CREATE TABLE IF NOT EXISTS review_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    document_id TEXT NOT NULL,
    journal_id TEXT NOT NULL,
    journal_version TEXT NOT NULL,
    rule_snapshot_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS editorial_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    action TEXT NOT NULL,
    editor TEXT NOT NULL,
    reason TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, finding_id, revision)
);

CREATE TABLE IF NOT EXISTS evaluation_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    category TEXT NOT NULL,
    action TEXT NOT NULL,
    editor TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_documents (
    doc_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_run ON editorial_decisions(run_id, finding_id);
CREATE INDEX IF NOT EXISTS idx_feedback_rule ON evaluation_feedback(rule_id);
"""


class ReviewStore:
    """SQLite 存储管理器：保证重复提交同一输入不清空已有裁决历史。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @staticmethod
    def _has_failed_tasks(run: ReviewRun) -> bool:
        return run.status == "failed" or any(t.status == "failed" for t in run.plan.tasks)

    def save_run(self, run: ReviewRun) -> ReviewRun:
        """保存运行快照：成功记录保持幂等复用且绝不覆盖裁决；若前次存在失败节点则保留原失败记录并分配重试槽位。"""
        base_id = run.run_id
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            candidate_id = base_id
            prev_id: str | None = None
            attempt = 1
            while True:
                row = conn.execute(
                    "SELECT payload_json FROM review_runs WHERE run_id = ?",
                    (candidate_id,),
                ).fetchone()
                if row is None:
                    to_save = run.model_copy(
                        update={
                            "run_id": candidate_id,
                            "retry_of": prev_id,
                            "decisions": [],
                        }
                    )
                    payload = to_save.model_dump_json()
                    conn.execute(
                        """
                        INSERT INTO review_runs (
                            run_id, created_at, document_id, journal_id,
                            journal_version, rule_snapshot_hash, status, payload_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            to_save.run_id,
                            to_save.created_at,
                            to_save.document.document_id,
                            to_save.journal.id,
                            to_save.journal.version,
                            to_save.plan.rule_snapshot_hash,
                            to_save.status,
                            payload,
                        ),
                    )
                    conn.commit()
                    break

                existing = ReviewRun.model_validate_json(row["payload_json"])
                if not self._has_failed_tasks(existing):
                    conn.rollback()
                    return self.get_run(candidate_id)

                prev_id = candidate_id
                attempt += 1
                candidate_id = f"{base_id}-r{attempt}"

        return self.get_run(candidate_id)

    def get_run(self, run_id: str) -> ReviewRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM review_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"未找到审校运行记录: {run_id}")
            decisions_rows = conn.execute(
                """
                SELECT id, run_id, finding_id, action, editor, reason, revision, created_at
                FROM editorial_decisions
                WHERE run_id = ?
                ORDER BY id ASC
                """,
                (run_id,),
            ).fetchall()

        run = ReviewRun.model_validate_json(row["payload_json"])
        decisions = [EditorialDecision(**dict(r)) for r in decisions_rows]
        return run.model_copy(update={"decisions": decisions})

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, created_at, document_id, journal_id, journal_version, status
                FROM review_runs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def submit_decision(self, run_id: str, req: DecisionRequest) -> EditorialDecision:
        """追加提交编辑裁决，通过 expected_revision 实现乐观并发控制，并同步评估反馈队列。"""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json FROM review_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"未找到审校运行记录: {run_id}")

            run = ReviewRun.model_validate_json(row["payload_json"])
            finding = next((f for f in run.findings if f.id == req.finding_id), None)
            if finding is None:
                raise NotFoundError(f"审校运行 {run_id} 中不存在 Finding: {req.finding_id}")

            latest_row = conn.execute(
                """
                SELECT MAX(revision) AS max_rev
                FROM editorial_decisions
                WHERE run_id = ? AND finding_id = ?
                """,
                (run_id, req.finding_id),
            ).fetchone()
            current_rev = int(latest_row["max_rev"] or 0)
            if req.expected_revision != current_rev:
                raise ConflictError(
                    f"裁决修订号冲突: 预期 {req.expected_revision}，当前最新为 {current_rev}，请刷新后重试"
                )

            new_rev = current_rev + 1
            cur = conn.execute(
                """
                INSERT INTO editorial_decisions (
                    run_id, finding_id, action, editor, reason, revision, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    req.finding_id,
                    req.action,
                    req.editor.strip(),
                    req.reason.strip(),
                    new_rev,
                    now,
                ),
            )
            decision_id = int(cur.lastrowid)

            # 反馈仅写入评估队列，绝不直接改写正式规则
            conn.execute(
                """
                INSERT INTO evaluation_feedback (
                    run_id, finding_id, rule_id, category, action, editor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    req.finding_id,
                    finding.rule_id,
                    finding.category,
                    req.action,
                    req.editor.strip(),
                    req.reason.strip(),
                    now,
                ),
            )
            conn.commit()

        return EditorialDecision(
            id=decision_id,
            run_id=run_id,
            finding_id=req.finding_id,
            action=req.action,
            editor=req.editor.strip(),
            reason=req.reason.strip(),
            revision=new_rev,
            created_at=now,
        )

    def latest_decisions_map(self, run: ReviewRun) -> dict[str, EditorialDecision]:
        latest: dict[str, EditorialDecision] = {}
        for dec in run.decisions:
            prev = latest.get(dec.finding_id)
            if prev is None or dec.revision > prev.revision:
                latest[dec.finding_id] = dec
        return latest

    def list_feedback(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluation_feedback ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_knowledge_document(self, doc_id: str, payload_json: str, updated_at: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_documents (doc_id, payload_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (doc_id, payload_json, updated_at),
            )
            conn.commit()

    def list_knowledge_documents(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM knowledge_documents ORDER BY updated_at ASC"
            ).fetchall()
        return [str(r["payload_json"]) for r in rows]
