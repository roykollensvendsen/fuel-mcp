# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's own pump prices, by voice: saved here at once, sent elsewhere only on his yes.

A report is saved on the owner's list straight away, and can be taken back
within 10 minutes. Saving only changes what he hears later; sending changes
something outside, which can't be undone. So a private sender, if he has one,
gets a report only through `Reporter.confirm`, with the single-use token the
question gave. The token is bound to one report and to the exact services the
question named. "Only" can narrow those, never widen them.

The position finds the station and is then forgotten: a report holds the
station's id, never where the phone was.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import secrets
import threading
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fuel_mcp.geo import distance_km
from fuel_mcp.model import Fuel, OwnReport, SinkRefusedError, SourceUnavailableError, StationPrice
from fuel_mcp.service import DEFAULT_ACCURACY_M, FARTHEST_KM
from fuel_mcp.speech import spoken_name, spoken_price

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping

    from fuel_mcp.model import PriceSink, PriceSource, Station, StationList
    from fuel_mcp.sinks import Sinks
    from fuel_mcp.sinks.local import LocalSink

#: How long the owner may take back his latest report by voice.
UNDO_WITHIN = dt.timedelta(minutes=10)
#: How long a "Send it to … too?" question may be answered yes.
TOKEN_FOR = dt.timedelta(minutes=2)
#: How far from the phone a station may be, before the position's own accuracy is added.
NEAR_KM = 0.3
#: A position rougher than this can't tell one forecourt from the next.
ROUGHEST_M = 300.0
#: The most names an either/or question reads out.
MOST_NAMED = 3
#: Whose price an own report is, as it is said and given to a program.
OWN = "your own report"
EXPIRED = "That question has run out, so nothing was sent. Your own list still has it."
NO_LIST = (
    'No fuel price source is switched on, so there\'s no station list. Add sources = ["drivstoffpriser"].'
)
log = logging.getLogger("fuel_mcp")

Answer = dict[str, Any]


@dataclass(frozen=True)
class ReportChoices:
    """The owner's choices for reports: the usual fuel, believable prices, and the time a send may take."""

    fuel: Fuel = Fuel.DIESEL
    price_low: float = 10.0
    price_high: float = 40.0
    send_timeout_s: float = 15.0
    farthest_km: float = FARTHEST_KM
    #: The owner's own names for stations, matched first: "my usual" = "<station id>".
    station_names: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Where:
    """Where the phone is, for this one report only."""

    lat: float
    lon: float
    accuracy_m: float | None = None


@dataclass(frozen=True)
class _Asked:
    """A question waiting for the owner's yes: the report, and the services it named."""

    report: OwnReport
    labels: frozenset[str]
    asked_at: dt.datetime


