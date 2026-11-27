"""Authenticated coordinator-side result intake; no HTTP endpoint exposed."""
import hashlib
import json

from elimination_chess import checkpoint_board
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_protocol import validate_result
from remote_worker_tokens import WorkerTokenStore


class AuthenticatedResultIngest:
    def __init__(self, db_path, *, clock=None):
        self.tokens = WorkerTokenStore(db_path, **({'clock': clock} if clock else {}))
        self.jobs = BoundWorkerResults(db_path, **({'clock': clock} if clock else {}))

    def accept(self, worker_id, token, result):
        """Validate credentials, assignment, lease, FEN and legal UCI before staging.

        No trust is placed in a worker-supplied FEN or pairing ID. The board is
        reconstructed exclusively from the assigned durable checkpoint.
        """
        if not self.tokens.authenticate(worker_id, token):
            raise ValueError('Worker authentication failed')
        if not isinstance(result, dict):
            raise ValueError('Invalid result')
        job_id = result.get('job_id')
        self.jobs._id(job_id)
        with self.jobs._db() as db:
            db.execute('BEGIN IMMEDIATE')
            binding = db.execute('''SELECT worker_id,tournament_id,pairing_id,checkpoint_sha256,fen
                FROM remote_job_binding WHERE job_id=?''', (job_id,)).fetchone()
            if not binding or binding[0] != worker_id:
                raise ValueError('Unknown or foreign job')
            worker_state = db.execute('''SELECT state,job_id,deadline FROM worker_job_safety
                WHERE worker_id=?''', (worker_id,)).fetchone()
            if not worker_state or worker_state[0] != 'running' or worker_state[1] != job_id or worker_state[2] <= self.jobs.clock():
                raise ValueError('Job not running or lease expired')
            checkpoint = db.execute('''SELECT payload_json,sha256 FROM tournament_checkpoints
                WHERE tournament_id=?''', (binding[1],)).fetchone()
            if not checkpoint or checkpoint[1] != binding[3] or hashlib.sha256(checkpoint[0].encode()).hexdigest() != binding[3]:
                raise ValueError('Assigned checkpoint changed')
            saved = json.loads(checkpoint[0])
            if saved.get('phase') != 'game' or saved.get('pairing', {}).get('id') != binding[2] or saved.get('fen') != binding[4]:
                raise ValueError('Assigned game changed')
            board = checkpoint_board(saved)
            move = validate_result(result, {'job_id': job_id}, board)
            # Token revocation or rotation must also invalidate an in-flight
            # intake. Verify credentials inside the write transaction.
            token_row = db.execute('SELECT token_digest,expires_at,revoked_at FROM worker_tokens WHERE worker_id=?', (worker_id,)).fetchone()
            import hmac
            if not token_row or token_row[2] is not None or token_row[1] <= self.jobs.clock() or not hmac.compare_digest(token_row[0], self.tokens._digest(token)):
                raise ValueError('Worker token no longer valid')
            payload = json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False)
            if len(payload) > 16384:
                raise ValueError('Result too large')
            if db.execute('SELECT 1 FROM worker_result_journal WHERE job_id=?', (job_id,)).fetchone():
                raise ValueError('Duplicate result')
            now = self.jobs.clock()
            updated = db.execute('''UPDATE worker_job_safety SET job_id=NULL,
                state='idle_confirmed',deadline=NULL,updated_at=?
                WHERE worker_id=? AND job_id=? AND state='running' AND deadline>?''',
                (now, worker_id, job_id, now))
            if updated.rowcount != 1:
                raise ValueError('Job lease changed')
            db.execute('''INSERT INTO worker_result_journal VALUES (?, ?, ?, 'pending_application', ?)''',
                       (job_id, worker_id, payload, now))
            return move
