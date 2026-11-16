"""Version- and artifact-pinned operational permissions for AI Chess Arena.

Operational scopes remain separate from model_allowlist.admission_scope.
This module never downloads or executes a model or writes tournament tables.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

SCOPES = frozenset({"TEST_ONLY", "EVALUATION", "TOURNAMENT"})
_ACTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}$")


def _validate_actor(actor: str | None) -> str:
    """Require an explicit actor supplied by a trusted authentication boundary."""
    if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
        raise ValueError("Brak zweryfikowanej tożsamości operatora; decyzja odmówiona.")
    return actor
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_ARTIFACT_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _add_column_if_missing(connection: sqlite3.Connection, table: str,
                            column: str, declaration: str) -> None:
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS model_permission_requests (
            id INTEGER PRIMARY KEY,
            model_id INTEGER NOT NULL,
            repo_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL DEFAULT '',
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
            artifact_sha256 TEXT NOT NULL DEFAULT '',
            scope TEXT NOT NULL,
            event TEXT NOT NULL CHECK(event IN ('requested','approved','rejected','revoked')),
            actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_model_permission_events_model
            ON model_permission_events(model_id, id);
        CREATE TRIGGER IF NOT EXISTS trg_model_permission_events_no_update
            BEFORE UPDATE ON model_permission_events
            BEGIN SELECT RAISE(ABORT, 'model permission history is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS trg_model_permission_events_no_delete
            BEFORE DELETE ON model_permission_events
            BEGIN SELECT RAISE(ABORT, 'model permission history is append-only'); END;
    """)
    # Older, not-yet-deployed development DBs fail closed with an empty hash.
    _add_column_if_missing(connection, "model_permission_requests",
                           "artifact_sha256", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(connection, "model_permission_events",
                           "artifact_sha256", "TEXT NOT NULL DEFAULT ''")
    # Create this only after migrating older tables that predate artifact_sha256.
    connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_model_permission_pending_identity
        ON model_permission_requests(model_id,repo_id,revision,artifact_sha256,scope)
        WHERE status='pending'
    """)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _validate_identity(model_id: int, repo_id: str, revision: str,
                       artifact_sha256: str) -> None:
    if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id < 1:
        raise ValueError("Nieprawidłowy model_id.")
    if not isinstance(repo_id, str) or not repo_id.strip() or len(repo_id) > 255:
        raise ValueError("Nieprawidłowe repo_id.")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise ValueError("Wymagana jest pełna, przypięta rewizja SHA.")
    if not isinstance(artifact_sha256, str) or not _ARTIFACT_SHA256.fullmatch(artifact_sha256):
        raise ValueError("Wymagany jest pełny SHA-256 konkretnego artefaktu GGUF.")


def _active_scope(connection: sqlite3.Connection, model_id: int,
                  repo_id: str, revision: str, artifact_sha256: str,
                  scope: str) -> bool:
    row = connection.execute("""
        SELECT status FROM model_permission_requests
        WHERE model_id=? AND repo_id=? AND revision=? AND artifact_sha256=? AND scope=?
          AND status IN ('approved','revoked') ORDER BY id DESC LIMIT 1
    """, (model_id, repo_id, revision, artifact_sha256, scope)).fetchone()
    return bool(row and row["status"] == "approved")


def list_permissions(connection: sqlite3.Connection) -> list[dict]:
    ensure_schema(connection)
    rows = connection.execute(
        "SELECT r.* FROM model_permission_requests r ORDER BY r.id DESC"
    ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        events = connection.execute("""
            SELECT event,actor,occurred_at,reason,artifact_sha256
            FROM model_permission_events WHERE request_id=? ORDER BY id
        """, (item["id"],)).fetchall()
        item["history"] = [dict(event) for event in events]
    return items


def request_permission(connection: sqlite3.Connection, *, model_id: int,
                       repo_id: str, revision: str, artifact_sha256: str,
                       scope: str, confirm: bool, reason: str = "",
                       snapshot: dict | None = None, actor: str | None = None) -> dict:
    ensure_schema(connection)
    actor = _validate_actor(actor)
    _validate_identity(model_id, repo_id, revision, artifact_sha256)
    if confirm is not True:
        raise ValueError("Wymagane jawne potwierdzenie żądania.")
    if scope not in SCOPES:
        raise ValueError("Nieznany zakres uprawnień.")
    if scope == "TOURNAMENT":
        raise ValueError("TOURNAMENT jest zarządzany wyłącznie przez wersjonowaną allowlistę i osobny proces admission.")
    if not isinstance(reason, str) or len(reason.strip()) < 3:
        raise ValueError("Podaj uzasadnienie żądania (co najmniej 3 znaki).")
    if scope == "EVALUATION" and not _active_scope(
            connection, model_id, repo_id, revision, artifact_sha256, "TEST_ONLY"):
        raise ValueError("EVALUATION wymaga osobnej, aktywnej zgody TEST_ONLY dla tej samej rewizji i artefaktu.")
    pending = connection.execute("""
        SELECT id FROM model_permission_requests
        WHERE model_id=? AND repo_id=? AND revision=? AND artifact_sha256=?
          AND scope=? AND status='pending'
    """, (model_id, repo_id, revision, artifact_sha256, scope)).fetchone()
    if pending:
        raise ValueError("Istnieje już oczekujące żądanie dla tej rewizji, artefaktu i zakresu.")
    now = _now()
    data = snapshot if isinstance(snapshot, dict) else {}
    data = {**data, "artifact_sha256": artifact_sha256}
    snapshot_json = json.dumps(data, ensure_ascii=False, sort_keys=True)
    try:
        cursor = connection.execute("""
            INSERT INTO model_permission_requests
            (model_id,repo_id,revision,artifact_sha256,scope,status,requested_by,requested_at,reason,snapshot_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (model_id, repo_id, revision, artifact_sha256, scope, "pending",
              actor, now, reason.strip(), snapshot_json))
    except sqlite3.IntegrityError as exc:
        # The partial unique index closes the race between concurrent requests.
        if "UNIQUE constraint failed" in str(exc):
            raise ValueError("Istnieje już oczekujące żądanie dla tej rewizji, artefaktu i zakresu.") from exc
        raise
    request_id = cursor.lastrowid
    connection.execute("""
        INSERT INTO model_permission_events
        (request_id,model_id,repo_id,revision,artifact_sha256,scope,event,actor,occurred_at,reason,snapshot_json)
        VALUES(?,?,?,?,?,?,'requested',?,?,?,?)
    """, (request_id, model_id, repo_id, revision, artifact_sha256, scope,
          actor, now, reason.strip(), snapshot_json))
    return {"id": request_id, "model_id": model_id, "repo_id": repo_id,
            "revision": revision, "artifact_sha256": artifact_sha256,
            "scope": scope, "status": "pending", "requested_at": now,
            "reason": reason.strip()}


def decide_permission(connection: sqlite3.Connection, *, request_id: int,
                      action: str, confirm: bool, reason: str = "",
                      actor: str | None = None) -> dict:
    ensure_schema(connection)
    actor = _validate_actor(actor)
    if confirm is not True:
        raise ValueError("Wymagane jawne potwierdzenie decyzji.")
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
    if not _ARTIFACT_SHA256.fullmatch(row["artifact_sha256"] or ""):
        raise ValueError("Brak przypiętego SHA-256 artefaktu; decyzja odmówiona.")
    if action == "approve":
        if row["status"] != "pending":
            raise ValueError("Można zatwierdzić wyłącznie oczekujące żądanie.")
        if row["scope"] == "EVALUATION" and not _active_scope(
                connection, row["model_id"], row["repo_id"], row["revision"],
                row["artifact_sha256"], "TEST_ONLY"):
            raise ValueError("TEST_ONLY nie jest już aktywne dla tej rewizji i artefaktu.")
        status, event = "approved", "approved"
    elif action == "reject":
        if row["status"] != "pending":
            raise ValueError("Można odrzucić wyłącznie oczekujące żądanie.")
        status, event = "rejected", "rejected"
    else:
        if row["status"] != "approved":
            raise ValueError("Można wycofać wyłącznie aktywne uprawnienie.")
        if row["scope"] == "TEST_ONLY" and _active_scope(
                connection, row["model_id"], row["repo_id"], row["revision"],
                row["artifact_sha256"], "EVALUATION"):
            raise ValueError("Najpierw wycofaj EVALUATION dla tej rewizji i artefaktu.")
        status, event = "revoked", "revoked"
    if action in {"reject", "revoke"} and (not isinstance(reason, str) or len(reason.strip()) < 3):
        raise ValueError("Podaj uzasadnienie (co najmniej 3 znaki).")
    now = _now()
    reason = reason.strip()
    # Compare-and-set makes a decision single-winner when two operators race.
    updated = connection.execute("""
        UPDATE model_permission_requests
        SET status=?,decided_by=?,decided_at=?,reason=?
        WHERE id=? AND status=?
    """, (status, actor, now, reason or row["reason"], request_id, row["status"]))
    if updated.rowcount != 1:
        raise ValueError("Żądanie zmieniło status równolegle; odśwież listę przed kolejną decyzją.")
    connection.execute("""
        INSERT INTO model_permission_events
        (request_id,model_id,repo_id,revision,artifact_sha256,scope,event,actor,occurred_at,reason,snapshot_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, (request_id, row["model_id"], row["repo_id"], row["revision"],
          row["artifact_sha256"], row["scope"], event, actor, now,
          reason or row["reason"], row["snapshot_json"]))
    return {"id": request_id, "model_id": row["model_id"], "repo_id": row["repo_id"],
            "revision": row["revision"], "artifact_sha256": row["artifact_sha256"],
            "scope": row["scope"], "status": status, "decided_at": now,
            "reason": reason or row["reason"]}


def has_permission(connection: sqlite3.Connection, *, model_id: int,
                   repo_id: str, revision: str, artifact_sha256: str,
                   scope: str) -> bool:
    """Read-only exact model/repository/revision/artifact/scope check."""
    _validate_identity(model_id, repo_id, revision, artifact_sha256)
    if scope not in {"TEST_ONLY", "EVALUATION"}:
        return False
    if scope == "EVALUATION" and not _active_scope(
            connection, model_id, repo_id, revision, artifact_sha256, "TEST_ONLY"):
        return False
    return _active_scope(connection, model_id, repo_id, revision, artifact_sha256, scope)
