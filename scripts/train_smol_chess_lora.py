#!/usr/bin/env python3
"""CPU-only, local-files-only LoRA training. Execution is explicitly gated."""
from __future__ import annotations

import argparse
import json
import os
import inspect
from pathlib import Path

from preflight_smol_chess_training import run_preflight

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREPARED = ROOT / 'datasets' / 'chess_sft' / 'prepared-v2'


def dry_run_report(base_model_dir: Path | None, prepared_dir: Path, output_dir: Path) -> dict:
    required = [prepared_dir / 'train.jsonl', prepared_dir / 'validation.jsonl', prepared_dir / 'manifest.json']
    return {
        'mode': 'dry_run',
        'prepared_files_present': all(path.is_file() for path in required),
        'prepared_files': [str(path) for path in required],
        'preflight': run_preflight(model_dir=base_model_dir, output_dir=output_dir),
        'network_access': False,
        'model_loaded': False,
        'training_started': False,
    }


def train(*, base_model_dir: Path, prepared_dir: Path, output_dir: Path, execute: bool,
          confirm_local_offline_training: bool, max_length: int = 512, epochs: int = 2) -> dict:
    if not execute:
        return dry_run_report(base_model_dir, prepared_dir, output_dir)
    if not confirm_local_offline_training:
        raise ValueError('Wymagane --confirm-local-offline-training; brak zgody nie uruchamia treningu.')
    report = run_preflight(model_dir=base_model_dir, output_dir=output_dir)
    if report['status'] != 'ready_for_owner_review':
        raise ValueError('Preflight zablokował trening: ' + '; '.join(report['blockers']))
    for name in ('train.jsonl', 'validation.jsonl'):
        if not (prepared_dir / name).is_file():
            raise ValueError(f'Brak przygotowanego splitu: {name}')
    base = base_model_dir.expanduser().resolve()
    output = output_dir.expanduser().resolve()
    if output == base or output.is_relative_to(base) or base.is_relative_to(output):
        raise ValueError('Katalog adaptera musi być oddzielony od katalogu modelu bazowego.')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Katalog wyjściowy adaptera nie jest pusty.')

    # Offline flags are set before importing any Hugging Face library.
    os.environ.update({
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_DATASETS_OFFLINE': '1', 'WANDB_DISABLED': 'true',
    })
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, Trainer, TrainingArguments

    class ChatDataset(torch.utils.data.Dataset):
        def __init__(self, path, tokenizer):
            self.items = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
            self.tokenizer = tokenizer

        def __len__(self):
            return len(self.items)

        def __getitem__(self, index):
            messages = self.items[index]['messages']
            prompt = self.tokenizer.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
            full = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
            prompt_ids = self.tokenizer(prompt, add_special_tokens=False, truncation=True, max_length=max_length)['input_ids']
            full_ids = self.tokenizer(full, add_special_tokens=False, truncation=True, max_length=max_length)['input_ids']
            prompt_len = min(len(prompt_ids), len(full_ids))
            labels = [-100] * prompt_len + full_ids[prompt_len:]
            return {'input_ids': full_ids, 'attention_mask': [1] * len(full_ids), 'labels': labels}

    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(str(base), local_files_only=True, trust_remote_code=False)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    model = get_peft_model(model, LoraConfig(
        r=8, lora_alpha=16, lora_dropout=0.05,
        target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj'],
        task_type=TaskType.CAUSAL_LM,
    ))

    def collate(items):
        width = max(len(item['input_ids']) for item in items)
        pad = tokenizer.pad_token_id
        return {
            'input_ids': torch.tensor([item['input_ids'] + [pad] * (width-len(item['input_ids'])) for item in items]),
            'attention_mask': torch.tensor([item['attention_mask'] + [0] * (width-len(item['attention_mask'])) for item in items]),
            'labels': torch.tensor([item['labels'] + [-100] * (width-len(item['labels'])) for item in items]),
        }

    training_kwargs = {
        'output_dir': str(output), 'num_train_epochs': epochs,
        'per_device_train_batch_size': 1, 'per_device_eval_batch_size': 1,
        'gradient_accumulation_steps': 4, 'learning_rate': 2e-4,
        'logging_steps': 5, 'save_strategy': 'epoch', 'report_to': [],
        'fp16': False, 'bf16': False, 'save_safetensors': True,
        'seed': 1337, 'data_seed': 1337, 'dataloader_num_workers': 0,
    }
    training_parameters = inspect.signature(TrainingArguments.__init__).parameters
    training_kwargs['eval_strategy' if 'eval_strategy' in training_parameters else 'evaluation_strategy'] = 'epoch'
    if 'use_cpu' in training_parameters:
        training_kwargs['use_cpu'] = True
    else:
        training_kwargs['no_cuda'] = True
    args = TrainingArguments(**training_kwargs)
    trainer = Trainer(
        model=model, args=args,
        train_dataset=ChatDataset(prepared_dir / 'train.jsonl', tokenizer),
        eval_dataset=ChatDataset(prepared_dir / 'validation.jsonl', tokenizer),
        data_collator=collate,
    )
    train_result = trainer.train()
    eval_metrics = trainer.evaluate()
    model.save_pretrained(output)
    tokenizer.save_pretrained(output)
    return {
        'status': 'completed', 'adapter_dir': str(output), 'base_model_dir': str(base),
        'train_metrics': train_result.metrics, 'validation_metrics': eval_metrics,
        'network_access': False, 'allowlist_changed': False, 'admission_changed': False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-model-dir', type=Path)
    parser.add_argument('--prepared-dir', type=Path, default=DEFAULT_PREPARED)
    parser.add_argument('--output-dir', type=Path, default=Path('/tmp/arena-smol-lora-output'))
    parser.add_argument('--max-length', type=int, default=512)
    parser.add_argument('--epochs', type=int, default=2)
    parser.add_argument('--execute', action='store_true', help='Enable training; default is dry-run only.')
    parser.add_argument('--confirm-local-offline-training', action='store_true')
    args = parser.parse_args()
    if args.execute and args.base_model_dir is None:
        parser.error('--execute wymaga --base-model-dir wskazującego lokalny snapshot')
    report = train(base_model_dir=args.base_model_dir, prepared_dir=args.prepared_dir,
                   output_dir=args.output_dir, execute=args.execute,
                   confirm_local_offline_training=args.confirm_local_offline_training,
                   max_length=args.max_length, epochs=args.epochs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
