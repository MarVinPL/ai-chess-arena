"""Regression guard: production server remains local-only and single writer."""
import ast
from pathlib import Path


def test_production_server_uses_explicit_local_only_selection():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'server.py').read_text())
    selections = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == 'select_game_runner']
    assert len(selections) == 1
    assert len(selections[0].args) == 1
    assert isinstance(selections[0].args[0], ast.Constant)
    assert selections[0].args[0].value == 'local'
    assert not any(k.arg in ('remote_enabled', 'remote_factory') for k in selections[0].keywords)
