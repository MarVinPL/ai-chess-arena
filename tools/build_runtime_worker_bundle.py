"""Build a deterministic, standard-library-only executable zipapp for the isolated runtime worker."""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tempfile
import zipfile
from pathlib import Path

WORKER_MODULES = (
    "runtime_resource_policy.py",
    "runtime_socket_handoff.py",
    "runtime_unix_bridge.py",
)
WORKER_ENTRYPOINT = "runtime_worker_supervised.py"
INTERPRETER = "/usr/bin/python3"


def build_worker_bundle(repo_root: str | Path, output_path: str | Path) -> str:
    root = Path(repo_root).resolve(strict=True)
    output = Path(output_path)
    source_names = (WORKER_ENTRYPOINT, *WORKER_MODULES)
    source_paths = {root / name for name in source_names}
    if output.resolve(strict=False) in source_paths:
        raise ValueError("Worker bundle output must not overwrite a source module.")

    entries: list[tuple[str, bytes]] = []
    for source_name in source_names:
        source = root / source_name
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or source.is_symlink():
            raise ValueError(f"Worker bundle source is not a regular file: {source_name}")
        if source.resolve(strict=True).parent != root:
            raise ValueError(f"Worker bundle source escaped repository root: {source_name}")
        archive_name = "__main__.py" if source_name == WORKER_ENTRYPOINT else source_name
        entries.append((archive_name, source.read_bytes()))

    output.parent.mkdir(parents=True, exist_ok=True)
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
                raise ValueError("Generated worker bundle failed ZIP integrity validation.")
            expected = {"__main__.py", *WORKER_MODULES}
            if set(archive.namelist()) != expected:
                raise ValueError("Generated worker bundle contains an unexpected file set.")
        fd, staged_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".stage", dir=output.parent)
        os.close(fd)
        staged = Path(staged_name)
        try:
            staged.write_bytes(f"#!{INTERPRETER}\n".encode("ascii") + temporary.read_bytes())
            os.chmod(staged, 0o755)
            staged.replace(output)
        finally:
            staged.unlink(missing_ok=True)
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
    digest = build_worker_bundle(args.repo_root, args.output)
    print(f"worker={args.output.resolve()} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
