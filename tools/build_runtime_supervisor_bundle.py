"""Build a deterministic, standard-library-only zipapp for the root supervisor."""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

BUNDLE_MODULES = (
    "model_permissions.py",
    "runtime_artifact_handoff.py",
    "runtime_lease_broker.py",
    "runtime_resource_policy.py",
    "runtime_socket_handoff.py",
    "runtime_supervisor.py",
    "runtime_supervisor_authority.py",
    "runtime_supervisor_handlers.py",
    "runtime_supervisor_ipc.py",
    "runtime_supervisor_service.py",
    "runtime_systemd.py",
    "runtime_systemd_backend.py",
)

_BOOTSTRAP = b'''"""Verify the immutable supervisor bundle before importing service modules."""
import sys

if not (sys.flags.isolated and sys.flags.no_site):
    raise SystemExit("runtime supervisor must run with Python isolated/no-site mode (-I -S)")
sys.dont_write_bytecode = True

import hashlib
import os
import stat
from pathlib import Path

expected_path = Path("/usr/local/lib/ai-chess-arena-runtime/runtime_supervisor.pyz")
path = Path(sys.argv[0])
expected_hash = os.environ.get("ARENA_RUNTIME_SUPERVISOR_BUNDLE_SHA256", "")
if (path != expected_path or path.resolve(strict=True) != path
        or len(expected_hash) != 64
        or any(char not in "0123456789abcdef" for char in expected_hash)):
    raise SystemExit("runtime supervisor bundle identity is invalid")
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0 or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) & 0o022):
    raise SystemExit("runtime supervisor bundle ownership/mode is unsafe")
digest = hashlib.sha256()
with path.open("rb") as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
if digest.hexdigest() != expected_hash:
    raise SystemExit("runtime supervisor bundle SHA-256 mismatch")

from runtime_supervisor_service import main
raise SystemExit(main())
'''


def build_bundle(repo_root: str | Path, output_path: str | Path) -> str:
    root = Path(repo_root).resolve(strict=True)
    output = Path(output_path)
    source_paths = {root / filename for filename in BUNDLE_MODULES}
    if output.resolve(strict=False) in source_paths:
        raise ValueError("Bundle output must not overwrite a source module.")
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[str, bytes]] = [("__main__.py", _BOOTSTRAP)]
    for filename in BUNDLE_MODULES:
        source = root / filename
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or source.is_symlink():
            raise ValueError(f"Supervisor bundle source is not a regular file: {filename}")
        if source.resolve(strict=True).parent != root:
            raise ValueError(f"Supervisor bundle source escaped repository root: {filename}")
        entries.append((filename, source.read_bytes()))

    fd, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9, strict_timestamps=True) as archive:
            for name, data in sorted(entries):
                item = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                item.flag_bits = 0
                archive.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        with zipfile.ZipFile(temporary, "r") as archive:
            if archive.testzip() is not None:
                raise ValueError("Generated supervisor bundle failed ZIP integrity validation.")
            expected = {"__main__.py", *BUNDLE_MODULES}
            if set(archive.namelist()) != expected:
                raise ValueError("Generated supervisor bundle contains an unexpected file set.")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)

    digest = hashlib.sha256()
    with output.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1], type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    digest = build_bundle(args.repo_root, args.output)
    print(f"bundle={args.output.resolve()} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
