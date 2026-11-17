# SPDX-License-Identifier: GPL-3.0-or-later
"""Each cleanup step in tests/conftest.py is proven by turning it off.

The rule check never mutates tests/, since a test that judged its own mutated code would
prove nothing. So each step is turned off here, from outside: a scratch project gets a
copy of the conftest with that one step changed, and the test named for the step must
then fail. The same tests pass against the conftest as it is.
"""

import os
import pathlib
import shutil
import sys

import pytest
from bounded import run_bounded

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: The test files the scratch project runs, as they are here.
COPIED = ("bounded.py", "test_no_temp_left.py", "test_speech.py", "test_stop_handlers_kept.py")

#: Each step, the change that turns it off, and the test that must then fail.
SABOTAGE = {
    "1a, the lock is taken before the name": (
        [
            ("    fcntl.flock(lock, fcntl.LOCK_EX)\n    while True:", "    while True:"),
            (
                "        starting.rename(folder)\n",
                "        starting.rename(folder)\n        fcntl.flock(lock, fcntl.LOCK_EX)\n",
            ),
        ],
        "test_a_new_runs_folder_is_never_seen_unlocked_under_a_runs_name",
    ),
    "1a, the lock is always made new": (
        [("os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW", "os.O_CREAT | os.O_NOFOLLOW")],
        "test_a_new_run_never_takes_over_a_lock_already_there",
    ),
    "1, the locked folder is never pytest's basetemp": (
        [('config.option.basetemp = str(folder / "pytest")', "config.option.basetemp = str(folder)")],
        "test_a_running_test_run_s_folder_keeps_its_lock",
    ),
    "2, the sweep never creates a lock": (
        [
            (
                "OPEN_THEIR_LOCK = os.O_RDWR | os.O_NOFOLLOW",
                "OPEN_THEIR_LOCK = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW",
            )
        ],
        "test_a_runs_folder_not_yet_locked_is_left_alone_and_given_no_lock",
    ),
    "3a, a lockless folder waits out the grace period": (
        [("STARTING_SECONDS = 3600", "STARTING_SECONDS = 0")],
        "test_a_run_that_is_starting_now_is_left_alone",
    ),
    "3a, the first look's age is reused": (
        [
            (
                "time.time() - found.st_mtime >= STARTING_SECONDS",
                "time.time() - path.lstat().st_mtime >= STARTING_SECONDS",
            )
        ],
        "test_a_folder_another_sweep_removed_first_is_passed_over",
    ),
    "3a, only this user's folders are swept": (
        [("or found.st_uid != os.getuid():", "or False:")],
        "test_another_users_folder_is_never_swept",
    ),
    "3a, rights are given back before removal": (
        [
            ("            _open_up(pathlib.Path(place) / name)\n", "            pass\n"),
            ("shutil.rmtree(folder, onexc=_writable_again)", "shutil.rmtree(folder, ignore_errors=True)"),
        ],
        "test_a_dead_runs_folder_is_swept_even_where_a_test_shut_it",
    ),
    "7, a test's stop handlers are put back": (
        [("        signal.signal(each, handler)\n", "        pass\n")],
        "test_a_test_s_stop_handlers_are_put_back_after_it",
    ),
}


def _scratch(tmp_path, changes):
    """A project holding this one's settings and copied tests, under a conftest with `changes`."""
    project, temp = tmp_path / "project", tmp_path / "temp"
    (project / "tests").mkdir(parents=True)
    temp.mkdir()
    shutil.copy(ROOT / "pyproject.toml", project / "pyproject.toml")
    for name in COPIED:
        shutil.copy(ROOT / "tests" / name, project / "tests" / name)
    conftest = (ROOT / "tests/conftest.py").read_text()
    for old, new in changes:
        assert conftest.count(old) == 1, f"the step to turn off is no longer written as: {old!r}"
        conftest = conftest.replace(old, new)
    (project / "tests/conftest.py").write_text(conftest)
    return project, temp


def _run(project, temp, *tests):
    return run_bounded(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", *tests],
        cwd=project,
        env={**os.environ, "TMPDIR": str(temp)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("step", list(SABOTAGE))
def test_a_conftest_step_turned_off_fails_its_named_test(tmp_path, step):
    changes, named = SABOTAGE[step]
    project, temp = _scratch(tmp_path, changes)
    done = _run(project, temp, "tests", "-k", named)
    assert done.returncode == 1, f"{step}: {named} still passed with the step off\n{done.stdout[-1500:]}"
    assert f"::{named}" in done.stdout, done.stdout[-1500:]


def test_every_named_test_passes_with_the_conftest_as_it_is(tmp_path):
    """Without this, a sabotage run failing for some other reason would look like proof."""
    project, temp = _scratch(tmp_path, [])
    named = " or ".join(test for _, test in SABOTAGE.values())
    done = _run(project, temp, "tests", "-k", named)
    assert done.returncode == 0, done.stdout[-2000:]
    assert f"{len(SABOTAGE) + 1} passed" in done.stdout, done.stdout[-500:]
