# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for fuel-mcp: serve the tool to a voice session, or read one price."""

from __future__ import annotations

import argparse
import atexit
import datetime as dt
import os
import pathlib
import sys
import tempfile

from fuel_mcp.model import Fuel, StationList
from fuel_mcp.reports import OwnReports, ReportChoices, Reporter
from fuel_mcp.server import build_server, simulated_answer
from fuel_mcp.service import Choices, FuelService
from fuel_mcp.settings import (
    SettingsError,
    build_source,
    cache_folder,
    config_folder,
    load_settings,
    state_folder,
)
from fuel_mcp.simulator import FakeSource
from fuel_mcp.sinks import Sinks, load_private_sinks
from fuel_mcp.sinks.local import LocalSink

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "price")


def _simulated_list() -> pathlib.Path:
    """A simulated run's own list: a temporary folder, removed when the process ends."""
    folder = tempfile.TemporaryDirectory(prefix="fuel-mcp-simulated-")
    atexit.register(folder.cleanup)
    return pathlib.Path(folder.name)


def parts(*, simulate: bool) -> tuple[FuelService, Reporter]:
    """The price service and the reporter: the made-up stations, or what the local settings switch on.

    The service hears the owner's own reports beside the source's prices. A simulated run
    keeps its reports in a temporary folder, never on the owner's own list.
    """
    if simulate:
        fake = FakeSource()
        local = LocalSink(_simulated_list(), keep=dt.timedelta(hours=12))
        own = OwnReports(fake, local, fake, counts_for=dt.timedelta(hours=12))
        return FuelService(own), Reporter(fake, Sinks(local, []))
    folder = config_folder(os.environ)
    settings = load_settings(folder)
    local = LocalSink(state_folder(os.environ), keep=dt.timedelta(hours=settings.own_report_keep_hours))
    source = build_source(settings, cache_folder(os.environ))
    stations = source if isinstance(source, StationList) else None
    counts_for = dt.timedelta(hours=settings.own_report_hours)
    priced = OwnReports(source, local, stations, counts_for=counts_for) if source and stations else source
    service = FuelService(
        priced,
        home=settings.home,
        config_file=str(folder / "config.toml"),
        choices=Choices(settings.fuel, settings.farthest_km, settings.old_after_days),
    )
    choices = ReportChoices(
        settings.fuel,
        settings.price_low,
        settings.price_high,
        settings.send_timeout_s,
        settings.farthest_km,
        settings.station_names,
    )
    sinks = Sinks(local, load_private_sinks(settings.private_sinks))
    return service, Reporter(stations, sinks, home=settings.home, choices=choices)


def _fuel(text: str) -> Fuel:
    """A fuel named on the command line; a wrong one is told the known ones."""
    try:
        return Fuel(text)
    except ValueError:
        msg = f"unknown fuel {text!r}; the known ones are {', '.join(Fuel)}"
        raise argparse.ArgumentTypeError(msg) from None


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="fuel-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS, help="serve: MCP over stdio; price: read once")
    parser.add_argument("--simulate", action="store_true", help="use made-up stations; touches no network")
    parser.add_argument("--fuel", type=_fuel, choices=list(Fuel), help="by default the one in the settings")
    parser.add_argument("--lat", type=float, help="latitude of the point to search around")
    parser.add_argument("--lon", type=float, help="longitude of the point to search around")
    parser.add_argument("--accuracy-m", type=float, help="how exact the point is, in metres")
    args = parser.parse_args(argv)
    try:
        service, reporter = parts(simulate=args.simulate)
    except SettingsError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    if args.command == "price":
        answer = service.price(args.fuel, lat=args.lat, lon=args.lon, accuracy_m=args.accuracy_m)
        if args.simulate:
            answer = simulated_answer(answer)
        sys.stdout.write(f"{answer['say']}\n{answer['credit']}\n")
        return 0 if answer["success"] else 1
    build_server(service, reporter, simulated=args.simulate).run()  # pragma: no cover - blocks on stdio
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
