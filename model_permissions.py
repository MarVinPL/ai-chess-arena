"""Version-pinned operational permission requests for AI Chess Arena.

These scopes are intentionally separate from model_allowlist.admission_scope,
which describes tournament formats (PURE_AI / AI_ENGINE). This module never
downloads or executes a model and never writes tournament tables.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

SCOPES = frozenset({"TEST_ONLY", "EVALUATION", "TOURNAMENT"})
ACTOR = "arena"
_SHA = re.compile(r"^[0-9a-f]{40}$")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS model_permission_requests (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            repo_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('TEST_ONLY','EVALUATION','TOURNAMENT')),
            status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','revoked')),
            requested_by TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            reason TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_model_permission_requests_model
            ON model_permission_requests(model_id, id);
        CREATE TABLE IF NOT EXISTS model_permission_events (
            id INTEGER PRIMARY KEY,
            request_id INTEGER NOT NULL,
            model_id INTEGER NOT NULL,
            repo_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            scope TEXT NOT NULL,
            event TEXT NOT NULL CHECK(event IN ('requested','approved','rejected','revoked')),
            actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_model_permission_events_model
            ON model_permission_events(model_id, id);
    """)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_identity(model_id: int, repo_id: str, revision: str) -> None:
    if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id < 1:
        raise ValueError("Nieprawidłowy model_id.")
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 255:
        raise ValueError("Nieprawidłowe repo_id.")
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise ValueError("Wymagana jest pełna, przypięta rewizja SHA.")


def _active_scope(connection: sqlite3.Connection, model_id: int,
                  repo_id: str, revision: str, scope: str) -> bool:
    row = connection.execute("""
        SELECT 1 FROM model_permission_requests
        WHERE model_id=? AND repo_id=? AND revision=? AND scope=?
          AND status='approved' ORDER BY id DESC LIMIT 1
    """, (model_id, repo_id, revision, scope)).fetchone()
    return row is not None


def list_permissions(connection: sqlite3.Connection) -> list[dict]:
    ensure_schema(connection)
    rows = connection.execute("""
        SELECT r.*, e.event AS latest_event, e.occurred_at AS latest_event_at
        FROM model_permission_requests r
        LEFT JOIN model_permission_events e ON e.id=(
            SELECT MAX(e2.id) FROM model_permission_events e2 WHERE e2.request_id=r.id
        )
        ORDER BY r.id DESC
    """).fetchall()
    return [dict(row) for row in rows]


def request_permission(connection: sqlite3.Connection, *, model_id: int,
                       repo_id: str, revision: str, scope: str,
                       confirm: bool, reason: str = "",
                       snapshot: dict | None = None, actor: str = ACTOR) -> dict:
    ensure_schema(connection)
    _validate_identity(model_id, repo_id, revision)
    if confirm is not True:
        raise ValueError("Wymagane jawne potwierdzenie żądania.")
    if actor != ACTOR:
        raise ValueError("Nieprawidłowy aktor audytu.")
    if scope not in SCOPES:
        raise ValueError("Nieznany zakres uprawnień.")
    if scope == "TOURNAMENT":
        raise ValueError("TOURNAMENT jest zarządzany wyłącznie przez wersjonowaną allowlistę i osobny proces admission.")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError("Podaj uzasadnienie żądania (co najmniej 3 znaki).")
    if scope == "EVALUATION" and not _active_scope(
            connection, model_id, repo_id, revision, "TEST_ONLY"):
        raise ValueError("EVALUATION wymaga osobnej, aktywnej zgody TEST_ONLY dla tej samej rewizji.")
    pending = connection.execute("""
        SELECT id FROM model_permission_requests
        WHERE model_id=? AND repo_id=? AND revision=? AND scope=? AND status='pending'
    """, (model_id, repo_id, revision, scope)).fetchone()
    if pending:
        raise ValueError("Istnieje już oczekujące żądanie dla tej rewizji i zakresu.")
    now = _now()
    data = snapshot if isinstance(snapshot, dict) else {}
    snapshot_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
    cursor = connection.execute("""
        INSERT INTO model_permission_requests
        (model_id,repo_id,revision,scope,status,requested_by,requested_at,reason,snapshot_json)
        VALUES(?,?,?,?,?,?,?,?,?)
    """, (model_id, repo_id, revision, scope, "pending", actor, now,
          reason.strip(), snapshot_json))
    request_id = cursor.lastrowid
    connection.execute("""
        INSERT INTO model_permission_events
        (request_id,model_id,repo_id,revision,scope,event,actor,occurred_at,reason,snapshot_json)
        VALUES(?,?,?,?,?,'requested',?,?,?,?)
    """, (request_id, model_id, repo_id, revision, scope, actor, now,
          reason.strip(), snapshot_json))
    return {"id": request_id, "model_id": model_id, "repo_id": repo_id,
            "revision": revision, "scope": scope, "status": "pending",
            "requested_at": now, "reason": reason.strip()}


