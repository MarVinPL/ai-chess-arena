"""Worker release policy: Android active, desktop builds explicitly paused."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/worker-multiplatform.yml"
NOTE = ROOT / "docs/worker-build-platforms.md"


def test_linux_windows_jobs_are_paused_without_deleting_job_steps():
    src = WORKFLOW.read_text(encoding="utf-8")
    for platform in ("linux", "windows"):
        assert "false && needs.detect.outputs." + platform in src
        assert "  " + platform + ":\n    needs: detect" in src
    assert "Build Linux executable" in src
    assert "Build Windows executable" in src


def test_android_remains_active_and_requires_real_source_changes():
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "  android:\n    needs: detect" in src
    assert "  android_signed:\n    needs: detect" in src
    assert "github.event_name == 'pull_request'" in src
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in src
    assert "    paths:" in src
    assert "workflow_dispatch:" not in src
    assert "remote_worker_gui/src-tauri/gen/android/**" in src


def test_desktop_only_sources_do_not_start_workflow():
    src = WORKFLOW.read_text(encoding="utf-8")
    top_level_lines = [line for line in src.splitlines() if line.startswith("      - ")]
    for platform in ("linux", "windows"):
        assert not any(f"/gen/{platform}/" in line for line in top_level_lines)
        assert not any(f"src-tauri/{platform}/" in line for line in top_level_lines)
    assert "on:\n  push:" in src
    assert "  pull_request:" in src


def test_decision_and_reactivation_instructions_are_recorded():
    note = NOTE.read_text(encoding="utf-8")
    for phrase in ("2026-11-22", "Android ARM64", "Linux", "Windows", "Issue #225"):
        assert phrase in note
    assert "How to resume desktop builds" in note
