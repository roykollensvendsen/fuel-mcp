# SPDX-License-Identifier: GPL-3.0-or-later
"""A run leaves nothing in the shared temporary folder.

The temporary folder is shared by every project on the machine, and one that
only fills up stops every test run there with "Disk quota exceeded". So each
run here cleans up after itself: pytest removes its folders when the session
ends and sweeps those a killed run left, and the quality script's copies are
removed even when it is stopped.

Each test points TMPDIR at a folder of its own and looks inside it afterwards.
"""

import os
import pathlib
import re
import subprocess
import sys
import tempfile
import types

import pytest

ROOT = pathlib.Path(__file__).parent.parent


def _files_in(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if not p.is_dir())


def _pytest(temp, *tests):
    """Run some of this project's tests, with its own settings, using `temp` as the temporary folder."""
    env = {**os.environ, "TMPDIR": str(temp)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_test_run_leaves_nothing_in_the_temporary_folder(tmp_path):
    temp = tmp_path / "temp"
    temp.mkdir()
    done = _pytest(temp, "tests/test_speech.py")
    assert done.returncode == 0, done.stdout
    assert _files_in(temp) == []


def _run_folder(temp, digits="123456789012"):
    folder = temp / f"fuel-mcp-tests-{digits}"
    folder.mkdir(parents=True)
    (folder / "kept").write_text("a run's folder")
    return folder


def test_a_dead_runs_folder_is_swept_at_the_next_run(tmp_path):
    """A killed run never reaches its own cleanup; nothing holds its lock any more."""
    temp = tmp_path / "temp"
    _run_folder(temp)
    done = _pytest(temp, "tests/test_speech.py")
    assert done.returncode == 0, done.stdout
    assert _files_in(temp) == []


HOLDS_THE_LOCK = """
import fcntl, os, sys
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("holding", flush=True)
sys.stdin.read()
"""


def test_a_live_runs_folder_is_kept(tmp_path):
    """A run still going holds its folder's lock, whatever its process number looks like from here."""
    temp = tmp_path / "temp"
    live = _run_folder(temp)
    with subprocess.Popen(
        [sys.executable, "-c", HOLDS_THE_LOCK, str(live / ".lock")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout.readline().strip() == "holding"
        done = _pytest(temp, "tests/test_speech.py")
        holder.stdin.close()
    assert done.returncode == 0, done.stdout
    assert _files_in(temp) == [f"{live.name}/.lock", f"{live.name}/kept"]


def test_a_link_named_like_a_run_is_left_alone(tmp_path):
    """A link could point anywhere; the sweep never follows one."""
    temp, elsewhere = tmp_path / "temp", tmp_path / "elsewhere"
    temp.mkdir()
    elsewhere.mkdir()
    (elsewhere / "precious").write_text("not a run's")
    (temp / "fuel-mcp-tests-123456789012").symlink_to(elsewhere)
    done = _pytest(temp, "tests/test_speech.py")
    assert done.returncode == 0, done.stdout
    assert (temp / "fuel-mcp-tests-123456789012").is_symlink()
    assert [p.name for p in elsewhere.iterdir()] == ["precious"]  # not even a lock made there
    assert (elsewhere / "precious").read_text() == "not a run's"


def test_a_tests_temporary_files_go_in_the_runs_own_folder(tmp_path):
    """tmp_path, tempfile and every program a test starts all write inside the run's folder."""
    run = pathlib.Path(os.environ["TMPDIR"])
    assert re.fullmatch(r"fuel-mcp-tests-\d+", run.name)
    assert tmp_path.is_relative_to(run)
    assert pathlib.Path(tempfile.gettempdir()) == run
    child = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert pathlib.Path(child.stdout.strip()) == run


def test_the_temporary_folder_is_put_back_when_the_run_ends(tmp_path, monkeypatch):
    import conftest  # noqa: PLC0415 - the hooks under test

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", None)
    config = types.SimpleNamespace(option=types.SimpleNamespace(basetemp=None), stash=pytest.Stash())
    conftest.pytest_configure(config)
    assert os.environ["TMPDIR"] != str(tmp_path)
    conftest.pytest_unconfigure(config)
    assert os.environ["TMPDIR"] == str(tmp_path)
    assert tempfile.tempdir is None
    assert list(tmp_path.iterdir()) == []
