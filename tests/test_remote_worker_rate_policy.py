import pytest
from remote_worker_rate_policy import ROUTE_LIMITS, WorkerRatePolicy


@pytest.mark.parametrize('route,limit', ROUTE_LIMITS.items())
def test_authenticated_route_limit_and_recovery(route, limit):
    now = [100.0]
    policy = WorkerRatePolicy(clock=lambda: now[0])
    assert all(policy.admit_authenticated(route, 'worker_a') for _ in range(limit))
    assert not policy.admit_authenticated(route, 'worker_a')
    assert policy.admit_authenticated(route, 'worker_b')
    now[0] += 60
    assert policy.admit_authenticated(route, 'worker_a')


def test_peer_limit_precedes_identity_and_blocks_rotating_ids():
    policy = WorkerRatePolicy()
    for i in range(120):
        assert policy.admit_peer('authenticate', '127.0.0.1')
        assert policy.admit_authenticated('authenticate', f'worker_{i}')
    assert not policy.admit_peer('authenticate', '127.0.0.1')
    assert policy.admit_peer('authenticate', '127.0.0.2')


def test_aggregate_peer_budget_across_routes():
    policy = WorkerRatePolicy()
    for route in ('job-poll', 'update-pause-poll', 'board-view'):
        assert all(policy.admit_peer(route, 'peer') for _ in range(200))
    assert not policy.admit_peer('ready', 'peer')


def test_unknown_route_and_bounded_state():
    now = [100.0]
    policy = WorkerRatePolicy(clock=lambda: now[0], max_peers=2)
    assert not policy.admit_peer('unknown', 'peer')
    assert not policy.admit_authenticated('unknown', 'worker')
    assert policy.admit_peer('job', 'a')
    assert policy.admit_peer('job', 'b')
    assert not policy.admit_peer('job', 'c')
    assert policy.admit_authenticated('job', 'a')
    assert policy.admit_authenticated('job', 'b')
    assert not policy.admit_authenticated('job', 'c')
    now[0] += 60
    assert policy.admit_peer('job', 'c')
    assert policy.admit_authenticated('job', 'c')
