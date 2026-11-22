import pytest
from remote_worker_result_journal import WorkerResultJournal
from remote_worker_jobs import DurableWorkerJobs


def test_atomic_staging_survives_restart_and_retries(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    result = {'job_id': job, 'uci': 'e2e4'}
    assert journal.stage_validated(worker, job, result)
    assert not journal.stage_validated(worker, job, result)
    with pytest.raises(ValueError, match='Conflicting'):
        journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e3'})
    DurableWorkerJobs(path, clock=lambda: 110).recover_after_restart()
    assert WorkerResultJournal(path, clock=lambda: 110).pending() == [(job, worker, result)]
    assert journal.state(worker) == 'unknown'


def test_invalid_or_expired_result_never_stages(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    now = [100]
    journal = WorkerResultJournal(path, clock=lambda: now[0])
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=10)
    with pytest.raises(ValueError):
        journal.stage_validated(worker, job, {'job_id': 'job_87654321', 'uci': 'e2e4'})
    now[0] = 111
    with pytest.raises(ValueError):
        journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    assert journal.pending() == []
    assert journal.state(worker) == 'unknown'


def test_failed_journal_insert_rolls_back_worker_terminal_state(tmp_path):
    import sqlite3
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    # Simulate a database failure after the safety UPDATE but before INSERT.
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TRIGGER reject_result BEFORE INSERT ON worker_result_journal
                      BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END''')
    with pytest.raises(sqlite3.IntegrityError, match='simulated disk failure'):
        journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    assert journal.state(worker) == 'running'
    assert journal.pending() == []


def test_pending_results_remain_replayable_after_multiple_restarts(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    result = {'job_id': job, 'uci': 'e2e4'}
    journal.stage_validated(worker, job, result)
    for now in (110, 120, 130):
        DurableWorkerJobs(path, clock=lambda now=now: now).recover_after_restart()
        assert WorkerResultJournal(path, clock=lambda now=now: now).pending() == [(job, worker, result)]


def test_atomic_application_exactly_once(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    with journal._db() as db:
        db.execute('CREATE TABLE demo_moves (job_id TEXT PRIMARY KEY, uci TEXT)')

    def apply(db, owner, result):
        assert owner == worker
        db.execute('INSERT INTO demo_moves VALUES (?,?)', (result['job_id'], result['uci']))

    assert journal.apply_atomically(job, apply)
    assert not WorkerResultJournal(path, clock=lambda: 110).apply_atomically(job, apply)
    with journal._db() as db:
        assert db.execute('SELECT * FROM demo_moves').fetchall() == [(job, 'e2e4')]
    assert journal.pending() == []


def test_atomic_application_failure_rolls_back(tmp_path):
    path = tmp_path / 'jobs.sqlite'
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    with journal._db() as db:
        db.execute('CREATE TABLE demo_moves (job_id TEXT PRIMARY KEY)')

    def failing(db, owner, result):
        db.execute('INSERT INTO demo_moves VALUES (?)', (result['job_id'],))
        raise RuntimeError('scheduler failed')

    with pytest.raises(RuntimeError, match='scheduler failed'):
        journal.apply_atomically(job, failing)
    assert journal.pending() == [(job, worker, {'job_id': job, 'uci': 'e2e4'})]
    with journal._db() as db:
        assert db.execute('SELECT * FROM demo_moves').fetchall() == []


def test_checkpoint_and_result_ack_are_atomic_and_idempotent(tmp_path):
    import sqlite3
    import tournament_checkpoint
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    old = {'schema_version': 1, 'tournament_id': 1, 'moves': []}
    original = tournament_checkpoint.save(path, 1, old)
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    new = {'schema_version': 1, 'tournament_id': 1, 'moves': ['e2e4']}
    assert journal.apply_checkpoint_atomically(job, 1, new, expected_sha256=original['sha256'])
    assert not journal.apply_checkpoint_atomically(job, 1, new, expected_sha256=original['sha256'])
    assert tournament_checkpoint.load(path, 1) == new
    assert journal.pending() == []


def test_checkpoint_conflict_keeps_result_pending(tmp_path):
    import sqlite3
    import tournament_checkpoint
    path = tmp_path / 'arena.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE tournaments (id INTEGER PRIMARY KEY)')
        db.execute('INSERT INTO tournaments VALUES (1)')
    tournament_checkpoint.ensure_schema(path)
    original = tournament_checkpoint.save(path, 1, {'schema_version': 1, 'tournament_id': 1, 'moves': []})
    journal = WorkerResultJournal(path, clock=lambda: 100)
    worker, job = 'worker_12345678', 'job_12345678'
    journal.assign(worker, job, timeout_seconds=90)
    journal.stage_validated(worker, job, {'job_id': job, 'uci': 'e2e4'})
    changed = {'schema_version': 1, 'tournament_id': 1, 'moves': ['d2d4']}
    tournament_checkpoint.save(path, 1, changed)
    with pytest.raises(ValueError, match='Checkpoint changed'):
        journal.apply_checkpoint_atomically(job, 1, {'schema_version': 1, 'tournament_id': 1, 'moves': ['e2e4']}, expected_sha256=original['sha256'])
    assert tournament_checkpoint.load(path, 1) == changed
    assert len(journal.pending()) == 1
