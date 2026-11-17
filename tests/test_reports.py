# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner reports a pump price by voice: saved here at once, sent elsewhere only on his yes.

Every station here is made up, at made-up points near 0,0 far out at sea.
"""

import datetime as dt
import threading
import time

import pytest
from bounded import WITHIN

from fuel_mcp.model import Fuel, OwnReport, SinkRefusedError, StationPrice
from fuel_mcp.reports import TOKEN_FOR, UNDO_WITHIN, OwnReports, ReportChoices, Reporter
from fuel_mcp.service import FuelService
from fuel_mcp.simulator import FakeSource
from fuel_mcp.sinks import Sinks
from fuel_mcp.sinks.local import LocalSink

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)
KEEP = dt.timedelta(hours=12)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def pass_(self, **delta):
        self.now += dt.timedelta(**delta)


#: The longest a silent sender in these tests stays silent, in seconds: far past any deadline set here.
SILENT_S = 5


class Sender:
    """A made-up private sender that keeps what it is given, or refuses, or is slow."""

    needs_yes = True

    def __init__(self, label, *, refuse=False, slow_s=0.0, held=None):
        self.label = label
        self.taken: list[OwnReport] = []
        self._refuse, self._slow_s, self._held = refuse, slow_s, held

    def take(self, report):
        if self._slow_s:
            time.sleep(self._slow_s)
        if self._held is not None:
            # Until the test lets go: a sender that hasn't answered yet. It answers after
            # SILENT_S at the latest, so a lost deadline fails a test instead of hanging it.
            self._held.wait(SILENT_S)
        if self._refuse:
            msg = "you've reported there 5 times today"
            raise SinkRefusedError(msg)
        self.taken.append(report)


def _reporter(tmp_path, clock, private=(), **choices):
    local = LocalSink(tmp_path / "state", keep=KEEP)
    source = FakeSource(now=NOW)
    reporter = Reporter(
        source, Sinks(local, list(private)), now=clock, home=None, choices=ReportChoices(**choices)
    )
    return reporter, local


def test_a_spoken_report_is_saved_at_once_and_read_back(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    answer = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["success"] is True
    assert answer["say"] == (
        "Saved: diesel twenty-one forty-nine at Fake Fuel Gamma, as your own report. "
        "Say 'undo' to take it back."
    )
    [kept] = local.recent(Fuel.DIESEL, clock())
    assert (kept.station_id, kept.price) == ("fake_gamma", 21.49)
    assert "token" not in answer  # no sender: nothing to ask


@pytest.mark.parametrize("price", [2.14, 214.9, 9.99, 40.01])
def test_a_price_outside_the_allowed_range_is_not_saved(tmp_path, price):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    answer = reporter.report(price, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["success"] is False
    assert answer["say"] == f"That price looks wrong: {price:.2f}. Say it again?"
    assert local.recent(Fuel.DIESEL, clock()) == []


def test_the_allowed_range_is_the_owner_s(tmp_path):
    clock = Clock()
    reporter, _ = _reporter(tmp_path, clock, price_low=15.0, price_high=25.0)
    assert reporter.report(14.0, lat=0.0, lon=0.0, accuracy_m=10)["success"] is False
    assert reporter.report(24.0, lat=0.0, lon=0.0, accuracy_m=10)["success"] is True


def test_several_candidate_stations_save_nothing_until_one_is_named(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    answer = reporter.report(21.49, lat=0.0, lon=-0.003, accuracy_m=10)
    assert answer["success"] is False
    assert answer["say"] == "Fake Fuel Gamma or Fake Fuel Zeta?"
    assert local.recent(Fuel.DIESEL, clock()) == []
    named = reporter.report(21.49, station="zeta", lat=0.0, lon=-0.003, accuracy_m=10)
    assert named["say"].startswith("Saved: diesel twenty-one forty-nine at Fake Fuel Zeta,")


def test_no_station_near_saves_nothing(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    answer = reporter.report(21.49, lat=0.5, lon=0.5, accuracy_m=10)
    assert answer == {"success": False, "say": "I can't see a station here."}
    assert local.recent(Fuel.DIESEL, clock()) == []


def test_a_rough_position_needs_the_station_named(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    answer = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=500)
    assert answer["say"] == "Your position is too rough to tell the station. Say its name."
    assert local.recent(Fuel.DIESEL, clock()) == []


def test_a_spoken_name_matches_whatever_its_case_and_accents(tmp_path):
    clock = Clock()
    reporter, _ = _reporter(tmp_path, clock)
    answer = reporter.report(21.49, station="FAKE GÁMMA", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Saved: diesel twenty-one forty-nine at Fake Fuel Gamma,")
    unknown = reporter.report(21.49, station="nowhere", lat=0.0, lon=0.0)
    assert unknown["say"] == "I don't know a station called that near here."
    several = reporter.report(21.49, station="fake fuel", lat=0.0, lon=0.0)
    assert several == {"success": False, "say": "Several stations match. Say more of its name."}


def test_the_owner_s_own_names_for_stations_are_matched_first(tmp_path):
    clock = Clock()
    reporter, _ = _reporter(tmp_path, clock, station_names={"My Usual": "fake_delta"})
    answer = reporter.report(19.99, station="my usual", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Saved: diesel nineteen ninety-nine at Fake Fuel Delta,")


def test_a_named_station_is_found_near_home_without_a_position(tmp_path):
    clock = Clock()
    local = LocalSink(tmp_path / "state", keep=KEEP)
    reporter = Reporter(
        FakeSource(now=NOW), Sinks(local, []), now=clock, home=(0.0, 0.0), choices=ReportChoices()
    )
    assert reporter.report(21.49, station="gamma")["success"] is True
    nowhere = Reporter(FakeSource(now=NOW), Sinks(local, []), now=clock, home=None, choices=ReportChoices())
    assert nowhere.report(21.49)["say"] == "Which station? Say its name, or share your position."


def test_the_usual_fuel_is_reported_when_none_is_named(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock, fuel=Fuel.PETROL95)
    answer = reporter.report(20.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["say"].startswith("Saved: petrol 95 twenty forty-nine at")
    assert [r.fuel for r in local.recent(Fuel.PETROL95, clock())] == [Fuel.PETROL95]


def test_undo_removes_only_the_latest_own_report_and_only_within_10_minutes(tmp_path):
    clock = Clock()
    reporter, local = _reporter(tmp_path, clock)
    reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    clock.pass_(minutes=1)
    reporter.report(21.59, lat=0.0, lon=0.0, accuracy_m=10)
    clock.pass_(minutes=1)
    assert (
        reporter.undo()["say"] == "Taken off your own list: diesel twenty-one fifty-nine at Fake Fuel Gamma."
    )
    assert [r.price for r in local.recent(Fuel.DIESEL, clock())] == [21.49]
    clock.pass_(minutes=10)
    assert reporter.undo()["say"] == "There's no report from the last 10 minutes to take back."
    assert [r.price for r in local.recent(Fuel.DIESEL, clock())] == [21.49]
    assert dt.timedelta(minutes=10) == UNDO_WITHIN


def test_confirm_with_no_sender_set_up_sends_nothing(tmp_path):
    clock = Clock()
    reporter, _ = _reporter(tmp_path, clock)
    reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    answer = reporter.confirm("anything")
    assert answer == {"success": False, "say": "There's no sender set up. Nothing was sent."}


def test_a_report_is_sent_only_with_a_token_from_roy_s_yes(tmp_path):
    clock = Clock()
    north, other = Sender("Service North"), Sender("Service Other")
    reporter, _ = _reporter(tmp_path, clock, private=[north, other])
    answer = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["say"].endswith("Send it to Service North and Service Other too?")
    assert north.taken == other.taken == []  # asked, not sent
    sent = reporter.confirm(answer["token"])
    assert sent["say"] == "Sent to Service North. Sent to Service Other."
    assert [r.price for r in north.taken] == [21.49]
    assert reporter.confirm("made-up")["success"] is False
    assert len(north.taken) == 1


def test_a_token_works_once_and_expires_after_2_minutes(tmp_path):
    clock = Clock()
    north = Sender("Service North")
    reporter, _ = _reporter(tmp_path, clock, private=[north])
    first = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert reporter.confirm(first["token"])["success"] is True
    again = reporter.confirm(first["token"])
    assert again["say"] == "That question has run out, so nothing was sent. Your own list still has it."
    late = reporter.report(21.59, lat=0.0, lon=0.0, accuracy_m=10)
    clock.now += TOKEN_FOR + dt.timedelta(seconds=1)
    assert reporter.confirm(late["token"])["success"] is False
    assert [r.price for r in north.taken] == [21.49]


def test_a_token_is_bound_to_one_report_and_the_services_its_question_named(tmp_path):
    clock = Clock()
    north, later = Sender("Service North"), Sender("Service Later")
    reporter, _ = _reporter(tmp_path, clock, private=[north])
    first = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    reporter.report(21.59, lat=0.0, lon=0.0, accuracy_m=10)
    reporter.sinks.private.append(later)  # switched on after the question
    reporter.confirm(first["token"])
    assert [r.price for r in north.taken] == [21.49]  # the first report, not the newer one
    assert later.taken == []


def test_only_narrows_the_services_and_never_widens_them(tmp_path):
    clock = Clock()
    north, other = Sender("Service North"), Sender("Service Other")
    reporter, _ = _reporter(tmp_path, clock, private=[north, other])
    token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
    stray = reporter.confirm(token, only=["Service North", "Service Elsewhere"])
    assert stray["say"] == "Nothing was sent: the question didn't name Service Elsewhere."
    assert north.taken == other.taken == []  # the stray name refuses the whole send
    narrowed = reporter.confirm(token, only=["service north"])
    assert narrowed["say"] == "Sent to Service North."
    assert len(north.taken) == 1
    assert other.taken == []


def test_one_refusing_service_leaves_the_others_sent(tmp_path):
    clock = Clock()
    refusing, other = Sender("Service North", refuse=True), Sender("Service Other")
    reporter, _ = _reporter(tmp_path, clock, private=[refusing, other])
    token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
    answer = reporter.confirm(token)
    assert answer["say"] == (
        "Not sent to Service North: you've reported there 5 times today. Sent to Service Other."
    )
    assert len(other.taken) == 1


def test_a_send_past_its_time_limit_is_said_as_may_have_been_sent(tmp_path):
    clock = Clock()
    slow = Sender("Service North", slow_s=1.5)
    reporter, _ = _reporter(tmp_path, clock, private=[slow], send_timeout_s=0.2)
    token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
    started = time.monotonic()
    answer = reporter.confirm(token)
    assert time.monotonic() - started < 1.0
    assert answer["say"] == "It may have been sent to Service North: it didn't answer in time."
    _let_the_senders_end()  # the slow sender's own, so no thread outlives the test


def _ended(worker, within=WITHIN):
    """Wait for `worker` up to `within`, and fail the test, not hang it, if it is still going."""
    worker.join(within)
    assert not worker.is_alive(), f"{worker.name} was still going after {within} s"


def _let_the_senders_end(held=None):
    if held is not None:
        held.set()
    for worker in threading.enumerate():
        if worker.name.startswith("fuel-mcp-send"):
            _ended(worker)


def test_all_sends_share_one_deadline(tmp_path):
    held = threading.Event()
    labels = ["Service North", "Service Other", "Service Elsewhere"]
    private = [Sender("Service Fast"), *(Sender(label, held=held) for label in labels)]
    reporter, _ = _reporter(tmp_path, Clock(), private=private, send_timeout_s=0.25)
    token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
    started = time.monotonic()
    try:
        answer = reporter.confirm(token)
        took = time.monotonic() - started
    finally:
        _let_the_senders_end(held)
    assert took < 0.5, f"three silent senders took {took:.2f} s, not one deadline of 0.25 s"
    assert answer["say"] == "Sent to Service Fast. " + " ".join(
        f"It may have been sent to {label}: it didn't answer in time." for label in labels
    )


class _Guarded(dict):
    """A dict that notes every use made while the reporter's lock isn't held."""

    def __init__(self, held, unguarded):
        super().__init__()
        self._held, self._unguarded = held, unguarded

    def _check(self, use):
        if not self._held():
            self._unguarded.append(f"{use} in {threading.current_thread().name}")


for _use in (
    "__setitem__",
    "__getitem__",
    "__delitem__",
    "__contains__",
    "pop",
    "setdefault",
    "get",
    "items",
):

    def _checked(self, *args, _use=_use, **kwargs):
        self._check(_use)
        return getattr(dict, _use)(self, *args, **kwargs)

    setattr(_Guarded, _use, _checked)


def test_the_questions_and_sends_are_changed_only_under_one_lock(tmp_path):
    held = threading.Event()
    silent = Sender("Service Other", held=held)
    reporter, _ = _reporter(tmp_path, Clock(), private=[Sender("Service North"), silent], send_timeout_s=0.1)

    def locked():
        lock = getattr(reporter, "_lock", None)
        return lock is not None and lock.locked()

    unguarded: list[str] = []
    guarded = _Guarded(locked, unguarded), _Guarded(locked, unguarded)
    reporter._asked, reporter._sent = guarded  # noqa: SLF001 - what is touched outside the lock is the point
    try:
        token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
        reporter.confirm(token, only=["Service Nowhere"])  # refused: the token is put back
        said = reporter.confirm(token)["say"]  # one sent, from its thread; one past the deadline
        reporter.report(21.59, lat=0.0, lon=0.0, accuracy_m=10)
        reporter.undo()
    finally:
        _let_the_senders_end(held)
    assert said.startswith("Sent to Service North. It may have been sent to Service Other")
    assert unguarded == []


class _SlowList(LocalSink):
    """The own list, slow between reading the file and writing it back, as a busy disk might be."""

    def _kept(self, now):
        kept = super()._kept(now)
        time.sleep(0.05)
        return kept


def test_report_undo_and_confirm_run_one_at_a_time(tmp_path):
    local = _SlowList(tmp_path / "state", keep=KEEP)
    reporter = Reporter(FakeSource(now=NOW), Sinks(local, []), now=Clock())
    prices = [21.11, 21.22, 21.33, 21.44, 21.55]
    start = threading.Barrier(len(prices), timeout=WITHIN)

    def report(price):
        start.wait()
        reporter.report(price, lat=0.0, lon=0.0, accuracy_m=10)

    workers = [threading.Thread(target=report, args=(price,)) for price in prices]
    for worker in workers:
        worker.start()
    for worker in workers:
        _ended(worker)
    assert sorted(r.price for r in local.recent(Fuel.DIESEL, NOW)) == prices  # none lost


def test_a_sent_record_is_kept_only_as_long_as_undo_can_use_it(tmp_path):
    clock = Clock()
    reporter, _ = _reporter(tmp_path, clock, private=[Sender("Service North")])
    reporter.confirm(reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"])
    first = reporter._sent.copy()  # noqa: SLF001 - what is kept is the point
    clock.pass_(minutes=11)
    reporter.confirm(reporter.report(21.59, lat=0.0, lon=0.0, accuracy_m=10)["token"])
    kept = reporter._sent  # noqa: SLF001 - what is kept is the point
    assert len(first) == 1
    assert len(kept) == 1
    assert not set(first) & set(kept)


def test_undo_says_what_was_already_sent(tmp_path):
    clock = Clock()
    north = Sender("Service North")
    reporter, _ = _reporter(tmp_path, clock, private=[north])
    token = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)["token"]
    reporter.confirm(token)
    assert reporter.undo()["say"] == (
        "Taken off your own list: diesel twenty-one forty-nine at Fake Fuel Gamma. "
        "It was already sent to Service North; that can't be taken back from here."
    )


def _price_service(tmp_path, clock, reports, hours=12):
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=168))
    for report in reports:
        local.take(report)
    source = FakeSource(now=NOW)
    own = OwnReports(source, local, source, counts_for=dt.timedelta(hours=hours), now=clock)
    return FuelService(own, now=clock)


def _own(price, minutes_ago, station="fake_gamma", name="Fake Fuel Gamma"):
    return OwnReport(station, name, Fuel.DIESEL, price, NOW - dt.timedelta(minutes=minutes_ago))


def test_an_own_report_is_said_as_the_owner_s_own_with_its_age(tmp_path):
    clock = Clock()
    service = _price_service(tmp_path, clock, [_own(18.00, 20)])
    answer = service.price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["say"] == (
        "Diesel is cheapest at Fake Fuel Gamma, eighteen kroner, 200 metres away. "
        "That's your own report, under an hour old."
    )
    assert answer["station"]["source"] == "your own report"


def test_the_newest_price_for_a_station_wins_whichever_it_comes_from(tmp_path):
    clock = Clock()
    # Gamma's source price, 20.15, is an hour old: a report from two hours ago loses to it.
    old = _price_service(tmp_path / "old", clock, [_own(19.50, 120)])
    old_say = old.price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)["say"]
    assert old_say.startswith("Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine")
    # A report from ten minutes ago beats it.
    new = _price_service(tmp_path / "new", clock, [_own(19.50, 10)])
    new_say = new.price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)["say"]
    assert new_say.startswith("Diesel is cheapest at Fake Fuel Gamma, nineteen fifty")


@pytest.mark.parametrize(("hours", "said"), [(2, False), (4, True)])
def test_an_own_report_older_than_own_report_hours_is_not_said(tmp_path, hours, said):
    # At Zeta, which has no price of its own, so only the hours can keep the report out.
    report = _own(18.00, 3 * 60, station="fake_zeta", name="Fake Fuel Zeta")
    service = _price_service(tmp_path / str(hours), Clock(), [report], hours=hours)
    answer = service.price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)
    assert ("your own report" in answer["say"]) is said, answer["say"]


def test_an_estimate_is_never_chosen_over_a_reported_price():
    clock = Clock()

    class Estimating(FakeSource):
        def prices_near(self, lat, lon, radius_km, fuel):
            reported = super().prices_near(lat, lon, radius_km, fuel)
            gamma = next(p for p in reported if p.station_id == "fake_gamma")
            newer = gamma.updated_at + dt.timedelta(minutes=1)  # newer, so only the rule keeps it out
            guess = StationPrice(**{**gamma.__dict__, "price": 15.00, "estimate": True, "updated_at": newer})
            return [*reported, guess]

    answer = FuelService(Estimating(now=NOW), now=clock).price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["say"].startswith("Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine")
    assert answer["say"].endswith(" Drivstoffpriser's estimate at Fake Fuel Gamma is fifteen kroner.")


class _Crashing(Sender):
    def take(self, report):
        msg = "a bug in the sender"
        raise RuntimeError(msg)


class _Refusing(LocalSink):
    """The owner's own list, when it can't be written or read: a folder open to others, say."""

    def take(self, report):
        msg = "Your own list's folder is open to others. Fix it with chmod go-rwx."
        raise SinkRefusedError(msg)

    forget_latest = recent = lambda self, *_: self.take(None)  # every use refuses alike


def test_a_crashing_sender_is_said_as_failed_and_the_others_are_still_sent(tmp_path):
    reporter, _ = _reporter(tmp_path, Clock(), private=[_Crashing("Service North"), Sender("Service Other")])
    asked = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert reporter.confirm(asked["token"])["say"] == (
        "Not sent to Service North: it failed. The log says why. Sent to Service Other."
    )


def test_when_the_own_list_refuses_the_report_and_the_undo_say_why(tmp_path):
    local = _Refusing(tmp_path / "state", keep=KEEP)
    reporter = Reporter(FakeSource(now=NOW), Sinks(local, []), now=Clock())
    expected = "Your own list's folder is open to others. Fix it with chmod go-rwx."
    assert reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10) == {"success": False, "say": expected}
    assert reporter.undo() == {"success": False, "say": expected}


def test_with_no_station_list_to_hand_a_report_says_so_and_saves_nothing(tmp_path):
    local = LocalSink(tmp_path / "state", keep=KEEP)
    reporter = Reporter(FakeSource(now=NOW, failing=True), Sinks(local, []), now=Clock())
    answer = reporter.report(21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert answer["say"] == "The station list isn't available right now. Try again in a few minutes."
    assert local.recent(Fuel.DIESEL, NOW) == []


def test_own_reports_that_can_t_be_placed_are_left_out_and_the_source_still_answers(tmp_path):
    clock = Clock()
    refusing = OwnReports(
        FakeSource(now=NOW), _Refusing(tmp_path, keep=KEEP), FakeSource(now=NOW), counts_for=KEEP, now=clock
    )
    local = LocalSink(tmp_path / "state", keep=KEEP)
    local.take(_own(18.00, 10, station="fake_gone", name="Fake Fuel Gone"))  # a station no longer listed
    unlisted = OwnReports(FakeSource(now=NOW), local, FakeSource(now=NOW), counts_for=KEEP, now=clock)
    no_list = OwnReports(
        FakeSource(now=NOW), local, FakeSource(now=NOW, failing=True), counts_for=KEEP, now=clock
    )
    for source in (refusing, unlisted, no_list):
        answer = FuelService(source, now=clock).price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=10)
        assert answer["say"].startswith("Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine")


def test_a_thread_that_never_ends_fails_its_test_instead_of_hanging():
    """A mutant can keep a sender's thread alive; waiting on it without end would hang the run."""
    never = threading.Event()
    stuck = threading.Thread(target=never.wait, name="stuck", daemon=True)
    stuck.start()
    try:
        with pytest.raises(AssertionError, match="still going after"):
            _ended(stuck, within=0.2)
    finally:
        never.set()
        stuck.join(1)
