"""Transactional, fail-closed lifecycle ledger for isolated model test jobs.

This module never invokes systemd or executes a model. It creates an immutable
execution plan for a separately reviewed privileged adapter to consume.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

import model_permissions
from model_sandbox import (
    SandboxLimits, artifact_fingerprint, build_systemd_run_argv,
)

WORKER_EXECUTABLE = "/home/debian/ai-chess-arena/bin/model-test-worker"
_JOB_ID = re.compile(r"^[a-f0-9]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SCOPES = {"TEST_ONLY", "EVALUATION"}


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS model_test_jobs (
            job_id TEXT PRIMARY KEY,
            model_id INTEGER NOT NULL,
            repo_id TEXT NOT NULL,
            revision TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('TEST_ONLY','EVALUATION')),
            artifact_path TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            artifact_size_bytes INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status IN
                ('queued','starting','running','succeeded','failed','cancelled')),
            requested_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            systemd_unit TEXT,
            consent_consumed INTEGER NOT NULL DEFAULT 0 CHECK(consent_consumed IN (0,1)),
            limits_json TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_model_test_jobs_model
            ON model_test_jobs(model_id, created_at);
        CREATE TABLE IF NOT EXISTS model_test_job_events (
            id INTEGER PRIMARY KEY,
            job_id TEXT NOT NULL,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            detail_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_model_test_job_events_job
            ON model_test_job_events(job_id, id);
        CREATE TABLE IF NOT EXISTS model_evaluation_metrics (
            id INTEGER PRIMARY KEY,
            job_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL NOT NULL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES model_test_jobs(job_id)
        );
    """)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _actor(actor: str) -> str:
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
        raise ValueError("Wymagana jest wiarygodna tożsamość aktora audytu.")
    return actor.strip()


def _event(connection, job_id: str, event: str, actor: str, detail=None) -> None:
    connection.execute("""
        INSERT INTO model_test_job_events(job_id,event,actor,occurred_at,detail_json)
        VALUES(?,?,?,?,?)
    """, (job_id, event, actor, _now(),
          json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)))


