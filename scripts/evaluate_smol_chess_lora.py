#!/usr/bin/env python3
"""Local-only adapter evaluation on the held-out test split; inference is gated."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

from preflight_smol_chess_training import run_preflight

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST = ROOT / 'datasets' / 'chess_sft' / 'prepared-v2' / 'test.jsonl'
UCI_RE = re.compile(r'^[a-h][1-8][a-h][1-8][qrbn]?$')


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
        'mode': 'dry_run', 'test_rows': len(rows),
        'test_sha256': hashlib.sha256(test_path.read_bytes()).hexdigest(),
        'base_model_dir': str(base_model_dir) if base_model_dir else None,
        'adapter_dir': str(adapter_dir) if adapter_dir else None,
        'preflight': run_preflight(model_dir=base_model_dir),
        'network_access': False, 'model_loaded': False, 'inference_run': False,
    }


def evaluate(*, base_model_dir: Path | None, adapter_dir: Path | None, test_path: Path,
             execute: bool, confirm_local_inference: bool) -> dict:
    if not execute:
        return dry_run_report(base_model_dir, adapter_dir, test_path)
    if not confirm_local_inference:
        raise ValueError('Wymagane --confirm-local-inference; brak zgody nie uruchamia inference.')
    if base_model_dir is None or adapter_dir is None:
        raise ValueError('Wymagane lokalne --base-model-dir i --adapter-dir.')
    report = run_preflight(model_dir=base_model_dir)
    if report['status'] != 'ready_for_owner_review':
        raise ValueError('Preflight zablokował inference: ' + '; '.join(report['blockers']))
    base = base_model_dir.expanduser().resolve()
    adapter = adapter_dir.expanduser().resolve()
    if not adapter.is_dir() or not (adapter / 'adapter_config.json').is_file():
        raise ValueError('Brak lokalnego adaptera PEFT.')
    if adapter == base or adapter.is_relative_to(base) or base.is_relative_to(adapter):
        raise ValueError('Adapter i model bazowy muszą być w oddzielnych katalogach.')
    rows = load_test_rows(test_path)
    os.environ.update({'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1'})
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    model = PeftModel.from_pretrained(model, str(adapter), local_files_only=True)
    model.eval()
    legal_count = 0
    exact_count = 0
    elapsed = 0.0
    details = []
    by_task = {}
    for row in rows:
        prompt = tokenizer.apply_chat_template(row['messages'][:-1], tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors='pt', add_special_tokens=False)
        started = time.perf_counter()
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=12, do_sample=False, pad_token_id=tokenizer.eos_token_id)
        elapsed += time.perf_counter() - started
        decoded = tokenizer.decode(output[0][inputs['input_ids'].shape[1]:], skip_special_tokens=True).strip()
        candidate = decoded.split()[0].strip('`.,;:') if decoded else ''
        legal_moves_text = row['messages'][1]['content'].split('Legalne ruchy: ', 1)[-1]
        legal = bool(UCI_RE.fullmatch(candidate)) and candidate in legal_moves_text.split(', ')
        exact = candidate == row['messages'][-1]['content']
        legal_count += int(legal)
        exact_count += int(exact)
        details.append({'id': row['id'], 'task_type': row['task_type'], 'candidate': candidate,
                        'legal': legal, 'exact_target': exact})
        metrics = by_task.setdefault(row['task_type'], {'rows': 0, 'legal': 0, 'exact_target': 0})
        metrics['rows'] += 1
        metrics['legal'] += int(legal)
        metrics['exact_target'] += int(exact)
    count = len(rows)
    return {
        'status': 'completed', 'base_model_dir': str(base), 'adapter_dir': str(adapter),
        'test_sha256': hashlib.sha256(test_path.read_bytes()).hexdigest(), 'rows': count,
        'legal_move_rate': legal_count / count, 'task_target_accuracy': exact_count / count,
        'mean_inference_seconds': elapsed / count,
        'by_task': {key: {**value, 'legal_move_rate': value['legal'] / value['rows'],
                          'task_target_accuracy': value['exact_target'] / value['rows']}
                    for key, value in by_task.items()},
        'details': details,
        'network_access': False, 'admission_changed': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-model-dir', type=Path)
    parser.add_argument('--adapter-dir', type=Path)
    parser.add_argument('--test-jsonl', type=Path, default=DEFAULT_TEST)
    parser.add_argument('--execute', action='store_true', help='Enable local inference; default is dry-run only.')
    parser.add_argument('--confirm-local-inference', action='store_true')
    args = parser.parse_args()
    report = evaluate(base_model_dir=args.base_model_dir, adapter_dir=args.adapter_dir,
                      test_path=args.test_jsonl, execute=args.execute,
                      confirm_local_inference=args.confirm_local_inference)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
