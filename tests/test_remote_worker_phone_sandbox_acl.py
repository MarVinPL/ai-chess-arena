"""Minimal path traversal rule and auditable resumption of failed staging."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEPLOY=(ROOT/'scripts/deploy_phone_sandbox.sh').read_text()
RECOVER=(ROOT/'scripts/recover_phone_sandbox_acl.sh').read_text()
CLEAN=(ROOT/'scripts/cleanup_phone_sandbox.sh').read_text()


def test_acl_is_minimal_per_user_and_is_removed():
    assert 'setfacl -m u:arena-phone-test:--x /home/debian' in DEPLOY
    assert 'setfacl -m u:arena-phone-test:--x /home/debian' in RECOVER
    assert 'setfacl -x u:arena-phone-test /home/debian' in CLEAN
    assert 'chmod 755 /home/debian' not in DEPLOY+RECOVER+CLEAN
    assert 'getfacl' not in DEPLOY+RECOVER


def test_recovery_accepts_only_prior_reviewed_stage_and_no_real_tournament():
    for needle in (
        'origin/main', 'merge-base --is-ancestor', 'Staging identity missing',
        'Staging DB owner mismatch', 'port occupied', 'status --db',
        'systemctl start "$unit"', 'caddy validate', 'systemctl reload caddy',
        'STAGE_RESUME_ROLLBACK', 'PRODUCTION_DB_NEVER_WRITTEN',
    ):
        assert needle.lower() in RECOVER.lower()
    assert 'systemctl restart ai-chess-arena.service' not in RECOVER
    assert 'ARENA_PHONE_INFERENCE_ENABLED=1' not in RECOVER
    assert 'bootstrap' not in RECOVER
    assert 'WorkerTokenStore.issue' not in RECOVER