def create_job(connection: sqlite3.Connection, *, model_id: int, repo_id: str,
               revision: str, scope: str, artifact_path: str,
               expected_sha256: str, confirm_operation: bool,
               actor: str, limits: SandboxLimits = SandboxLimits(),
               model_root: str | Path | None = None,
               capacity_check=None) -> dict:
    """Record a one-operation-consented job; no process is started."""
    ensure_schema(connection)
    actor = _actor(actor)
    if scope not in _SCOPES:
        raise ValueError("Zakres musi być TEST_ONLY albo EVALUATION.")
    if confirm_operation is not True:
        raise ValueError("Wymagana osobna, jawna zgoda na tę operację.")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256 or ""):
        raise ValueError("Wymagany jest oczekiwany SHA-256 artefaktu.")
    if not model_permissions.has_permission(
            connection, model_id=model_id, repo_id=repo_id,
            revision=revision, artifact_sha256=expected_sha256, scope=scope):
        raise PermissionError("Brak aktywnego uprawnienia dla dokładnej rewizji modelu.")
    if capacity_check is None or capacity_check() is not True:
        raise RuntimeError("Brak pozytywnego, aktualnego potwierdzenia dostępności zasobów.")
    fingerprint = artifact_fingerprint(
        artifact_path, model_root=model_root or
        __import__("model_sandbox").MODEL_ROOT)
    if fingerprint["sha256"] != expected_sha256:
        raise ValueError("SHA-256 artefaktu nie zgadza się z zatwierdzonym odciskiem.")
    job_id = secrets.token_hex(16)
    now = _now()
    limits_data = {
        "cpu_quota_percent": limits.cpu_quota_percent,
        "memory_max_bytes": limits.memory_max_bytes,
        "runtime_max_seconds": limits.runtime_max_seconds,
        "tasks_max": limits.tasks_max,
    }
    with connection:
        connection.execute("""
            INSERT INTO model_test_jobs
            (job_id,model_id,repo_id,revision,scope,artifact_path,artifact_sha256,
             artifact_size_bytes,status,requested_by,created_at,consent_consumed,limits_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (job_id, model_id, repo_id, revision, scope,
              str(Path(artifact_path).resolve(strict=True)), fingerprint["sha256"],
              fingerprint["size_bytes"], "queued", actor, now, 0,
              json.dumps(limits_data, sort_keys=True)))
        _event(connection, job_id, "queued", actor, {
            "scope": scope, "revision": revision,
            "artifact_sha256": fingerprint["sha256"],
            "operation_consent": "explicit_one_use",
        })
    return {"job_id": job_id, "status": "queued", "scope": scope,
            "artifact_sha256": fingerprint["sha256"], "created_at": now}


def claim_job_for_start(connection: sqlite3.Connection, *, job_id: str,
                         actor: str, model_root: str | Path,
                         capacity_check=None,
                         limits: SandboxLimits = SandboxLimits()) -> dict:
    """Atomically consume operation consent and return a fixed-worker argv plan.

    The caller must still pass this plan through a separately reviewed launcher.
    This function itself has no process or systemd side effects.
    """
    ensure_schema(connection)
    actor = _actor(actor)
    if capacity_check is None or capacity_check() is not True:
        raise RuntimeError("Brak aktualnego potwierdzenia dostępności zasobów przed startem.")
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise ValueError("Nieprawidłowy identyfikator zadania.")
    with connection:
        row = connection.execute(
            "SELECT * FROM model_test_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Nie znaleziono zadania.")
        if row["status"] != "queued" or row["consent_consumed"]:
            raise ValueError("Zadanie nie oczekuje na start albo zgoda została już zużyta.")
        if not model_permissions.has_permission(
                connection, model_id=row["model_id"], repo_id=row["repo_id"],
                revision=row["revision"], artifact_sha256=row["artifact_sha256"],
                scope=row["scope"]):
            raise PermissionError("Uprawnienie zostało cofnięte lub wygasło.")
        fingerprint = artifact_fingerprint(row["artifact_path"], model_root=model_root)
        if fingerprint["sha256"] != row["artifact_sha256"] or fingerprint["size_bytes"] != row["artifact_size_bytes"]:
            raise ValueError("Artefakt zmienił się od czasu zatwierdzenia zadania.")
        if limits != SandboxLimits(**json.loads(row["limits_json"])):
            raise ValueError("Limity uruchomienia różnią się od zatwierdzonych limitów zadania.")
        worker_argv = [
            WORKER_EXECUTABLE, "--job-id", job_id,
            "--model", row["artifact_path"],
            "--output-dir", f"/run/ai-chess-model-test-{job_id[:16]}",
            "--scope", row["scope"],
        ]
        argv = build_systemd_run_argv(
            job_id=job_id[:16], artifact_path=row["artifact_path"],
            worker_argv=worker_argv, limits=limits, model_root=model_root)
        now = _now()
        unit = f"ai-chess-model-test-{job_id[:16]}.service"
        connection.execute("""
            UPDATE model_test_jobs
            SET status='starting', consent_consumed=1, started_at=?, systemd_unit=?
            WHERE job_id=? AND status='queued' AND consent_consumed=0
        """, (now, unit, job_id))
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise RuntimeError("Nie udało się atomowo przejąć zadania.")
        _event(connection, job_id, "start_authorized", actor, {
            "systemd_unit": unit, "consent_consumed": True,
            "artifact_sha256": row["artifact_sha256"],
        })
    return {"job_id": job_id, "status": "starting", "systemd_unit": unit,
            "argv": argv, "worker": WORKER_EXECUTABLE,
            "side_effects_performed": False}


def mark_job_running(connection: sqlite3.Connection, *, job_id: str,
                     systemd_unit: str, actor: str) -> None:
    ensure_schema(connection)
    actor = _actor(actor)
    with connection:
        row = connection.execute(
            "SELECT status,systemd_unit FROM model_test_jobs WHERE job_id=?",
            (job_id,)).fetchone()
        if row is None or row["status"] != "starting" or row["systemd_unit"] != systemd_unit:
            raise ValueError("Nieprawidłowy stan lub jednostka systemd zadania.")
        connection.execute(
            "UPDATE model_test_jobs SET status='running' WHERE job_id=?", (job_id,))
        _event(connection, job_id, "running", actor, {"systemd_unit": systemd_unit})


def finish_job(connection: sqlite3.Connection, *, job_id: str, success: bool,
               actor: str, result: dict | None = None) -> None:
    ensure_schema(connection)
    actor = _actor(actor)
    if success is not True and success is not False:
        raise ValueError("Nieprawidłowy wynik zadania.")
    with connection:
        row = connection.execute(
            "SELECT status FROM model_test_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row["status"] not in {"starting", "running"}:
            raise ValueError("Zadanie nie może zostać zakończone z obecnego stanu.")
        status = "succeeded" if success else "failed"
        now = _now()
        safe_result = result if isinstance(result, dict) else {}
        connection.execute("""
            UPDATE model_test_jobs SET status=?,finished_at=?,result_json=?
            WHERE job_id=?
        """, (status, now, json.dumps(safe_result, ensure_ascii=False, sort_keys=True), job_id))
        _event(connection, job_id, status, actor, safe_result)


def record_evaluation_metric(connection: sqlite3.Connection, *, job_id: str,
                              metric_name: str, metric_value: float,
                              actor: str) -> None:
    ensure_schema(connection)
    actor = _actor(actor)
    if not isinstance(metric_name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", metric_name):
        raise ValueError("Nieprawidłowa nazwa metryki.")
    if isinstance(metric_value, bool) or not isinstance(metric_value, (int, float)):
        raise ValueError("Metryka musi być liczbą.")
    import math
    if not math.isfinite(metric_value):
        raise ValueError("Metryka musi być skończoną liczbą.")
    with connection:
        row = connection.execute(
            "SELECT scope,status FROM model_test_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row["scope"] != "EVALUATION" or row["status"] not in {"starting", "running", "succeeded"}:
            raise ValueError("Metryki można zapisywać wyłącznie dla zadania EVALUATION.")
        connection.execute("""
            INSERT INTO model_evaluation_metrics(job_id,metric_name,metric_value,recorded_at)
            VALUES(?,?,?,?)
        """, (job_id, metric_name, float(metric_value), _now()))
        _event(connection, job_id, "metric_recorded", actor,
               {"metric_name": metric_name, "metric_value": float(metric_value)})


def get_job(connection: sqlite3.Connection, job_id: str) -> dict | None:
    ensure_schema(connection)
    row = connection.execute(
        "SELECT * FROM model_test_jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["limits"] = json.loads(item.pop("limits_json"))
    item["result"] = json.loads(item.pop("result_json"))
    item["events"] = [dict(event) for event in connection.execute(
        "SELECT event,actor,occurred_at,detail_json FROM model_test_job_events WHERE job_id=? ORDER BY id",
        (job_id,)).fetchall()]
    item["metrics"] = [dict(metric) for metric in connection.execute(
        "SELECT metric_name,metric_value,recorded_at FROM model_evaluation_metrics WHERE job_id=? ORDER BY id",
        (job_id,)).fetchall()]
    return item
