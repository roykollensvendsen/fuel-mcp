# SPDX-License-Identifier: GPL-3.0-or-later
"""A test that waits on another process fails when its own time is up, and never hangs.

No limit is put on a test or a run from outside. Each wait inside a test carries its own bound,
so a child that never answers fails that one test instead of holding a mutation run for hours.
"""

import os
import re
import subprocess
import sys
import time

import pytest
from bounded import line_within, run_bounded


def test_a_child_that_never_answers_fails_the_test_instead_of_hanging():
    with subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.PIPE, text=True
    ) as child:
        started = time.monotonic()
        try:
            with pytest.raises(pytest.fail.Exception, match="no line within 1 s"):
                line_within(child.stdout, 1)
        finally:
            child.kill()
    assert time.monotonic() - started < 30


def test_a_line_that_comes_in_time_is_returned_whole():
    with subprocess.Popen(
        [sys.executable, "-c", "print('ready', flush=True)"], stdout=subprocess.PIPE, text=True
    ) as child:
        assert line_within(child.stdout, 30) == "ready\n"


def _group_gone(pgid, seconds=30):
    """Whether no process of group `pgid` is left; a killed one may take a moment to be reaped."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_a_child_still_going_at_its_bound_is_killed_with_its_whole_group():
    """A grandchild holding the output would otherwise live on into the next tests."""
    started = time.monotonic()
    with pytest.raises(pytest.fail.Exception, match="still going after 1 s") as failed:
        run_bounded(["sh", "-c", "echo $$; sleep 60 & wait"], 1, capture_output=True, text=True)
    assert time.monotonic() - started < 30
    pgid = int(re.search(r"said: '(\d+)", str(failed.value)).group(1))
    assert _group_gone(pgid), "a process of the child's group is still running"


def test_a_child_that_ends_in_time_is_answered_as_subprocess_run_would():
    done = run_bounded([sys.executable, "-c", "print(input())"], input="hi\n", capture_output=True, text=True)
    assert (done.returncode, done.stdout) == (0, "hi\n")
    with pytest.raises(subprocess.CalledProcessError):
        run_bounded([sys.executable, "-c", "raise SystemExit(3)"], check=True)
