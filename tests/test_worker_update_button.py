"""Manual Worker update CTA must never bypass release or APK safety gates."""
from pathlib import Path

UI = (Path(__file__).resolve().parents[1] /
      "remote_worker_gui/public/index.html")


def test_update_button_is_explicitly_manual_and_hidden_by_default():
    html = UI.read_text(encoding="utf-8")
    assert 'id="updateNow" class="release-action"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'aria-describedby="updateManualHelp" hidden>Aktualizuj</a>' in html
    assert 'id="updateManualHelp"' in html
    assert 'Nie pobiera ani nie instaluje APK.' in html
    assert '.release-action[hidden]{display:none}' in html


def test_button_cannot_show_for_equal_version_or_untrusted_release():
    html = UI.read_text(encoding="utf-8")
    assert "const updateNow=document.getElementById('updateNow');" in html
    assert 'updateNow.hidden=true;' in html
    assert "updateNow.removeAttribute('href');" in html
    assert "if(info.update_available===true){" in html
    assert "updateNow.href=url;" in html
    assert "updateNow.hidden=false;" in html
    assert "url==='https://github.com/MarVinPL/ai-chess-arena/releases/tag/'+tag" in html
    assert html.index("hideReleaseLink();\n status.textContent='Sprawdzanie wydań") < html.index("const info=await invoke('check_worker_release'")
    assert html.index("hideReleaseLink();\n document.getElementById('updateStatus')") > html.index("releaseChannel.addEventListener('change'")


def test_native_install_is_not_enabled_by_manual_button():
    html = UI.read_text(encoding="utf-8")
    assert "invoke('install_update')" not in html
    assert "invoke('download_update')" not in html
    assert "updateNow.addEventListener('click'" not in html
    assert "new WebSocket(" not in html
