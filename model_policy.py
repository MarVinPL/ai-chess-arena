"""Fail-closed admission policy for explicitly deferred catalog entries.

The manifest's model IDs 9 and 10 identify two exact upstream repositories.
Runtime checks bind the exclusion to those repository identities, not to a
SQLite autoincrement value that may be reused by unrelated test/catalog rows.
Their license/status and manifest metadata are not rewritten; this policy only
prevents admission, download, license mutation, and runtime use until a separate
owner-reviewed PR.
"""
from __future__ import annotations

import json

EXCLUDED_MODEL_IDS = frozenset({9, 10})
EXCLUDED_MODEL_REPOS = frozenset({
    "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF",
    "mradermacher/JonathanColetti-Qwen3.8-27B-Uncensored-GGUF",
})
EXCLUSION_REASON = (
    "Wpis jest czasowo wyłączony z kwalifikacji decyzją właściciela projektu; "
    "jego status i metadane pozostają bez zmian do osobnego PR."
)


def _repo_from_source(source):
    prefix = "https://huggingface.co/"
    if not isinstance(source, str) or not source.startswith(prefix):
        return None
    tail = source[len(prefix):].split("/", 1)[0:2]
    return "/".join(tail) if len(tail) == 2 else None


def is_model_excluded(model_id=None, source=None, metadata=None) -> bool:
    """Match the exact deferred upstreams; unknown provenance stays fail-closed for IDs 9–10."""
    repo = _repo_from_source(source)
    if repo in EXCLUDED_MODEL_REPOS:
        return True
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = None
    if isinstance(metadata, dict):
        license_evidence = metadata.get("license_evidence")
        if (metadata.get("repo_id") in EXCLUDED_MODEL_REPOS or
                isinstance(license_evidence, dict) and license_evidence.get("repo_id") in EXCLUDED_MODEL_REPOS):
            return True
    if type(model_id) is int and model_id in EXCLUDED_MODEL_IDS and not source and not metadata:
        return True
    return False


def is_model_admission_allowed(model_id=None, source=None, metadata=None) -> bool:
    """Shared predicate for current and future admission/allowlist consumers."""
    return not is_model_excluded(model_id, source, metadata)


def assert_model_not_excluded(model_id, action: str, source=None, metadata=None) -> None:
    """Reject operations on deferred entries without mutating their records."""
    if is_model_excluded(model_id, source, metadata):
        raise ValueError(
            f"Model ID {model_id} jest wyłączony z operacji: {action}. "
            f"{EXCLUSION_REASON}"
        )
