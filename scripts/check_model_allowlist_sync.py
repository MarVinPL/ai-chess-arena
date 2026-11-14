#!/usr/bin/env python3
"""Read-only check that the checkout and model allowlist match origin/main."""
import json
from pathlib import Path
from model_allowlist import check_repository_sync

result = check_repository_sync(Path(__file__).resolve().parents[1])
print(json.dumps(result, ensure_ascii=False, sort_keys=True))
raise SystemExit(0 if result['in_sync'] else 2)
