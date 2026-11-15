# SPDX-License-Identifier: GPL-3.0-or-later
"""The Drivstoffpriser source, fed responses in the export's real shape.

The field names are the export's own, read on 2026-11-14. The places are
made up and sit near 0,0.
"""

import datetime as dt
import email.utils
import io
import json
import threading
import time
import urllib.error

import pytest

from fuel_mcp.model import Fuel, SourceUnavailableError
from fuel_mcp.sources import drivstoffpriser
from fuel_mcp.sources.drivstoffpriser import DrivstoffpriserSource, Response

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)
STATIONS = {
    "exportedAt": "2030-01-01T06:00:00.000000+00:00",
    "count": 2,
    "stations": [
        {"id": "osm_1", "name": "Made-up North", "brand": "Brand A", "address": "", "city": "",
         "latitude": 0.009, "longitude": 0.0},
        {"city": "Nowhere", "id": "ck_2", "name": "Made-up East", "brand": "Brand B", "address": "1 Road",
         "latitude": 0.0, "longitude": 0.018},
    ],
}  # fmt: skip
PRICES = {
    "exportedAt": "2030-01-01T06:00:00.000000+00:00",
    "count": 4,
    "prices": [
        {"stationId": "osm_1", "fuelType": "diesel", "price": 19.84,
         "updatedAt": "2030-01-01T09:00:00.000000", "reportCount": 3},
        {"stationId": "ck_2", "fuelType": "diesel", "price": 19.79,
         "updatedAt": "2029-12-31T06:00:00.000000", "reportCount": 1},
        {"stationId": "ck_2", "fuelType": "petrol95", "price": 20.49,
         "updatedAt": "2030-01-01T09:00:00.000000", "reportCount": 1},
        {"stationId": "user_9", "fuelType": "diesel", "price": 1.0, "updatedAt": "2030-01-01T09:00:00.000000",
         "reportCount": 1},
    ],
}  # fmt: skip
BODIES = {"stations.json": json.dumps(STATIONS).encode(), "prices.json": json.dumps(PRICES).encode()}


class Host:
    """A pretend GitHub Pages: it records each request and answers like the real one."""

    def __init__(self, *, down=False):
        self.requests = []
        self.timeouts = []
        self.down = down

    def __call__(self, url, headers, timeout=None):
        self.timeouts.append(timeout)
        self.requests.append((url.rsplit("/", 1)[-1], dict(headers)))
        if self.down:
            msg = "pretend outage"
            raise OSError(msg)
        name = url.rsplit("/", 1)[-1]
        tag = f'"tag-{name}"'
        if headers.get("If-None-Match") == tag:
            return Response(304, {}, b"")
        return Response(200, {"ETag": tag, "Last-Modified": "Tue, 01 Jan 2030 06:00:00 GMT"}, BODIES[name])


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _source(tmp_path, host, clock=None):
    return DrivstoffpriserSource(tmp_path, fetch=host, now=clock or Clock())


def test_the_export_is_read_into_prices_with_names_and_ages(tmp_path):
    prices = _source(tmp_path, Host()).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    by_name = {p.name: p for p in prices}
    assert set(by_name) == {"Made-up North", "Made-up East"}
    north = by_name["Made-up North"]
    assert (north.price, north.brand, north.report_count) == (19.84, "Brand A", 3)
    assert north.updated_at == dt.datetime(2030, 1, 1, 8, 0, tzinfo=dt.UTC)  # 09:00 in Oslo, winter
    assert north.source == "Drivstoffpriser"


def test_a_price_for_a_station_not_in_the_station_list_is_skipped(tmp_path):
    prices = _source(tmp_path, Host()).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert all(p.price != 1.0 for p in prices)


def test_only_the_fuel_asked_for_and_only_nearby_stations_come_back(tmp_path):
    source = _source(tmp_path, Host())
    assert [p.name for p in source.prices_near(0.0, 0.0, 5.1, Fuel.PETROL95)] == ["Made-up East"]
    assert source.prices_near(1.0, 1.0, 5.1, Fuel.DIESEL) == []


def test_a_copy_checked_within_the_hour_is_used_without_asking(tmp_path):
    host, clock = Host(), Clock()
    source = _source(tmp_path, host, clock)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=59)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 2  # one for each file, the first time only


def test_a_check_sends_back_the_tag_from_the_last_answer(tmp_path):
    host, clock = Host(), Clock()
    source = _source(tmp_path, host, clock)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=61)
    prices = source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    second = dict(host.requests[2:])
    assert second["prices.json"]["If-None-Match"] == '"tag-prices.json"'
    assert second["prices.json"]["If-Modified-Since"] == "Tue, 01 Jan 2030 06:00:00 GMT"
    assert len(prices) == 2  # a 304 keeps the copy


