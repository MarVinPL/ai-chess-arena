"""Prevent process-lifetime and tournament locks from deadlocking each other."""
import ast
from pathlib import Path


def test_tournament_lock_reuses_process_lifetime_owner():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
    scope = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name == '_exclusive_tournament_coordinator')
    nested = next(n for n in ast.walk(scope) if isinstance(n, ast.FunctionDef)
                  and n.name == 'locked_run')
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == 'get' and n.args and isinstance(n.args[0], ast.Constant)
               and n.args[0].value == '_coordinator_lock' for n in ast.walk(nested))
    assert any(isinstance(n, ast.With) for n in ast.walk(nested))
    assert any(isinstance(n, ast.Raise) for n in ast.walk(nested))
