"""SQLite-backed task queue shared by the CLI, the orchestrator and the dashboard."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    repo         TEXT NOT NULL,
    text         TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'queued',  -- queued|running|awaiting_approval|approved|done|failed|cancelled
    stage        TEXT NOT NULL DEFAULT '',
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    error        TEXT NOT NULL DEFAULT '',
    result       TEXT,
    pr_url       TEXT NOT NULL DEFAULT '',
    created      REAL NOT NULL,
    updated      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status ON tasks(status, created);
"""
UPDATABLE = {"status", "stage", "error", "result", "pr_url"}


class Queue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()

    def submit(self, repo: str, text: str, max_attempts: int = 2, **options) -> str:
        tid = uuid.uuid4().hex[:8]
        now = time.time()
        with self._db() as db:
            db.execute("INSERT INTO tasks (id, repo, text, options, max_attempts, created, updated) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (tid, repo, text, json.dumps(options), max_attempts, now, now))
        return tid

    def claim(self) -> dict | None:
        """Atomically move the oldest queued task to running and return it."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM tasks WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                db.execute("COMMIT")
                return None
            db.execute("UPDATE tasks SET status='running', attempts=attempts+1, stage='', updated=? "
                       "WHERE id=?", (time.time(), row["id"]))
            db.execute("COMMIT")
        return self.get(row["id"])

    def update(self, tid: str, **fields) -> None:
        bad = set(fields) - UPDATABLE
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        if "result" in fields and not isinstance(fields["result"], (str, type(None))):
            fields["result"] = json.dumps(fields["result"], default=str)
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._db() as db:
            db.execute(f"UPDATE tasks SET {cols}, updated=? WHERE id=?",
                       (*fields.values(), time.time(), tid))

    def fail(self, tid: str, error: str) -> str:
        """Record a crash: requeue if attempts remain, else mark failed. Returns the new status."""
        task = self.get(tid)
        status = "queued" if task and task["attempts"] < task["max_attempts"] else "failed"
        self.update(tid, status=status, error=error[-4000:])
        return status

    def requeue_stale(self) -> int:
        """Tasks left 'running' by a dead orchestrator go back to the queue. The
        interrupted attempt is not counted: the task resumes from its checkpoints."""
        with self._db() as db:
            return db.execute("UPDATE tasks SET status='queued', attempts=MAX(attempts - 1, 0), "
                              "stage='interrupted', updated=? WHERE status='running'",
                              (time.time(),)).rowcount

    def transition(self, tid: str, frm: str, to: str, **fields) -> bool:
        """Atomically move a task from status `frm` to `to`; False if it was not in `frm`
        (e.g. a second click on Approve while the first is opening the PR)."""
        bad = set(fields) - UPDATABLE
        if bad:
            raise ValueError(f"cannot update {sorted(bad)}")
        sets = "".join(f", {k}=?" for k in fields)
        with self._db() as db:
            return db.execute(f"UPDATE tasks SET status=?{sets}, updated=? WHERE id=? AND status=?",
                              (to, *fields.values(), time.time(), tid, frm)).rowcount == 1

    def cancel(self, tid: str) -> bool:
        with self._db() as db:
            return db.execute("UPDATE tasks SET status='cancelled', updated=? WHERE id=? AND status='queued'",
                              (time.time(), tid)).rowcount == 1

    def retry(self, tid: str) -> bool:
        """Put a failed or cancelled task back in the queue with a fresh attempt count."""
        with self._db() as db:
            return db.execute("UPDATE tasks SET status='queued', attempts=0, error='', stage='', "
                              "updated=? WHERE id=? AND status IN ('failed', 'cancelled')",
                              (time.time(), tid)).rowcount == 1

    def find_issue(self, repo: str, number: int) -> dict | None:
        """Latest task created for GitHub issue repo#number, if any."""
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE repo=? AND json_extract(options, '$.issue')=? "
                             "ORDER BY created DESC LIMIT 1", (repo, number)).fetchone()
        return _row(row) if row else None

    def get(self, tid: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        return _row(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in rows]


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["options"] = json.loads(d["options"] or "{}")
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d
