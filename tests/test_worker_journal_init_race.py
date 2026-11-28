"""Races between journal initialization and coordinator assignment APIs."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import chess
import pytest

from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_job_registry import WorkerJobRegistry
from remote_worker_tokens import WorkerTokenStore
from test_remote_worker_job_binding import setup_game


WORKER = 'worker_12345678'
JOB = 'job_12345678'
MODEL = 'a' * 64
NONCE = '1' * 32


@pytest.mark.parametrize('assignment', ['durable', 'registry', 'bound', 'inference'])
def test_assignment_and_journal_fence_cannot_both_commit(tmp_path, assignment):
    path, bound, _, digest = setup_game(tmp_path)
    clock = lambda: 100
    token = WorkerTokenStore(path, clock=clock).issue(WORKER)
    registry = WorkerJobRegistry(path, clock=clock)
    inference = WorkerInferenceDelivery(path, clock=clock)
    assert inference.register_local_ready(WORKER, MODEL) == 'ready'
    job = {'version': 2, 'job_id': JOB, 'fen': chess.STARTING_FEN,
           'model_sha256': MODEL, 'max_tokens': 32, 'timeout_seconds': 30,
           'legal_moves': sorted(move.uci() for move in chess.Board().legal_moves)}
    assign = {
        'durable': lambda: registry.assign(WORKER, JOB, timeout_seconds=30),
        'registry': lambda: registry.record_assignment(WORKER, JOB, deadline=130),
        'bound': lambda: bound.assign_bound(
            WORKER, JOB, tournament_id=1, pairing_id=7,
            expected_sha256=digest, timeout_seconds=30,
        ),
        'inference': lambda: inference.assign_from_arena(WORKER, job),
    }[assignment]

    barrier = Barrier(2)

    def race(operation):
        barrier.wait(timeout=5)
        try:
            operation()
        except ValueError:
            return False
        return True

    def acquire():
        assert registry.acquire_journal_init(WORKER, token, NONCE, admit=lambda _: None)['fenced']

    with ThreadPoolExecutor(max_workers=2) as executor:
        acquired = executor.submit(race, acquire)
        assigned = executor.submit(race, assign)
        won_fence, won_assignment = acquired.result(timeout=15), assigned.result(timeout=15)
    assert won_fence != won_assignment
    with sqlite3.connect(path) as db:
        fence = db.execute('SELECT nonce FROM worker_journal_init_fences').fetchone()
        state = db.execute('SELECT state,job_id FROM worker_job_safety WHERE worker_id=?', (WORKER,)).fetchone()
        bindings = db.execute('SELECT job_id FROM remote_job_binding').fetchall()
        inference_jobs = db.execute('SELECT job_id FROM worker_inference_assignments').fetchall()
    assert fence == ((NONCE,) if won_fence else None)
    assert state == (('idle_confirmed', None) if won_fence else ('running', JOB))
    assert bindings == ([(JOB,)] if assignment == 'bound' and won_assignment else [])
    assert inference_jobs == ([(JOB,)] if assignment == 'inference' and won_assignment else [])