def test_a_not_modified_answer_restarts_the_hour(tmp_path):
    host, clock = Host(), Clock()
    source = _source(tmp_path, host, clock)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=61)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=30)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 4


def test_the_copy_outlasts_a_restart(tmp_path):
    host, clock = Host(), Clock()
    _source(tmp_path, host, clock).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    _source(tmp_path, host, clock).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 2


def test_when_the_host_is_down_the_last_copy_is_used(tmp_path):
    host, clock = Host(), Clock()
    _source(tmp_path, host, clock).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(hours=2)
    prices = _source(tmp_path, Host(down=True), clock).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(prices) == 2


def test_when_the_host_is_down_and_there_is_no_copy_the_source_is_unavailable(tmp_path):
    with pytest.raises(SourceUnavailableError):
        _source(tmp_path, Host(down=True)).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)


def test_an_export_in_a_changed_format_is_unavailable_not_a_crash(tmp_path):
    def broken(_url, _headers, _timeout=None):
        return Response(200, {}, b'{"stations": "not a list"}')

    with pytest.raises(SourceUnavailableError, match="format"):
        DrivstoffpriserSource(tmp_path, fetch=broken, now=Clock()).prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)


class _Reply(io.BytesIO):
    status = 200
    headers = {"ETag": '"x"'}  # noqa: RUF012

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_the_real_fetch_turns_a_304_into_a_response(monkeypatch):
    seen = {}

    def not_modified(request, **_):
        seen["agent"] = request.get_header("User-agent")
        stamp = email.utils.formatdate(usegmt=True)
        raise urllib.error.HTTPError(request.full_url, 304, "Not Modified", {"Date": stamp}, None)

    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", not_modified)
    assert drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 3.0).status == 304
    assert seen["agent"] == "fuel-mcp"


def test_the_real_fetch_reads_a_body(monkeypatch):
    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", lambda *_, **__: _Reply(b"{}"))
    reply = drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {"If-None-Match": '"y"'}, 3.0)
    assert (reply.status, reply.body, reply.headers["ETag"]) == (200, b"{}", '"x"')


class Flaky(Host):
    """A host that can be switched from working to failing, and counts every ask."""

    def __init__(self):
        super().__init__()
        self.answer = None  # None works; an OSError instance raises it; an int is that status
        self.answer_headers = {}

    def __call__(self, url, headers, _timeout=None):
        if isinstance(self.answer, OSError):
            self.requests.append((url.rsplit("/", 1)[-1], dict(headers)))
            raise self.answer
        if isinstance(self.answer, int):
            self.requests.append((url.rsplit("/", 1)[-1], dict(headers)))
            return Response(self.answer, self.answer_headers, b"")
        if isinstance(self.answer, bytes):
            self.requests.append((url.rsplit("/", 1)[-1], dict(headers)))
            return Response(200, {"ETag": '"broken"'}, self.answer)
        return super().__call__(url, headers, _timeout)


def _expired_copy(tmp_path):
    host, clock = Flaky(), Clock()
    source = _source(tmp_path, host, clock)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(hours=2)
    host.requests.clear()
    return source, host, clock


def test_a_failing_host_is_not_asked_again_for_ten_minutes(tmp_path):
    source, host, clock = _expired_copy(tmp_path)
    host.answer = TimeoutError("the host hangs")
    for _ in range(3):
        assert len(source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)) == 2  # the copy answers
        clock.now += dt.timedelta(minutes=3)
    assert len(host.requests) == 1  # one ask in nine minutes, and the second file was not tried
    clock.now += dt.timedelta(minutes=2)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 2


def test_an_error_status_backs_off_like_a_failure(tmp_path):
    source, host, clock = _expired_copy(tmp_path)
    host.answer = 503
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=5)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 1


def test_with_no_copy_a_failing_host_is_said_at_once_and_not_asked_again(tmp_path):
    host, clock = Flaky(), Clock()
    host.answer = TimeoutError("the host hangs")
    source = _source(tmp_path, host, clock)
    for _ in range(2):
        with pytest.raises(SourceUnavailableError):
            source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 1


def test_the_host_is_asked_as_usual_once_it_answers_again(tmp_path):
    source, host, clock = _expired_copy(tmp_path)
    host.answer = TimeoutError("the host hangs")
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    host.answer = None
    clock.now += dt.timedelta(minutes=11)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=30)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 3  # the failed ask, then one for each file, then the hourly copy


def test_a_bad_export_keeps_the_good_copy(tmp_path):
    source, host, _ = _expired_copy(tmp_path)
    host.answer = b'{"stations": "not a list"}'
    assert len(source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)) == 2
    assert json.loads((tmp_path / "stations.json").read_bytes()) == STATIONS


