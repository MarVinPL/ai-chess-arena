#!/usr/bin/env python3
"""Skip a Worker platform only when a previous verified artifact matches its sources.

This supplements paths-filter's PR-base diff. A PR may have Linux, Windows and
Android source changes, but only Android may need retrying after desktop jobs
already succeeded. If API access or Git history cannot prove reuse safe, BUILD.
No external dependencies or action packages are required.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from urllib.parse import quote
from urllib.request import Request, urlopen

SHARED_SOURCES = (
    "remote_worker_gui/package.json",
    "remote_worker_gui/package-lock.json",
    "remote_worker_gui/public",
    "remote_worker_gui/src-tauri/Cargo.toml",
    "remote_worker_gui/src-tauri/Cargo.lock",
    "remote_worker_gui/src-tauri/build.rs",
    "remote_worker_gui/src-tauri/src",
    "remote_worker_gui/src-tauri/capabilities",
    "remote_worker_gui/src-tauri/tauri.conf.json",
    "remote_worker_gui/src-tauri/icons/icon.png",
)

PLATFORM_SOURCES = {
    "linux": (
        "remote_worker_gui/src-tauri/gen/linux",
        "remote_worker_gui/src-tauri/linux",
        "remote_worker_gui/src-tauri/icons/32x32.png",
        "remote_worker_gui/src-tauri/icons/128x128.png",
        "remote_worker_gui/src-tauri/icons/128x128@2x.png",
    ),
    "windows": (
        "remote_worker_gui/src-tauri/gen/windows",
        "remote_worker_gui/src-tauri/windows",
        "remote_worker_gui/src-tauri/icons/icon.ico",
    ),
    "android": (
        "remote_worker_gui/src-tauri/gen/android",
        "remote_worker_gui/src-tauri/android",
    ),
}

ARTIFACT_NAMES = {
    "linux": "worker-linux-unsigned",
    "windows": "worker-windows-unsigned",
    "android": "ai-chess-arena-worker-arm64-debug",
}


def git_sources_match(previous_sha: str, paths: tuple[str, ...]) -> bool:
    """Only reuse an artifact if its source SHA is our ancestor and sources match."""
    if not previous_sha or len(previous_sha) != 40:
        return False
    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", previous_sha, "HEAD"],
            capture_output=True, check=False, timeout=10,
        )
        if ancestor.returncode != 0:
            return False
        diff = subprocess.run(
            ["git", "diff", "--quiet", previous_sha, "HEAD", "--", *paths],
            capture_output=True, check=False, timeout=20,
        )
        return diff.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def list_artifacts(repo: str, name: str, token: str) -> list[dict]:
    """Use GitHub artifact metadata only; never download archives or secrets."""
    base_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    collected: list[dict] = []
    for page in range(1, 4):
        url = (
            f"{base_url}/repos/{repo}/actions/artifacts?"
            f"name={quote(name)}&per_page=100&page={page}"
        )
        req = Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urlopen(req, timeout=20) as resp:
            items = json.load(resp).get("artifacts", [])
        if not isinstance(items, list):
            raise ValueError("Invalid GitHub artifacts response")
        collected.extend(items)
        if len(items) < 100:
            break
    return collected


def can_reuse_platform(
    platform: str, artifacts: list[dict], head_branch: str
) -> bool:
    """Other branches, expired archives and missing provenance never authorize reuse."""
    paths = SHARED_SOURCES + PLATFORM_SOURCES[platform]
    for artifact in artifacts:
        workflow = artifact.get("workflow_run") or {}
        if (
            artifact.get("name") != ARTIFACT_NAMES[platform]
            or artifact.get("expired") is not False
            or workflow.get("head_branch") != head_branch
        ):
            continue
        if git_sources_match(str(workflow.get("head_sha") or ""), paths):
            return True
    return False


def select_platforms(env: dict[str, str]) -> dict[str, bool]:
    shared = env.get("SHARED", "").lower() == "true"
    requested = {
        platform: shared or env.get(platform.upper(), "").lower() == "true"
        for platform in PLATFORM_SOURCES
    }
    # For main pushes, the PR run-reuse check is intentionally not used.
    # GitHub's push path filter and the PR-independent platform filter apply.
    if (
        env.get("GITHUB_EVENT_NAME") != "pull_request"
        or not env.get("GITHUB_HEAD_REF")
        or not env.get("GITHUB_TOKEN")
        or not env.get("GITHUB_REPOSITORY")
    ):
        return requested

    branch = env["GITHUB_HEAD_REF"]
    token = env["GITHUB_TOKEN"]
    repo = env["GITHUB_REPOSITORY"]
    for platform, should_build in requested.items():
        if not should_build:
            continue
        try:
            existing = list_artifacts(repo, ARTIFACT_NAMES[platform], token)
            if can_reuse_platform(platform, existing, branch):
                requested[platform] = False
                print(
                    f"Reusing previously uploaded {platform} artifact: "
                    "platform sources unchanged"
                )
            else:
                print(f"{platform}: no matching verified artifact; building")
        except (OSError, ValueError, TimeoutError) as exc:
            # Fail open: never silently skip an app build because of API failure.
            print(
                f"{platform}: artifact check unavailable ({type(exc).__name__}); "
                "building",
                file=sys.stderr,
            )
    return requested


def main() -> int:
    decisions = select_platforms(dict(os.environ))
    for platform, build in decisions.items():
        print(f"{platform}={'true' if build else 'false'}")
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with open(output_file, "a", encoding="utf-8") as out:
            for platform, build in decisions.items():
                out.write(f"{platform}={'true' if build else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
