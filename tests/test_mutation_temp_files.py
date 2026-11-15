# SPDX-License-Identifier: GPL-3.0-or-later
"""A mutation run, and the test suite itself, leave no temporary files behind.

`scripts/mutate.py` runs the whole suite once per rule, and every run made its
own pytest folder of temporary files. Over a day of runs those folders filled
the machine's temporary space until no program could start a command. Each run
now gets a folder of its own, outside the checkout, removed when the run ends;
whatever a killed run left is swept away at the next start.
"""

import importlib.util
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).parent.parent

LEAVES_FILES = """
import os

def test_it_writes_files_it_cannot_delete(tmp_path):
    (tmp_path / "plain.txt").write_text("x")
    (tmp_path / "locked.txt").write_text("x")
    os.chmod(tmp_path / "locked.txt", 0)
    (tmp_path / "shut").mkdir()
    (tmp_path / "shut" / "inside.txt").write_text("x")
    os.chmod(tmp_path / "shut", 0)
"""


def _mutate(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("mutate_temp", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_leaves_files.py").write_text(LEAVES_FILES)
    monkeypatch.setattr(module, "ROOT", project)
    monkeypatch.setattr(module, "MUTATE_TMP", tmp_path / "mutate-tmp")
    return module


def test_a_suite_run_leaves_no_temporary_files_behind(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert list(mutate.MUTATE_TMP.iterdir()) == []


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows needs extra rights to make a link, and has no 0755"
)
def test_removing_a_run_s_files_never_changes_a_folder_it_links_to(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(0o755)
    (outside / "kept.txt").write_text("x")
    (mutate.ROOT / "tests" / "test_links_out.py").write_text(
        f"def test_it_links_out(tmp_path):\n    (tmp_path / 'link').symlink_to({str(outside)!r})\n"
    )
    failed, _ = mutate.run_suite()
    assert failed == 0
    assert (outside.stat().st_mode & 0o777, (outside / "kept.txt").exists()) == (0o755, True)


def test_a_killed_run_s_temporary_files_are_swept_at_the_next_start(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    left = mutate.MUTATE_TMP / "run-left-by-a-killed-run"
    (left / "shut").mkdir(parents=True)
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)
    mutate.sweep()
    assert not left.exists()


def test_the_temporary_files_live_outside_the_checkout():
    """Inside it, a test that makes a git repository of its own would find this one instead."""
    spec = importlib.util.spec_from_file_location("mutate_where", ROOT / "scripts/mutate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert ROOT.resolve() not in module.MUTATE_TMP.resolve().parents


def test_a_passing_test_keeps_no_temporary_files():
    settings = (ROOT / "pyproject.toml").read_text()
    assert 'tmp_path_retention_policy = "failed"' in settings
    assert "tmp_path_retention_count = 1" in settings


def _whole_run(tmp_path, monkeypatch):
    """The runner set up for a whole run: one row on a one-line file of the tiny project."""
    mutate = _mutate(tmp_path, monkeypatch)
    (mutate.ROOT / "rule.py").write_text("x = 1\n")
    rows = tmp_path / "mutations"
    (rows / "rule.py").mkdir(parents=True)
    (rows / "rule.py" / "a.toml").write_text(
        "[[rule]]\nname = \"a\"\nfile = \"rule.py\"\nfind = '^x = 1$'\nreplace = 'x = 2'\n"
    )
    monkeypatch.setattr(mutate, "ROWS", rows)
    monkeypatch.setattr(mutate, "RECOVERY", tmp_path / "recovery.json")
    monkeypatch.setattr(mutate.sys, "argv", ["mutate.py"])
    return mutate


def test_a_finished_run_leaves_no_folder_of_its_own_behind(tmp_path, monkeypatch):
    """Each run's files went, but the folder holding them stayed in /tmp for good."""
    mutate = _whole_run(tmp_path, monkeypatch)
    assert mutate.all_rows(), "the run must have a row, or it checks nothing"
    mutate.main()
    assert not mutate.MUTATE_TMP.exists()


def test_an_interrupted_run_leaves_no_folder_of_its_own_behind(tmp_path, monkeypatch):
    mutate = _whole_run(tmp_path, monkeypatch)

    def interrupted():
        mutate.MUTATE_TMP.mkdir(parents=True, exist_ok=True)
        raise KeyboardInterrupt

    monkeypatch.setattr(mutate, "run_suite", interrupted)
    with pytest.raises(KeyboardInterrupt):
        mutate.main()
    assert not mutate.MUTATE_TMP.exists()
    assert (mutate.ROOT / "rule.py").read_text() == "x = 1\n"


STOPPED_RUN = """
import importlib.util, pathlib, sys, time
root, tmp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("mutate_stopped", root / "scripts/mutate.py")
mutate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mutate)
mutate.ROOT, mutate.ROWS = tmp / "project", tmp / "mutations"
mutate.RECOVERY, mutate.MUTATE_TMP = tmp / "recovery.json", tmp / "mutate-tmp"
sys.argv = ["mutate.py"]
assert [r["file"] for r in mutate.all_rows()] == ["rule.py"], "would mutate the real tree"

def held():
    mutate.MUTATE_TMP.mkdir(parents=True, exist_ok=True)
    (tmp / "ready").write_text("")
    time.sleep(60)

mutate.run_suite = held
sys.exit(mutate.main())
"""


def _until(path, child, deadline):
    while not path.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.mark.parametrize(
    "stop", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP], ids=["SIGINT", "SIGTERM", "SIGHUP"]
)
def test_a_stopped_run_puts_the_tree_back_in_a_whole_run(tmp_path, monkeypatch, stop):
    """A cancelled CI job, a closed terminal or Ctrl-C stops the run, and `finally` must still run.

    Each ends it with 128 plus the signal's number, so Ctrl-C is exit 130, not an interrupt.
    """
    _whole_run(tmp_path, monkeypatch)
    child = subprocess.Popen(
        [sys.executable, "-c", STOPPED_RUN, str(ROOT), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _until(tmp_path / "ready", child, time.monotonic() + 30)
    assert (tmp_path / "ready").exists(), (
        child.communicate()[0] if child.poll() is not None else "never ready"
    )
    assert (tmp_path / "project/rule.py").read_text() == "x = 2\n", "the rule was switched off"
    child.send_signal(stop)
    out, _ = child.communicate(timeout=30)
    assert child.returncode == 128 + stop, out
    assert (tmp_path / "project/rule.py").read_text() == "x = 1\n"
    assert not (tmp_path / "recovery.json").exists()
    assert not (tmp_path / "mutate-tmp").exists()


SLOW_CLEANUP = """
restore = mutate.restore

def slow_restore(*args):
    (tmp / "cleaning").write_text("")
    time.sleep(2)
    return restore(*args)

mutate.restore = slow_restore
"""


def test_a_second_signal_cannot_cut_the_cleanup_short_of_a_whole_run(tmp_path, monkeypatch):
    """GitHub cancels with SIGINT, then SIGTERM: the second must not cut the first one's cleanup short."""
    _whole_run(tmp_path, monkeypatch)
    script = STOPPED_RUN.replace("mutate.run_suite = held\n", "mutate.run_suite = held\n" + SLOW_CLEANUP)
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(ROOT), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    _until(tmp_path / "ready", child, deadline)
    child.send_signal(signal.SIGINT)
    _until(tmp_path / "cleaning", child, deadline)
    assert (tmp_path / "cleaning").exists(), child.communicate()[0]
    child.send_signal(signal.SIGTERM)
    out, _ = child.communicate(timeout=30)
    assert (tmp_path / "project/rule.py").read_text() == "x = 1\n", out
    assert not (tmp_path / "recovery.json").exists()
    assert not (tmp_path / "mutate-tmp").exists()


def _refused(step):
    with pytest.raises(SystemExit) as stopped:
        step()
    return str(stopped.value.code)


def test_a_run_trusts_its_folder_only_when_it_is_really_ours(tmp_path, monkeypatch):
    """Its name is easy to guess in a shared /tmp, so a folder planted there first is never used."""
    mutate = _mutate(tmp_path, monkeypatch)
    folder = mutate.MUTATE_TMP
    folder.mkdir(mode=0o700)
    folder.chmod(0o755)  # others may enter
    for step in (mutate.sweep, mutate.run_suite):
        said = _refused(step)
        assert str(folder) in said
        assert f"rm -r {folder}" in said
    folder.chmod(0o700)
    mine = os.getuid()
    monkeypatch.setattr(mutate.os, "getuid", lambda: mine + 1)  # someone else's
    assert str(folder) in _refused(mutate.sweep)


def test_a_file_planted_at_the_run_s_folder_stops_the_run(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    mutate.MUTATE_TMP.write_text("not a folder")
    assert str(mutate.MUTATE_TMP) in _refused(mutate.sweep)
    assert mutate.MUTATE_TMP.read_text() == "not a folder"


def test_a_link_at_the_run_s_folder_is_never_followed(tmp_path, monkeypatch):
    mutate = _mutate(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    (elsewhere / "kept").write_text("x")
    mutate.MUTATE_TMP.symlink_to(elsewhere)
    assert str(mutate.MUTATE_TMP) in _refused(mutate.sweep)
    assert (elsewhere / "kept").exists()


HOLDS_THE_CHECKOUT = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_EX)
print("holding", flush=True)
sys.stdin.read()
"""


def test_one_mutation_run_per_checkout(tmp_path, monkeypatch):
    """Two runs of one checkout would mutate the same files, and sweep each other's folders."""
    mutate = _whole_run(tmp_path, monkeypatch)
    with subprocess.Popen(
        [sys.executable, "-c", HOLDS_THE_CHECKOUT, str(mutate.ROOT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as other:
        assert other.stdout.readline().strip() == "holding"
        said = _refused(mutate.main)
        other.stdin.close()
    assert "already running" in said
    assert (mutate.ROOT / "rule.py").read_text() == "x = 1\n"
    assert not mutate.RECOVERY.exists()


def test_a_repair_only_run_puts_back_a_killed_run_s_file_and_does_nothing_else(tmp_path, monkeypatch):
    mutate = _whole_run(tmp_path, monkeypatch)
    (mutate.ROOT / "rule.py").write_text("x = 2\n")  # a killed run left it mutated
    mutate.RECOVERY.write_text(json.dumps({"file": "rule.py", "original": "x = 1\n", "mutated": "x = 2\n"}))
    left = mutate.MUTATE_TMP / "run-left"
    left.mkdir(parents=True)

    def no_suite():
        msg = "a repair-only run ran the suite"
        raise AssertionError(msg)

    monkeypatch.setattr(mutate, "run_suite", no_suite)
    monkeypatch.setattr(mutate.sys, "argv", ["mutate.py", "--repair-only"])
    assert mutate.main() == 0
    assert (mutate.ROOT / "rule.py").read_text() == "x = 1\n"
    assert not mutate.RECOVERY.exists()
    assert left.exists(), "nothing else: not even the sweep"
