import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import tournament_checkpoint
from remote_worker_job_binding import BoundWorkerResults
from remote_worker_tokens import WorkerTokenStore

ROOT = Path(__file__).resolve().parents[1]


def test_service_rejects_uninitialized_database(tmp_path):
    path = tmp_path / 'empty.sqlite'
    sqlite3.connect(path).close()
    proc = subprocess.run([sys.executable, str(ROOT / 'remote_worker_service.py'),
                           '--db', str(path), '--port', '19444'],
                          capture_output=True, text=True, timeout=5)
    assert proc.returncode != 0
    assert 'missing remote worker tables' in proc.stderr


def test_systemd_template_is_disabled_by_default():
    unit = (ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service.example').read_text()
    assert '127.0.0.1' in (ROOT / 'remote_worker_http_ingest.py').read_text()
    assert 'ExecStart=' in unit
    assert 'NoNewPrivileges=true' in unit
    assert 'IPAddressDeny=any' in unit
    assert 'IPAddressAllow=localhost' in unit
    assert 'ProtectSystem=strict' in unit
    assert not (ROOT / 'deploy/remote-worker/ai-chess-remote-worker.service').exists()
