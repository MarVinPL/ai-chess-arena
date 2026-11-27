import pytest
from remote_worker_pairing import PairingError, PairingRegistry

WORKER = 'worker_12345678'


def registry():
    now = [100.0]
    return PairingRegistry(clock=lambda: now[0], code_ttl=60, token_ttl=120), now


def test_pairing_requires_approval_and_is_single_use():
    reg, _ = registry()
    code = reg.create(WORKER)
    assert len(code) == 8
    with pytest.raises(PairingError, match='not approved'):
        reg.redeem(code, WORKER)
    reg.approve(code)
    token = reg.redeem(code, WORKER)
    assert reg.authorize(token, WORKER)
    assert not reg.authorize(token, 'other_worker_123')
    with pytest.raises(PairingError):
        reg.redeem(code, WORKER)
    reg.revoke(token)
    assert not reg.authorize(token, WORKER)


def test_code_and_token_expiration():
    reg, now = registry()
    code = reg.create(WORKER)
    now[0] += 60
    with pytest.raises(PairingError):
        reg.approve(code)
    code = reg.create(WORKER)
    reg.approve(code)
    token = reg.redeem(code, WORKER)
    now[0] += 120
    assert not reg.authorize(token, WORKER)


def test_wrong_worker_limited_attempts():
    reg, _ = registry()
    code = reg.create(WORKER)
    reg.approve(code)
    for _ in range(5):
        with pytest.raises(PairingError):
            reg.redeem(code, 'wrong_worker_123')
    with pytest.raises(PairingError):
        reg.redeem(code, WORKER)


def test_pending_limit_and_input_validation():
    reg, _ = registry()
    with pytest.raises(PairingError):
        reg.create('../bad')
    reg.create(WORKER)
    with pytest.raises(PairingError):
        reg.create(WORKER)
    assert not reg.authorize('bogus', WORKER)
