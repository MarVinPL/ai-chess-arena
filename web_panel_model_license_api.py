"""Browser-only model registration and license evidence workflows (#314).

HTTP routing, session validation, and Origin checks remain in server.Handler.
All model-version reads/writes use the existing league.connect() database;
this adapter neither authenticates Worker tokens nor owns a model registry.
"""
import json

import league
from model_exclusions import is_excluded_model_identity

MODEL_LICENSE_POST_ROUTES = frozenset({
    "/api/models/license-evidence",
    "/api/models/verify-license",
    "/api/models/register",
})


def handle_model_license_post(path, payload, *, fetch_evidence):
    """Preserve existing admin operations and return their result unchanged."""
    if path == "/api/models/license-evidence":
        version_id = int(payload.get("version_id", 0))
        if version_id < 1:
            raise ValueError("Nieprawidłowy identyfikator wersji modelu")
        with league.connect() as connection:
            row = connection.execute(
                """SELECT v.id,v.source,v.metadata_json,m.name
                FROM model_versions v JOIN league_models m ON m.id=v.model_id
                WHERE v.id=?""", (version_id,)).fetchone()
        if row is None:
            raise ValueError("Nieznana wersja modelu")
        if is_excluded_model_identity(
            name=row["name"], source=row["source"], metadata=row["metadata_json"]
        ):
            raise ValueError(
                "Model jest wyłączony z AI Chess Arena (Issue #71); "
                "nie można przetwarzać go jako kandydata do zatwierdzenia"
            )
        evidence = fetch_evidence(row["source"])
        with league.connect() as connection:
            current = connection.execute(
                "SELECT metadata_json FROM model_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            if current is None:
                raise ValueError("Wersja modelu została usunięta podczas pobierania dowodów")
            metadata = json.loads(current["metadata_json"] or "{}")
            metadata["license_evidence"] = evidence
            connection.execute(
                "UPDATE model_versions SET metadata_json=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), version_id),
            )
        return {"version_id": version_id, "evidence": evidence, "license_verified": False}

    if path == "/api/models/verify-license":
        if payload.get("confirm_review") is not True:
            raise ValueError("Wymagane jawne potwierdzenie samodzielnego sprawdzenia licencji")
        version_id = int(payload.get("version_id", 0))
        if version_id < 1:
            raise ValueError("Nieprawidłowy identyfikator wersji modelu")
        with league.connect() as connection:
            row = connection.execute(
                "SELECT id,source,license,metadata_json FROM model_versions WHERE id=?",
                (version_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Nieznana wersja modelu")
            source = (row["source"] or "").strip()
            license_name = (payload.get("license") or row["license"] or "").strip()
            metadata = json.loads(row["metadata_json"] or "{}")
            model_name = connection.execute(
                "SELECT name FROM league_models "
                "WHERE id=(SELECT model_id FROM model_versions WHERE id=?)",
                (version_id,),
            ).fetchone()["name"]
            if is_excluded_model_identity(
                name=model_name, source=source, metadata=metadata
            ):
                raise ValueError(
                    "Model jest trwale wyłączony z AI Chess Arena (Issue #71); "
                    "nie można zatwierdzić licencji"
                )
            evidence = metadata.get("license_evidence") or {}
            has_license_document = bool(
                evidence.get("license_files") or evidence.get("license")
            )
            if (
                not source or not source.startswith("https://")
                or not license_name
                or license_name.lower() in (
                    "nieustalona", "unknown", "n/a", "unverified", "unspecified", "none"
                )
            ):
                raise ValueError(
                    "Wersja wymaga źródła HTTPS i konkretnego identyfikatora licencji"
                )
            if not row["license"] and not has_license_document:
                raise ValueError(
                    "Najpierw pobierz dowody z repozytorium; "
                    "nie znaleziono deklaracji ani pliku licencji"
                )
            connection.execute(
                "UPDATE model_versions SET license=?,license_verified=1 WHERE id=?",
                (license_name, version_id),
            )
        return {"version_id": version_id, "license_verified": True}

    if path == "/api/models/register":
        return league.register_model(
            payload.get("name", ""),
            payload.get("filename"),
            payload.get("source", ""),
            payload.get("license", ""),
            payload.get("metadata"),
            payload.get("format", "PURE_AI"),
            bool(payload.get("license_verified", False)),
        )
    raise ValueError("Unknown panel model license route")
