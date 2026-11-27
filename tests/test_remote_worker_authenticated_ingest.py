import pytest
from remote_worker_authenticated_ingest import AuthenticatedResultIngest
from test_remote_worker_job_binding import setup_game


def test_authenticated_intake_validates_lease_and_legal_move(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    ingest = AuthenticatedResultIngest(path, clock=lambda: 100)
    token = ingest.tokens.issue('worker_12345678', ttl_seconds=600)
    job = 'job_12345678'
    journal.assign_bound('worker_12345678', job, tournament_id=1, pairing_id=7,
                         expected_sha256=digest, timeout_seconds=90)
    result = {'version': 1, 'job_id': job, 'uci': 'e2e4'}
    with pytest.raises(ValueError, match='authentication'):
        ingest.accept('worker_12345678', 'invalid', result)
    with pytest.raises(ValueError, match='foreign'):
        ingest.accept('worker_12345678', token, {**result, 'job_id': 'job_87654321'})
    with pytest.raises(ValueError):
        ingest.accept('worker_12345678', token, {**result, 'uci': 'e2e5'})
    assert ingest.accept('worker_12345678', token, result).uci() == 'e2e4'
    assert len(journal.pending()) == 1
    with pytest.raises(ValueError, match='not running'):
        ingest.accept('worker_12345678', token, result)


def test_revoked_token_cannot_stage_result(tmp_path):
    path, journal, old, digest = setup_game(tmp_path)
    ingest = AuthenticatedResultIngest(path, clock=lambda: 100)
    token = ingest.tokens.issue('worker_12345678', ttl_seconds=600)
    journal.assign_bound('worker_12345678', 'job_12345678', tournament_id=1,
                         pairing_id=7, expected_sha256=digest, timeout_seconds=90)
    ingest.tokens.revoke('worker_12345678')
    with pytest.raises(ValueError, match='authentication'):
        ingest.accept('worker_12345678', token, {'version': 1, 'job_id': 'job_12345678', 'uci': 'e2e4'})
    assert journal.pending() == []
