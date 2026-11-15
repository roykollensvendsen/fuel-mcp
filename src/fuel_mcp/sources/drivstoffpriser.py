# SPDX-License-Identifier: GPL-3.0-or-later
"""Prices from Drivstoffpriser's open export, checked at most hourly.

The export is two static files, ``stations.json`` and ``prices.json``, under
ODbL. A copy of each is kept in the cache folder with the tags of the answer
that brought it. A question uses the copy if it was checked within the hour.
Otherwise it asks again with those tags, and a ``304`` keeps the copy
(ADR-FM-001).
"""

from __future__ import annotations

import datetime as dt
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fuel_mcp.geo import distance_km
from fuel_mcp.model import Fuel, SourceUnavailableError, StationPrice
from fuel_mcp.speech import NORWAY

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Callable, Mapping

BASE_URL = "https://drivstoffpriser.github.io/Drivstoffpriser-App/data/"
FILES = ("stations.json", "prices.json")
CHECK_EVERY = dt.timedelta(hours=1)
#: After a timeout or a lost network the host is left alone this long, and the copy answers at once.
RETRY_AFTER = dt.timedelta(minutes=10)
#: A host that says it is busy (429) or broken (5xx) is left alone an hour, or as long as it asks.
BUSY_QUIET = CHECK_EVERY
#: The longest a Retry-After is honoured, so a typo in it cannot silence the host for good.
LONGEST_QUIET = dt.timedelta(days=1)
#: Seconds one question may wait on the host, both files together. The voice gives up near 15.
BUDGET_S = 8.0
#: Until when the host is left alone, kept beside the copy so a restart does not ask again at once.
QUIET_UNTIL = "host-quiet-until"
NOT_MODIFIED, OK, TOO_MANY, SERVER_ERROR = 304, 200, 429, 500
_CHUNK = 64 * 1024
#: A generic name: the request says which program asks, never who.
USER_AGENT = "fuel-mcp"
#: Each header a check sends, and the tag it sends back.
CONDITIONAL = (("If-None-Match", "etag"), ("If-Modified-Since", "last_modified"))


@dataclass(frozen=True)
class Response:
    """What the host answered: the status, its headers, and the body."""

    status: int
    headers: Mapping[str, str]
    body: bytes


def urllib_fetch(url: str, headers: Mapping[str, str], timeout: float) -> Response:
    """One GET with `headers`, finished within `timeout` seconds in all; an HTTP status is an answer."""
    request = urllib.request.Request(url, headers={**headers, "User-Agent": USER_AGENT})  # noqa: S310
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310 - a fixed https URL
            body = bytearray()
            while chunk := reply.read(_CHUNK):
                body += chunk
                if time.monotonic() > deadline:
                    msg = f"the body took more than {timeout:.1f} s"
                    raise TimeoutError(msg)
            return Response(reply.status, dict(reply.headers), bytes(body))
    except urllib.error.HTTPError as error:
        return Response(error.code, dict(error.headers or {}), b"")


