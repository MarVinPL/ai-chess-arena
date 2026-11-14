"""Fail-closed, revision-pinned model admission allowlist.

This module only validates policy metadata. It never downloads weights,
runs inference, changes the tournament roster, or modifies a database.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ALLOWLIST_PATH = Path(__file__).resolve().parent / "manifests" / "model-admission-allowlist.json"
_SHA = re.compile(r"^[0-9a-f]{40}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}$")
_STATES = {"eligible", "restricted", "blocked", "review_required"}


class AllowlistError(ValueError):
    """Allowlist is malformed, stale, or insufficiently evidenced."""


def load_allowlist(path: Path = ALLOWLIST_PATH, *, expected_main_sha: str) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_allowlist(payload, expected_main_sha=expected_main_sha)
    return payload


def validate_allowlist(payload: dict, *, expected_main_sha: str) -> None:
    if not isinstance(expected_main_sha, str) or not _SHA.fullmatch(expected_main_sha):
        raise AllowlistError("Oczekiwany SHA main musi być pełnym commitem")
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise AllowlistError("Nieobsługiwana wersja allowlisty")
    if payload.get("source_main_sha") != expected_main_sha:
        raise AllowlistError("Allowlista pochodzi z innej wersji main; wymagana synchronizacja")
    policy = payload.get("policy")
    if not isinstance(policy, dict) or policy.get("default_decision") != "blocked":
        raise AllowlistError("Polityka musi domyślnie blokować modele")
    if policy.get("empty_list_means_no_models_eligible") is not True:
        raise AllowlistError("Pusta allowlista musi oznaczać brak kwalifikujących się modeli")
    models = payload.get("models")
    if not isinstance(models, list):
        raise AllowlistError("models musi być listą")

    seen = set()
    for item in models:
        if not isinstance(item, dict):
            raise AllowlistError("Nieprawidłowy wpis modelu")
        model_id = item.get("model_id")
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise AllowlistError("Nieprawidłowy stabilny model_id")
        if model_id in seen:
            raise AllowlistError("model_id musi być unikalny")
        seen.add(model_id)
        if item.get("state") not in _STATES:
            raise AllowlistError("Nieznany stan kwalifikacji")
        repo = item.get("repo_id")
        if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise AllowlistError("Brak dokładnego repo_id")
        if not isinstance(item.get("artifact_revision"), str) or not _SHA.fullmatch(item["artifact_revision"]):
            raise AllowlistError("artifact_revision musi być pełnym SHA")
        if not isinstance(item.get("audit_evidence_ref"), str) or not item["audit_evidence_ref"].strip():
            raise AllowlistError("Brak referencji do dowodów audytu")
        if not isinstance(item.get("reviewed_at"), str) or not item["reviewed_at"].strip():
            raise AllowlistError("Brak daty przeglądu")
        if not isinstance(item.get("scope"), str) or not item["scope"].strip():
            raise AllowlistError("Brak zakresu decyzji")
        if not isinstance(item.get("restrictions", []), list):
            raise AllowlistError("restrictions musi być listą")
        if not isinstance(item.get("blockers", []), list):
            raise AllowlistError("blockers musi być listą")
        if item["state"] == "eligible":
            if item.get("license_audit_status") != "verified_permissive":
                raise AllowlistError("eligible wymaga pozytywnego audytu licencji")
            if item.get("technical_status") != "ready":
                raise AllowlistError("eligible wymaga gotowości technicznej")
            if item.get("blockers"):
                raise AllowlistError("Model z blokadami nie może być eligible")
            if item.get("scope") != "tournament":
                raise AllowlistError("eligible wymaga jawnego zakresu tournament")


def is_eligible(payload: dict, model_id: str, *, expected_main_sha: str) -> bool:
    """Return True only for an explicitly and fully qualified exact model entry."""
    validate_allowlist(payload, expected_main_sha=expected_main_sha)
    for item in payload["models"]:
        if item["model_id"] == model_id:
            return item["state"] == "eligible"
    return False
