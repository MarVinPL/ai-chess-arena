"""Android Worker release channels regression (Issue #243)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'remote_worker_gui'
HTML = (ROOT / 'public' / 'index.html').read_text(encoding='utf-8')
RUST = (ROOT / 'src-tauri' / 'src' / 'lib.rs').read_text(encoding='utf-8')


def test_channel_selector_and_local_persistence():
    assert '<label for="updateChannel">Kanał aktualizacji</label>' in HTML
    assert '<select id="updateChannel" aria-describedby="updateChannelHelp">' in HTML
    assert '<option value="latest">Latest' in HTML
    assert '<option value="pre-release">Pre-release' in HTML
    assert "const updateChannelKey='arena-worker-release-channel-v1';" in HTML
    assert "return value==='pre-release'?'pre-release':'latest';" in HTML
    assert "localStorage.setItem(updateChannelKey,channel)" in HTML
    assert "releaseChannel.addEventListener('change'" in HTML
    assert "invoke('check_worker_release',{channel})" in HTML
    assert "updateLastKey+'-'+releaseChannel.value" in HTML
    assert "if(channel!==releaseChannel.value)return;" in HTML
    assert "localStorage.setItem(token" not in HTML
    assert "localStorage.setItem(field.value" not in HTML


def test_native_channel_filter_and_only_verified_worker_links():
    assert 'async fn check_worker_release(channel: String)' in RUST
    assert 'WorkerReleaseChannel::parse(&channel)?' in RUST
    assert '"latest" => Ok(Self::Latest)' in RUST
    assert '"pre-release" => Ok(Self::PreRelease)' in RUST
    assert 'Some(channel.is_prerelease())' in RUST
    assert 'tag.strip_prefix("worker-v")' in RUST
    assert 'worker_release_version(tag, channel)' in RUST
    assert 'worker-v0.1.0-test.1' in RUST
    assert 'url.path() == expected_path' in RUST
    assert 'url.host_str() == Some("github.com")' in RUST
    assert "workerReleaseLink.removeAttribute('href')" in HTML
    assert "workerReleaseLink.href=url;" in HTML
    assert 'rel="noopener noreferrer"' in HTML
    assert "invoke('install_update')" not in HTML
    assert 'Command::new' not in RUST


def test_equal_version_test_release_not_misrepresented_as_upgrade():
    assert 'same base version' in RUST
    assert 'update_available: latest > installed' in RUST
    assert 'Ten sam numer wersji podstawowej nie oznacza nowszego APK' in HTML
    assert 'Instalacja tylko ręczna' in HTML
    assert 'nie pobiera ani nie instaluje APK' in HTML
