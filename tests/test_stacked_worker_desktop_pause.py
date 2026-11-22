"""Guard desktop build suspension on the stacked Worker integration branch."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/worker-multiplatform.yml"


def test_stacked_desktop_matrix_disabled_but_preserved():
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "  desktop:\n    needs: changes" in src
    assert "false && (needs.changes.outputs.linux == 'true' || needs.changes.outputs.windows == 'true')" in src
    assert "Build desktop executable (no installer signing)" in src
    assert "worker-${{ matrix.platform }}-unsigned" in src


def test_android_build_stays_enabled_and_selective():
    src = WORKFLOW.read_text(encoding="utf-8")
    assert "  android:\n    needs: changes\n    if: needs.changes.outputs.android == 'true'" in src
    assert "python scripts/reuse_worker_artifacts.py" in src
    assert "workflow_dispatch:" not in src
