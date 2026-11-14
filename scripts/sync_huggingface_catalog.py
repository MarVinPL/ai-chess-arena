#!/usr/bin/env python3
"""GitHub Actions entrypoint for the metadata-only Hugging Face catalog sync."""
from __future__ import annotations

import sys
from pathlib import Path

# When Python executes a file under scripts/, sys.path[0] is scripts/, not
# the repository root. Resolve the root from this file so imports also work
# when the process is launched from an unrelated working directory.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
repository_root_text = str(REPOSITORY_ROOT)
if repository_root_text not in sys.path:
    sys.path.insert(0, repository_root_text)


def main() -> int:
    import json

    from huggingface_catalog_sync import CatalogSyncError, run_sync

    try:
        report = run_sync()
    except CatalogSyncError as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
