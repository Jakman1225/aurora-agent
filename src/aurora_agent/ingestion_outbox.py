from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .canonical import canonical_bytes
from .exceptions import LifecycleError
from .ingestion_constants import (
    LOCAL_RUN_FINALIZE_QUEUED,
    LOCAL_RUN_OPEN,
    STATE_ACKNOWLEDGED,
    STATE_CONFLICT,
    STATE_PENDING,
    STATE_REJECTED,
    STATE_SUBMITTING,
)

_SCHEMA = "aurora-agent-ingestion-outbox-v0.1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class OutboxConflict(LifecycleError):
    pass


@dataclass(frozen=True)
class LocalRun:
    run_id: str
    capture_mode: str
    next_sequence: int
    last_event_id: Optional[str]
    state: str


@dataclass(frozen=True)
class OutboxItem:
    id: int
    request_key: str
    run_id: str
    ordinal: int
    method: str
    endpoint: str
    idempotency_key: str
    request_bytes: bytes
    state: str
    attempts: int
    response_status: Optional[int]
    response_bytes: Optional[bytes]
    error_code: Optional[str]


class IngestionOutbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ingestion_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id TEXT PRIMARY KEY,
                    capture_mode TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL,
                    last_event_id TEXT,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingestion_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_key TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_bytes BLOB NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    response_status INTEGER,
                    response_bytes BLOB,
                    error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(run_id, ordinal),
                    FOREIGN KEY(run_id) REFERENCES ingestion_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_ingestion_outbox_state_id
                    ON ingestion_outbox(state, id);
                """
            )
            row = conn.execute(
                "SELECT value FROM ingestion_meta WHERE key='schema'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO ingestion_meta(key,value) VALUES('schema',?)",
                    (_SCHEMA,),
                )
            elif row["value"] != _SCHEMA:
                raise LifecycleError(
                    f"ingestion outbox schema mismatch: expected {_SCHEMA}, got {row['value']}"
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _item(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            id=int(row["id"]),
            request_key=str(row["request_key"]),
            run_id=str(row["run_id"]),
            ordinal=int(row["ordinal"]),
            method=str(row["method"]),
            endpoint=str(row["endpoint"]),
            idempotency_key=str(row["idempotency_key"]),
            request_bytes=bytes(row["request_bytes"]),
            state=str(row["state"]),
            attempts=int(row["attempts"]),
            response_status=(
                int(row["response_status"])
                if row["response_status"] is not None
                else None
            ),
            response_bytes=(
                bytes(row["response_bytes"])
                if row["response_bytes"] is not None
                else None
            ),
            error_code=row["error_code"],
        )

    def create_run(
        self,
        *,
        run_id: str,
        capture_mode: str,
        request_key: str,
        endpoint: str,
        idempotency_key: str,
        body: dict,
    ) -> LocalRun:
        raw = canonical_bytes(body)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing is not None:
                queued = conn.execute(
                    "SELECT request_bytes FROM ingestion_outbox WHERE request_key=?",
                    (request_key,),
                ).fetchone()
                if queued is None or bytes(queued["request_bytes"]) != raw:
                    raise OutboxConflict("run_id already exists with different request bytes")
                conn.commit()
                return LocalRun(
                    run_id=run_id,
                    capture_mode=str(existing["capture_mode"]),
                    next_sequence=int(existing["next_sequence"]),
                    last_event_id=existing["last_event_id"],
                    state=str(existing["state"]),
                )
            conn.execute(
                "INSERT INTO ingestion_runs VALUES(?,?,?,?,?,?,?)",
                (run_id, capture_mode, 0, None, LOCAL_RUN_OPEN, now, now),
            )
            conn.execute(
                """
                INSERT INTO ingestion_outbox(
                    request_key,run_id,ordinal,method,endpoint,idempotency_key,
                    request_bytes,state,attempts,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,0,?,?)
                """,
                (
                    request_key,
                    run_id,
                    0,
                    "POST",
                    endpoint,
                    idempotency_key,
                    raw,
                    STATE_PENDING,
                    now,
                    now,
                ),
            )
            conn.commit()
            return LocalRun(run_id, capture_mode, 0, None, LOCAL_RUN_OPEN)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def run(self, run_id: str) -> LocalRun:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise LifecycleError(f"unknown ingestion run: {run_id}")
            return LocalRun(
                run_id=run_id,
                capture_mode=str(row["capture_mode"]),
                next_sequence=int(row["next_sequence"]),
                last_event_id=row["last_event_id"],
                state=str(row["state"]),
            )
        finally:
            conn.close()

    def get_item(self, request_key: str) -> Optional[OutboxItem]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE request_key=?",
                (request_key,),
            ).fetchone()
            return self._item(row) if row is not None else None
        finally:
            conn.close()

    def requeue_exact(self, request_key: str) -> OutboxItem:
        """Requeue the exact persisted request bytes for evidence-only replay.

        This never reconstructs or mutates the request and therefore cannot
        change the idempotency binding. It is intentionally restricted to
        requests that were previously acknowledged.
        """

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE request_key=?",
                (request_key,),
            ).fetchone()
            if row is None:
                raise LifecycleError(f"unknown outbox request: {request_key}")
            if str(row["state"]) != STATE_ACKNOWLEDGED:
                raise LifecycleError(
                    "only an acknowledged request may be requeued for exact replay"
                )
            conn.execute(
                """
                UPDATE ingestion_outbox
                   SET state=?, response_status=NULL, response_bytes=NULL,
                       error_code=NULL, updated_at=?
                 WHERE id=? AND state=?
                """,
                (STATE_PENDING, _utc_now(), row["id"], STATE_ACKNOWLEDGED),
            )
            updated = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE id=?", (row["id"],)
            ).fetchone()
            conn.commit()
            return self._item(updated)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def append_event(
        self,
        *,
        run_id: str,
        event_id: str,
        request_key: str,
        endpoint: str,
        idempotency_key: str,
        body: dict,
    ) -> OutboxItem:
        raw = canonical_bytes(body)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise LifecycleError(f"unknown ingestion run: {run_id}")
            if run["state"] != LOCAL_RUN_OPEN:
                raise LifecycleError("ingestion run is not accepting new local events")
            sequence = int(run["next_sequence"])
            if int(body["sequence"]) != sequence:
                raise LifecycleError(
                    f"event sequence mismatch: expected {sequence}, got {body['sequence']}"
                )
            ordinal = sequence + 1
            existing = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing is not None:
                if bytes(existing["request_bytes"]) != raw:
                    raise OutboxConflict("request_key already bound to different bytes")
                conn.commit()
                return self._item(existing)
            conn.execute(
                """
                INSERT INTO ingestion_outbox(
                    request_key,run_id,ordinal,method,endpoint,idempotency_key,
                    request_bytes,state,attempts,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,0,?,?)
                """,
                (
                    request_key,
                    run_id,
                    ordinal,
                    "POST",
                    endpoint,
                    idempotency_key,
                    raw,
                    STATE_PENDING,
                    now,
                    now,
                ),
            )
            item_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                """
                UPDATE ingestion_runs
                   SET next_sequence=?, last_event_id=?, updated_at=?
                 WHERE run_id=?
                """,
                (sequence + 1, event_id, now, run_id),
            )
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE id=?", (item_id,)
            ).fetchone()
            conn.commit()
            return self._item(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def queue_finalize(
        self,
        *,
        run_id: str,
        request_key: str,
        endpoint: str,
        idempotency_key: str,
        body: dict,
    ) -> OutboxItem:
        raw = canonical_bytes(body)
        now = _utc_now()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM ingestion_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise LifecycleError(f"unknown ingestion run: {run_id}")
            existing = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing is not None:
                if bytes(existing["request_bytes"]) != raw:
                    raise OutboxConflict("finalize key already bound to different bytes")
                conn.commit()
                return self._item(existing)
            if run["state"] != LOCAL_RUN_OPEN:
                raise LifecycleError("finalization is already queued")
            ordinal = int(run["next_sequence"]) + 1
            conn.execute(
                """
                INSERT INTO ingestion_outbox(
                    request_key,run_id,ordinal,method,endpoint,idempotency_key,
                    request_bytes,state,attempts,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,0,?,?)
                """,
                (
                    request_key,
                    run_id,
                    ordinal,
                    "POST",
                    endpoint,
                    idempotency_key,
                    raw,
                    STATE_PENDING,
                    now,
                    now,
                ),
            )
            item_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute(
                "UPDATE ingestion_runs SET state=?, updated_at=? WHERE run_id=?",
                (LOCAL_RUN_FINALIZE_QUEUED, now, run_id),
            )
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE id=?", (item_id,)
            ).fetchone()
            conn.commit()
            return self._item(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def recover_submitting(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE ingestion_outbox SET state=?, error_code=?, updated_at=? WHERE state=?",
                (STATE_PENDING, "RECOVERED_AFTER_CRASH", _utc_now(), STATE_SUBMITTING),
            )
            conn.commit()
            return int(cur.rowcount)
        finally:
            conn.close()

    def claim_next(self) -> Optional[OutboxItem]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE state=? ORDER BY id LIMIT 1",
                (STATE_PENDING,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            conn.execute(
                "UPDATE ingestion_outbox SET state=?, attempts=attempts+1, updated_at=? WHERE id=? AND state=?",
                (STATE_SUBMITTING, _utc_now(), row["id"], STATE_PENDING),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
            if changed != 1:
                conn.commit()
                return None
            claimed = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE id=?", (row["id"],)
            ).fetchone()
            conn.commit()
            return self._item(claimed)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete(
        self,
        item_id: int,
        *,
        state: str,
        status: Optional[int],
        response_bytes: Optional[bytes],
        error_code: Optional[str],
    ) -> OutboxItem:
        if state not in {
            STATE_PENDING,
            STATE_ACKNOWLEDGED,
            STATE_CONFLICT,
            STATE_REJECTED,
        }:
            raise ValueError("unsupported completion state")
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE ingestion_outbox
                   SET state=?, response_status=?, response_bytes=?, error_code=?, updated_at=?
                 WHERE id=? AND state=?
                """,
                (
                    state,
                    status,
                    response_bytes,
                    error_code,
                    _utc_now(),
                    item_id,
                    STATE_SUBMITTING,
                ),
            )
            row = conn.execute(
                "SELECT * FROM ingestion_outbox WHERE id=?", (item_id,)
            ).fetchone()
            conn.commit()
            if row is None:
                raise LifecycleError("outbox item not found")
            return self._item(row)
        finally:
            conn.close()

    def items(self, run_id: Optional[str] = None) -> list[OutboxItem]:
        conn = self._connect()
        try:
            if run_id is None:
                rows = conn.execute(
                    "SELECT * FROM ingestion_outbox ORDER BY id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ingestion_outbox WHERE run_id=? ORDER BY id",
                    (run_id,),
                ).fetchall()
            return [self._item(row) for row in rows]
        finally:
            conn.close()
