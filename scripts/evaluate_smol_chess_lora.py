#!/usr/bin/env python3
"""Archived adapter evaluation prototype; no adapter was created for Issue #133."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from preflight_smol_chess_training import run_preflight

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST = ROOT / 'datasets' / 'chess_sft' / 'prepared-v2' / 'test.jsonl'


def load_test_rows(path: Path = DEFAULT_TEST) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        raise ValueError('Test split jest pusty.')
    if any(row.get('split') != 'test' for row in rows):
        raise ValueError('Plik ewaluacyjny zawiera rekord spoza splitu test.')
    return rows


def dry_run_report(base_model_dir: Path | None, adapter_dir: Path | None, test_path: Path) -> dict:
    rows = load_test_rows(test_path)
    return {
        'mode': 'archived_dry_run', 'test_rows': len(rows),
        'test_sha256': hashlib.sha256(test_path.read_bytes()).hexdigest(),
        'base_model_dir': str(base_model_dir) if base_model_dir else None,
        'adapter_dir': str(adapter_dir) if adapter_dir else None,
        'preflight': run_preflight(model_dir=base_model_dir),
        'network_access': False, 'model_loaded': False, 'inference_run': False,
        'adapter_status': 'NOT_CREATED', 'evaluation_status': 'NOT_APPLICABLE',
    }


def evaluate(*, base_model_dir: Path | None, adapter_dir: Path | None, test_path: Path,
             execute: bool, confirm_local_inference: bool) -> dict:
    if execute:
        raise ValueError('Ewaluacja adaptera jest NOT_APPLICABLE: trening anulowano, adapter nie został utworzony.')
    return dry_run_report(base_model_dir, adapter_dir, test_path)


def main() -> int:
    parser = argparse.ArgumentParser(description='Archiwalny dry-run; adapter nie został utworzony.')
    parser.add_argument('--base-model-dir', type=Path)
    parser.add_argument('--adapter-dir', type=Path)
    parser.add_argument('--test-jsonl', type=Path, default=DEFAULT_TEST)
    parser.add_argument('--execute', action='store_true', help='Wyłączone: adapter nie został utworzony.')
    parser.add_argument('--confirm-local-inference', action='store_true', help='Nie reaktywuje ewaluacji.')
    args = parser.parse_args()
    report = evaluate(base_model_dir=args.base_model_dir, adapter_dir=args.adapter_dir,
                      test_path=args.test_jsonl, execute=args.execute,
                      confirm_local_inference=args.confirm_local_inference)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
