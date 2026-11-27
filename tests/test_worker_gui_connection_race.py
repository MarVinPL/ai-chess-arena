"""Regression checks: delayed HTTPS checks cannot reactivate a stale Worker session."""
from pathlib import Path

UI = (Path(__file__).resolve().parents[1] /
      "remote_worker_gui/public/index.html")


def test_connection_changes_invalidate_previous_https_check():
    html = UI.read_text(encoding="utf-8")
    assert "let connectionRevision=0" in html
    assert "connectionRevision++" in html
    assert "const revision=connectionRevision" in html
    assert "if(revision!==connectionRevision ||" in html
    assert "server!==document.getElementById('server').value.trim()" in html
    assert "workerId!==document.getElementById('workerId').value.trim()" in html
    assert html.index("if(revision!==connectionRevision ||") < html.index(
        "verifiedJobPreviewConnection=ok?{server,workerId}:null")


def test_connection_check_is_single_flight_and_remains_manual():
    html = UI.read_text(encoding="utf-8")
    assert "if(checkingConnection)return;" in html
    assert "checkingConnection=true" in html
    assert "button.disabled=true" in html
    assert "checkingConnection=false" in html
    assert "button.disabled=false" in html
    assert "document.getElementById('pair').addEventListener('click'" in html
    # Polling inference/board is separate from manual authentication.
    assert "setInterval(" not in html.split("document.getElementById('pair').addEventListener('click'", 1)[1]
    assert "if(!phonePaired||!phoneWorkerEnabled||phoneWorkerRunning" in html
    assert "if(!phonePaired||workerJobPollInFlight||" in html
    assert "if(!phonePaired||workerBoardPollInFlight||" in html
    assert "phonePaired=false;phoneWorkerEnabled=false;refreshPhoneButtons();" in html


def test_token_changes_revoke_pending_connection_before_secret_mutation():
    html = UI.read_text(encoding="utf-8")
    assert html.index("invalidateJobPreview(); // Invalidate any pending HTTPS") < html.index(
        "await invoke('store_worker_token'")
    assert html.index("invalidateJobPreview(); // Pending HTTPS") < html.index(
        "await invoke('forget_worker_token'")
    assert "verifiedJobPreviewConnection=null;" in html
    assert "document.getElementById('previewJob').disabled=true" in html
