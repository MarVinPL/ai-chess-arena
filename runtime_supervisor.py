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
            "RestrictAddressFamilies": "AF_UNIX AF_INET", "KillMode": "control-group",
            "Delegate": "no", "WorkingDirectory": f"/run/arena-runtime/job-{job_id}",
            "ReadOnlyPaths": "/usr/local/lib/ai-chess-arena-runtime",
            "BindReadOnlyPaths": (
                f"/var/lib/arena-handoff/job-{job_id}/model.gguf:"
                f"/run/arena-runtime/job-{job_id}/model.gguf"
            ),
            "ReadWritePaths": f"/run/arena-runtime/job-{job_id}",
        }
        if any(props.get(key) != value for key, value in required.items()):
            raise RuntimeSupervisorError("Specyfikacja nie spełnia wymaganej polityki izolacji.")
        if "/var/lib/arena-handoff" not in props.get("InaccessiblePaths", "").split():
            raise RuntimeSupervisorError("Katalog źródłowy artefaktów musi być ukryty przed runtime.")
        environment = props.get("Environment", "").split()
        if (len(environment) != 2
                or not environment[0].startswith("ARENA_API_UID=")
                or not environment[1].startswith("ARENA_API_GID=")):
            raise RuntimeSupervisorError("Środowisko runtime może zawierać wyłącznie UID/GID API.")
        try:
            if int(environment[0].split("=", 1)[1]) < 1 or int(environment[1].split("=", 1)[1]) < 1:
                raise ValueError
        except ValueError as exc:
            raise RuntimeSupervisorError("Nieprawidłowe identyfikatory API w środowisku runtime.") from exc
        if not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
            raise RuntimeSupervisorError("Polecenie runtime jest nieprawidłowe.")
        if not argv[0].startswith("/"):
            raise RuntimeSupervisorError("Plik wykonywalny musi mieć bezwzględną ścieżkę.")
        return job_id

    def start(self, spec: RuntimeUnitSpec, argv: tuple[str, ...], *,
              before_start: Callable[[], None] | None = None) -> str:
        """Persist identity first; optionally re-authorize immediately before backend start."""
        job_id = self._validate(spec, argv)
        if before_start is not None and not callable(before_start):
            raise RuntimeSupervisorError("Callback przed startem musi być wywoływalny.")
        self._persist(RuntimeUnitRecord(job_id, spec.unit_name, "start_pending"))
        if before_start is not None:
            try:
                before_start()
            except Exception:
                # No backend call has happened yet, so this exact lease can be
                # marked stopped. If the DB write fails, start_pending remains
                # recoverable and no artifact cleanup is performed by caller.
                self._set_state(job_id, "stopped")
                raise
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

    def __init__(self, database_path, *, initialize_schema: bool = True):
        from pathlib import Path
        import sqlite3

        self.database_path = str(Path(database_path))
        self._database_uri = Path(self.database_path).resolve().as_uri() + "?mode=rw"
        if initialize_schema:
            with sqlite3.connect(self.database_path) as connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_unit_leases (
                        job_id INTEGER PRIMARY KEY,
                        unit_name TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
        else:
            # The privileged daemon must never create/migrate application DB
            # schema at startup. mode=rw also refuses to create a missing DB.
            try:
                with sqlite3.connect(self._database_uri, uri=True) as connection:
                    columns = {row[1] for row in connection.execute(
                        "PRAGMA table_info(runtime_unit_leases)"
                    )}
            except sqlite3.Error as exc:
                raise RuntimeSupervisorError(
                    "Pre-provisioned runtime lease schema is unavailable."
                ) from exc
            if not {"job_id", "unit_name", "state", "updated_at"}.issubset(columns):
                raise RuntimeSupervisorError(
                    "Pre-provisioned runtime lease schema is incomplete."
                )

    def persist(self, record: RuntimeUnitRecord) -> None:
        import sqlite3

        RuntimeSupervisor._validate_record(record)
        if record.state != "start_pending":
            raise RuntimeSupervisorError("Nowa dzierżawa musi zaczynać się od start_pending.")
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT status,cancel_requested FROM runtime_test_jobs WHERE id=?",
                (record.job_id,),
            ).fetchone()
            if not job or job[0] != "running" or bool(job[1]):
                raise RuntimeSupervisorError(
                    "Dzierżawa wymaga nieanulowanego zadania w stanie running."
                )
            existing = connection.execute(
                "SELECT unit_name,state FROM runtime_unit_leases WHERE job_id=?", (record.job_id,)
            ).fetchone()
            if existing:
                # A retry after an ambiguous broker response is safe only while
                # the original start is still pending and the exact identity matches.
                if existing[0] == record.unit_name and existing[1] == "start_pending":
                    return
                raise RuntimeSupervisorError("Dla tego zadania istnieje już dzierżawa runtime.")
            connection.execute(
                "INSERT INTO runtime_unit_leases(job_id,unit_name,state) VALUES(?,?,?)",
                (record.job_id, record.unit_name, record.state),
            )

    def set_state(self, job_id: int, state: str, unit_name: str | None = None) -> None:
        import sqlite3

        transitions = {
            "start_pending": {"start_uncertain", "active", "stopped", "recovery_stop_pending"},
            "start_uncertain": {"stop_pending", "recovery_stop_pending", "stopped"},
            "active": {"stop_pending", "recovery_stop_pending"},
            "stop_pending": {"recovery_stop_pending", "stopped"},
            "recovery_stop_pending": {"stopped"},
            "stopped": set(),
        }
        if (type(job_id) is not int or job_id < 1
                or not isinstance(state, str) or state not in transitions):
            raise RuntimeSupervisorError("Nieznany stan dzierżawy runtime.")
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT unit_name,state FROM runtime_unit_leases WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None or (unit_name is not None and row[0] != unit_name):
                raise RuntimeSupervisorError("Brak zgodnej dzierżawy runtime dla zadania.")
            if row[1] == state:
                return
            if state not in transitions.get(row[1], set()):
                raise RuntimeSupervisorError("Niedozwolone przejście stanu dzierżawy runtime.")
            changed = connection.execute(
                "UPDATE runtime_unit_leases SET state=?,updated_at=CURRENT_TIMESTAMP "
                "WHERE job_id=? AND unit_name=? AND state=?",
                (state, job_id, row[0], row[1]),
            )
            if changed.rowcount != 1:
                raise RuntimeSupervisorError("Konflikt aktualizacji dzierżawy runtime.")

    def get(self, job_id: int) -> RuntimeUnitRecord | None:
        import sqlite3

        if type(job_id) is not int or job_id < 1:
            raise RuntimeSupervisorError("Nieprawidłowy identyfikator zadania.")
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            row = connection.execute(
                "SELECT job_id,unit_name,state FROM runtime_unit_leases WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return RuntimeUnitRecord(*row) if row else None

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
