"""Offline coordinator bridge for a chess runner; no network transport.

The bridge stages validated inference results, then commits the runner's exact
move record and checkpoint in one transaction. Caller must ensure that only
this bridge writes the active checkpoint while a remote job is in flight.
"""
import secrets
import chess
from elimination_chess import EliminationBoard
import tournament_checkpoint
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_protocol import validate_result


class RemoteRunnerBridge:
    def __init__(self, db_path, tournament_id, pairing_id, worker_id, infer, *, clock=None, require_fresh_heartbeat=False):
        self.journal = BoundWorkerResults(db_path, clock=clock)
        self.db_path = db_path
        self.tournament_id = tournament_id
        self.pairing_id = pairing_id
        self.worker_id = worker_id
        self.infer = infer  # offline test transport: (job, board, player) -> result
        self.inflight = None
        self.require_fresh_heartbeat = require_fresh_heartbeat

    def provide(self, board, player, stop_event, kwargs):
        if self.inflight is not None:
            raise RuntimeError('Previous move not committed')
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError('Game stopped')
        checkpoint = tournament_checkpoint.load(self.db_path, self.tournament_id)
        if (checkpoint is None or checkpoint.get('fen') != board.fen()
                or checkpoint.get('variant', 'standard') != ('elimination' if isinstance(board, EliminationBoard) else 'standard')):
            raise ValueError('Runner and durable checkpoint disagree')
        with self.journal._db() as db:
            digest = db.execute('SELECT sha256 FROM tournament_checkpoints WHERE tournament_id=?', (self.tournament_id,)).fetchone()[0]
        job_id = 'job_' + secrets.token_hex(12)
        self.journal.assign_bound(self.worker_id, job_id, tournament_id=self.tournament_id,
                                  pairing_id=self.pairing_id, expected_sha256=digest, timeout_seconds=600,
                                  require_fresh_heartbeat=self.require_fresh_heartbeat)
        job = {'version': 1, 'job_id': job_id, 'fen': board.fen()}
        # Preserve the in-flight identity even if transport or validation fails.
        # The worker remains busy/unknown until explicitly reconciled.
        self.inflight = job_id
        result = self.infer(job, board.copy(stack=True), player)
        move = validate_result(result, job, board)
        self.journal.stage_validated(self.worker_id, job_id, result)
        return move, {'seconds': 0.1}

    def replay_staged(self, job_id, record):
        """Recover a staged result after coordinator restart, without redispatch.

        Caller supplies the move record reconstructed from the validated result.
        `apply_bound` verifies its UCI, FEN, ply and checkpoint CAS before commit.
        No worker-idle claim is inferred by replaying a result.
        """
        self.journal._id(job_id)
        pending = {item[0]: item for item in self.journal.pending()}
        if job_id not in pending or pending[job_id][1] != self.worker_id:
            raise ValueError('No staged result owned by worker')
        if self.inflight is not None:
            raise RuntimeError('Bridge already has an in-flight move')
        self.inflight = job_id
        try:
            self.publish(record)
        except Exception:
            self.inflight = None
            raise

    def publish(self, record):
        if self.inflight is None:
            raise RuntimeError('No staged result for move')
        checkpoint = tournament_checkpoint.load(self.db_path, self.tournament_id)
        if checkpoint is None:
            raise ValueError('Missing checkpoint')
        moves = checkpoint.get('moves')
        if not isinstance(moves, list):
            raise ValueError('Invalid checkpoint moves')
        next_checkpoint = {**checkpoint, 'moves': moves + [dict(record)],
                           'move_times': checkpoint.get('move_times', []) + [float(record['seconds'])],
                           'fen': record['fen'],
                           'side_to_move': 'black' if record['color'] == 'white' else 'white'}
        self.journal.apply_bound(self.inflight, next_checkpoint)
        self.inflight = None
