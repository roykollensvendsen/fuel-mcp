# SPDX-License-Identifier: GPL-3.0-or-later
"""A test that installs stop handlers leaves none behind for the next test.

The first test installs a handler, as a test of a script's `main()` does. The second,
run after it, finds the handler the test run started with. Proven by
`tests/test_conftest_proven.py`, which turns the conftest's restoring off.
"""

import signal

BEFORE = signal.getsignal(signal.SIGHUP)


def _installed(_signum, _frame):
    """A stop handler a test left behind."""


def test_a_test_s_stop_handlers_are_put_back_after_it():
    signal.signal(signal.SIGHUP, _installed)
    assert signal.getsignal(signal.SIGHUP) is _installed


def test_a_test_s_stop_handlers_are_put_back_after_it_for_the_next_test():
    assert signal.getsignal(signal.SIGHUP) is BEFORE
