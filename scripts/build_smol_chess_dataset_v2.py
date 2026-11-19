#!/usr/bin/env python3
"""Build a deterministic, offline, synthetic chess SFT dataset; no model or network access."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]
SEED_V1 = ROOT / "datasets" / "chess_sft" / "seed.jsonl"
OUTPUT = ROOT / "datasets" / "chess_sft" / "dataset-v2.jsonl"
MANIFEST_OUTPUT = ROOT / "datasets" / "chess_sft" / "manifest-v2.json"
LICENSE = "CC0-1.0"
GAMES_PER_SPLIT = 60
MAX_PLIES = 100

# Existing independently authored KQK mate-in-one examples are retained as
# endgame tasks. New rows are generated from independent deterministic games.
RANDOM_QUOTAS = {
    "train": {"unique_check": 38, "unique_capture": 20, "unique_castle": 10, "unique_evasion": 5, "promotion_choice": 2},
    "validation": {"unique_check": 4, "unique_capture": 2, "unique_castle": 1, "unique_evasion": 1, "promotion_choice": 1},
    "test": {"unique_check": 3, "unique_capture": 2, "unique_castle": 1, "unique_evasion": 1, "promotion_choice": 1},
}
SPLIT_SEEDS = {"train": 11000, "validation": 21000, "test": 31000}
TASK_INSTRUCTIONS = {
    "endgame_mate_in_one": "Znajdź jedyny legalny ruch dający natychmiastowego mata.",
    "unique_check": "Wybierz jedyny legalny ruch dający szacha.",
    "unique_capture": "Wybierz jedyne legalne bicie w tej pozycji.",
    "unique_castle": "Wybierz jedyne legalne roszowanie.",
    "unique_evasion": "Król jest szachowany; wybierz jedyną legalną odpowiedź.",
    "promotion_choice": "Wybierz legalną promocję o największej wartości materialnej.",
}


def _row(board: chess.Board, move: chess.Move, *, row_id: str, split: str,
         task_type: str, source: str, source_group: str, label_method: str,
         target_rule: str, explanation: str) -> dict:
    legal_moves = sorted(candidate.uci() for candidate in board.legal_moves)
    if move.uci() not in legal_moves:
        raise ValueError(f"Target move is not legal: {move.uci()} / {board.fen()}")
    return {
        "id": row_id,
        "fen": board.fen(),
        "legal_moves": legal_moves,
        "best_move": move.uci(),
        "task_type": task_type,
        "task_instruction": TASK_INSTRUCTIONS[task_type],
        "target_rule": target_rule,
        "label_method": label_method,
        "split": split,
        "source": source,
        "source_group": source_group,
        "license": LICENSE,
        "explanation": explanation,
    }


def _seed_endgames(seen: set[str]) -> dict[str, list[dict]]:
    result = {split: [] for split in RANDOM_QUOTAS}
    for line in SEED_V1.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        board = chess.Board(item["fen"])
        move = chess.Move.from_uci(item["best_move"])
        after = board.copy()
        after.push(move)
        if not board.is_valid() or move not in board.legal_moves or not after.is_checkmate():
            raise ValueError(f"Invalid v1 endgame seed: {item['id']}")
        fen = board.fen()
        if fen in seen:
            raise ValueError(f"Duplicate v1 FEN: {fen}")
        seen.add(fen)
        split = item["split"]
        result[split].append(_row(
            board, move, row_id=item["id"], split=split,
            task_type="endgame_mate_in_one", source="synthetic_kqk_mate_in_one_v1",
            source_group=f"seed-v1-{split}", label_method="python-chess-exact-checkmate",
            target_rule="unique_legal_mate_in_one",
            explanation="Jedyny legalny ruch matujący w końcówce K+H kontra K.",
        ))
    return result


def _promotion_candidates(split: str, count: int, seen: set[str]) -> list[dict]:
    rng = random.Random(SPLIT_SEEDS[split] + 777)
    rows = []
    attempts = 0
    while len(rows) < count and attempts < 20000:
        attempts += 1
        pawn_file = rng.randrange(8)
        pawn_square = chess.square(pawn_file, 6)
        white_king = rng.randrange(64)
        black_king = rng.randrange(64)
        if len({pawn_square, white_king, black_king}) != 3:
            continue
        board = chess.Board.empty()
        board.set_piece_at(pawn_square, chess.Piece(chess.PAWN, chess.WHITE))
        board.set_piece_at(white_king, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(black_king, chess.Piece(chess.KING, chess.BLACK))
        board.turn = chess.WHITE
        if not board.is_valid() or board.is_check():
            continue
        fen = board.fen()
        if fen in seen:
            continue
        promotions = [m for m in board.legal_moves if m.from_square == pawn_square and m.promotion]
        queen_move = chess.Move(pawn_square, chess.square(pawn_file, 7), promotion=chess.QUEEN)
        if len(promotions) != 4 or queen_move not in promotions:
            continue
        after = board.copy()
        after.push(queen_move)
        if after.is_stalemate():
            continue
        seen.add(fen)
        rows.append(_row(
            board, queen_move, row_id=f"synthetic-promotion-{split}-{len(rows)+1:03d}",
            split=split, task_type="promotion_choice", source="synthetic_legal_promotion_positions_v1",
            source_group=f"promotion-{split}-{len(rows)+1:03d}",
            label_method="rule-based-piece-value-order", target_rule="highest_material_value_legal_promotion",
            explanation="Hetman ma największą wartość materialną spośród czterech legalnych promocji; nie jest to ocena całej pozycji silnikiem.",
        ))
    if len(rows) != count:
        raise ValueError(f"Could not generate {count} promotion rows for {split}; generated {len(rows)}")
    return rows


def _random_playout_candidates(split: str, quotas: dict[str, int], seen: set[str]) -> list[dict]:
    collected: dict[str, list[dict]] = defaultdict(list)
    rng_seed = SPLIT_SEEDS[split]
    for game_index in range(GAMES_PER_SPLIT):
        rng = random.Random(rng_seed + game_index)
        board = chess.Board()
        source_group = f"random-legal-game-{split}-{game_index+1:03d}"
        for ply in range(MAX_PLIES):
            if board.is_game_over():
                break
            legal = list(board.legal_moves)
            if board.is_check():
                if len(legal) == 1 and len(collected["unique_evasion"]) < quotas.get("unique_evasion", 0):
                    category, target = "unique_evasion", legal[0]
                    method, rule = "python-chess-unique-legal-evasion", "only_legal_evasion"
                else:
                    category = target = None
            else:
                checks = [move for move in legal if board.gives_check(move)]
                captures = [move for move in legal if board.is_capture(move)]
                castles = [move for move in legal if board.is_castling(move)]
                if len(castles) == 1 and len(collected["unique_castle"]) < quotas.get("unique_castle", 0):
                    category, target = "unique_castle", castles[0]
                    method, rule = "python-chess-unique-legal-castle", "only_legal_castling_move"
                elif len(captures) == 1 and len(collected["unique_capture"]) < quotas.get("unique_capture", 0):
                    category, target = "unique_capture", captures[0]
                    method, rule = "python-chess-unique-legal-capture", "only_legal_capture"
                elif len(checks) == 1 and len(collected["unique_check"]) < quotas.get("unique_check", 0):
                    category, target = "unique_check", checks[0]
                    method, rule = "python-chess-unique-checking-move", "only_legal_checking_move"
                else:
                    category = target = None
            if category and target:
                fen = board.fen()
                if fen not in seen:
                    seen.add(fen)
                    collected[category].append(_row(
                        board, target,
                        row_id=f"synthetic-{category}-{split}-{len(collected[category])+1:03d}",
                        split=split, task_type=category, source="synthetic_deterministic_random_legal_playouts_v1",
                        source_group=source_group, label_method=method, target_rule=rule,
                        explanation="Jedyny legalny ruch spełniający jawny warunek zadania; nie jest to twierdzenie o globalnie najlepszym ruchu silnikowym.",
                    ))
            if all(len(collected[key]) >= amount for key, amount in quotas.items()):
                break
            board.push(rng.choice(legal))
        if all(len(collected[key]) >= amount for key, amount in quotas.items()):
            break
    missing = {key: amount - len(collected[key]) for key, amount in quotas.items() if len(collected[key]) < amount}
    if missing:
        raise ValueError(f"Insufficient deterministic candidates for {split}: {missing}")
    return [row for task in sorted(quotas) for row in collected[task][:quotas[task]]]


def build_dataset() -> tuple[list[dict], dict]:
    seen: set[str] = set()
    seed_rows = _seed_endgames(seen)
    rows_by_split = {split: list(seed_rows[split]) for split in RANDOM_QUOTAS}
    for split, quotas in RANDOM_QUOTAS.items():
        if quotas.get("promotion_choice", 0):
            rows_by_split[split].extend(_promotion_candidates(split, quotas["promotion_choice"], seen))
        playout_quotas = {key: value for key, value in quotas.items() if key != "promotion_choice"}
        rows_by_split[split].extend(_random_playout_candidates(split, playout_quotas, seen))
    rows = []
    for split in ("train", "validation", "test"):
        rows.extend(rows_by_split[split])
    counts = Counter(row["split"] for row in rows)
    task_counts = Counter(row["task_type"] for row in rows)
    raw = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")
    manifest = {
        "schema_version": 2,
        "dataset_id": "smollm2-360m-chess-synthetic-v2",
        "dataset_path": str(OUTPUT.relative_to(ROOT)),
        "generator": "scripts/build_smol_chess_dataset_v2.py",
        "generator_version": 2,
        "source": "self-authored CC0 synthetic legal positions and deterministic random legal playouts",
        "license": LICENSE,
        "external_sources": [],
        "model_target": "HuggingFaceTB/SmolLM2-360M-Instruct",
        "purpose": "small experimental SFT dataset; rule-verified task targets, not engine-optimal chess evaluation",
        "rows": len(rows),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "split_counts": dict(counts),
        "task_counts": dict(task_counts),
        "split_policy": "independent deterministic source-game seeds per split; exact FEN deduplication across all splits",
        "label_policy": "best_move is the unique legal move satisfying task-specific rule; no general engine-optimality claim",
        "validator": f"python-chess {chess.__version__}",
        "engine_reference": None,
        "network_access": False,
        "model_weights_downloaded": False,
        "training_or_inference_run": False,
    }
    return rows, manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=MANIFEST_OUTPUT)
    args = parser.parse_args()
    rows, manifest = build_dataset()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
    manifest["dataset_path"] = str(args.output.relative_to(ROOT)) if args.output.is_relative_to(ROOT) else str(args.output)
    manifest["sha256"] = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(rows), "sha256": manifest["sha256"], "splits": manifest["split_counts"], "tasks": manifest["task_counts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
