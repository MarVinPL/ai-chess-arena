"""Trusted, fixed-command child entry point for a single GGUF smoke test.

This module is launched only by runtime_subprocess.py. It accepts a verified
quarantine path and a loopback port, never a user-supplied executable or shell
command. The parent owns the process group and enforces the wall-clock timeout.
"""
from __future__ import annotations

import hashlib
import os
import resource
import stat
import sys
from pathlib import Path

BINARY = Path('/home/debian/ai-agent/bin/llamafile')
BINARY_SHA256 = '756bf97febae557084e8ced9bde7a1cb0c7f6ce778530d582afa219406237a50'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:
        return 64
    model_raw, expected_sha, port_raw = args
    try:
        port = int(port_raw)
    except ValueError:
        return 64
    if not 1 <= port <= 65535 or len(expected_sha) != 64:
        return 64

    try:
        quarantine = Path('/var/lib/ai-chess-arena/hf-candidate-downloads').resolve(strict=True)
        model = Path(model_raw).resolve(strict=True)
        if not model.is_file() or not model.is_relative_to(quarantine):
            return 65
        mode = model.stat().st_mode
        if not stat.S_ISREG(mode):
            return 65
        if _sha256(model) != expected_sha:
            return 66
        binary = BINARY.resolve(strict=True)
        if not binary.is_file() or not os.access(binary, os.X_OK):
            return 67
        if _sha256(binary) != BINARY_SHA256:
            return 68
    except (OSError, ValueError):
        return 69

    # Bound CPU time, log growth, open descriptors and core dumps for this child.
    cpu_limit = 1802
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    with binary.open('rb') as stream:
        is_elf = stream.read(4) == b'\x7fELF'
    command = [str(binary)] if is_elf else ['/bin/sh', str(binary)]
    command += [
        '-m', str(model), '--server', '--host', '127.0.0.1',
        '--port', str(port), '--threads', '1', '--ctx-size', '2048',
        '--parallel', '1', '--temp', '0', '--no-ui', '--no-warmup',
    ]
    os.execve(command[0], command, {
        'PATH': '/usr/bin:/bin',
        'HOME': os.environ.get('HOME', '/tmp'),
        'TMPDIR': os.environ.get('TMPDIR', '/tmp'),
        'LANG': 'C.UTF-8',
        'OMP_NUM_THREADS': '1',
        'LLAMA_CACHE': os.environ.get('TMPDIR', '/tmp'),
    })
    return 70


if __name__ == '__main__':
    raise SystemExit(main())
