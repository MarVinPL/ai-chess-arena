#!/usr/bin/env python3
"""Create the deterministic, CC0 UCI-format-only corpus for Issue #190.

The target is deliberately a seeded legal move, not an engine choice.  This keeps
the experiment about emitting one UCI token and not about chess strength.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
SYSTEM = "Return exactly one legal chess move in UCI format. No prose, punctuation, or markdown."
SPLITS = (("train", 500, 19001), ("validation", 50, 29001), ("test", 50, 39001))


def make_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for split, wanted, seed in SPLITS:
        rng = random.Random(seed)
        game = 0
        while sum(r["split"] == split for r in rows) < wanted:
            game += 1
            board = chess.Board()
            group = f"uci-format-{split}-game-{game:04d}"
            for ply in range(120):
                if board.is_game_over() or sum(r["split"] == split for r in rows) >= wanted:
                    break
                legal = sorted(move.uci() for move in board.legal_moves)
                fen = board.fen()
                if fen not in seen:
                    seen.add(fen)
                    target = legal[rng.randrange(len(legal))]
                    rows.append({
                        "id": f"uci-format-{split}-{sum(r['split'] == split for r in rows)+1:04d}",
                        "fen": fen, "legal_moves": legal, "best_move": target,
                        "split": split, "source_group": group,
                        "source": "deterministic_self_authored_random_legal_playouts_v1",
                        "license": "CC0-1.0", "task_type": "uci_format_only",
                        "target_rule": "seeded_member_of_legal_moves",
                        "label_method": "python-chess-legal-move-enumeration-plus-seeded-selection",
                    })
                board.push(chess.Move.from_uci(legal[rng.randrange(len(legal))]))
    return rows


def write_dataset(output: Path, manifest: Path) -> dict:
    rows = make_rows()
    raw = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    report = {
        "schema_version": 1, "dataset_id": "uci-format-only-v1", "rows": len(rows),
        "split_counts": dict(Counter(row["split"] for row in rows)),
        "sha256": hashlib.sha256(raw).hexdigest(), "license": "CC0-1.0",
        "generator": "scripts/build_uci_format_dataset.py", "validator": f"python-chess {chess.__version__}",
        "purpose": "UCI response formatting only; not chess strategy, Elo, or passkey behavior",
        "split_policy": "independent seeded games per split and global exact-FEN de-duplication",
        "network_access": False, "model_weights_downloaded": False,
    }
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "datasets/uci_format_only/dataset.jsonl")
    parser.add_argument("--manifest", type=Path, default=ROOT / "datasets/uci_format_only/manifest.json")
    args = parser.parse_args()
    print(json.dumps(write_dataset(args.output, args.manifest), ensure_ascii=False))
