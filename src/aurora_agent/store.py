from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from .canonical import canonical_bytes, commitment, strict_json_loads
from .exceptions import LifecycleError
from .model import Phase

_STORE_SCHEMA = "aurora.agent-sdk-store.v0.1"


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sdk_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    boundary_json TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    authorization_required INTEGER NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS grants (
                    grant_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    grant_type TEXT NOT NULL,
                    proposal_digest TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    method TEXT,
                    decision_reference TEXT,
                    created_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    action_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id)
                );
                """
            )
            row = conn.execute("SELECT value FROM sdk_meta WHERE key='schema'").fetchone()
            if row is None:
                conn.execute("INSERT INTO sdk_meta(key, value) VALUES('schema', ?)", (_STORE_SCHEMA,))
            elif row["value"] != _STORE_SCHEMA:
                raise LifecycleError(
                    f"SDK store schema mismatch: expected {_STORE_SCHEMA}, got {row['value']}"
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _json_text(value: Any) -> str:
        return canonical_bytes(value).decode("ascii")

    @staticmethod
    def _parse_json(text: str, *, label: str) -> Any:
        return strict_json_loads(text.encode("ascii"), label=label)

    def create_action(self, record: dict[str, Any], proposed_event: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO actions (
                    action_id, proposal_id, run_id, boundary_json, tool_name,
                    arguments_json, risk, authorization_required, proposal_digest,
                    created_at, state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["action_id"], record["proposal_id"], record["run_id"],
                    self._json_text(record["boundary"]), record["tool_name"],
                    self._json_text(record["arguments"]), record["risk"],
                    1 if record["authorization_required"] else 0,
                    record["proposal_digest"], record["created_at"], Phase.PROPOSED.value,
                ),
            )
            self._insert_event(conn, proposed_event)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _insert_event(self, conn: sqlite3.Connection, event: dict[str, Any]) -> None:
        body = event["body"]
        protected = {
            "event_id": event["event_id"],
            "action_id": event["action_id"],
            "phase": event["phase"],
            "created_at": event["created_at"],
            "body": body,
        }
        event_digest = commitment(protected)
        conn.execute(
            "INSERT INTO events(event_id, action_id, phase, created_at, body_json, event_digest) VALUES (?, ?, ?, ?, ?, ?)",
            (
                event["event_id"], event["action_id"], event["phase"], event["created_at"],
                self._json_text(body), event_digest,
            ),
        )

    def action(self, action_id: str) -> dict[str, Any]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise LifecycleError(f"unknown action_id: {action_id}")
            return {
                "action_id": row["action_id"],
                "proposal_id": row["proposal_id"],
                "run_id": row["run_id"],
                "boundary": self._parse_json(row["boundary_json"], label="boundary_json"),
                "tool_name": row["tool_name"],
                "arguments": self._parse_json(row["arguments_json"], label="arguments_json"),
                "risk": row["risk"],
                "authorization_required": bool(row["authorization_required"]),
                "proposal_digest": row["proposal_digest"],
                "created_at": row["created_at"],
                "state": row["state"],
            }
        finally:
            conn.close()

    def create_grant(self, grant: dict[str, Any], event: dict[str, Any], *, expected_state: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state, proposal_digest FROM actions WHERE action_id=?", (grant["action_id"],)).fetchone()
            if row is None:
                raise LifecycleError("unknown action")
            if row["state"] != expected_state:
                raise LifecycleError(f"grant requires state {expected_state}; got {row['state']}")
            if row["proposal_digest"] != grant["proposal_digest"]:
                raise LifecycleError("grant proposal digest mismatch")
            conn.execute(
                "INSERT INTO grants(grant_id, action_id, grant_type, proposal_digest, actor_ref, method, decision_reference, created_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (
                    grant["grant_id"], grant["action_id"], grant["grant_type"],
                    grant["proposal_digest"], grant["actor_ref"], grant.get("method"),
                    grant.get("decision_reference"), grant["created_at"],
                ),
            )
            self._insert_event(conn, event)
            conn.execute("UPDATE actions SET state=? WHERE action_id=?", (event["phase"], grant["action_id"]))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def precommit(self, *, action_id: str, grant_id: str, expected_phase: str, event: dict[str, Any], consumed_at: str) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            action = conn.execute("SELECT state, proposal_digest FROM actions WHERE action_id=?", (action_id,)).fetchone()
            grant = conn.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            if action is None or grant is None:
                raise LifecycleError("action or gate grant not found")
            if action["state"] != expected_phase:
                raise LifecycleError(f"precommit requires state {expected_phase}; got {action['state']}")
            if grant["action_id"] != action_id or grant["proposal_digest"] != action["proposal_digest"]:
                raise LifecycleError("gate grant does not bind this exact proposal")
            if grant["consumed_at"] is not None:
                raise LifecycleError("gate grant replay rejected")
            self._insert_event(conn, event)
            conn.execute("UPDATE grants SET consumed_at=? WHERE grant_id=?", (consumed_at, grant_id))
            conn.execute("UPDATE actions SET state=? WHERE action_id=?", (Phase.PRECOMMITTED.value, action_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transition(self, *, action_id: str, expected_state: str, event: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM actions WHERE action_id=?", (action_id,)).fetchone()
            if row is None:
                raise LifecycleError("unknown action")
            if row["state"] != expected_state:
                raise LifecycleError(f"transition requires state {expected_state}; got {row['state']}")
            self._insert_event(conn, event)
            conn.execute("UPDATE actions SET state=? WHERE action_id=?", (event["phase"], action_id))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def events(self, action_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM events WHERE action_id=? ORDER BY sequence", (action_id,)).fetchall()
            return [
                {
                    "sequence": int(row["sequence"]),
                    "event_id": row["event_id"],
                    "action_id": row["action_id"],
                    "phase": row["phase"],
                    "created_at": row["created_at"],
                    "body": self._parse_json(row["body_json"], label="event body"),
                    "event_digest": row["event_digest"],
                }
                for row in rows
            ]
        finally:
            conn.close()

    def grant(self, grant_id: str) -> Optional[dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM grants WHERE grant_id=?", (grant_id,)).fetchone()
            if row is None:
                return None
            return dict(row)
        finally:
            conn.close()
