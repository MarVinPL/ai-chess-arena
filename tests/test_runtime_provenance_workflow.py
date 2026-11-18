from pathlib import Path


def test_runtime_artifact_attestation_is_main_only_and_requires_release_environment():
    workflow_path = Path(__file__).resolve().parents[1] / ".github/workflows/runtime-artifact-attestation.yml"
    workflow = workflow_path.read_text()

    assert "  push:\n    branches: [main]" in workflow
    assert "      name: runtime-release" in workflow
    assert "      id-token: write" in workflow
    assert "      attestations: write" in workflow
    assert "      contents: read" in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert workflow.count("uses: actions/attest@") == 2
    assert "subject-path: ${{ runner.temp }}/runtime-artifacts/runtime_worker" in workflow
    assert "subject-path: ${{ runner.temp }}/runtime-artifacts/runtime_supervisor.pyz" in workflow
