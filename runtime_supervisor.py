"""Fail-closed orchestration for exact per-job transient systemd units."""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from runtime_systemd import RuntimeUnitSpec

_UNIT_RE = re.compile(r"^ai-chess-arena-runtime-([1-9][0-9]*)\.service$")


class RuntimeSupervisorError(RuntimeError):
    """Raised when a unit operation cannot be proven safe."""


@dataclass(frozen=True)
class RuntimeUnitRecord:
    job_id: int
    unit_name: str
    state: str


class RuntimeSupervisor:
    """Coordinate an injected backend using durable unit-identity callbacks.

    persist(record) must commit before backend.start(). backend.stop() must be
    idempotent and verify that the exact unit's cgroup is empty.
    """

    def __init__(self, *, backend, persist: Callable[[RuntimeUnitRecord], None],
                 set_state: Callable[[int, str], None],
                 load_recoverable: Callable[[], Iterable[RuntimeUnitRecord]]):
        self._backend = backend
        self._persist = persist
        self._set_state = set_state
        self._load_recoverable = load_recoverable

    @staticmethod
    def _validate(spec: RuntimeUnitSpec, argv: tuple[str, ...]) -> int:
        if not isinstance(spec, RuntimeUnitSpec):
            raise RuntimeSupervisorError("Wymagana jest specyfikacja RuntimeUnitSpec.")
        match = _UNIT_RE.fullmatch(spec.unit_name)
        if not match:
            raise RuntimeSupervisorError("Nazwa jednostki poza dozwolonym wzorcem.")
        job_id = int(match.group(1))
        props = spec.as_dict()
        required = {
            "User": "arena-runtime", "Group": "arena-runtime",
            "NoNewPrivileges": "yes", "CapabilityBoundingSet": "",
            "AmbientCapabilities": "", "ProtectSystem": "strict",
            "ProtectHome": "yes", "PrivateTmp": "yes",
            "PrivateDevices": "yes", "PrivateNetwork": "yes",
            "ProtectControlGroups": "yes", "RestrictNamespaces": "yes",
            "RestrictAddressFamilies": "AF_UNIX", "KillMode": "control-group",
            "Delegate": "no", "WorkingDirectory": f"/run/arena-runtime/job-{job_id}",
            "ReadWritePaths": f"/run/arena-runtime/job-{job_id}",
        }
        if any(props.get(key) != value for key, value in required.items()):
            raise RuntimeSupervisorError("Specyfikacja nie spełnia wymaganej polityki izolacji.")
        if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise RuntimeSupervisorError("Polecenie runtime jest nieprawidłowe.")
        if not argv[0].startswith("/"):
            raise RuntimeSupervisorError("Plik wykonywalny musi mieć bezwzględną ścieżkę.")
        return job_id

    def start(self, spec: RuntimeUnitSpec, argv: tuple[str, ...]) -> str:
        """Persist identity first; ambiguous starts remain recoverable."""
        job_id = self._validate(spec, argv)
        self._persist(RuntimeUnitRecord(job_id, spec.unit_name, "start_pending"))
        try:
            self._backend.start(spec, argv)
        except Exception:
            self._set_state(job_id, "start_uncertain")
            raise
        self._set_state(job_id, "active")
        return spec.unit_name

    def stop(self, record: RuntimeUnitRecord) -> None:
        self._validate_record(record)
        persisted = tuple(self._load_recoverable())
        if not any(item.job_id == record.job_id and item.unit_name == record.unit_name
                   for item in persisted):
            raise RuntimeSupervisorError("Odmowa zatrzymania jednostki bez aktywnej dzierżawy w bazie.")
        self._set_state(record.job_id, "stop_pending")
        self._backend.stop(record.unit_name)
        self._set_state(record.job_id, "stopped")

    def recover(self) -> dict:
        """Stop only durable, validated unit names; never enumerate processes."""
        recovered = []
        for record in tuple(self._load_recoverable()):
            self._validate_record(record)
            self._set_state(record.job_id, "recovery_stop_pending")
            self._backend.stop(record.unit_name)
            self._set_state(record.job_id, "stopped")
            recovered.append(record.unit_name)
        return {"stopped_units": recovered, "count": len(recovered)}

    @staticmethod
    def _validate_record(record: RuntimeUnitRecord) -> None:
        if not isinstance(record, RuntimeUnitRecord):
            raise RuntimeSupervisorError("Nieprawidłowy rekord odzyskiwania.")
        match = _UNIT_RE.fullmatch(record.unit_name)
        if not match or int(match.group(1)) != record.job_id:
            raise RuntimeSupervisorError("Rekord nie wskazuje jednostki przypisanej do tego zadania.")
        if record.state not in {"start_pending", "start_uncertain", "active", "stop_pending", "recovery_stop_pending"}:
            raise RuntimeSupervisorError("Stan jednostki nie podlega odzyskiwaniu.")


class SQLiteRuntimeUnitStore:
    """Durable lease store backed by the Arena SQLite database."""

    def __init__(self, database_path):
        from pathlib import Path
        import sqlite3

        self.database_path = str(Path(database_path))
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS runtime_unit_leases (
                    job_id INTEGER PRIMARY KEY,
                    unit_name TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def persist(self, record: RuntimeUnitRecord) -> None:
        import sqlite3

        RuntimeSupervisor._validate_record(record)
        if record.state != "start_pending":
            raise RuntimeSupervisorError("Nowa dzierżawa musi zaczynać się od start_pending.")
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT status FROM runtime_test_jobs WHERE id=?", (record.job_id,)
            ).fetchone()
            if not job or job[0] != "running":
                raise RuntimeSupervisorError("Dzierżawa wymaga istniejącego zadania w stanie running.")
            existing = connection.execute(
                "SELECT unit_name FROM runtime_unit_leases WHERE job_id=?", (record.job_id,)
            ).fetchone()
            if existing:
                raise RuntimeSupervisorError("Dla tego zadania istnieje już dzierżawa runtime.")
            connection.execute(
                "INSERT INTO runtime_unit_leases(job_id,unit_name,state) VALUES(?,?,?)",
                (record.job_id, record.unit_name, record.state),
            )

    def set_state(self, job_id: int, state: str) -> None:
        import sqlite3

        allowed = {
            "start_pending", "start_uncertain", "active", "stop_pending",
            "recovery_stop_pending", "stopped",
        }
        if state not in allowed:
            raise RuntimeSupervisorError("Nieznany stan dzierżawy runtime.")
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            changed = connection.execute(
                "UPDATE runtime_unit_leases SET state=?,updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (state, job_id),
            )
            if changed.rowcount != 1:
                raise RuntimeSupervisorError("Brak zapisanej dzierżawy runtime dla zadania.")

    def load_recoverable(self) -> tuple[RuntimeUnitRecord, ...]:
        import sqlite3

        with sqlite3.connect(self.database_path, timeout=5) as connection:
            rows = connection.execute(
                """SELECT job_id,unit_name,state FROM runtime_unit_leases
                   WHERE state IN ('start_pending','start_uncertain','active',
                                   'stop_pending','recovery_stop_pending')
                   ORDER BY job_id"""
            ).fetchall()
        return tuple(RuntimeUnitRecord(*row) for row in rows)
