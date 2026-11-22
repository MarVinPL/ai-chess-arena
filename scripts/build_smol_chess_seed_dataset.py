#!/usr/bin/env python3
"""Build a tiny deterministic, self-authored chess SFT seed set; no network access."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "datasets" / "chess_sft" / "seed.jsonl"
SOURCE = "synthetic_kqk_mate_in_one_v1"
LICENSE = "CC0-1.0"

# Eight deterministic legal K+Q vs K positions. The unique mate-in-one label
# is calculated and verified with python-chess rather than copied from a game.
FENS = (
    "8/8/8/8/8/8/8/k1KQ4 w - - 0 1",
    "8/8/8/8/8/8/8/k1K1Q3 w - - 0 1",
    "8/8/8/8/8/8/8/k1K2Q2 w - - 0 1",
    "8/8/8/8/8/8/8/k1K3Q1 w - - 0 1",
    "8/8/8/8/8/8/8/k1K4Q w - - 0 1",
    "8/8/8/8/8/8/7Q/k1K5 w - - 0 1",
    "8/8/8/8/8/6Q1/8/k1K5 w - - 0 1",
    "8/8/8/8/8/7Q/8/k1K5 w - - 0 1",
)

SPLITS = ("train", "train", "train", "train", "train", "validation", "test", "test")


def build_rows() -> list[dict]:
    rows = []
    seen = set()
    for index, (fen, split) in enumerate(zip(FENS, SPLITS), start=1):
        board = chess.Board(fen)
        if not board.is_valid() or board.turn != chess.WHITE or board.is_check():
            raise ValueError(f"Invalid seed position: {fen}")
        normalized = board.fen()
        if normalized in seen:
            raise ValueError(f"Duplicate position: {normalized}")
        seen.add(normalized)
        legal_moves = sorted(move.uci() for move in board.legal_moves)
        mating_moves = []
        for move in board.legal_moves:
            after = board.copy()
            after.push(move)
            if after.is_checkmate():
                mating_moves.append(move.uci())
        if len(mating_moves) != 1:
            raise ValueError(f"Expected exactly one mate-in-one, got {mating_moves}: {fen}")
        rows.append({
            "id": f"synthetic-kqk-{index:03d}",
            "fen": normalized,
            "legal_moves": legal_moves,
            "best_move": mating_moves[0],
            "task_type": "mate_in_one",
            "split": split,
            "source": SOURCE,
            "license": LICENSE,
            "explanation": "Jedyny legalny ruch, który natychmiast kończy partię matem.",
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rows = build_rows()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps({"rows": len(rows), "splits": {
        split: sum(row["split"] == split for row in rows)
        for split in ("train", "validation", "test")
    }, "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
