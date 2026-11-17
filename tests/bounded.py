# SPDX-License-Identifier: GPL-3.0-or-later
"""Waits on another process that end in a failure of the waiting test, never in a hang.

No limit is put on a test or a run from outside. Each wait carries its own bound, and
running out fails that one test, so a mutant that hangs counts as killed.
"""

import contextlib
import os
import queue
import signal
import subprocess
import threading

import pytest

#: The one bound for every wait in the tests: long enough that it fires only on a real hang.
WITHIN = 300


def run_bounded(cmd, within=WITHIN, *, check=False, capture_output=False, input=None, **kw):  # noqa: A002
    """Run `cmd` as subprocess.run does; a failure, not a hang, if it is still going after `within`.

    The child starts in a session of its own. On time, the whole group is killed with SIGKILL,
    so a grandchild under a shell or `uv run` never lives on into the next tests.
    """
    if capture_output:
        kw["stdout"] = kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw["stdin"] = subprocess.PIPE
    with subprocess.Popen(cmd, start_new_session=True, **kw) as child:
        try:
            out, err = child.communicate(input, timeout=within)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):  # the whole group ended just now
                os.killpg(child.pid, signal.SIGKILL)
            out, err = child.communicate()
            pytest.fail(f"{cmd!r} still going after {within} s; it said: {out!r} {err!r}")
    done = subprocess.CompletedProcess(cmd, child.returncode, out, err)
    if check:
        done.check_returncode()
    return done


def line_within(stream, seconds):
    """The next line from `stream`; a failure, not a hang, if none comes within `seconds`.

    The read runs on a thread of its own, since a pipe's readline cannot be given a limit.
    A child that never answers is then the test's failure, which kills it as it unwinds.
    """
    got = queue.Queue()
    threading.Thread(target=lambda: got.put(stream.readline()), daemon=True).start()
    try:
        return got.get(timeout=seconds)
    except queue.Empty:
        pytest.fail(f"no line within {seconds} s")
