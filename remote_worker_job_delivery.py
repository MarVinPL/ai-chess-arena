"""Authenticated, read-only retrieval of an already coordinator-assigned job."""
import hashlib
import json

from remote_worker_job_binding import BoundWorkerResults
from remote_worker_tokens import WorkerTokenStore


class AuthenticatedJobDelivery:
    def __init__(self, db_path, *, clock=None):
        options = {'clock': clock} if clock is not None else {}
        self.tokens = WorkerTokenStore(db_path, **options)
        self.jobs = BoundWorkerResults(db_path, **options)

    def retrieve(self, worker_id, token):
        if not self.tokens.authenticate(worker_id, token):
            raise ValueError('Invalid worker credentials')
        with self.jobs._db() as db:
            db.execute('BEGIN IMMEDIATE')
            state = db.execute('SELECT job_id,deadline FROM worker_job_safety WHERE worker_id=? AND state=?',
                               (worker_id, 'running')).fetchone()
            if not state or not state[0] or state[1] is None or state[1] <= self.jobs.clock():
                return None
            binding = db.execute('SELECT tournament_id,pairing_id,checkpoint_sha256,fen FROM remote_job_binding WHERE job_id=? AND worker_id=?',
                                 (state[0], worker_id)).fetchone()
            if not binding:
                return None
            saved = db.execute('SELECT payload_json,sha256 FROM tournament_checkpoints WHERE tournament_id=?',
                               (binding[0],)).fetchone()
            if not saved or saved[1] != binding[2] or hashlib.sha256(saved[0].encode()).hexdigest() != saved[1]:
                return None
            checkpoint = json.loads(saved[0])
            if checkpoint.get('phase') != 'game' or checkpoint.get('pairing', {}).get('id') != binding[1] or checkpoint.get('fen') != binding[3]:
                return None
            # Check credential again inside the same transaction as the read.
            row = db.execute('SELECT token_digest,expires_at,revoked_at FROM worker_tokens WHERE worker_id=?', (worker_id,)).fetchone()
            import hmac
            if not row or row[2] is not None or row[1] <= self.jobs.clock() or not hmac.compare_digest(row[0], self.tokens._digest(token)):
                raise ValueError('Credentials revoked')
            return {'version': 1, 'job_id': state[0], 'fen': binding[3]}
