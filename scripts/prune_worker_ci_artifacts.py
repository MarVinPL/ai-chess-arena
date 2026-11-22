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

ARTIFACT_NAMES = (
    "worker-linux-unsigned",
    "worker-windows-unsigned",
    "ai-chess-arena-worker-arm64-debug",
)


def select_old_artifact_ids(artifacts: list[dict], name: str, run_id: int) -> list[int]:
    """Return old main-branch IDs only if this run uploaded a replacement."""
    matching = [a for a in artifacts if a.get("name") == name]
    current = [
        a for a in matching
        if not a.get("expired")
        and a.get("workflow_run", {}).get("id") == run_id
        and a.get("workflow_run", {}).get("head_branch") == "main"
    ]
    if not current:
        return []
    current_ids = {int(a["id"]) for a in current}
    return sorted({
        int(a["id"])
        for a in matching
        if a.get("workflow_run", {}).get("head_branch") == "main"
        and int(a["id"]) not in current_ids
    })


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
        for name in ARTIFACT_NAMES:
            artifacts = list_artifacts(repo, token, name)
            old_ids = select_old_artifact_ids(artifacts, name, run_id)
            if not old_ids:
                print(f"{name}: no verified new artifact or no older main artifact")
                continue
            for artifact_id in old_ids:
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
