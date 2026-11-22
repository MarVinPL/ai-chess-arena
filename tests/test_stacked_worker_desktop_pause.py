"""Guard desktop build suspension on the stacked Worker integration branch."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/worker-multiplatform.yml"


def test_stacked_desktop_jobs_disabled_but_preserved():
    src = WORKFLOW.read_text(encoding="utf-8")
    for platform in ("linux", "windows"):
        assert "  " + platform + ":" + chr(10) + "    needs: detect" in src
        assert "false && needs.detect.outputs." + platform + " == 'true'" in src
    assert "Build Linux executable" in src
    assert "Build Windows executable" in src


def test_android_build_stays_enabled_and_selective():
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "  android:" + chr(10) + "    needs: detect" in src
    assert "  android_signed:" + chr(10) + "    needs: detect" in src
    assert "github.event_name == 'pull_request'" in src
    assert "github.event_name == 'push' && github.ref == 'refs/heads/main'" in src
    assert "python scripts/reuse_worker_artifacts.py" in src
    assert "workflow_dispatch:" not in src
