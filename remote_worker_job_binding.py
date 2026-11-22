"""Durable coordinator-only binding of remote jobs to an exact chess position."""
import hashlib
import json
import sqlite3

from remote_worker_result_journal import WorkerResultJournal


class BoundWorkerResults(WorkerResultJournal):
    def __init__(self, path, *, clock=None):
        if clock is None:
            super().__init__(path)
        else:
            super().__init__(path, clock=clock)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS remote_job_binding (
                job_id TEXT PRIMARY KEY, worker_id TEXT NOT NULL,
                tournament_id INTEGER NOT NULL, pairing_id INTEGER NOT NULL,
                checkpoint_sha256 TEXT NOT NULL, fen TEXT NOT NULL)''')

    def assign_bound(self, worker_id, job_id, *, tournament_id, pairing_id, expected_sha256, timeout_seconds):
        """Bind a job and reserve worker atomically against a live checkpoint."""
        self._id(worker_id)
        self._id(job_id)
        if type(tournament_id) is not int or tournament_id <= 0 or type(pairing_id) is not int or pairing_id <= 0:
            raise ValueError('Invalid tournament or pairing')
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600:
            raise ValueError('Invalid timeout')
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise ValueError('Invalid checkpoint hash')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?', (tournament_id,)).fetchone()
            if not row or row[1] != expected_sha256 or hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
                raise ValueError('Checkpoint mismatch')
            checkpoint = json.loads(row[0])
            if checkpoint.get('phase') != 'game' or checkpoint.get('pairing', {}).get('id') != pairing_id:
                raise ValueError('Not an active pairing')
            import chess
            board = chess.Board(checkpoint['fen'])
            if not board.is_valid():
                raise ValueError('Invalid checkpoint board')
            if db.execute('SELECT 1 FROM remote_job_binding WHERE job_id=?', (job_id,)).fetchone():
                raise ValueError('Duplicate job')
            # Staging a result marks the worker idle, but the chess move is
            # not committed yet. Do not assign another position until the
            # previous result has been atomically applied to its checkpoint.
            if db.execute('''SELECT 1 FROM worker_result_journal
                WHERE worker_id=? AND state='pending_application' LIMIT 1''', (worker_id,)).fetchone():
                raise ValueError('Worker has unapplied result')
            # A staged result still owns its source position until applied.
            # Never dispatch a competing job to another worker for that FEN.
            conflict = db.execute('''SELECT 1 FROM remote_job_binding b
                LEFT JOIN worker_result_journal r ON r.job_id=b.job_id
                LEFT JOIN worker_job_safety s ON s.worker_id=b.worker_id AND s.job_id=b.job_id
                WHERE b.tournament_id=? AND b.pairing_id=? AND b.checkpoint_sha256=?
                AND (r.state='pending_application' OR
                     (r.job_id IS NULL AND s.state IN ('running','unknown')))
                LIMIT 1''', (tournament_id, pairing_id, expected_sha256)).fetchone()
            if conflict:
                raise ValueError('Position already has an outstanding remote job')
            state = db.execute('SELECT state FROM worker_job_safety WHERE worker_id=?', (worker_id,)).fetchone()
            if state and state[0] != 'idle_confirmed':
                raise ValueError('Worker not confirmed idle')
            now = self.clock()
            db.execute('''INSERT INTO worker_job_safety VALUES (?,?,'running',?,?)
                ON CONFLICT(worker_id) DO UPDATE SET job_id=excluded.job_id,state='running',
                deadline=excluded.deadline,updated_at=excluded.updated_at''', (worker_id, job_id, now + timeout_seconds, now))
            db.execute('INSERT INTO remote_job_binding VALUES (?,?,?,?,?,?)',
                       (job_id, worker_id, tournament_id, pairing_id, expected_sha256, checkpoint['fen']))
            return checkpoint['fen']

    def confirm_idle_after_restart(self, worker_id, *, operator_confirmed, worker_reconnected):
        """Coordinator-only reconciliation; caller must verify both attestations."""
        self._id(worker_id)
        if operator_confirmed is not True or worker_reconnected is not True:
            raise ValueError('Independent operator and worker confirmation required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state,job_id FROM worker_job_safety WHERE worker_id=?', (worker_id,)).fetchone()
            if row is None or row[0] != 'unknown' or row[1] is not None:
                raise ValueError('Worker not eligible for restart reconciliation')
            pending = db.execute("SELECT 1 FROM worker_result_journal WHERE worker_id=? AND state='pending_application' LIMIT 1", (worker_id,)).fetchone()
            if pending:
                raise ValueError('Worker has unapplied results')
            db.execute("UPDATE worker_job_safety SET state='idle_confirmed',updated_at=? WHERE worker_id=? AND state='unknown' AND job_id IS NULL", (self.clock(), worker_id))
            return True

    def apply_bound(self, job_id, checkpoint):
        """Only apply to the exact position and pairing reserved at assignment."""
        self._id(job_id)
        with self._db() as db:
            row = db.execute('SELECT worker_id,tournament_id,pairing_id,checkpoint_sha256,fen FROM remote_job_binding WHERE job_id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('Missing durable job binding')
        worker_id, tournament_id, pairing_id, digest, fen = row
        if checkpoint.get('pairing', {}).get('id') != pairing_id:
            raise ValueError('Wrong pairing')
        # Recheck the binding within the actual write transaction, not only here.
        from tournament_checkpoint import save_in_transaction
        from remote_worker_protocol import validate_result
        import chess
        def apply(db, actual_worker, result):
            bound = db.execute('SELECT worker_id,tournament_id,pairing_id,checkpoint_sha256,fen FROM remote_job_binding WHERE job_id=?', (job_id,)).fetchone()
            if bound != row or actual_worker != worker_id:
                raise ValueError('Job binding changed')
            stored = db.execute('SELECT payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?', (tournament_id,)).fetchone()
            if not stored or stored[1] != digest or hashlib.sha256(stored[0].encode()).hexdigest() != digest:
                raise ValueError('Source checkpoint changed')
            old = json.loads(stored[0])
            if old.get('phase') != 'game' or old.get('pairing', {}).get('id') != pairing_id or old.get('fen') != fen:
                raise ValueError('Source position changed')
            board = chess.Board(fen)
            move = validate_result(result, {'job_id': job_id}, board)
            old_moves, new_moves = old.get('moves'), checkpoint.get('moves')
            if not isinstance(old_moves, list) or not isinstance(new_moves, list) or new_moves[:-1] != old_moves or len(new_moves) != len(old_moves) + 1:
                raise ValueError('Invalid move append')
            if not isinstance(new_moves[-1], dict) or new_moves[-1].get('uci') != move.uci():
                raise ValueError('Move mismatch')
            board.push(move)
            if checkpoint.get('fen') != board.fen() or checkpoint.get('side_to_move') != ('white' if board.turn else 'black'):
                raise ValueError('Board mismatch')
            if checkpoint.get('pairing') != old.get('pairing') or checkpoint.get('tournament_id') != tournament_id or checkpoint.get('phase') != 'game':
                raise ValueError('Checkpoint identity mismatch')
            # A remote move may update only game progress. Never permit the
            # result path to silently rewrite participants, schedule or rules.
            mutable = {'moves', 'move_times', 'fen', 'side_to_move', 'updated_at'}
            if set(checkpoint) != set(old) or any(checkpoint[key] != old[key] for key in old if key not in mutable):
                raise ValueError('Immutable checkpoint metadata changed')
            if type(new_moves[-1].get('ply')) is not int or new_moves[-1]['ply'] != len(new_moves):
                raise ValueError('Invalid move number')
            if new_moves[-1].get('color') != ('white' if not board.turn else 'black'):
                raise ValueError('Invalid move color')
            if new_moves[-1].get('fen') != board.fen():
                raise ValueError('Move record board mismatch')
            old_times, new_times = old.get('move_times'), checkpoint.get('move_times')
            if not isinstance(old_times, list) or not isinstance(new_times, list) or new_times[:-1] != old_times or len(new_times) != len(old_times) + 1:
                raise ValueError('Invalid timing append')
            if type(new_times[-1]) not in (int, float) or not 0 <= new_times[-1] <= 3600:
                raise ValueError('Invalid move duration')
            if new_moves[-1].get('seconds') != new_times[-1]:
                raise ValueError('Move timing mismatch')
            save_in_transaction(db, tournament_id, checkpoint, expected_sha256=digest)
        return self.apply_atomically(job_id, apply)