class DrivstoffpriserSource:
    """The open export, kept in `cache` and checked at most hourly."""

    label = "Drivstoffpriser"

    def __init__(
        self,
        cache: pathlib.Path,
        *,
        fetch: Callable[[str, Mapping[str, str], float], Response] = urllib_fetch,
        now: Callable[[], dt.datetime] | None = None,
        elapsed: Callable[[], float] = time.monotonic,
    ) -> None:
        """`fetch`, `now` and `elapsed` are replaced in tests; the real ones use the network and clocks."""
        self._cache = cache
        self._fetch = fetch
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._elapsed = elapsed
        self._deadline = 0.0

    def prices_near(self, lat: float, lon: float, radius_km: float, fuel: Fuel) -> list[StationPrice]:
        """Every price for `fuel` within `radius_km` of the point."""
        self._deadline = self._elapsed() + BUDGET_S
        stations, prices = (self._file(name) for name in FILES)
        return [
            p
            for p in parse_export(stations, prices, source=self.label)
            if p.fuel == fuel and distance_km(lat, lon, p.lat, p.lon) <= radius_km
        ]

    def _file(self, name: str) -> bytes:
        """The file's body, from the copy or from the host."""
        copy, tags = self._cache / name, self._cache / f"{name}.tags.json"
        meta = _tags(tags) if copy.is_file() else {}
        now = self._now()
        checked = dt.datetime.fromisoformat(meta["checked_at"]) if "checked_at" in meta else None
        # RULE: a copy checked within the hour is used without asking
        if checked is not None and now - checked < CHECK_EVERY:
            return copy.read_bytes()
        # RULE: a failing host is not asked again for ten minutes
        if self._failed_recently(now):
            return self._copy_or_unavailable(
                name, meta, "is not asked for: the host failed a short while ago"
            )
        return self._ask(now, name, meta)

    def _ask(self, now: dt.datetime, name: str, meta: dict[str, str]) -> bytes:
        """Ask the host for the file, within what is left of the question's time."""
        copy, tags = self._cache / name, self._cache / f"{name}.tags.json"
        left = self._deadline - self._elapsed()
        if left <= 0:
            return self._failed(now, name, meta, "was not asked for: the host was too slow", RETRY_AFTER)
        # RULE: a check sends back the tag from the last answer
        ask = {header: meta[key] for header, key in CONDITIONAL if key in meta}
        try:
            reply = self._fetch(BASE_URL + name, ask, left)
        except OSError as error:  # URLError and timeouts are OSErrors too
            return self._failed(now, name, meta, f"could not be fetched: {error}", RETRY_AFTER)
        if reply.status == NOT_MODIFIED and meta:
            meta["checked_at"] = now.isoformat()
            tags.write_text(json.dumps(meta), encoding="utf-8")
            self._recovered()
            return copy.read_bytes()
        if reply.status != OK:
            quiet = (
                _quiet_for(reply) if reply.status == TOO_MANY or reply.status >= SERVER_ERROR else RETRY_AFTER
            )
            return self._failed(now, name, meta, f"answered {reply.status}", quiet)
        return self._store(now, name, meta, reply)

    def _store(self, now: dt.datetime, name: str, meta: dict[str, str], reply: Response) -> bytes:
        """Keep a new body and its tags, but only once it reads; a bad one leaves the copy alone."""
        copy, tags = self._cache / name, self._cache / f"{name}.tags.json"
        try:
            _records(reply.body, name.removesuffix(".json"))
        except (KeyError, TypeError, ValueError) as error:
            why = f"came in a format this version does not know: {error!r}"
            return self._failed(now, name, meta, why, RETRY_AFTER)
        headers = {k.lower(): v for k, v in reply.headers.items()}
        self._cache.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(reply.body)
        fresh = {"checked_at": now.isoformat()}
        fresh |= {
            key: headers[h]
            for key, h in (("etag", "etag"), ("last_modified", "last-modified"))
            if h in headers
        }
        tags.write_text(json.dumps(fresh), encoding="utf-8")
        self._recovered()
        return reply.body

    def _failed_recently(self, now: dt.datetime) -> bool:
        marker = self._cache / QUIET_UNTIL
        if not marker.is_file():
            return False
        try:
            return now < dt.datetime.fromisoformat(marker.read_text(encoding="utf-8").strip())
        except ValueError:
            return False

    def _failed(
        self, now: dt.datetime, name: str, meta: dict[str, str], why: str, quiet: dt.timedelta
    ) -> bytes:
        """Note the failure, so the host is left alone for `quiet`, then answer from the copy."""
        self._cache.mkdir(parents=True, exist_ok=True)
        (self._cache / QUIET_UNTIL).write_text((now + quiet).isoformat(), encoding="utf-8")
        return self._copy_or_unavailable(name, meta, why)

    def _recovered(self) -> None:
        (self._cache / QUIET_UNTIL).unlink(missing_ok=True)

    def _copy_or_unavailable(self, name: str, meta: dict[str, str], why: str) -> bytes:
        if meta:
            return (self._cache / name).read_bytes()
        msg = f"{name} {why}, and there is no copy"
        raise SourceUnavailableError(msg)


def _quiet_for(reply: Response) -> dt.timedelta:
    """How long a busy or broken host is left alone: an hour, or longer if its Retry-After asks."""
    asked = {k.lower(): v for k, v in reply.headers.items()}.get("retry-after", "").strip()
    seconds = int(asked) if asked.isdigit() else 0
    return min(max(BUSY_QUIET, dt.timedelta(seconds=seconds)), LONGEST_QUIET)


def _tags(path: pathlib.Path) -> dict[str, str]:
    """The tags kept with a copy; a missing or broken file is taken as none, so the host is asked."""
    try:
        found = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}


def parse_export(stations: bytes, prices: bytes, *, source: str) -> list[StationPrice]:
    """Both files joined into one price per station and fuel.

    Raises SourceUnavailableError when the export is in a format this version does not know.
    """
    try:
        places = {s["id"]: s for s in _records(stations, "stations")}
        found = []
        for p in _records(prices, "prices"):
            station = places.get(p["stationId"])
            if station is None or p["fuelType"] not in Fuel:
                continue  # a price reported for a station the list lacks, or a fuel this plugin does not know
            found.append(
                StationPrice(
                    station_id=str(station["id"]),
                    name=str(station["name"]),
                    brand=str(station["brand"]),
                    lat=float(station["latitude"]),
                    lon=float(station["longitude"]),
                    fuel=Fuel(p["fuelType"]),
                    price=float(p["price"]),
                    updated_at=_utc(p["updatedAt"]),
                    source=source,
                    report_count=int(p.get("reportCount", 1)),
                )
            )
    except (KeyError, TypeError, ValueError) as error:
        msg = f"the export is in a format this version does not know: {error!r}"
        raise SourceUnavailableError(msg) from error
    return found


def _records(body: bytes, key: str) -> list[dict[str, Any]]:
    records = json.loads(body)[key]
    if not isinstance(records, list):
        msg = f"{key} is not a list"
        raise TypeError(msg)
    return records


def _utc(stamp: str) -> dt.datetime:
    """A timestamp from the export, in UTC.

    The app stores a price's time as the reporting phone's local time, with no
    offset; only exportedAt carries one. A time with no offset is therefore
    read as Norwegian time.
    """
    moment = dt.datetime.fromisoformat(stamp)
    if moment.tzinfo is not None:
        return moment.astimezone(dt.UTC)
    # RULE: a price time with no offset is read as norwegian time
    return moment.replace(tzinfo=NORWAY).astimezone(dt.UTC)