class Stopwatch:
    """A clock for elapsed time, moved on by the fake host as it answers."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


@pytest.mark.parametrize(("seconds_each", "timeouts", "answered"), [(5, [8.0, 3.0], True), (8, [8.0], False)])
def test_a_question_waits_on_the_host_for_eight_seconds_in_all(tmp_path, seconds_each, timeouts, answered):
    watch, host = Stopwatch(), Host()

    def slow(url, headers, timeout=None):
        watch.t += seconds_each
        return host(url, headers, timeout)

    source = DrivstoffpriserSource(tmp_path, fetch=slow, now=Clock(), elapsed=watch)
    if answered:
        assert len(source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)) == 2
    else:
        with pytest.raises(SourceUnavailableError):  # no copy yet, and no time left for the second file
            source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert host.timeouts == timeouts


@pytest.mark.parametrize(
    ("status", "header", "quiet_minutes"), [(429, {}, 60), (503, {"Retry-After": "7200"}, 120)]
)
def test_a_busy_host_is_left_alone_for_an_hour_or_as_long_as_it_asks(tmp_path, status, header, quiet_minutes):
    source, host, clock = _expired_copy(tmp_path)
    host.answer, host.answer_headers = status, header
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    clock.now += dt.timedelta(minutes=quiet_minutes - 1)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 1
    clock.now += dt.timedelta(minutes=2)
    source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)
    assert len(host.requests) == 2


@pytest.mark.parametrize(
    ("stamp", "utc_hour"),
    [
        ("2030-01-15T12:00:00.000000", 11),
        ("2030-07-15T12:00:00.000000", 10),
        ("2030-07-15T12:00:00+00:00", 12),
    ],
    ids=["winter", "summer", "with an offset"],
)
def test_a_price_time_with_no_offset_is_read_as_norwegian_time(stamp, utc_hour):
    # The app stores the reporting phone's local time with no offset; only exportedAt is UTC.
    prices = {**PRICES, "prices": [{**PRICES["prices"][0], "updatedAt": stamp}]}
    found = drivstoffpriser.parse_export(BODIES["stations.json"], json.dumps(prices).encode(), source="x")
    assert found[0].updated_at.astimezone(dt.UTC).hour == utc_hour


@pytest.fixture(autouse=True)
def _no_hung_hosts():
    """Each test starts with no host marked as hung, whatever the one before left."""
    drivstoffpriser._ABANDONED.clear()  # noqa: SLF001 - module state, cleared for each test
    yield
    drivstoffpriser._ABANDONED.clear()  # noqa: SLF001


def _settle(host):
    """Wait for a released GET's thread to end, so the next test finds the host free."""
    deadline = time.monotonic() + 3
    while drivstoffpriser.still_hung(host) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not drivstoffpriser.still_hung(host)


def test_a_slow_host_never_holds_a_question_past_its_time(monkeypatch):
    """A name lookup or a read that hangs is not covered by a socket timeout, so the wait is cut off."""
    released = threading.Event()

    def hangs(*_, **__):
        released.wait(5)
        return _Reply(b"{}")

    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", hangs)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match=r"0\.2 s"):
        drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 0.2)
    assert time.monotonic() - started < 1.5
    released.set()
    _settle("example.invalid")


def test_a_failure_inside_the_fetch_reaches_the_question(monkeypatch):
    def refused(*_, **__):
        msg = "connection refused"
        raise urllib.error.URLError(msg)

    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", refused)
    with pytest.raises(urllib.error.URLError, match="refused"):
        drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 3.0)


def test_a_hung_host_is_never_asked_twice_at_once(monkeypatch):
    """A GET that hangs keeps its thread; another to that host is refused at once, so none pile up."""
    released = threading.Event()

    def hangs(*_, **__):
        released.wait(5)
        return _Reply(b"{}")

    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", hangs)
    with pytest.raises(TimeoutError):
        drivstoffpriser.urllib_fetch("https://example.invalid/stations.json", {}, 0.2)
    started = time.monotonic()
    with pytest.raises(OSError, match="hasn't answered its last request; try again in a minute"):
        drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 3.0)
    assert time.monotonic() - started < 0.5
    released.set()
    _settle("example.invalid")
    assert drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 3.0).status == 200


def test_two_questions_at_once_to_a_healthy_host_both_get_their_answer(monkeypatch):
    """Only a request given up on blocks the host; one still within its time does not."""

    def answers(*_, **__):
        time.sleep(0.1)
        return _Reply(b"{}")

    monkeypatch.setattr(drivstoffpriser.urllib.request, "urlopen", answers)
    replies = []

    def ask():
        replies.append(drivstoffpriser.urllib_fetch("https://example.invalid/prices.json", {}, 3.0).status)

    askers = [threading.Thread(target=ask) for _ in range(2)]
    for asker in askers:
        asker.start()
    for asker in askers:
        asker.join(5)
    assert replies == [200, 200]