def decide_permission(connection: sqlite3.Connection, *, request_id: int,
                      action: str, confirm: bool, reason: str = "",
                      actor: str = ACTOR) -> dict:
    ensure_schema(connection)
    if confirm is not True:
        raise ValueError("Wymagane jawne potwierdzenie decyzji.")
    if actor != ACTOR:
        raise ValueError("Nieprawidłowy aktor audytu.")
    if action not in {"approve", "reject", "revoke"}:
        raise ValueError("Nieznana decyzja.")
    if isinstance(request_id, bool) or not isinstance(request_id, int) or request_id < 1:
        raise ValueError("Nieprawidłowy identyfikator żądania.")
    row = connection.execute(
        "SELECT * FROM model_permission_requests WHERE id=?", (request_id,)
    ).fetchone()
    if row is None:
        raise ValueError("Nie znaleziono żądania uprawnień.")
    if row["scope"] == "TOURNAMENT":
        raise ValueError("TOURNAMENT nie może być zatwierdzany w rejestrze uprawnień operacyjnych.")
    if action == "approve":
        if row["status"] != "pending":
            raise ValueError("Można zatwierdzić wyłącznie oczekujące żądanie.")
        if row["scope"] == "EVALUATION" and not _active_scope(
                connection, row["model_id"], row["repo_id"], row["revision"], "TEST_ONLY"):
            raise ValueError("TEST_ONLY nie jest już aktywne dla tej rewizji.")
        status, event = "approved", "approved"
    elif action == "reject":
        if row["status"] != "pending":
            raise ValueError("Można odrzucić wyłącznie oczekujące żądanie.")
        status, event = "rejected", "rejected"
    else:
        if row["status"] != "approved":
            raise ValueError("Można wycofać wyłącznie aktywne uprawnienie.")
        if row["scope"] == "TEST_ONLY" and _active_scope(
                connection, row["model_id"], row["repo_id"], row["revision"], "EVALUATION"):
            raise ValueError("Najpierw wycofaj EVALUATION dla tej samej rewizji.")
        status, event = "revoked", "revoked"
    if action in {"reject", "revoke"} and (not isinstance(reason, str) or len(reason.strip()) < 3):
        raise ValueError("Podaj uzasadnienie (co najmniej 3 znaki).")
    now = _now()
    reason = reason.strip()
    connection.execute("""
        UPDATE model_permission_requests
        SET status=?,decided_by=?,decided_at=?,reason=?
        WHERE id=?
    """, (status, actor, now, reason or row["reason"], request_id))
    connection.execute("""
        INSERT INTO model_permission_events
        (request_id,model_id,repo_id,revision,scope,event,actor,occurred_at,reason,snapshot_json)
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (request_id, row["model_id"], row["repo_id"], row["revision"],
          row["scope"], event, actor, now, reason or row["reason"], row["snapshot_json"]))
    return {"id": request_id, "model_id": row["model_id"], "repo_id": row["repo_id"],
            "revision": row["revision"], "scope": row["scope"], "status": status,
            "decided_at": now, "reason": reason or row["reason"]}


def has_permission(connection: sqlite3.Connection, *, model_id: int,
                   repo_id: str, revision: str, scope: str) -> bool:
    """Read-only exact-version scope check; does not start/download a model."""
    _validate_identity(model_id, repo_id, revision)
    if scope not in {"TEST_ONLY", "EVALUATION"}:
        return False
    if scope == "EVALUATION" and not _active_scope(
            connection, model_id, repo_id, revision, "TEST_ONLY"):
        return False
    return _active_scope(connection, model_id, repo_id, revision, scope)
