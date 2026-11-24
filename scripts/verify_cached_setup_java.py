#!/usr/bin/env python3
"""Verify cached setup-java source bytes/modes before local action execution."""
import hashlib
import stat
import subprocess
from pathlib import Path

ACTION = Path(".github/actions/setup-java")
PIN = "d1374f8ac74474200d16ef8ecee22c7ae7a51880"
MANIFEST = "42afc9201b4cd2311b240fbcd3376afa562830826e894535f9d6d5d9cf73e6f0"


def verify():
    expected = f"160000 commit {PIN}\t{ACTION.as_posix()}"
    actual = subprocess.check_output(
        ["git", "ls-tree", "HEAD", "--", ACTION.as_posix()], text=True
    ).strip()
    if actual != expected:
        raise SystemExit("Unexpected setup-java gitlink")
    if ACTION.is_symlink() or not ACTION.is_dir():
        raise SystemExit("Missing or invalid action directory")
    h = hashlib.sha256()
    count = total = 0
    for p in sorted(ACTION.rglob("*"), key=lambda p: p.relative_to(ACTION).as_posix().encode()):
        rel = p.relative_to(ACTION).as_posix()
        mode = p.lstat().st_mode
        if ".git" in p.relative_to(ACTION).parts:
            raise SystemExit("Git metadata must not be cached")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or p.lstat().st_nlink != 1:
            raise SystemExit("Unexpected non-regular or hardlinked action file")
        data = p.read_bytes()
        h.update(rel.encode() + b"\0" + (b"100755" if mode & stat.S_IXUSR else b"100644") + b"\0" + hashlib.sha256(data).digest())
        count += 1
        total += len(data)
    if h.hexdigest() != MANIFEST or count != 272 or total != 28358024:
        raise SystemExit("Action manifest mismatch; refusing execution")
    print(f"VERIFIED_ACTION {PIN} files={count} bytes={total}")


if __name__ == "__main__":
    verify()
