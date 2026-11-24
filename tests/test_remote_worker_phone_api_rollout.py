"""Static guard for deploy-only-after-merge and deliberate phone-mode isolation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/deploy_worker_phone_api_gateway.sh").read_text()
DROPIN = (ROOT / "systemd/ai-chess-arena-worker-api.conf").read_text()


def test_service_only_enables_machine_api_not_phone_tournament_dispatch():
    assert "Environment=ARENA_PHONE_WORKER_API_ENABLED=1" in DROPIN
    assert "Environment=ARENA_PHONE_INFERENCE_ENABLED=0" in DROPIN
    assert "Environment=ARENA_PHONE_INFERENCE_ENABLED=1" not in DROPIN
    assert "ExecStart=" not in DROPIN


def test_rollout_requires_merged_main_idle_workers_and_rollback():
    for marker in (
        "origin/main", "expected", "--preflight", "--deploy",
        "LIVE_TOURNAMENT", "WORKER_UNSAFE", "ROLLBACK:",
        "systemctl daemon-reload", "systemctl restart",
        "trap rollback EXIT", "DEPLOY_SUCCESS", "login_url",
        "arena.sqlite3", "managed-models",
    ):
        assert marker in SCRIPT
    assert "ARENA_PHONE_INFERENCE_ENABLED=1" not in SCRIPT
    assert "git push" not in SCRIPT
    assert "DROP TABLE" not in SCRIPT
