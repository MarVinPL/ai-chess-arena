import chess
import pytest
from engine_runner import SequentialChessRunner, RunnerError


class NoLocalRuntime:
    def generate(self, *args, **kwargs):
        raise AssertionError('Local inference must not run when provider is set')


def test_runner_can_play_full_game_with_injected_coordinator_provider():
    moves = iter(['f2f3', 'e7e5', 'g2g4', 'd8h4'])
    seen = []

    def provider(board, player, stop, kwargs):
        uci = next(moves)
        seen.append((board.fen(), player['name'], uci))
        return chess.Move.from_uci(uci), {'seconds': 0.1}

    runner = SequentialChessRunner(NoLocalRuntime(), move_provider=provider)
    result = runner.play_game({'name': 'White'}, {'name': 'Black'})
    assert result['status'] == 'finished'
    assert result['result'] == '0-1'
    assert [m['uci'] for m in result['moves']] == ['f2f3', 'e7e5', 'g2g4', 'd8h4']
    assert len(seen) == 4


def test_runner_rejects_illegal_injected_move():
    runner = SequentialChessRunner(NoLocalRuntime(), move_provider=lambda board, player, stop, kwargs: (chess.Move.from_uci('e2e5'), {'seconds': 0.1}))
    with pytest.raises(RunnerError, match='illegal chess move'):
        runner.play_game({'name': 'White'}, {'name': 'Black'})
