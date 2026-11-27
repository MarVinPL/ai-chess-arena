"""Read-only restart reconciliation inventory; never releases a worker."""
import hashlib
import json

from remote_worker_job_binding import BoundWorkerResults


def inspect_unresolved_jobs(db_path):
    """List unresolved bindings without changing coordinator or worker state."""
    jobs = BoundWorkerResults(db_path)
    with jobs._db() as db:
        db.execute('BEGIN')
        rows = db.execute('''SELECT b.job_id,b.worker_id,b.tournament_id,b.pairing_id,
            b.checkpoint_sha256,b.fen,s.state,s.job_id,s.deadline,
            r.state,c.payload_json,c.sha256
            FROM remote_job_binding b
            LEFT JOIN worker_job_safety s ON s.worker_id=b.worker_id
            LEFT JOIN worker_result_journal r ON r.job_id=b.job_id
            LEFT JOIN tournament_checkpoints c ON c.tournament_id=b.tournament_id
            WHERE r.state='pending_application' OR r.job_id IS NULL
            ORDER BY b.tournament_id,b.job_id''').fetchall()
        report = []
        for job_id, worker, tournament, pairing, digest, fen, state, active_job, deadline, result_state, payload, saved_hash in rows:
            checkpoint_matches = False
            if payload is not None and saved_hash == digest and hashlib.sha256(payload.encode()).hexdigest() == saved_hash:
                try:
                    data = json.loads(payload)
                    checkpoint_matches = (data.get('phase') == 'game' and
                        data.get('pairing', {}).get('id') == pairing and data.get('fen') == fen)
                except (ValueError, TypeError, AttributeError):
                    pass
            if result_state == 'pending_application':
                action = 'review_staged_result' if checkpoint_matches else 'manual_checkpoint_conflict'
            elif not checkpoint_matches:
                action = 'manual_checkpoint_conflict'
            elif state == 'running' and active_job == job_id and deadline is not None and deadline > jobs.clock():
                action = 'wait_for_worker'
            else:
                action = 'manual_reconciliation'
            report.append({'job_id': job_id, 'worker_id': worker,
                           'tournament_id': tournament, 'pairing_id': pairing,
                           'worker_state': state or 'missing',
                           'result_state': result_state or 'not_staged',
                           'checkpoint_matches': checkpoint_matches,
                           'action': action})
        return report
