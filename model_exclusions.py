"""Hard exclusions for catalog entries that are not admitted to the Arena."""
from __future__ import annotations
import json
from urllib.parse import urlsplit

EXCLUDED_HF_REPOSITORIES = frozenset({
    "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF",
    "mradermacher/JonathanColetti-Qwen3.8-27B-Uncensored-GGUF",
})
EXCLUDED_MANIFEST_IDS = frozenset({9, 10})


def is_excluded_model_identity(*, name: str = "", source: str = "", metadata=None) -> bool:
    """Match only the exact excluded repositories, never a broad name substring."""
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    repo_ids = {metadata.get("repo_id"), metadata.get("upstream_repo_id")}
    if any(isinstance(repo, str) and repo in EXCLUDED_HF_REPOSITORIES for repo in repo_ids):
        return True
    if isinstance(source, str) and source:
        parsed = urlsplit(source)
        if parsed.scheme == "https" and parsed.netloc.lower() == "huggingface.co":
            path = parsed.path.strip("/")
            for repo in EXCLUDED_HF_REPOSITORIES:
                if path == repo or path.startswith(repo + "/"):
                    return True
    return isinstance(name, str) and name.strip() in EXCLUDED_HF_REPOSITORIES
