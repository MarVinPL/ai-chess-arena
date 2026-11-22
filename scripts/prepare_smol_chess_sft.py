#!/usr/bin/env python3
"""Prepare offline chat-style JSONL splits from the verified synthetic dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "datasets" / "chess_sft" / "dataset-v2.jsonl"
DEFAULT_OUTPUT = ROOT / "datasets" / "chess_sft" / "prepared-v2"
SYSTEM_PROMPT = "Jesteś asystentem szachowym. Wybieraj wyłącznie ruch z podanej listy legalnych ruchów. Zwróć dokładnie jeden ruch w formacie UCI, bez dodatkowego tekstu."


def prepare_rows(input_path: Path = DEFAULT_INPUT) -> dict[str, list[dict]]:
    result = {"train": [], "validation": [], "test": []}
    seen_fens: set[str] = set()
    source_splits: dict[str, str] = {}
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        split = row.get("split")
        if split not in result:
            raise ValueError(f"Invalid split at line {line_number}: {split}")
        board = chess.Board(row["fen"])
        if not board.is_valid():
            raise ValueError(f"Invalid FEN at line {line_number}")
        legal = sorted(move.uci() for move in board.legal_moves)
        if row.get("legal_moves") != legal or row.get("best_move") not in legal:
            raise ValueError(f"Legal move list/target mismatch at line {line_number}")
        if row["fen"] in seen_fens:
            raise ValueError(f"Duplicate FEN at line {line_number}")
        seen_fens.add(row["fen"])
        group = row.get("source_group")
        if group:
            previous = source_splits.setdefault(group, split)
            if previous != split:
                raise ValueError(f"Source group leakage across splits: {group}")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{row['task_instruction']}\nFEN: {row['fen']}\nLegalne ruchy: {', '.join(legal)}"},
            {"role": "assistant", "content": row["best_move"]},
        ]
        result[split].append({
            "id": row["id"], "messages": messages, "split": split,
            "task_type": row["task_type"], "target_rule": row["target_rule"],
            "source_group": group, "source": row["source"], "license": row["license"],
        })
    if not all(result.values()):
        raise ValueError("Every split must contain at least one example")
    return result


def write_prepared(input_path: Path, output_dir: Path) -> dict:
    splits = prepare_rows(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for split, rows in splits.items():
        path = output_dir / f"{split}.jsonl"
        raw = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
        path.write_bytes(raw)
        outputs[split] = {"rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest(), "path": path.name}
    manifest = {
        "schema_version": 1,
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "outputs": outputs,
        "license": "CC0-1.0",
        "network_access": False,
        "model_loaded": False,
        "purpose": "offline formatting only; no training or inference",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(write_prepared(args.input, args.output_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
