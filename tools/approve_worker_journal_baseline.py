"""Offline attestation for an independently verified Android Worker baseline."""
import argparse
import sqlite3
from pathlib import Path

from remote_worker_inference_delivery import WorkerInferenceDelivery
from remote_worker_job_registry import WorkerJobRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--worker-id', required=True)
    parser.add_argument('--origin', required=True)
    parser.add_argument('--version-code', type=int, required=True)
    parser.add_argument('--confirm-reviewed-baseline', action='store_true', required=True,
                        help='Attest that the device and any former installer transaction were independently verified')
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error('Select an existing coordinator database')
    try:
        registry = WorkerJobRegistry(args.db)
        WorkerInferenceDelivery(args.db)
        registry.approve_journal_baseline(args.worker_id, args.origin, args.version_code)
    except (ValueError, sqlite3.Error) as error:
        parser.exit(1, f'Baseline approval failed: {error}\n')
    print('Baseline approved. Initialization still requires authenticated native evidence.')


if __name__ == '__main__':
    main()
