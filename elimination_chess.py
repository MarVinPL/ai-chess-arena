"""Chess rules for the Arena's king-capture variant (Szachy Eliminacyjne)."""
from __future__ import annotations

import chess
import chess.variant


class EliminationBoard(chess.Board):
    """A chess board where capturing the opposing king immediately wins.

    Piece movement follows chess movement rules, but check/checkmate do not
    constrain play. A king may remain in check; the only decisive win is to
    capture the opposing king. Standard chess intentionally forbids king
    captures, so those moves are generated explicitly.
    """

    uci_variant = "chess-elimination"
    aliases = ["Elimination Chess", "Szachy Eliminacyjne"]

    def winning_king_capture_moves(self) -> list[chess.Move]:
        """Return legal moves that immediately capture the opposing king."""
        if self.is_variant_end():
            return []
        enemy_king = self.king(not self.turn)
        if enemy_king is None:
            return []
        return [move for move in self.legal_moves if move.to_square == enemy_king]

    def is_variant_end(self) -> bool:
        return self.king(chess.WHITE) is None or self.king(chess.BLACK) is None

    def generate_legal_moves(self, from_mask=chess.BB_ALL, to_mask=chess.BB_ALL):
        if self.is_variant_end():
            return
        # Use pseudo-legal piece movement: in this variant check and checkmate
        # do not restrict the side to move. This is essential so a checkmated
        # king does not freeze the game before it can be captured.
        base_moves = list(super().generate_pseudo_legal_moves(from_mask, to_mask))
        yield from base_moves
        base_move_set = set(base_moves)
        enemy_king = self.king(not self.turn)
        if enemy_king is None or self.king(self.turn) is None:
            return
        attackers = self.attackers_mask(self.turn, enemy_king) & from_mask
        for from_square in chess.scan_reversed(attackers):
            if not (chess.BB_SQUARES[enemy_king] & to_mask):
                continue
            piece = self.piece_at(from_square)
            if piece is None:
                continue
            promotions = (chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT) if (
                piece.piece_type == chess.PAWN and chess.square_rank(enemy_king) in (0, 7)
            ) else (None,)
            for promotion in promotions:
                move = chess.Move(from_square, enemy_king, promotion=promotion)
                if move in base_move_set:
                    continue
                yield move

    def is_legal(self, move: chess.Move) -> bool:
        # python-chess normally rejects moves that leave the moving side's king
        # attacked. That is intentionally not a rule in this variant.
        return move in self.generate_legal_moves()

    def is_pseudo_legal(self, move: chess.Move) -> bool:
        if super().is_pseudo_legal(move):
            return True
        if self.is_variant_end() or move.drop is not None:
            return False
        enemy_king = self.king(not self.turn)
        if enemy_king is None or move.to_square != enemy_king:
            return False
        if move.promotion not in (None, chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT):
            return False
        piece = self.piece_at(move.from_square)
        if piece is None or piece.color != self.turn:
            return False
        if not (self.attacks(move.from_square) & chess.BB_SQUARES[enemy_king]):
            return False
        if piece.piece_type == chess.PAWN:
            last_rank = chess.square_rank(enemy_king) in (0, 7)
            if last_rank != (move.promotion is not None):
                return False
        elif move.promotion is not None:
            return False
        return True

    def outcome(self, *, claim_draw: bool = False):
        if self.king(chess.WHITE) is None:
            return chess.Outcome(chess.Termination.VARIANT_LOSS, chess.BLACK)
        if self.king(chess.BLACK) is None:
            return chess.Outcome(chess.Termination.VARIANT_LOSS, chess.WHITE)
        outcome = super().outcome(claim_draw=claim_draw)
        # Checkmate is not a win in Elimination Chess. A side that has no
        # check-evasion can still move any piece-legal move and try to capture
        # the opposing king. Other standard draw conditions remain available.
        if outcome and outcome.termination in (
                chess.Termination.CHECKMATE,
                chess.Termination.INSUFFICIENT_MATERIAL):
            return None
        return outcome

    def result(self, *, claim_draw: bool = False) -> str:
        outcome = self.outcome(claim_draw=claim_draw)
        return outcome.result() if outcome else "*"


# Register the custom board so python-chess PGN readers can reconstruct games.
if EliminationBoard not in chess.variant.VARIANTS:
    chess.variant.VARIANTS.append(EliminationBoard)


def board_for_variant(fen, variant='standard', *, active=True):
    """Reconstruct a position using its explicit, immutable ruleset."""
    if variant == 'standard':
        board = chess.Board(fen)
        valid = board.is_valid()
    elif variant == 'elimination':
        board = EliminationBoard(fen)
        # Opposite-side check and multiple checkers are allowed in this variant.
        allowed = chess.STATUS_OPPOSITE_CHECK | chess.STATUS_TOO_MANY_CHECKERS | chess.STATUS_IMPOSSIBLE_CHECK
        valid = not (board.status() & ~allowed)
    else:
        raise ValueError('Unknown chess variant')
    if not valid or (active and board.is_variant_end()):
        raise ValueError('Invalid board state')
    return board


def checkpoint_board(checkpoint):
    return board_for_variant(checkpoint['fen'], checkpoint.get('variant', 'standard'))
