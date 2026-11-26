"""Guard against removing exclusive ownership from production tournament loop."""
import ast
from pathlib import Path


def test_production_tournament_loop_is_coordinator_locked():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    tournament = functions['tournament_loop']
    assert any(isinstance(d, ast.Name) and d.id == '_exclusive_tournament_coordinator'
               for d in tournament.decorator_list)
    scope = functions['_exclusive_tournament_coordinator']
    assert any(isinstance(n, ast.With) and any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == 'CoordinatorLock'
        for item in n.items) for n in ast.walk(scope))
