#!/usr/bin/env python3
"""Archived LoRA prototype for Issue #133; execution was cancelled by the owner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from preflight_smol_chess_training import run_preflight

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREPARED = ROOT / 'datasets' / 'chess_sft' / 'prepared-v2'


def dry_run_report(base_model_dir: Path | None, prepared_dir: Path, output_dir: Path) -> dict:
    required = [prepared_dir / 'train.jsonl', prepared_dir / 'validation.jsonl', prepared_dir / 'manifest.json']
    return {
        'mode': 'archived_dry_run',
        'prepared_files_present': all(path.is_file() for path in required),
        'prepared_files': [str(path) for path in required],
        'preflight': run_preflight(model_dir=base_model_dir, output_dir=output_dir),
        'network_access': False, 'model_loaded': False, 'training_started': False,
        'training_status': 'CANCELLED_BY_OWNER', 'adapter_status': 'NOT_CREATED',
    }


def train(*, base_model_dir: Path | None, prepared_dir: Path, output_dir: Path, execute: bool,
          confirm_local_offline_training: bool, max_length: int = 512, epochs: int = 2) -> dict:
    if execute:
        raise ValueError('Trening LoRA w Issue #133 został anulowany decyzją właściciela; wykonanie jest wyłączone.')
    return dry_run_report(base_model_dir, prepared_dir, output_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description='Archiwalny dry-run; trening LoRA został anulowany.')
    parser.add_argument('--base-model-dir', type=Path)
    parser.add_argument('--prepared-dir', type=Path, default=DEFAULT_PREPARED)
    parser.add_argument('--output-dir', type=Path, default=Path('/home/debian/arena-smol-lora-output'))
    parser.add_argument('--max-length', type=int, default=512)
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--execute', action='store_true', help='Wyłączone: trening anulowano.')
    parser.add_argument('--confirm-local-offline-training', action='store_true', help='Nie reaktywuje anulowanego treningu.')
    args = parser.parse_args()
    report = train(base_model_dir=args.base_model_dir, prepared_dir=args.prepared_dir,
                   output_dir=args.output_dir, execute=args.execute,
                   confirm_local_offline_training=args.confirm_local_offline_training,
                   max_length=args.max_length, epochs=args.epochs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
