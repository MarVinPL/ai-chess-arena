#!/usr/bin/env python3
"""Metadata-only Hugging Face GGUF catalog discovery.

Never downloads model files, reads model cards, approves licenses, or changes
Arena registry/tournament state. Writes only the informational candidate list.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "manifests" / "huggingface-candidates.json"
API_ROOT = "https://huggingface.co/api/models"
PAGE_SIZE = 100
MAX_PAGES = 20
USER_AGENT = "AI-Chess-Arena-Catalog-Sync/1.0 (+metadata-only)"
_SHA = re.compile(r"^[0-9a-f]{40}$", re.I)
_BLOCKED_TAGS = {
    "adult", "erotic", "nsfw", "porn", "pornography", "sexual",
    "uncensored", "gore",
}
_BLOCKED_TERMS = (
    "cyber", "hacking", "exploit", "malware", "phishing", "ransomware", "pentest", "red-team", "redteam",
    "uncensored", "heretic", "weapon", "firearm", "gun", "knife", "knives", "drug", "narcotic",
    "intoxicant", "alcohol", "gambling", "betting", "casino", "explosive", "bomb", "ammo",
    "ammunition", "self-harm", "selfharm", "suicide",
)
_HELPER_MARKERS = ("mmproj", "imatrix", "draft")


class CatalogSyncError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _request_json(url: str) -> tuple[object, str | None]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "huggingface.co" or not parsed.path.startswith("/api/models"):
        raise CatalogSyncError("Odmowa żądania poza publicznym API Hugging Face")
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
            link = response.headers.get("Link")
    except HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise CatalogSyncError(f"Hugging Face API HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise CatalogSyncError(f"Nie udało się pobrać metadanych Hugging Face: {exc}") from exc
    next_url = None
    if link:
        for part in link.split(","):
            match = re.match(r'\s*<([^>]+)>;\s*rel="next"', part)
            if match:
                next_url = urljoin(url, match.group(1))
                break
    return payload, next_url


def _initial_url() -> str:
    params = [
        ("filter", "gguf"), ("limit", str(PAGE_SIZE)),
        ("sort", "lastModified"), ("direction", "-1"),
    ]
    for field in ("sha", "lastModified", "createdAt", "tags", "siblings",
                  "pipeline_tag", "library_name", "private", "gated", "disabled"):
        params.append(("expand[]", field))
    return f"{API_ROOT}?{urlencode(params)}"


def fetch_catalog(max_pages: int = MAX_PAGES,
                  request_json: Callable[[str], tuple[object, str | None]] = _request_json
                  ) -> tuple[list[dict], bool, int]:
    """Fetch bounded pages; incomplete scans never imply repository deletion."""
    url = _initial_url()
    records: list[dict] = []
    seen_urls: set[str] = set()
    pages = 0
    next_url: str | None = url
    while next_url and pages < max_pages:
        if next_url in seen_urls:
            raise CatalogSyncError("API zwróciło zapętloną paginację")
        seen_urls.add(next_url)
        payload, following = request_json(next_url)
        if not isinstance(payload, list):
            raise CatalogSyncError("API zwróciło nieprawidłową listę modeli")
        if pages == 0 and not payload:
            raise CatalogSyncError("API zwróciło pusty katalog; zachowano poprzedni manifest")
        if not all(isinstance(item, dict) for item in payload):
            raise CatalogSyncError("API zwróciło nieprawidłowy wpis modelu")
        records.extend(payload)
        pages += 1
        next_url = following
    return records, next_url is None, pages


def _is_blocked_record(record: dict) -> bool:
    repo_id = record.get("id") if isinstance(record.get("id"), str) else ""
    tags = record.get("tags") if isinstance(record.get("tags"), list) else []
    normalized = [tag.casefold() for tag in tags if isinstance(tag, str)]
    repo_tokens = [token for token in re.split(r"[./_-]+", repo_id.casefold()) if token]
    tag_tokens = [token for tag in normalized for token in re.split(r"[./_:-]+", tag) if token]
    siblings = record.get("siblings") if isinstance(record.get("siblings"), list) else []
    file_tokens = [token for item in siblings if isinstance(item, dict)
                   and isinstance(item.get("rfilename"), str)
                   for token in re.split(r"[./_:-]+", item["rfilename"].casefold()) if token]
    tag_blocked = bool(set(normalized) & _BLOCKED_TAGS) or any(
        tag.startswith(_BLOCKED_TERMS) for tag in normalized
    ) or any(token.startswith(_BLOCKED_TERMS) for token in tag_tokens)
    repo_blocked = any(token.startswith(_BLOCKED_TERMS) for token in repo_tokens)
    file_blocked = any(token.startswith(_BLOCKED_TERMS) for token in file_tokens)
    return tag_blocked or repo_blocked or file_blocked


def _redacted_history(history: object) -> list[dict]:
    if not isinstance(history, list):
        return []
    allowed = {"event", "at", "from_revision", "to_revision", "revision", "previous_status"}
    return [{key: value for key, value in event.items() if key in allowed}
            for event in history if isinstance(event, dict)]


def _redacted_policy_tombstone(previous: dict, now: str) -> dict:
    repo_id = previous.get("repo_id", "")
    fingerprint = hashlib.sha256(str(repo_id).encode("utf-8")).hexdigest()
    history = _redacted_history(previous.get("history"))
    if not history or history[-1].get("event") != "filtered_by_discovery_policy":
        history.append({"event": "filtered_by_discovery_policy", "at": now})
    return {
        "repository_fingerprint": fingerprint,
        "detected_at": previous.get("detected_at", now),
        "last_verified_at": previous.get("last_verified_at", now),
        "status": "filtered_by_discovery_policy",
        "status_checked_at": now,
        "review_required": True,
        "history": history,
    }


def _safe_gguf_files(record: dict) -> list[str]:
    siblings = record.get("siblings")
    if not isinstance(siblings, list):
        return []
    files = []
    for item in siblings:
        if not isinstance(item, dict):
            continue
        name = item.get("rfilename")
        if not isinstance(name, str) or not name.lower().endswith(".gguf"):
            continue
        lower = name.lower()
        if any(marker in lower for marker in _HELPER_MARKERS):
            continue
        files.append(name)
    return sorted(set(files))


def normalize_candidate(record: dict, now: str) -> dict | None:
    """Return only public text-generation repos with a usable GGUF target file."""
    repo_id = record.get("id")
    if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id):
        return None
    # Apply discovery safety exclusions before validating optional metadata fields.
    if _is_blocked_record(record):
        return None
    revision = record.get("sha")
    tags = record.get("tags")
    if not isinstance(revision, str) or not _SHA.fullmatch(revision):
        raise CatalogSyncError(f"Brak pełnej rewizji repozytorium {repo_id}")
    if not isinstance(record.get("private"), bool) or not isinstance(record.get("disabled"), bool):
        raise CatalogSyncError(f"Brak statusu widoczności repozytorium {repo_id}")
    if record["private"] or record["disabled"]:
        return None
    gated = record.get("gated")
    if gated is not None and not isinstance(gated, (bool, str)):
        raise CatalogSyncError(f"Nieprawidłowy status gated repozytorium {repo_id}")
    if isinstance(gated, str) and not gated.strip():
        raise CatalogSyncError(f"Nieprawidłowy status gated repozytorium {repo_id}")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise CatalogSyncError(f"Nieprawidłowe tagi repozytorium {repo_id}")
    normalized_tags = sorted(set(tags))
    pipeline = record.get("pipeline_tag")
    if pipeline is not None and not isinstance(pipeline, str):
        raise CatalogSyncError(f"Nieprawidłowe zadanie repozytorium {repo_id}")
    library_name = record.get("library_name")
    if library_name is not None and not isinstance(library_name, str):
        raise CatalogSyncError(f"Nieprawidłowa biblioteka repozytorium {repo_id}")
    if pipeline != "text-generation" and "text-generation" not in normalized_tags:
        return None
    gguf_files = _safe_gguf_files(record)
    if not gguf_files:
        return None
    created_at = record.get("createdAt")
    last_modified = record.get("lastModified")
    if not isinstance(created_at, str) or not isinstance(last_modified, str):
        raise CatalogSyncError(f"Brak dat metadanych repozytorium {repo_id}")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        datetime.fromisoformat(last_modified.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CatalogSyncError(f"Nieprawidłowa data metadanych repozytorium {repo_id}") from exc
    license_tags = [tag.split(":", 1)[1].lower() for tag in normalized_tags if tag.lower().startswith("license:")]
    license_tag = license_tags[0] if len(license_tags) == 1 else None
    return {
        "repo_id": repo_id,
        "model_name": repo_id.split("/", 1)[1],
        "author": repo_id.split("/", 1)[0],
        "model_url": f"https://huggingface.co/{repo_id}",
        "revision": revision.lower(),
        "created_at": created_at,
        "last_modified": last_modified,
        "last_verified_at": now,
        "pipeline_tag": pipeline or "text-generation",
        "library_name": library_name,
        "license_tag": license_tag,
        "gated": gated,
        "gguf_file_count": len(gguf_files),
        "gguf_files_sample": gguf_files[:12],
        "tags": normalized_tags,
        "detected_at": now,
        "status": "unverified",
        "review_required": True,
    }


def _metadata_fingerprint(item: dict) -> str:
    keys = ("revision", "created_at", "last_modified", "pipeline_tag", "library_name",
            "license_tag", "gated", "gguf_file_count", "gguf_files_sample", "tags")
    return json.dumps({key: item.get(key) for key in keys}, sort_keys=True, ensure_ascii=False)


def build_manifest(existing: dict | None, records: list[dict], now: str,
                   scan_complete: bool,
                   detail_fetcher: Callable[[str], dict | None] | None = None,
                   max_pages: int = MAX_PAGES) -> dict:
    existing = existing or {"schema_version": 1, "provider": "Hugging Face", "models": []}
    old_models = existing.get("models", [])
    if not isinstance(old_models, list):
        raise CatalogSyncError("Dotychczasowy manifest kandydatów ma nieprawidłową strukturę")
    old_by_repo = {item["repo_id"]: item for item in old_models if isinstance(item, dict) and isinstance(item.get("repo_id"), str)}
    current: dict[str, dict] = {}
    # Keep redacted policy tombstones across later runs without restoring locator data.
    for item in old_models:
        if (isinstance(item, dict) and item.get("status") == "filtered_by_discovery_policy"
                and isinstance(item.get("repository_fingerprint"), str)):
            current[f"filtered:{item['repository_fingerprint']}"] = dict(item)
    for raw in records:
        item = normalize_candidate(raw, now)
        if item is None:
            continue
        repo = item["repo_id"]
        if repo in current:
            if current[repo]["revision"] != item["revision"]:
                raise CatalogSyncError(f"Niespójne rewizje w jednym skanie repozytorium {repo}")
            continue
        previous = old_by_repo.get(repo)
        if previous:
            item["detected_at"] = previous.get("detected_at", now)
            item["status"] = "unverified"
            item["review_required"] = True
            changed = _metadata_fingerprint(previous) != _metadata_fingerprint(item)
            item["last_verified_at"] = now if changed else previous.get("last_verified_at", now)
            history = list(previous.get("history", [])) if isinstance(previous.get("history", []), list) else []
            if previous.get("status") in {"unavailable", "renamed", "not_matching"}:
                history.append({"event": "repository_restored", "at": now, "previous_status": previous.get("status")})
            if previous.get("revision") != item["revision"]:
                history.append({"event": "revision_changed", "at": now,
                                "from_revision": previous.get("revision"), "to_revision": item["revision"]})
            elif changed:
                history.append({"event": "metadata_changed", "at": now})
            item["history"] = history
        else:
            item["history"] = [{"event": "discovered", "at": now, "revision": item["revision"]}]
        current[repo] = item

    # Search pagination is bounded, so absence from search results is never
    # treated as deletion. Verify each previously known, unseen repo directly.
    for repo, previous in old_by_repo.items():
        if repo in current:
            continue
        if detail_fetcher is None:
            raise CatalogSyncError("Brak bezpośredniej weryfikacji repozytorium nieobecnego w wynikach wyszukiwania")
        detail = detail_fetcher(repo)
        if detail is None:
            item = dict(previous)
            if item.get("status") != "unavailable":
                history = list(item.get("history", []))
                history.append({"event": "repository_unavailable", "at": now})
                item["history"] = history
                item["status_checked_at"] = now
            item["status"] = "unavailable"
            item["review_required"] = True
            current[repo] = item
            continue
        canonical_id = detail.get("id") if isinstance(detail, dict) else None
        if isinstance(detail, dict) and _is_blocked_record(detail):
            fingerprint = hashlib.sha256(repo.encode("utf-8")).hexdigest()
            current[f"filtered:{fingerprint}"] = _redacted_policy_tombstone(previous, now)
            continue
        normalized = normalize_candidate(detail, now) if isinstance(detail, dict) else None
        if isinstance(canonical_id, str) and canonical_id != repo:
            old_item = dict(previous)
            if old_item.get("status") != "renamed" or old_item.get("renamed_to") != canonical_id:
                old_item["status"] = "renamed"
                old_item["renamed_to"] = canonical_id
                old_item["status_checked_at"] = now
                old_item["history"] = list(old_item.get("history", [])) + [{"event": "repository_renamed", "at": now, "to_repo": canonical_id}]
            current[repo] = old_item
            if normalized:
                canonical_previous = old_by_repo.get(canonical_id)
                if canonical_previous:
                    normalized["detected_at"] = canonical_previous.get("detected_at", now)
                    normalized["history"] = list(canonical_previous.get("history", []))
                    normalized["last_verified_at"] = (now if _metadata_fingerprint(canonical_previous) != _metadata_fingerprint(normalized)
                                                       else canonical_previous.get("last_verified_at", now))
                else:
                    normalized["detected_at"] = now
                    normalized["history"] = [{"event": "repository_renamed", "at": now, "from_repo": repo}]
                    normalized["last_verified_at"] = now
                normalized["renamed_from"] = repo
                current[canonical_id] = normalized
        elif normalized is None:
            item = dict(previous)
            if item.get("status") != "not_matching":
                item["status_checked_at"] = now
                item["history"] = list(item.get("history", [])) + [{"event": "no_longer_matches", "at": now}]
            item["status"] = "not_matching"
            current[repo] = item
        else:
            # Still exists, but may no longer be in the first-party GGUF search index.
            normalized["detected_at"] = previous.get("detected_at", now)
            normalized["history"] = list(previous.get("history", []))
            if previous.get("status") in {"unavailable", "renamed", "not_matching"}:
                normalized["history"].append({"event": "repository_restored", "at": now,
                                               "previous_status": previous.get("status")})
            if _metadata_fingerprint(previous) != _metadata_fingerprint(normalized):
                normalized["last_verified_at"] = now
                normalized["history"].append({"event": "metadata_changed", "at": now})
            else:
                normalized["last_verified_at"] = previous.get("last_verified_at", now)
            current[repo] = normalized

    payload = {
        "schema_version": 1,
        "provider": "Hugging Face",
        "scan": {
            "status": "complete" if scan_complete else "partial",
            "page_size": PAGE_SIZE,
            "max_pages": max_pages,
            "absence_handling": "verify_each_known_repo_before_marking_unavailable",
        },
        "source": {
            "api": API_ROOT,
            "filter": "gguf",
            "candidate_criteria": ["public repository", "text-generation task", "at least one non-helper .gguf file"],
            "metadata_only": True,
        },
        "policy": {
            "weights_downloaded": False,
            "inference_run": False,
            "license_approved_automatically": False,
            "new_or_changed_candidates": "unverified",
            "note": "Wpis katalogowy nie oznacza audytu licencji, gotowości technicznej ani dopuszczenia do turnieju.",
        },
        "models": sorted(current.values(), key=lambda item: item.get("repo_id", item.get("repository_fingerprint", "")).casefold()),
    }
    comparable_old = {key: value for key, value in existing.items() if key != "updated_at"}
    if {key: value for key, value in payload.items() if key != "updated_at"} != comparable_old:
        payload["updated_at"] = now
    else:
        payload["updated_at"] = existing.get("updated_at", now)
    return payload


def fetch_repo_detail(repo_id: str) -> dict | None:
    if not isinstance(repo_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_id):
        raise CatalogSyncError("Nieprawidłowy identyfikator repozytorium w poprzednim manifeście")
    # Keep unauthenticated API traffic modest during per-repository absence checks.
    time.sleep(0.2)
    params = [("expand[]", field) for field in ("sha", "lastModified", "createdAt", "tags", "siblings", "pipeline_tag", "library_name", "private", "gated", "disabled")]
    url = f"{API_ROOT}/{repo_id}?{urlencode(params)}"
    payload, _ = _request_json(url)
    if payload is not None and not isinstance(payload, dict):
        raise CatalogSyncError(f"Nieprawidłowe szczegóły repozytorium {repo_id}")
    return payload


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    args = parser.parse_args(argv)
    if args.max_pages < 1 or args.max_pages > 100:
        parser.error("--max-pages musi mieścić się w zakresie 1–100")
    now = utc_now()
    try:
        records, complete, pages = fetch_catalog(args.max_pages)
        existing = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else None
        payload = build_manifest(existing, records, now, complete, fetch_repo_detail, args.max_pages)
        before = json.dumps(existing, ensure_ascii=False, sort_keys=True) if existing is not None else None
        after = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        changed = before != after
        if changed:
            write_json_atomic(args.output, payload)
        active = sum(item.get("status") == "unverified" for item in payload["models"])
        print(json.dumps({"checked_at": now, "pages": pages, "scan_complete": complete,
                          "models_seen": len(records), "candidates": active,
                          "manifest_entries": len(payload["models"]), "changed": changed,
                          "output": str(args.output)}, ensure_ascii=False, sort_keys=True))
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write("## Hugging Face catalog sync\n\n")
                summary.write(f"- Check: `{now}`\n- Pages: `{pages}`\n- Scan complete: `{str(complete).lower()}`\n")
                summary.write(f"- API entries: `{len(records)}`\n- Candidate entries: `{active}`\n- Manifest changed: `{str(changed).lower()}`\n")
                if not complete:
                    summary.write("\n> Skan osiągnął limit stron. Nie oznaczono nieobecnych repozytoriów jako usuniętych.\n")
        return 0
    except (CatalogSyncError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if "GITHUB_STEP_SUMMARY" in os.environ:
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
                summary.write(f"## Hugging Face catalog sync — ERROR\n\n`{exc}`\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
