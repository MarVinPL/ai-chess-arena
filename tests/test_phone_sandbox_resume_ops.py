"""Issue #282: deployment must resume an existing isolated database, not recreate it."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESUME = (ROOT / "scripts/resume_phone_sandbox.sh").read_text()
CLEAN = (ROOT / "scripts/cleanup_phone_sandbox.sh").read_text()
BOOT = (ROOT / "remote_worker_phone_sandbox.py").read_text()


def test_resume_requires_exact_reviewed_main_and_original_staging_tree():
    assert "origin/main" in RESUME
    assert '== "$sha"' in RESUME
    assert "358164beda866c05e34ac20af9a4450f320219c0" in RESUME
    assert "Unexpected partial deployment state" in RESUME
    assert "Unexpected staging owner" in RESUME
    assert "PHONE_SANDBOX_RESUME_PREFLIGHT_PASS" in RESUME
    assert "--preflight" in RESUME and "--resume" in RESUME


def test_resume_fails_closed_without_new_production_token_or_tournament():
    assert "SELECT status,count(*) FROM tournaments" in RESUME
    assert "worker_inference_assignments" in RESUME
    assert "worker_inference_results" in RESUME
    assert "worker_job_safety" in RESUME
    assert "file:{prod}?mode=ro" in RESUME
    assert "bootstrap" not in RESUME
    assert "systemctl restart ai-chess-arena.service" not in RESUME
    assert "ARENA_PHONE_INFERENCE_ENABLED=1" not in RESUME


def test_narrow_acl_has_rollback_and_cleanup_owner_marker():
    assert 'setfacl -m "u:$test_user:--x" /home/debian' in RESUME
    assert 'setfacl -x "u:$test_user" /home/debian' in RESUME
    assert "getfacl -cp /home/debian" in RESUME
    assert "ai-chess-phone-e2e-acl-owned" in RESUME
    assert 'setfacl -x u:arena-phone-test /home/debian' in CLEAN
    assert 'if [[ -e "$acl_marker" ]]' in CLEAN


def test_verifier_expires_within_one_hour_on_bootstrap_and_resume():
    assert 'min(int(record["expires_at"]), int(time.time()) + 3600)' in BOOT
    assert "cutoff=int(time.time())+3600" in RESUME
    assert "UPDATE worker_tokens SET expires_at=MIN(expires_at,?)" in RESUME


def test_checks_tls_and_bad_token_before_phone_address_is_announced():
    assert "caddy validate --config" in RESUME
    assert "https://$domain/health" in RESUME
    assert '[[ $code == 401 ]]' in RESUME
    assert "PHONE_SANDBOX_HTTPS_READY" in RESUME