class Reporter:
    """Saves the owner's reports, takes the latest back, and sends one only on his yes."""

    def __init__(
        self,
        stations: StationList | None,
        sinks: Sinks,
        *,
        now: Callable[[], dt.datetime] | None = None,
        home: tuple[float, float] | None = None,
        choices: ReportChoices | None = None,
    ) -> None:
        """`stations` names the station; no list means no source is switched on."""
        self.sinks = sinks
        self._stations = stations
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._home = home
        self._choices = choices or ReportChoices()
        self._asked: dict[str, _Asked] = {}
        self._sent: dict[tuple[str, dt.datetime], list[str]] = {}

    def report(  # noqa: PLR0913 - the tool's own arguments, as the voice sends them
        self,
        price: float,
        fuel: Fuel | None = None,
        station: str | None = None,
        *,
        lat: float | None = None,
        lon: float | None = None,
        accuracy_m: float | None = None,
    ) -> Answer:
        """Save the owner's report at once; ask about sending it when a sender is set up."""
        fuel = fuel or self._choices.fuel
        where = Where(lat, lon, accuracy_m) if lat is not None and lon is not None else None
        # RULE: a price outside the allowed range is not saved
        if not self._choices.price_low <= price <= self._choices.price_high:
            return _said(f"That price looks wrong: {price:.2f}. Say it again?")
        found = self._station(station, where)
        if isinstance(found, str):
            return _said(found)
        report = OwnReport(
            found.station_id, spoken_name(found.name, found.brand), fuel, round(price, 2), self._now()
        )
        try:
            self.sinks.local.take(report)
        except SinkRefusedError as error:
            return _said(str(error))
        saved = (
            f"Saved: {fuel.spoken.lower()} {spoken_price(report.price)} at {report.station_name}, "
            "as your own report. Say 'undo' to take it back."
        )
        labels = [sink.label for sink in self.sinks.private]
        if not labels:
            return _said(saved, success=True)
        token = secrets.token_urlsafe(9)
        # RULE: a report is sent only with a token from Roy's yes
        self._asked[token] = _Asked(report, frozenset(labels), report.reported_at)
        return {**_said(f"{saved} Send it to {_and(labels)} too?", success=True), "token": token}

    def undo(self) -> Answer:
        """Take the latest report off the owner's list, if it is under 10 minutes old."""
        try:
            gone = self.sinks.local.forget_latest(self._now(), UNDO_WITHIN)
        except SinkRefusedError as error:
            return _said(str(error))
        if gone is None:
            return _said(f"There's no report from the last {UNDO_WITHIN.seconds // 60} minutes to take back.")
        self._asked = {token: asked for token, asked in self._asked.items() if asked.report != gone}
        what = f"{gone.fuel.spoken.lower()} {spoken_price(gone.price)} at {gone.station_name}"
        said = f"Taken off your own list: {what}."
        sent = self._sent.pop((gone.station_id, gone.reported_at), [])
        if sent:
            said += f" It was already sent to {_and(sent)}; that can't be taken back from here."
        return _said(said, success=True)

    def confirm(self, token: str, only: Collection[str] | None = None) -> Answer:
        """Send the asked-about report to the services its question named, or to `only` of them."""
        if not self.sinks.private:
            return _said("There's no sender set up. Nothing was sent.")
        asked = self._asked.pop(token, None)
        # RULE: a token works once and expires after 2 minutes
        if asked is None or self._now() - asked.asked_at > TOKEN_FOR:
            return _said(EXPIRED)
        # RULE: a token is bound to one report and the services its question named
        named = asked.labels
        chosen = named if only is None else _narrowed(only, named)
        if isinstance(chosen, str):
            self._asked[token] = asked  # a misheard name uses nothing up: the yes can come again
            return _said(chosen)
        said = [self._send(sink, asked.report) for sink in self.sinks.private if sink.label in chosen]
        return _said(" ".join(said) or "Nothing was sent.", success=any(s.startswith("Sent") for s in said))

    def _send(self, sink: PriceSink, report: OwnReport) -> str:
        """One send, within its own time limit; what happened, as a sentence."""
        outcome: list[str] = []
        key = (report.station_id, report.reported_at)

        def send() -> None:
            try:
                sink.take(report)
            # RULE: one refusing service leaves the others sent
            except SinkRefusedError as refused:
                outcome.append(f"Not sent to {sink.label}: {str(refused).rstrip('.')}.")
            except (Exception, SystemExit) as error:  # noqa: BLE001 - a sender's crash is said, never raised
                log.warning("fuel-mcp: private sink %s failed: %s", sink.label, type(error).__name__)
                outcome.append(f"Not sent to {sink.label}: it failed. The log says why.")
            else:
                self._sent.setdefault(key, []).append(sink.label)
                outcome.append(f"Sent to {sink.label}.")

        worker = threading.Thread(target=send, name=f"fuel-mcp-send-{sink.label}", daemon=True)
        worker.start()
        # RULE: a send past its time limit is said as may have been sent
        worker.join(self._choices.send_timeout_s)
        if worker.is_alive() or not outcome:
            self._sent.setdefault(key, []).append(sink.label)  # it may still arrive
            return f"It may have been sent to {sink.label}: it didn't answer in time."
        return outcome[0]

    def _station(self, name: str | None, where: Where | None) -> Station | str:
        """The one station meant, or the sentence that asks or says why not."""
        if self._stations is None:
            return NO_LIST
        try:
            everything = self._stations.stations()
        except SourceUnavailableError as error:
            log.warning("fuel-mcp: no station list: %s", error)  # the reason only; never the position
            return "The station list isn't available right now. Try again in a few minutes."
        if name:
            return self._named(name, everything, where)
        if where is None:
            return "Which station? Say its name, or share your position."
        accuracy = DEFAULT_ACCURACY_M if where.accuracy_m is None else max(where.accuracy_m, 0.0)
        if accuracy > ROUGHEST_M:
            return "Your position is too rough to tell the station. Say its name."
        reach = NEAR_KM + accuracy / 1000
        near = [s for s in everything if distance_km(where.lat, where.lon, s.lat, s.lon) <= reach]
        return _one(near, "I can't see a station here.")

    def _named(self, name: str, everything: list[Station], where: Where | None) -> Station | str:
        """The station a spoken name means: the owner's own names first, then the list near him."""
        by_id = {s.station_id: s for s in everything}
        own = {_plain(k): v for k, v in self._choices.station_names.items()}
        if (meant := own.get(_plain(name))) in by_id:
            return by_id[meant]
        point = (where.lat, where.lon) if where is not None else self._home
        reach = self._choices.farthest_km
        pool = [s for s in everything if point is None or distance_km(*point, s.lat, s.lon) <= reach]
        said = _words(name)
        matches = [s for s in pool if said <= _words(f"{s.name} {s.brand}")]
        return _one(matches, "I don't know a station called that near here.")


