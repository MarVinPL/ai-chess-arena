"""Explicitly started loopback-only Remote Worker Gateway process."""
import argparse
import os
import signal
import sqlite3
from pathlib import Path

from remote_worker_http_ingest import create_loopback_server


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI Chess Arena remote worker gateway (loopback only)')
    parser.add_argument('--db', required=True, help='Existing arena SQLite database')
    parser.add_argument('--port', type=int, default=19444)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    db_path = Path(args.db).resolve(strict=True)
    if not db_path.is_file():
        parser.error('database path must be an existing file')
    # Do not create a new database by accident; avoid silently running with
    # an empty schema or against an unintended working directory.
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {'tournaments', 'tournament_checkpoints', 'worker_job_safety',
                'remote_job_binding', 'worker_result_journal', 'worker_tokens'}
    if not required.issubset(tables):
        parser.error('database is missing remote worker tables')
    server = create_loopback_server(str(db_path), port=args.port)
    def stop(signum, frame):
        # Avoid shutdown() from the serving thread; terminate via serve_forever
        # poll cycle by signalling a separate thread.
        import threading
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.1)
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
