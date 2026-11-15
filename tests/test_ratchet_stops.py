# SPDX-License-Identifier: GPL-3.0-or-later
"""A stopped quality check lets the program it started clean up, and waits for it.

The ratchet starts the suite and the rule check as children. Stopped, it used to kill
its child outright, which left a source file switched off and the suite's files behind.
Each test here starts a real ratchet, with a real child, and stops it from outside.
"""

import os
import pathlib
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: A child that waits to be stopped, then cleans up slowly, counting every stop it gets.
CHILD = """
import os, pathlib, signal, sys, time
marks = pathlib.Path(sys.argv[1])
stops = []
signal.signal(signal.SIGINT, lambda signum, _frame: stops.append(signum))
(marks / "own-session").write_text(str(os.getsid(0) == os.getpid()))
(marks / "ready").write_text("")
if sys.argv[2] == "orphan":
    while not (marks / "go").exists():
        time.sleep(0.05)
    print("x" * 1000, flush=True)  # after the ratchet is gone
    (marks / "finished").write_text("")
    sys.exit(0)
deadline = time.monotonic() + 20
while not stops and time.monotonic() < deadline:
    time.sleep(0.05)
if stops:
    (marks / "cleaning").write_text("")
    time.sleep(2)  # a slow cleanup; any further stop is counted meanwhile
    (marks / "cleaned").write_text(str(len(stops)))
"""

#: A ratchet that measures in a copy of its own, through its own way of starting a child.
RATCHET = """
import importlib.util, os, sys, tempfile
spec = importlib.util.spec_from_file_location("ratchet", sys.argv[1])
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
ratchet.stop_cleanly()
with tempfile.TemporaryDirectory() as copy:
    open(os.path.join(copy, "levels.json"), "w").close()
    ratchet._run(sys.executable, "-c", sys.argv[2], sys.argv[3], sys.argv[4])
"""


def _ratchet(tmp_path, mode="stop", *, own_group=False):
    temp, marks = tmp_path / "temp", tmp_path / "marks"
    temp.mkdir()
    marks.mkdir()
    ratchet = subprocess.Popen(
        [sys.executable, "-c", RATCHET, str(ROOT / "scripts/ratchet.py"), CHILD, str(marks), mode],
        env={**os.environ, "TMPDIR": str(temp)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=own_group,
    )
    _until(marks / "ready", 30)
    assert (marks / "ready").exists(), "the child never started"
    return ratchet, temp, marks


def _until(path, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def _ended(ratchet):
    try:
        return ratchet.wait(timeout=60)
    finally:
        ratchet.kill()


def test_a_stopped_measurement_lets_its_child_clean_up(tmp_path):
    """Ctrl-C reaches the child once, through the ratchet, which waits for it to finish."""
    ratchet, temp, marks = _ratchet(tmp_path)
    ratchet.send_signal(signal.SIGINT)
    assert _ended(ratchet) in {128 + signal.SIGINT, -signal.SIGINT}
    assert (marks / "cleaned").read_text() == "1"
    assert list(temp.iterdir()) == []


def test_a_second_signal_cannot_cut_the_cleanup_short_of_a_measurement_in_a_whole_run(tmp_path):
    """GitHub cancels with SIGINT and then SIGTERM; the child's cleanup must still run to its end."""
    ratchet, temp, marks = _ratchet(tmp_path)
    ratchet.send_signal(signal.SIGINT)
    _until(marks / "cleaning", 30)
    assert (marks / "cleaning").exists(), "the child was never told to stop"
    ratchet.send_signal(signal.SIGTERM)
    _ended(ratchet)
    assert (marks / "cleaned").read_text() == "1"
    assert list(temp.iterdir()) == []


def test_a_terminal_s_ctrl_c_reaches_a_measurement_s_child_only_once(tmp_path):
    """A terminal sends Ctrl-C to its whole group; a second Ctrl-C would cut pytest's cleanup short.

    Two that arrive together are merged into one, so counting them cannot tell. A child in a
    session of its own is outside the terminal's group, so only the ratchet's Ctrl-C reaches it.
    """
    ratchet, _, marks = _ratchet(tmp_path, own_group=True)
    assert (marks / "own-session").read_text() == "True"
    os.killpg(ratchet.pid, signal.SIGINT)
    _ended(ratchet)
    assert (marks / "cleaned").read_text() == "1"


def test_a_measurement_s_child_writes_to_a_file_never_a_pipe(tmp_path):
    """After a kill -9 of the ratchet, a child writing to a pipe would die mid-mutation."""
    ratchet, _, marks = _ratchet(tmp_path, "orphan")
    ratchet.kill()
    ratchet.wait()
    (marks / "go").write_text("")
    _until(marks / "finished", 30)
    assert (marks / "finished").exists(), "the child died once the ratchet was gone"
