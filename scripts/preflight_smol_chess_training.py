#!/usr/bin/env python3
"""Read-only, offline resource preflight; never downloads or loads model weights."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

GIB = 1024 ** 3


def available_ram_bytes(meminfo: Path = Path('/proc/meminfo')) -> int | None:
    try:
        for line in meminfo.read_text(encoding='ascii').splitlines():
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def inspect_local_model(model_dir: Path | None) -> dict:
    if model_dir is None:
        return {"status": "not_supplied", "path": None, "weight_bytes": 0, "has_config": False, "has_tokenizer": False}
    root = model_dir.expanduser().resolve()
    if not root.is_dir():
        return {"status": "missing_directory", "path": str(root), "weight_bytes": 0, "has_config": False, "has_tokenizer": False}
    has_config = (root / 'config.json').is_file()
    has_tokenizer = any((root / name).is_file() for name in ('tokenizer.json', 'tokenizer.model', 'tokenizer_config.json'))
    weight_bytes = sum(path.stat().st_size for pattern in ('*.safetensors', '*.bin', '*.pt') for path in root.glob(pattern) if path.is_file())
    status = 'ready_local_files_present' if has_config and has_tokenizer and weight_bytes > 0 else 'incomplete_local_snapshot'
    return {"status": status, "path": str(root), "weight_bytes": weight_bytes, "has_config": has_config, "has_tokenizer": has_tokenizer}


def run_preflight(*, model_dir: Path | None = None, min_ram_gib: float = 8.0,
                  min_disk_gib: float = 4.0, output_dir: Path = Path('/tmp/arena-smol-lora-output'),
                  meminfo: Path = Path('/proc/meminfo')) -> dict:
    ram = available_ram_bytes(meminfo)
    disk = shutil.disk_usage(output_dir.parent if output_dir.parent.exists() else Path('/tmp')).free
    model = inspect_local_model(model_dir)
    blockers = []
    if model['status'] != 'ready_local_files_present':
        blockers.append('Brak kompletnego, lokalnego snapshotu modelu bazowego; skrypt nie pobiera wag.')
    if ram is None or ram < min_ram_gib * GIB:
        blockers.append(f'Dostępny RAM poniżej progu bezpieczeństwa {min_ram_gib:.1f} GiB lub nieznany.')
    if disk < min_disk_gib * GIB:
        blockers.append(f'Wolne miejsce poniżej progu bezpieczeństwa {min_disk_gib:.1f} GiB.')
    return {
        "status": "blocked" if blockers else "ready_for_owner_review",
        "available_ram_bytes": ram,
        "available_disk_bytes": disk,
        "thresholds": {"min_ram_gib": min_ram_gib, "min_disk_gib": min_disk_gib},
        "local_model": model,
        "blockers": blockers,
        "network_access": False,
        "weights_downloaded": False,
        "model_loaded": False,
        "training_started": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-model-dir', type=Path)
    parser.add_argument('--min-ram-gib', type=float, default=8.0)
    parser.add_argument('--min-disk-gib', type=float, default=4.0)
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/arena-smol-lora-output'))
    args = parser.parse_args()
    report = run_preflight(model_dir=args.base_model_dir, min_ram_gib=args.min_ram_gib,
                           min_disk_gib=args.min_disk_gib, output_dir=args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'ready_for_owner_review' else 2


if __name__ == '__main__':
    raise SystemExit(main())
