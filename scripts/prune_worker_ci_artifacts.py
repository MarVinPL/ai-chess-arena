#!/usr/bin/env python3
"""Keep the newest successfully uploaded Worker artifact per platform on main.

Only artifacts belonging to this workflow run prove that the replacement exists.
Never delete artifacts from another branch, a failed build, or a platform that
was not built. GitHub Actions artifacts still expire by their configured TTL.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ARTIFACT_JOBS = {
    "worker-linux-unsigned": "LINUX_RESULT",
    "worker-windows-unsigned": "WINDOWS_RESULT",
    "ai-chess-arena-worker-arm64-debug": "ANDROID_SIGNED_RESULT",
}


def select_old_artifact_ids(artifacts: list[dict], name: str, run_id: int) -> list[int]:
    """Return old main-branch IDs only if this run uploaded a replacement."""
    matching = [a for a in artifacts if a.get("name") == name
                and a.get("workflow_run", {}).get("head_branch") == "main"]
    # Run IDs are not a documented chronological ordering contract.
    # Require explicit creation timestamps and protect equal/newer runs.
    current = [a for a in matching if a.get("expired") is False
               and a["workflow_run"].get("id") == run_id]
    if len(current) != 1:
        return []
    from datetime import datetime
    def created(a):
        value = datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
        if value.tzinfo is None:
            raise ValueError("Missing timezone")
        return value
    replacement = current[0]
    try:
        cutoff = created(replacement)
        # A newer upload can belong to a rerun of an older run. Protect both
        # dimensions; skip ambiguous records instead of guessing.
        return sorted({a["id"] for a in matching
                       if type(a["id"]) is int and a["id"] > 0
                       and type(a["workflow_run"].get("id")) is int
                       and a["workflow_run"]["id"] < run_id
                       and created(a) < cutoff})
    except (KeyError, TypeError, ValueError, AttributeError):
        return []


def request_json(url: str, token: str, method: str = "GET") -> dict:
    req = Request(url, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(req, timeout=20) as response:
        if method == "DELETE":
            return {}
        return json.load(response)


def list_artifacts(repo: str, token: str, name: str) -> list[dict]:
    found: list[dict] = []
    for page in range(1, 101):
        url = (
            f"https://api.github.com/repos/{repo}/actions/artifacts"
            f"?name={quote(name)}&per_page=100&page={page}"
        )
        batch = request_json(url, token).get("artifacts", [])
        found.extend(batch)
        if len(batch) < 100:
            return found
    raise RuntimeError("Too many artifact pages; refusing incomplete cleanup")


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run = os.environ.get("GITHUB_RUN_ID", "")
    event = os.environ.get("GITHUB_EVENT_NAME")
    ref = os.environ.get("GITHUB_REF")
    if event != "push" or ref != "refs/heads/main":
        print("Skipping: not a main-branch push")
        return 0
    if not token or not repo or not run.isdecimal():
        print("Missing GitHub cleanup context; refusing deletion", file=sys.stderr)
        return 2
    run_id = int(run)
    try:
        for name, result_env in ARTIFACT_JOBS.items():
            if os.environ.get(result_env) != "success":
                continue
            artifacts = list_artifacts(repo, token, name)
            old_ids = select_old_artifact_ids(artifacts, name, run_id)
            if not old_ids:
                print(f"{name}: no verified new artifact or no older main artifact")
                continue
            for artifact_id in old_ids:
                # Revalidate the replacement and candidate before every DELETE.
                fresh = list_artifacts(repo, token, name)
                if artifact_id not in select_old_artifact_ids(fresh, name, run_id):
                    continue
                request_json(
                    f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}",
                    token, method="DELETE"
                )
                print(f"{name}: deleted older artifact {artifact_id}")
    except (HTTPError, URLError, OSError, ValueError, RuntimeError) as exc:
        print(f"Cleanup failed safely: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
