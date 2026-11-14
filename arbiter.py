"""Fair, advisory-only monitoring for repetitive chess play."""
from __future__ import annotations

import chess

MAX_INTERVENTIONS_PER_GAME = 3


def detect_stagnation(board: chess.Board, move_records: list[dict] | None = None) -> dict | None:
    """Detect a repeated three-move piece cycle by either side without deciding a result.

    Formal draws are deliberately left to python-chess. This heuristic catches
    a piece cycling through three squares while the opponent's moves vary.
    """
    records = list(move_records or [])
    for color in ('white', 'black'):
        own_moves = [r for r in records if r.get('color') == color]
        if len(own_moves) < 6:
            continue
        tail = [str(r.get('uci', '')) for r in own_moves[-6:]]
        if (all(len(move) >= 4 for move in tail)
                and tail[:3] == tail[3:]
                and tail[0][2:4] == tail[1][:2]
                and tail[1][2:4] == tail[2][:2]
                and tail[2][2:4] == tail[0][:2]):
            return {
                'type': 'piece_cycle',
                'reason': f'{color}: ta sama figura powtórzyła dwukrotnie cykl trzech pól.',
                'color': color,
                'cycle_uci': tail,
                'position_fen': board.fen(),
            }
    return None


def intervention_prompt(signal: dict) -> str:
    """Neutral wording: request independent reassessment without advice."""
    return (
        '\nUWAGA SĘDZIEGO (neutralna): ostatnie ruchy wskazują na możliwe '
        'powtarzanie schematu. Oceń pozycję od nowa, rozważ całą listę legalnych '
        'ruchów i wybierz samodzielnie najlepszy ruch. Nie zakładaj, że musisz '
        'zmienić ruch; nie otrzymujesz podpowiedzi strategicznej. Zwróć wyłącznie UCI.\n'
    )
