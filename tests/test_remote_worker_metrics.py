import pytest
from remote_worker_metrics import WorkerMetrics


def test_all_time_retained_after_token_revocation(tmp_path):
    from remote_worker_tokens import WorkerTokenStore
    path = tmp_path / 'workers.sqlite3'
    tokens = WorkerTokenStore(path)
    metrics = WorkerMetrics(path)
    worker = 'worker_12345678'
    token = tokens.issue(worker)
    assert metrics.record(job_id='job_12345678', worker_id=worker, status='success', duration_ms=900, moves=1, cpu_ms=700, gpu_ms=100, finished_at=100)
    assert not metrics.record(job_id='job_12345678', worker_id=worker, status='success', duration_ms=900)
    metrics.record(job_id='job_87654321', worker_id=worker, status='error', duration_ms=400, finished_at=200)
    metrics.record(job_id='job_99999999', worker_id=worker, status='cancelled', duration_ms=200, finished_at=300)
    tokens.revoke(worker)
    assert not tokens.authenticate(worker, token)
    row = WorkerMetrics(path).summary()[0]
    assert (row['tasks'], row['successes'], row['errors'], row['cancelled']) == (3, 1, 1, 1)
    assert (row['duration_ms'], row['moves'], row['cpu_ms'], row['gpu_ms'], row['last_activity']) == (1500, 1, 700, 100, 300)
    assert WorkerMetrics(path).summary(since=201)[0]['tasks'] == 1


def test_metrics_reject_invalid_and_duplicate(tmp_path):
    metrics = WorkerMetrics(tmp_path / 'db')
    with pytest.raises(ValueError):
        metrics.record(job_id='bad', worker_id='worker_12345678', status='success', duration_ms=0)
    with pytest.raises(ValueError):
        metrics.record(job_id='job_12345678', worker_id='worker_12345678', status='success', duration_ms=True)
    with pytest.raises(ValueError):
        metrics.record(job_id='job_12345678', worker_id='worker_12345678', status='wrong', duration_ms=1)
    assert metrics.summary() == []


def test_panel_is_auth_gated_and_no_secret_exposure():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    server = (root / 'server.py').read_text()
    assert server.index("if not user:") < server.index("if path == '/api/remote-workers/metrics':")
    assert server.index("if not user:") < server.index("if path == '/remote-workers':")
    html = (root / 'remote_workers.html').read_text()
    assert '/api/remote-workers/metrics' in html
    assert 'worker_id' in html
    assert 'token_digest' not in html
    assert 'Zdalne komputery' in (root / 'index.html').read_text()


def test_machine_auth_endpoint_has_bounded_body_and_no_echo():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
    endpoint = source.split("if path == '/api/remote-workers/authenticate':", 1)[1].split("if path == '/api/auth/logout':", 1)[0]
    assert "length <= 2048" in endpoint
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in endpoint
    assert "'authenticated': True" in endpoint
    assert "'token': token" not in endpoint


def test_update_safety_endpoint_fails_closed_without_durable_job_registry():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'server.py').read_text()
    endpoint = source.split("if path == '/api/remote-workers/update-safety':", 1)[1].split("if path == '/api/auth/logout':", 1)[0]
    assert "WorkerTokenStore(DB).authenticate(worker_id, token)" in endpoint
    assert "length <= 2048" in endpoint
    assert 'WorkerJobRegistry(DB).state(worker_id)' in endpoint
    assert "'install_allowed': False" in endpoint
    assert "'token': token" not in endpoint
