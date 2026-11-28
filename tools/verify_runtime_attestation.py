"""Verify a runtime artifact against the repository's protected-main builder identity."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

REPOSITORY = "MarVinPL/ai-chess-arena"
SIGNER_WORKFLOW = "MarVinPL/ai-chess-arena/.github/workflows/runtime-artifact-attestation.yml"
SOURCE_REF = "refs/heads/main"
MIN_GH_VERSION = (2, 102, 0)  # GHSA-4mq3-hpgx-9cx8 fixed exact ref comparison in 2.102.0


class AttestationVerificationError(RuntimeError):
    pass


def _gh_version(gh: str) -> tuple[int, int, int]:
    result = subprocess.run([gh, "--version"], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AttestationVerificationError(f"Cannot execute GitHub CLI: {result.stderr.strip()}")
    match = re.search(r"gh version (\d+)\.(\d+)\.(\d+)", result.stdout)
    if not match:
        raise AttestationVerificationError("Unrecognized GitHub CLI version output.")
    return tuple(int(part) for part in match.groups())


def verify_artifact(artifact: str | Path, source_commit: str, gh: str = "gh") -> str:
    path = Path(artifact)
    if path.is_symlink() or not path.is_file():
        raise AttestationVerificationError("Artifact must be an existing regular, non-symlink file.")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source_commit):
        raise AttestationVerificationError("Source commit must be a full lowercase Git object ID.")

    version = _gh_version(gh)
    if version < MIN_GH_VERSION:
        required = ".".join(map(str, MIN_GH_VERSION))
        found = ".".join(map(str, version))
        raise AttestationVerificationError(
            f"GitHub CLI {required}+ is required for exact source-ref verification; found {found}."
        )

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    command = [
        gh, "attestation", "verify", str(path),
        "--repo", REPOSITORY,
        "--signer-workflow", SIGNER_WORKFLOW,
        "--source-ref", SOURCE_REF,
        "--source-digest", source_commit,
        "--predicate-type", "https://slsa.dev/provenance/v1",
        "--cert-oidc-issuer", "https://token.actions.githubusercontent.com",
        "--deny-self-hosted-runners",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise AttestationVerificationError(f"GitHub artifact attestation verification failed: {detail}")
    return digest



def verify_release_approval_statement(
    statement_path: str | Path, worker_path: str | Path, supervisor_path: str | Path, source_commit: str
) -> dict[str, str]:
    path = Path(statement_path)
    if path.is_symlink() or not path.is_file():
        raise AttestationVerificationError("Release approval statement must be a regular, non-symlink file.")
    try:
        statement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AttestationVerificationError(f"Release approval statement is not valid JSON: {exc}") from exc

    if not isinstance(statement, dict):
        raise AttestationVerificationError("Release approval statement root must be a JSON object.")

    expected = {
        "schema": "https://in-toto.io/Statement/v1",
        "_type": "https://in-toto.io/Statement/v1",
        "repository": REPOSITORY,
        "source_ref": SOURCE_REF,
        "source_commit": source_commit,
        "workflow": ".github/workflows/runtime-artifact-attestation.yml",
        "environment": "runtime-release",
        "required_reviewer": "MarVinPL",
        "approval_scope": "build-provenance-only; no runtime installation or model inference authorization",
    }
    for key, value in expected.items():
        if statement.get(key) != value:
            raise AttestationVerificationError(f"Release approval statement field {key!r} does not match policy.")
    if not isinstance(statement.get("run_id"), str) or not statement["run_id"].isdigit():
        raise AttestationVerificationError("Release approval statement has no valid GitHub Actions run ID.")

    artifact_paths = [Path(worker_path), Path(supervisor_path)]
    if any(item.is_symlink() or not item.is_file() for item in artifact_paths):
        raise AttestationVerificationError("Approved runtime artifacts must be regular, non-symlink files.")
    worker_digest = hashlib.sha256(artifact_paths[0].read_bytes()).hexdigest()
    supervisor_digest = hashlib.sha256(artifact_paths[1].read_bytes()).hexdigest()
    artifacts = statement.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"runtime_worker", "runtime_supervisor.pyz"}:
        raise AttestationVerificationError("Release approval statement artifact set does not match policy.")
    worker_entry = artifacts.get("runtime_worker")
    supervisor_entry = artifacts.get("runtime_supervisor.pyz")
    if not isinstance(worker_entry, dict) or not isinstance(supervisor_entry, dict):
        raise AttestationVerificationError("Release approval statement has no valid artifact digest map.")
    if worker_entry.get("sha256") != worker_digest:
        raise AttestationVerificationError("Release approval statement worker digest mismatch.")
    if supervisor_entry.get("sha256") != supervisor_digest:
        raise AttestationVerificationError("Release approval statement supervisor digest mismatch.")
    return {"worker_sha256": worker_digest, "supervisor_sha256": supervisor_digest, "run_id": statement["run_id"]}

def verify_approval_statement_attestation(
    statement_path: str | Path, worker_path: str | Path, supervisor_path: str | Path,
    source_commit: str, gh: str = "gh",
) -> dict[str, str]:
    # The approval payload is itself a signed subject. Never trust its JSON fields
    # until GitHub verifies the same protected-main workflow identity for it.
    statement_digest = verify_artifact(statement_path, source_commit, gh)
    bound = verify_release_approval_statement(
        statement_path, worker_path, supervisor_path, source_commit
    )
    return {"approval_statement_sha256": statement_digest, **bound}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subjects = parser.add_mutually_exclusive_group(required=True)
    subjects.add_argument("--artifact", type=Path)
    subjects.add_argument("--approval-statement", type=Path)
    parser.add_argument("--worker", type=Path, help="Required with --approval-statement")
    parser.add_argument("--supervisor", type=Path, help="Required with --approval-statement")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--gh", default="gh", help="GitHub CLI executable (default: gh)")
    args = parser.parse_args()
    if args.artifact is not None:
        if args.worker is not None or args.supervisor is not None:
            parser.error("--worker/--supervisor are only valid with --approval-statement")
        digest = verify_artifact(args.artifact, args.source_commit, args.gh)
        print(f"PASS repository={REPOSITORY} ref={SOURCE_REF} source_commit={args.source_commit} sha256={digest}")
        return 0
    if args.worker is None or args.supervisor is None:
        parser.error("--approval-statement requires both --worker and --supervisor")
    verified = verify_approval_statement_attestation(
        args.approval_statement, args.worker, args.supervisor, args.source_commit, args.gh
    )
    print(
        f"PASS signed_approval_statement_sha256={verified['approval_statement_sha256']} "
        f"worker_sha256={verified['worker_sha256']} "
        f"supervisor_sha256={verified['supervisor_sha256']} run_id={verified['run_id']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AttestationVerificationError as exc:
        raise SystemExit(f"FAIL: {exc}")
