# SPDX-License-Identifier: GPL-3.0-or-later
"""The mutation runner's own rules, each with a test named after it.

`scripts/mutate.py` proves every other rule, so its own rules are proven the same
way: `scripts/mutations/scripts/mutate.py/` switches each off in turn, and the test
here that names it goes red. The runner has already loaded its code when it switches
a line off, and the suite it starts loads the switched-off file, so it can prove itself.

None of these runs a real suite: the suite is a stand-in that records what it was
asked to do, so a test here never mutates anything.
"""

import importlib.util
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent


@pytest.fixture
def mutate(tmp_path, monkeypatch):
    """The runner, loaded by path, with its temporary folder under `tmp_path`."""
    spec = importlib.util.spec_from_file_location("mutate_runner", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def _suite_says(monkeypatch, mutate, stdout, returncode=1):
    """Stand in for the suite: it answers `stdout`, and the arguments it got are kept."""
    asked = {}

    def run(args, **kwargs):
        asked.update(args=args, **kwargs)
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(mutate.subprocess, "run", run)
    return asked


def test_a_file_changed_during_the_run_is_never_restored_over(tmp_path, mutate):
    """Someone saved the file while the suite ran: putting the original back would lose their edit."""
    source = tmp_path / "service.py"
    source.write_text("edited while the suite ran\n")
    assert mutate.restore(source, "original\n", "mutated\n") is False
    assert source.read_text() == "edited while the suite ran\n"


def test_two_mutations_never_share_a_bytecode_cache(monkeypatch, mutate):
    """Two equal-size mutations in one second would otherwise import the last one's bytecode."""
    # Unset here, so the runner has to set it itself: a mutation run sets it for the suite it starts.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    asked = _suite_says(monkeypatch, mutate, "1 passed\n", returncode=0)
    mutate.run_suite()
    assert asked["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_a_coloured_failure_line_is_still_attributed(monkeypatch, mutate):
    """With colour on, FAILED is wrapped in codes, and a kill must still name its test."""
    coloured = (
        "\x1b[31mFAILED\x1b[0m tests/test_service.py::test_a_token_works_once - assert\n"
        "\x1b[31m1 failed\x1b[0m\n"
    )
    _suite_says(monkeypatch, mutate, coloured)
    assert mutate.run_suite() == (1, ["test_a_token_works_once"])


def test_an_empty_rule_table_is_not_a_crash(tmp_path, monkeypatch, mutate, capsys):
    """A new repository has no rules yet; the run says what to add and passes."""
    # main() first repairs what a killed run left. Inside a mutation run the real record
    # names the file switched off now, so it would be put back mid-run: use one of its own.
    monkeypatch.setattr(mutate, "RECOVERY", tmp_path / "recovery.json")
    monkeypatch.setattr(mutate, "all_rows", lambda _folder=None: [])
    monkeypatch.setattr(mutate.sys, "argv", ["mutate.py"])
    assert mutate.main() == 0
    assert "add one file per rule" in capsys.readouterr().out


#: The function a killed run repairs with. A rule there would be switched off while it repairs.
UNPROVABLE = "repair"

REPAIR_WITH_A_RULE = '''
def repair():
    """Put back a file a killed run left mutated."""
    # RULE: a repaired file is the original
    if kept:
        write(kept)


def sweep():
    # RULE: a swept folder is gone
    remove()
'''


def _inside_repair(text, rows, mutate):
    """The rules marked, or switched off by a row, inside `repair()` in `text`."""
    import ast  # noqa: PLC0415

    body = next(f for f in ast.parse(text).body if isinstance(f, ast.FunctionDef) and f.name == UNPROVABLE)
    lines = text.splitlines()
    inside = range(body.lineno - 1, body.end_lineno)
    marked = [lines[i].split("# RULE:")[1].strip() for i in inside if "# RULE:" in lines[i]]
    rowed = [
        r["name"] for r in rows if mutate.line_of(text, r["find"], int(r.get("occurrence", 1))) in inside
    ]
    return sorted(set(marked + rowed))


def test_a_rule_inside_repair_is_found(mutate):
    """The guard below is only worth something if it sees a rule where one has been put."""
    rows = [
        {"name": "a swept folder is gone", "find": r"^\s*remove\(\)$"},
        {"name": "kept", "find": r"if kept:"},
    ]
    assert _inside_repair(REPAIR_WITH_A_RULE, rows, mutate) == ["a repaired file is the original", "kept"]


def test_no_rule_lives_inside_repair(mutate):
    """A killed run repairs with the code it left switched off, so a rule in repair() cannot be proven."""
    text = (ROOT / "scripts/mutate.py").read_text(encoding="utf-8")
    rows = [r for r in mutate.all_rows() if r["file"] == "scripts/mutate.py"]
    assert rows, "the runner's own rows are missing"
    assert _inside_repair(text, rows, mutate) == []
