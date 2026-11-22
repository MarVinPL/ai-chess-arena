"""Issue 282: dedicated UID traverse-only ACL and safe interrupted rollout."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = (ROOT / "scripts/deploy_phone_sandbox.sh").read_text()
RESUME = (ROOT / "scripts/resume_phone_sandbox_after_chdir_failure.sh").read_text()
CLEAN = (ROOT / "scripts/cleanup_phone_sandbox.sh").read_text()
SANDBOX = (ROOT / "remote_worker_phone_sandbox.py").read_text()


def test_traversal_acl_is_only_dedicated_uid_and_is_rolled_back():
    for script in (DEPLOY, RESUME):
        assert "setfacl -m u:arena-phone-test:--x /home/debian" in script
        assert "setfacl -x u:arena-phone-test /home/debian" in script
        assert "runuser -u arena-phone-test" in script
        assert "chmod 777" not in script
        assert "setfacl -m g:debian" not in script
    assert "setfacl -x u:arena-phone-test /home/debian" in CLEAN
    assert CLEAN.index("setfacl -x u:arena-phone-test") < CLEAN.index(
        "userdel arena-phone-test"
    )


def test_resumer_requires_exact_main_and_existing_private_isolated_db():
    for required in (
        "--preflight", "--resume", "origin/main", "merge-base --is-ancestor",
        "Staging filesystem permissions unsafe", "Staging database ownership mismatch",
        "token_digest", "compare_digest", "tournament_checkpoints",
        "worker_inference_assignments", "worker_inference_results",
        "caddy validate", "systemctl reload caddy",
        "SANDBOX_RESUME_ROLLBACK", "InaccessiblePaths",
    ):
        assert required in (RESUME + (ROOT / "systemd/ai-chess-arena-phone-e2e.service").read_text())
    assert "rm -f \"$db\"" not in RESUME
    assert "/var/lib/ai-chess-arena/arena.sqlite3" in RESUME
    assert "ARENA_PHONE_INFERENCE_ENABLED=1" not in RESUME


def test_staging_credential_verifier_lives_at_most_one_hour():
    assert 'expiry = min(record["expires_at"], int(time.time()) + 3600)' in SANDBOX
    assert "expires_at=MIN(expires_at,?)" in RESUME
    assert "source_db" in RESUME
