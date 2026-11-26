#!/usr/bin/env python3
"""Verify cached setup-node source bytes/modes before local action execution."""
import hashlib
import os
import stat
import subprocess
from pathlib import Path

ACTION = Path(".github/actions/setup-node")
PIN = "949feb2413d6458794dcd2491c4babbbce0c15c1"
MANIFEST = "d8fc34c4a137b29d7ce72623afe226b3947a59e2f1ca1a105b52a3289aa5b4fe"


WINDOWS_EXECUTABLES = {"__tests__/prepare-yarn-subprojects.sh", "__tests__/verify-node.sh"}


def verify():
    expected = f"160000 commit {PIN}\t{ACTION.as_posix()}"
    actual = subprocess.check_output(
        ["git", "ls-tree", "HEAD", "--", ACTION.as_posix()], text=True
    ).strip()
    if actual != expected:
        raise SystemExit("Unexpected setup-node gitlink")
    if ACTION.is_symlink() or not ACTION.is_dir():
        raise SystemExit("Missing or invalid action directory")
    h = hashlib.sha256()
    count = total = 0
    for p in sorted(ACTION.rglob("*"), key=lambda p: p.relative_to(ACTION).as_posix().encode()):
        rel = p.relative_to(ACTION).as_posix()
        mode = p.lstat().st_mode
        if ".git" in p.relative_to(ACTION).parts:
            raise SystemExit("Git metadata must not be cached")
        if getattr(p.lstat(), "st_file_attributes", 0) & 0x400:
            raise SystemExit("Unexpected reparse point")
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or p.lstat().st_nlink != 1:
            raise SystemExit("Unexpected non-regular or hardlinked action file")
        # Windows has no meaningful POSIX executable bit; pin paths instead.
        executable = rel in WINDOWS_EXECUTABLES if os.name == "nt" else bool(mode & stat.S_IXUSR)
        data = p.read_bytes()
        h.update(rel.encode() + b"\0" + (b"100755" if executable else b"100644") + b"\0" + hashlib.sha256(data).digest())
        count += 1
        total += len(data)
    if h.hexdigest() != MANIFEST or count != 153 or total != 8085681:
        raise SystemExit("Action manifest mismatch; refusing execution")
    print(f"VERIFIED_ACTION {PIN} files={count} bytes={total}")


if __name__ == "__main__":
    verify()
