"""Static safeguards for the isolated test VPS deployment."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deploy_phone_sandbox.sh").read_text()
CLEAN = (ROOT / "scripts/cleanup_phone_sandbox.sh").read_text()
UNIT = (ROOT / "systemd/ai-chess-arena-phone-e2e.service").read_text()
CADDY = (ROOT / "caddy/ai-chess-phone-e2e.caddy").read_text()
SOURCE = (ROOT / "remote_worker_phone_sandbox.py").read_text()


def test_staging_has_separate_db_only_loopback_and_disabled_phone_dispatcher():
    assert "127.0.0.1:8191" in CADDY
    assert "arena-worker-test.148-113-182-137.sslip.io" in CADDY
    assert "User=arena-phone-test" in UNIT
    assert "InaccessiblePaths=/var/lib/ai-chess-arena" in UNIT
    assert "ReadWritePaths=/var/lib/ai-chess-arena-phone-e2e" in UNIT
    assert "Environment=ARENA_PHONE_INFERENCE_ENABLED=0" in UNIT
    assert "Environment=ARENA_PHONE_INFERENCE_ENABLED=1" not in UNIT
    assert '("127.0.0.1", port)' in SOURCE
    assert 'os.environ["ARENA_PHONE_INFERENCE_ENABLED"] = "0"' in SOURCE
    assert "PRODUCTION_DB" in SOURCE
    assert "mode=ro" in SOURCE
    assert "token_digest" in SOURCE
    assert "token =" not in SOURCE
    assert "def do_GET" in SOURCE
    assert "def do_POST" in SOURCE
    assert "finalize_arena_result(" in SOURCE


def test_deployment_review_gate_no_production_unit_rewrite():
    for required in (
        "origin/main", "sha", "--preflight", "--deploy",
        "ai-chess-arena-phone-e2e.service", "ai-chess-phone-e2e.caddy",
        "source_db=/var/lib/ai-chess-arena/arena.sqlite3", "SOURCE_READ_ONLY",
        "PRODUCTION_SERVICE_AND_DB_UNCHANGED", "caddy validate",
        "systemctl reload caddy", "SANDBOX_ROLLBACK"
    ):
        assert required in DEPLOY
    assert "systemctl restart ai-chess-arena.service" not in DEPLOY
    assert "ARENA_PHONE_INFERENCE_ENABLED=1" not in DEPLOY
    assert "worker_tokens SET" not in DEPLOY
    assert "DROP TABLE" not in DEPLOY


def test_cleanup_does_not_touch_production_credentials():
    assert "SELECT payload_json FROM tournament_checkpoints" in CLEAN
    assert "Refusing cleanup" in CLEAN
    assert "systemctl stop" in CLEAN
    assert "rm -f \"$db\"" in CLEAN
    assert "systemctl reload caddy" in CLEAN
    assert "/var/lib/ai-chess-arena/arena.sqlite3" not in CLEAN
