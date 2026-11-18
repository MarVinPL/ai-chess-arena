import hashlib
import subprocess
from pathlib import Path

import pytest

from tools.verify_runtime_attestation import (
    AttestationVerificationError,
    verify_artifact,
    verify_release_approval_statement,
)


COMMIT = "e" * 40


def test_verifier_uses_exact_repository_workflow_main_ref_and_commit(tmp_path, monkeypatch):
    artifact = tmp_path / "runtime_worker"
    artifact.write_bytes(b"test runtime artifact")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "gh version 2.105.0 (test)\n", "")
        return subprocess.CompletedProcess(command, 0, "verified", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    digest = verify_artifact(artifact, COMMIT, "gh-test")

    assert digest == hashlib.sha256(artifact.read_bytes()).hexdigest()
    verify_command = calls[1]
    assert verify_command[0:3] == ["gh-test", "attestation", "verify"]
    assert "MarVinPL/ai-chess-arena" in verify_command
    assert "MarVinPL/ai-chess-arena/.github/workflows/runtime-artifact-attestation.yml" in verify_command
    assert "refs/heads/main" in verify_command
    assert COMMIT in verify_command
    assert "--deny-self-hosted-runners" in verify_command


def test_verifier_rejects_old_gh_before_attestation_lookup(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"x")
    monkeypatch.setattr(
        subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "gh version 2.46.0\n", ""),
    )
    with pytest.raises(AttestationVerificationError, match="2.102.0\+ is required"):
        verify_artifact(artifact, COMMIT)


def test_verifier_rejects_symlink_and_invalid_commit(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.write_bytes(b"x")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(AttestationVerificationError, match="non-symlink"):
        verify_artifact(link, COMMIT)
    with pytest.raises(AttestationVerificationError, match="full lowercase Git object ID"):
        verify_artifact(target, "not-a-commit")


def test_verifier_fails_closed_when_gh_verification_fails(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"x")

    def fake_run(command, **kwargs):
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, "gh version 2.105.0\n", "")
        return subprocess.CompletedProcess(command, 1, "", "no matching attestation")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(AttestationVerificationError, match="no matching attestation"):
        verify_artifact(artifact, COMMIT)


def test_release_approval_statement_binds_commit_reviewer_scope_and_exact_artifacts(tmp_path):
    worker = tmp_path / "runtime_worker"
    supervisor = tmp_path / "runtime_supervisor.pyz"
    worker.write_bytes(b"worker")
    supervisor.write_bytes(b"supervisor")
    statement = tmp_path / "release-approval.json"
    payload = {
        "repository": "MarVinPL/ai-chess-arena",
        "source_ref": "refs/heads/main",
        "source_commit": COMMIT,
        "workflow": ".github/workflows/runtime-artifact-attestation.yml",
        "environment": "runtime-release",
        "required_reviewer": "MarVinPL",
        "approval_scope": "build-provenance-only; no runtime installation or model inference authorization",
        "run_id": "123456789",
        "artifacts": {
            "runtime_worker": {"sha256": hashlib.sha256(worker.read_bytes()).hexdigest()},
            "runtime_supervisor.pyz": {"sha256": hashlib.sha256(supervisor.read_bytes()).hexdigest()},
        },
    }
    import json
    statement.write_text(json.dumps(payload))
    verified = verify_release_approval_statement(statement, worker, supervisor, COMMIT)
    assert verified["run_id"] == "123456789"

    payload["required_reviewer"] = "someone-else"
    statement.write_text(json.dumps(payload))
    with pytest.raises(AttestationVerificationError, match="required_reviewer"):
        verify_release_approval_statement(statement, worker, supervisor, COMMIT)
