# SPDX-License-Identifier: GPL-3.0-or-later
"""Command line for fuel-mcp: serve the tool to a voice session, or read one price."""

from __future__ import annotations

import argparse
import os
import sys

from fuel_mcp.model import Fuel
from fuel_mcp.server import build_server, simulated_answer
from fuel_mcp.service import FuelService
from fuel_mcp.settings import SettingsError, build_source, cache_folder, config_folder, load_settings
from fuel_mcp.simulator import FakeSource

#: Every verb this command has. The documentation test requires each one to
#: appear in a worked example, so a verb cannot be added without one.
COMMANDS: tuple[str, ...] = ("serve", "price")


def _service(*, simulate: bool) -> FuelService:
    """The made-up stations, or the sources the local settings switch on, with home."""
    if simulate:
        return FuelService(FakeSource())
    settings = load_settings(config_folder(os.environ))
    return FuelService(build_source(settings, cache_folder(os.environ)), home=settings.home)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch. Returns a process exit code."""
    parser = argparse.ArgumentParser(prog="fuel-mcp", description=__doc__)
    parser.add_argument("command", choices=COMMANDS, help="serve: MCP over stdio; price: read once")
    parser.add_argument("--simulate", action="store_true", help="use made-up stations; touches no network")
    parser.add_argument("--fuel", choices=[f.value for f in Fuel], default=Fuel.DIESEL.value)
    parser.add_argument("--lat", type=float, help="latitude of the point to search around")
    parser.add_argument("--lon", type=float, help="longitude of the point to search around")
    parser.add_argument("--accuracy-m", type=float, help="how exact the point is, in metres")
    args = parser.parse_args(argv)
    try:
        service = _service(simulate=args.simulate)
    except SettingsError as error:
        sys.stdout.write(f"{error}\n")
        return 1
    if args.command == "price":
        answer = service.price(Fuel(args.fuel), lat=args.lat, lon=args.lon, accuracy_m=args.accuracy_m)
        if args.simulate:
            answer = simulated_answer(answer)
        sys.stdout.write(f"{answer['say']}\n{answer['credit']}\n")
        return 0 if answer["success"] else 1
    build_server(service, simulated=args.simulate).run()  # pragma: no cover - blocks on stdio
    return 0  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
