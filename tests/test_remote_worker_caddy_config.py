import json
import shutil
import subprocess
from pathlib import Path
import pytest


CONFIG = Path(__file__).resolve().parents[1] / 'deploy/remote-worker/Caddyfile.loopback.example'


def test_https_example_only_listens_on_loopback():
    if not shutil.which('caddy'):
        pytest.skip('Caddy is not installed')
    proc = subprocess.run(['caddy', 'adapt', '--config', str(CONFIG), '--adapter', 'caddyfile'],
                          check=True, capture_output=True, text=True, timeout=10)
    cfg = json.loads(proc.stdout)
    servers = cfg['apps']['http']['servers'].values()
    assert all(listener.startswith('127.0.0.1:') for server in servers
               for listener in server['listen'])
    assert '19444' in proc.stdout
    assert 'internal' in proc.stdout
