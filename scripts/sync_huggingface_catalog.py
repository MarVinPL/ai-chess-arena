#!/usr/bin/env python3
import json
from existing_model_metadata_check import CatalogSyncError, run_sync
try: report=run_sync()
except CatalogSyncError as exc:
 print(json.dumps({'status':'error','error':str(exc)},ensure_ascii=False)); raise SystemExit(1)
print(json.dumps(report,ensure_ascii=False,sort_keys=True))
