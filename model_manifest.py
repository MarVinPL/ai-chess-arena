"""Pinned Hugging Face GGUF manifests and safe metadata-only import.

This module never downloads model weights. License declarations are evidence,
not approval: entries requiring review remain blocked for managed downloads.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

MANIFEST_PATH = Path(__file__).resolve().parent / "manifests" / "huggingface-models.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,180}\.gguf$", re.I)
_ALLOWED_LICENSES = {"apache-2.0", "llama3.1", "llama3.2", "cc-by-nc-4.0"}
_LICENSE_REVIEW_STATUSES = {"verified_permissive", "verified_restricted", "review_required", "incompatible", "evidence_missing"}
_LICENSE_AUDIT_REPORT = "docs/model-audit/license-review-2026-11-14.md"
_AUDIT_MODEL_IDS = frozenset(range(1, 9))


class ManifestError(ValueError):
    """Manifest metadata is incomplete, unsafe, or inconsistent."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_manifest(payload)
    return payload


def validate_manifest(payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ManifestError("Nieobsługiwana wersja manifestu")
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        raise ManifestError("Manifest nie zawiera modeli")
    seen_ids, seen_repos = set(), set()
    for item in models:
        if not isinstance(item, dict):
            raise ManifestError("Nieprawidłowy wpis manifestu")
        model_id, repo = item.get("model_id"), item.get("repo_id")
        if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id <= 0:
            raise ManifestError("Nieprawidłowy model_id")
        if model_id in seen_ids or not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ManifestError("model_id/repo_id duplikat lub nieprawidłowy")
        if repo in seen_repos:
            raise ManifestError("Repozytorium występuje więcej niż raz")
        seen_ids.add(model_id); seen_repos.add(repo)
        filename, size, digest, revision = (item.get(k) for k in ("filename", "size_bytes", "sha256", "revision"))
        if not isinstance(filename, str) or not _FILENAME.fullmatch(filename) or "draft" in filename.lower() or ".mmproj" in filename.lower():
            raise ManifestError("Nieprawidłowa nazwa GGUF albo plik pomocniczy/draft")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ManifestError("Brak wiarygodnego rozmiaru pliku")
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ManifestError("Brak wiarygodnego SHA-256")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ManifestError("Revision musi być pełnym SHA commit")
        if item.get("license") not in _ALLOWED_LICENSES:
            raise ManifestError("Brak rozpoznanej deklaracji licencji")
        review_status = item.get("license_review_status")
        if review_status not in _LICENSE_REVIEW_STATUSES:
            raise ManifestError("Nieznany status audytu licencji")
        if review_status in {"verified_permissive", "verified_restricted", "incompatible"}:
            audit = item.get("license_audit")
            if not isinstance(audit, dict) or audit.get("status") != review_status:
                raise ManifestError("Status audytu wymaga kompletnego obiektu license_audit")
            if audit.get("reviewed_revision") != revision:
                raise ManifestError("Dowód audytu licencji musi dotyczyć dokładnej rewizji artefaktu")
            if audit.get("report") != _LICENSE_AUDIT_REPORT:
                raise ManifestError("Brak referencji do raportu audytu licencji")
            if not isinstance(audit.get("reviewed_at"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", audit["reviewed_at"]):
                raise ManifestError("Nieprawidłowa data audytu licencji")
            evidence = audit.get("evidence")
            if not isinstance(evidence, list) or len(evidence) < 2 or not all(
                isinstance(url, str) and url.startswith("https://") for url in evidence
            ):
                raise ManifestError("Audyt licencji wymaga co najmniej dwóch źródeł HTTPS")
            if not isinstance(audit.get("summary"), str) or not audit["summary"].strip():
                raise ManifestError("Audyt licencji wymaga uzasadnienia")
        if not isinstance(item.get("license_url"), str) or not item["license_url"].startswith(f"https://huggingface.co/{repo}/"):
            raise ManifestError("Dowód licencji musi wskazywać to samo repozytorium")
        if not isinstance(item.get("license_terms_url"), str) or not item["license_terms_url"].startswith("https://"):
            raise ManifestError("Brak odnośnika do warunków licencji")
        expected_repo_url = f"https://huggingface.co/{repo}"
        expected_download = f"{expected_repo_url}/resolve/{revision}/{quote(item.get('source_path', ''), safe='/')}"
        if item.get("source_path") != filename:
            raise ManifestError("Ścieżka źródłowa nie zgadza się z wybranym plikiem")
        if item.get("download_url") not in (None, expected_download):
            raise ManifestError("Download URL nie jest przypięty do repo/revision")
        for excluded in item.get("excluded_files", []):
            if excluded.get("filename") == filename:
                raise ManifestError("Plik draft nie może być wybrany jako pełny model")


def apply_manifest(db_path: str | Path, payload: dict | None = None,
                   applied_at: str | None = None) -> dict:
    """Persist verified remote file metadata, without approving licenses or downloading.

    Updates existing registry versions only. It refuses missing/ambiguous rows and
    wraps only audit-scope records (model IDs 1–8) in one SQLite transaction.
    Entries 9–10 are preserved verbatim for the separate Issue #71. The existing
    model row's license_verified flag is always forced to 0. Even a revision-bound
    verified_restricted audit is not owner approval to use or distribute weights.
    """
    payload = payload or load_manifest()
    validate_manifest(payload)
    audit_models = [item for item in payload["models"] if item["model_id"] in _AUDIT_MODEL_IDS]
    if {item["model_id"] for item in audit_models} != _AUDIT_MODEL_IDS:
        raise ManifestError("Audyt #52 wymaga dokładnie wpisów model_id 1–8")
    applied_at = applied_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    updated = []
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        with connection:
            for item in audit_models:
                repo_url = f"https://huggingface.co/{item['repo_id']}"
                rows = connection.execute(
                    """SELECT v.id, v.metadata_json, v.managed_copy, v.status
                       FROM model_versions v
                       JOIN league_models m ON m.id=v.model_id
                       WHERE v.model_id=? AND (v.source=? OR v.source LIKE ?)""",
                    (item["model_id"], repo_url, f"{repo_url}/resolve/%"),
                ).fetchall()
                if len(rows) != 1:
                    raise ManifestError(f"Model {item['model_id']} ({item['repo_id']}): oczekiwano 1 wersji, znaleziono {len(rows)}")
                row = rows[0]
                if row["managed_copy"] == 1:
                    raise ManifestError("Import nie może przepinać już zarządzanego pliku GGUF")
                metadata = json.loads(row["metadata_json"] or "{}")
                metadata.update({
                    "provider": "Hugging Face",
                    "repo_id": item["repo_id"],
                    "filename": item["filename"],
                    "source_path": item["source_path"],
                    "revision": item["revision"],
                    "size_bytes": item["size_bytes"],
                    "sha256": item["sha256"],
                    "download_url": f"{repo_url}/resolve/{item['revision']}/{quote(item['source_path'], safe='/')}",
                    "manifest_audit": {
                        "source": "Hugging Face public model/tree API",
                        "applied_at": applied_at,
                        "license": item["license"],
                        "license_review_status": item["license_review_status"],
                        "license_evidence_url": item["license_url"],
                        "license_terms_url": item["license_terms_url"],
                        "license_notes": item["license_notes"],
                        "license_audit": item.get("license_audit"),
                        "weights_downloaded": False,
                    },
                })
                connection.execute(
                    """UPDATE model_versions
                       SET filename=?, sha256=?, size_bytes=?, metadata_json=?,
                           license=?, license_verified=0,
                           status=CASE WHEN managed_copy=1 THEN status ELSE 'catalogued' END
                       WHERE id=?""",
                    (item["filename"], item["sha256"], item["size_bytes"],
                     json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                     item["license"], row["id"]),
                )
                updated.append({"model_id": item["model_id"], "version_id": row["id"],
                                "filename": item["filename"], "license_verified": False})
    finally:
        connection.close()
    return {"updated": updated, "weights_downloaded": False,
            "license_approvals_granted": 0, "applied_at": applied_at}
