# SPDX-License-Identifier: GPL-3.0-or-later
"""No test switches off the conftest's guards before it ends.

`monkeypatch.undo()` in the middle of a test undoes every patch made with that fixture,
the autouse network guard's among them. The rest of that test could then reach the
internet unseen. A patch meant to last part of a test goes in `monkeypatch.context()`.
"""

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parent


def _undo_calls(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.name}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "undo"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "monkeypatch"
    ]


def test_no_test_undoes_the_network_guard_part_way():
    found = [call for path in sorted(TESTS.glob("*.py")) for call in _undo_calls(path)]
    assert found == [], f"use `with monkeypatch.context() as patch:` instead of monkeypatch.undo() at {found}"
