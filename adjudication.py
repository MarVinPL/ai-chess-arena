"""Conservative, fail-closed adjudication of interrupted chess games.

Only a pre-existing local UCI engine explicitly configured by the operator may
adjudicate. This module never downloads engines or models.
"""
from __future__ import annotations

import os
from pathlib import Path

import chess
import chess.engine

MIN_PLIES = 40
MIN_SCORE_CP = 800
ANALYSIS_DEPTHS = (12, 16)
ENGINE_TIMEOUT_SECONDS = 20


def _unresolved(reason: str, **extra) -> dict:
    return {"status": "unresolved", "reason": reason, **extra}


def adjudicate_failure(fen: str, move_records: list[dict], failed_color: str,
                       uci_attempts: list[dict] | None = None,
                       engine_path: str | None = None,
                       engine_factory=None) -> dict:
    """Return an auditable decision. Any uncertainty leaves the game unresolved."""
    attempts = uci_attempts or []
    if any(item.get("classification") in ("illegal_uci", "blocked_reversal")
           for item in attempts if isinstance(item, dict)):
        return _unresolved("illegal_or_forbidden_move_attempt")
    if failed_color not in ("white", "black"):
        return _unresolved("unknown_failed_side")
    if not isinstance(move_records, list) or len(move_records) < MIN_PLIES:
        return _unresolved("insufficient_game_length", plies=len(move_records or []),
                           min_plies=MIN_PLIES)
    try:
        board = chess.Board()
        for record in move_records:
            if not isinstance(record, dict):
                return _unresolved("invalid_move_record")
            move = chess.Move.from_uci(str(record.get("uci", "")))
            if move not in board.legal_moves:
                return _unresolved("illegal_or_inconsistent_move_history")
            board.push(move)
        supplied = chess.Board(fen)
        if supplied.fen() != board.fen():
            return _unresolved("fen_history_mismatch")
    except (ValueError, TypeError, AttributeError):
        return _unresolved("invalid_fen_or_move_history")

    if board.is_game_over(claim_draw=True):
        return _unresolved("position_already_terminal")
    side_to_move = "white" if board.turn == chess.WHITE else "black"
    if side_to_move != failed_color:
        return _unresolved("failed_side_does_not_match_side_to_move")
    winner_color = chess.BLACK if failed_color == "white" else chess.WHITE
    winner_name = "black" if winner_color == chess.BLACK else "white"

    configured = engine_path or os.environ.get("ARENA_ANALYSIS_ENGINE")
    if not configured:
        return _unresolved("analysis_engine_not_configured")
    path = Path(configured).expanduser()
    if not path.is_file() or not os.access(path, os.X_OK):
        return _unresolved("analysis_engine_unavailable", engine_path=str(path))

    factory = engine_factory or chess.engine.SimpleEngine.popen_uci
    try:
        with factory(str(path), timeout=ENGINE_TIMEOUT_SECONDS) as engine:
            try:
                engine.configure({"Threads": 1})
            except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
                return _unresolved("analysis_engine_configuration_failed")
            engine_name = (getattr(engine, "id", {}) or {}).get("name", path.name)
            analyses = []
            for depth in ANALYSIS_DEPTHS:
                info = engine.analyse(board, chess.engine.Limit(depth=depth))
                score = info.get("score")
                pv = info.get("pv") or []
                if score is None or not pv:
                    return _unresolved("incomplete_engine_analysis", engine=engine_name,
                                       depth=depth)
                cp = score.pov(winner_color).score(mate_score=100000)
                if cp is None:
                    return _unresolved("unscorable_engine_analysis", engine=engine_name,
                                       depth=depth)
                analyses.append({"depth": depth, "score_cp": int(cp),
                                 "bestmove": pv[0].uci()})
    except Exception as exc:
        return _unresolved("analysis_engine_error", engine_path=str(path),
                           error=f"{type(exc).__name__}: {exc}"[:500])

    if len({item["bestmove"] for item in analyses}) != 1:
        return _unresolved("unstable_best_move", engine=engine_name, analyses=analyses)
    if not all(item["score_cp"] >= MIN_SCORE_CP for item in analyses):
        return _unresolved("advantage_below_threshold", engine=engine_name,
                           threshold_cp=MIN_SCORE_CP, analyses=analyses)
    return {
        "status": "adjudicated",
        "winner_color": winner_name,
        "engine": engine_name,
        "engine_path": str(path),
        "threshold_cp": MIN_SCORE_CP,
        "analyses": analyses,
        "reason": "stable_decisive_advantage_for_opponent_of_failed_side",
    }