class OwnReports:
    """A price source with the owner's own reports added, each at its station from the list."""

    def __init__(
        self,
        source: PriceSource,
        local: LocalSink,
        stations: StationList,
        *,
        counts_for: dt.timedelta,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        """An own report counts for `counts_for`; older ones are kept but never said."""
        self.label = source.label
        self._source = source
        self._local = local
        self._stations = stations
        self._counts_for = counts_for
        self._now = now or (lambda: dt.datetime.now(dt.UTC))

    def prices_near(self, lat: float, lon: float, radius_km: float, fuel: Fuel) -> list[StationPrice]:
        """The source's prices, then the owner's own reports within `radius_km`."""
        return [*self._source.prices_near(lat, lon, radius_km, fuel), *self._own(lat, lon, radius_km, fuel)]

    def _own(self, lat: float, lon: float, radius_km: float, fuel: Fuel) -> list[StationPrice]:
        now = self._now()
        try:
            reports = self._local.recent(fuel, now)
        except SinkRefusedError as error:
            log.warning("fuel-mcp: own reports not used: %s", error)
            return []
        # RULE: an own report older than own_report_hours is not said
        counting = [r for r in reports if now - r.reported_at <= self._counts_for]
        if not counting:
            return []
        try:
            places = {s.station_id: s for s in self._stations.stations()}
        except SourceUnavailableError:
            return []  # with no list there's no knowing where a report's station is
        found = []
        for report in counting:
            place = places.get(report.station_id)
            if place is None or distance_km(lat, lon, place.lat, place.lon) > radius_km:
                continue
            found.append(
                StationPrice(
                    report.station_id,
                    place.name,
                    place.brand,
                    place.lat,
                    place.lon,
                    fuel,
                    report.price,
                    report.reported_at,
                    source=OWN,
                    own=True,
                )
            )
        return found


def _narrowed(only: Collection[str], named: frozenset[str]) -> frozenset[str] | str:
    """The services `only` names, if the question named every one of them; else why not."""
    by_word = {label.casefold(): label for label in named}
    stray = [label for label in only if label.casefold() not in by_word]
    # RULE: only narrows the services and never widens them
    if stray:
        return f"Nothing was sent: the question didn't name {_and(stray)}."
    return frozenset(by_word[label.casefold()] for label in only)


def _one(matches: list[Station], none_said: str) -> Station | str:
    """The one match, or the sentence for none or several."""
    if not matches:
        return none_said
    # RULE: several candidate stations save nothing until one is named
    if len(matches) > 1:
        if len(matches) > MOST_NAMED:
            return "Several stations match. Say more of its name."
        names = [spoken_name(s.name, s.brand) for s in matches]
        return f"{', '.join(names[:-1])} or {names[-1]}?"
    return matches[0]


def _plain(text: str) -> str:
    """`text` as a key, case- and accent-blind and in any word order: "Gámma Nord" is "gamma nord"."""
    return " ".join(sorted(_words(text)))


def _words(text: str) -> set[str]:
    """The words of `text`, case- and accent-blind."""
    bare = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return set(re.findall(r"[a-z0-9]+", bare.casefold()))


def _and(names: list[str]) -> str:
    """Names joined as said: "A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _said(say: str, *, success: bool = False) -> Answer:
    return {"success": success, "say": say}
