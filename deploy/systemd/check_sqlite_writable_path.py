#!/usr/bin/env python3
"""Check that systemd grants SQLite's resolved database directory write access."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def directory_is_writable_by_unit(database: Path, read_write_paths: list[Path]) -> tuple[bool, Path, Path]:
    resolved_database = database.resolve(strict=True)
    database_directory = resolved_database.parent.resolve(strict=True)
    allowed_directories = []
    for raw_path in read_write_paths:
        resolved = raw_path.resolve(strict=False)
        if resolved.is_dir():
            allowed_directories.append(resolved)
    allowed = any(
        database_directory == directory or directory in database_directory.parents
        for directory in allowed_directories
    )
    return allowed, resolved_database, database_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--read-write-path", required=True, action="append", type=Path)
    args = parser.parse_args()
    try:
        allowed, resolved_database, database_directory = directory_is_writable_by_unit(
            args.database, args.read_write_path
        )
    except OSError as exc:
        print(f"ERROR: cannot resolve database path: {exc}", file=sys.stderr)
        return 2
    print(f"SQLite database: {resolved_database}")
    print(f"Required writable directory (for rollback journal): {database_directory}")
    if not allowed:
        print("FAIL: no ReadWritePaths entry covers the resolved database directory", file=sys.stderr)
        return 1
    print("PASS: a ReadWritePaths directory covers the resolved database directory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
