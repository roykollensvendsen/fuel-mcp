# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's own list: his reports, kept on this machine only, and read back.

The file is ``$XDG_STATE_HOME/fuel-mcp/own-reports.jsonl``, one report a line,
0600 and written whole (`fuel_mcp.safe_file`). It holds the station's id and
name, the fuel, the price and the time, and nothing else: never a position.

Stations and times still make a trail: a station near home at 07:40 every
weekday says where the owner lives and when he leaves. So a report is kept only
for ``own_report_keep_hours``, 12 unless he sets another. It is removed at the
next save or read after that.
"""

from __future__ import annotations

import datetime as dt

# RULE: the public package holds no sender
import json
import logging
from typing import TYPE_CHECKING

from fuel_mcp.model import Fuel, OwnReport, SinkRefusedError
from fuel_mcp.safe_file import PrivateFileError, read_private, write_private

if TYPE_CHECKING:
    import pathlib

FILE_NAME = "own-reports.jsonl"
#: Everything a line of the file may hold. A position is not among them.
FIELDS = ("station_id", "station_name", "fuel", "price", "reported_at")
log = logging.getLogger("fuel_mcp")


class LocalSink:
    """The sink every report goes to first, and the only one read back."""

    label = "your own list"
    needs_yes = False

    def __init__(self, folder: pathlib.Path, *, keep: dt.timedelta) -> None:
        """`folder` is fuel-mcp's state folder; a report older than `keep` is removed."""
        self._path = folder / FILE_NAME
        self._keep = keep

    def take(self, report: OwnReport) -> None:
        """Add the report to the file; raises SinkRefusedError when the file can't be written."""
        kept = [*self._kept(report.reported_at), report]
        self._write(kept)

    def recent(self, fuel: Fuel, now: dt.datetime) -> list[OwnReport]:
        """Every report for `fuel` still kept at `now`, oldest first."""
        kept = self._kept(now)
        return [r for r in kept if r.fuel == fuel]

    def _kept(self, now: dt.datetime) -> list[OwnReport]:
        """The reports still inside the keep time; the file is rewritten when some have gone."""
        everything = self._read()
        # RULE: an own report is removed once its keep time has passed
        kept = [r for r in everything if now - r.reported_at <= self._keep]
        if len(kept) < len(everything):
            self._write(kept)
        return kept

    def _read(self) -> list[OwnReport]:
        try:
            body = read_private(self._path)
        except PrivateFileError as error:
            log.warning("fuel-mcp: own reports not read: %s", error)
            return []
        reports = []
        for line in (body or b"").decode("utf-8", "replace").splitlines():
            report = _parsed(line)
            if report is None:
                log.warning("fuel-mcp: a line of %s isn't a report, so it is left out", self._path)
            else:
                reports.append(report)
        return reports

    def _write(self, reports: list[OwnReport]) -> None:
        text = "".join(json.dumps(_line(r), ensure_ascii=False) + "\n" for r in reports)
        try:
            write_private(self._path, text.encode())
        except OSError as error:
            msg = f"Not saved: your own list can't be written. {error}"
            raise SinkRefusedError(msg) from None


def _line(report: OwnReport) -> dict[str, object]:
    """One line of the file: the five fields, whatever else the report carries."""
    # RULE: the own-reports file holds no position
    line = {field: getattr(report, field) for field in FIELDS}
    line["fuel"] = report.fuel.value
    line["reported_at"] = report.reported_at.astimezone(dt.UTC).isoformat()
    return line


def _parsed(text: str) -> OwnReport | None:
    try:
        line = json.loads(text)
        at = dt.datetime.fromisoformat(line["reported_at"])
        return OwnReport(
            station_id=str(line["station_id"]),
            station_name=str(line["station_name"]),
            fuel=Fuel(line["fuel"]),
            price=float(line["price"]),
            reported_at=at if at.tzinfo else at.replace(tzinfo=dt.UTC),
        )
    except (ValueError, KeyError, TypeError):
        return None
