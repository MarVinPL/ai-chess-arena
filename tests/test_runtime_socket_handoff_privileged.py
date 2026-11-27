"""Opt-in cross-UID DAC integration test; never provisions host identities."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from runtime_socket_handoff import verify_handoff


pytestmark = pytest.mark.skipif(
    os.environ.get("ARENA_RUN_PRIVILEGED_SOCKET_TESTS") != "1" or os.geteuid() != 0,
    reason="requires explicit opt-in and root to create an isolated temporary DAC fixture",
)


def test_runtime_uid_creates_socket_api_uid_connects_but_cannot_unlink():
    runtime_uid, runtime_gid = 65534, 65534  # ephemeral nobody identity
    api_uid, api_gid = 1000, 1000            # current debian service identity
    with tempfile.TemporaryDirectory(prefix="arena-sock-dac-") as root:
        os.chmod(root, 0o711)  # traversal only; unique temporary fixture
        directory = Path(root) / "job-1"
        directory.mkdir()
        os.chown(directory, runtime_uid, api_gid)
        os.chmod(directory, 0o2710)
        socket_path = directory / "control.sock"

        runtime_code = r"""
import os, socket, sys
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(sys.argv[1])
os.chmod(sys.argv[1], 0o660)
server.listen(1)
print("READY", flush=True)
conn, _ = server.accept()
conn.sendall(b"ok:" + conn.recv(64))
conn.close()
server.close()
"""
        runtime = subprocess.Popen(
            [
                "setpriv", f"--reuid={runtime_uid}", f"--regid={runtime_gid}",
                f"--groups={api_gid}", sys.executable, "-c", runtime_code,
                str(socket_path),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            assert runtime.stdout.readline().strip() == "READY", runtime.stderr.read()
            verify_handoff(
                directory, socket_path,
                runtime_uid=runtime_uid, api_gid=api_gid,
            )

            api_code = r"""
import os, socket, sys
path = sys.argv[1]
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.connect(path)
client.sendall(b"probe")
assert client.recv(64) == b"ok:probe"
client.close()
try:
    os.unlink(path)
except PermissionError:
    pass
else:
    raise AssertionError("API unexpectedly unlinked runtime socket")
try:
    open(path + ".replacement", "w").close()
except PermissionError:
    pass
else:
    raise AssertionError("API unexpectedly wrote in runtime directory")
"""
            api = subprocess.run(
                [
                    "setpriv", f"--reuid={api_uid}", f"--regid={api_gid}",
                    "--clear-groups", sys.executable, "-c", api_code,
                    str(socket_path),
                ],
                capture_output=True, text=True, timeout=5,
            )
            assert api.returncode == 0, api.stderr
            runtime.wait(timeout=5)
            assert runtime.returncode == 0, runtime.stderr.read()
        finally:
            if runtime.poll() is None:
                runtime.kill()
                runtime.wait(timeout=5)
