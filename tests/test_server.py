# SPDX-License-Identifier: GPL-3.0-or-later
"""The tool the voice sees, called as the bridge calls it."""

import asyncio
import datetime as dt

from fuel_mcp.model import CREDIT
from fuel_mcp.server import build_server
from fuel_mcp.service import FuelService
from fuel_mcp.simulator import FakeSource

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


def _server(*, simulated=False):
    return build_server(FuelService(FakeSource(now=NOW), now=lambda: NOW), simulated=simulated)


def _call(server, name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error, result
    return result.structured_content


def test_the_server_offers_exactly_fuel_price():
    tools = asyncio.run(_server().list_tools())
    assert [t.name for t in tools] == ["fuel_price"]
    assert tools[0].annotations.read_only_hint is True
    assert set(tools[0].input_schema["properties"]) == {"fuel", "lat", "lon", "accuracy_m"}


def test_diesel_is_the_fuel_when_none_is_named():
    answer = _call(_server(), "fuel_price", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Diesel is cheapest at Fake Fuel Beta")
    assert answer["credit"] == CREDIT


def test_the_simulated_answer_says_it_is_simulated():
    answer = _call(_server(simulated=True), "fuel_price", fuel="petrol95", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Simulated: Petrol 95 is cheapest at Fake Fuel Gamma")
    assert answer["simulated"] is True


def test_the_instructions_tell_the_voice_to_pass_the_phone_position():
    instructions = _server().instructions
    assert "lat" in instructions
    assert "accuracy_m" in instructions
    assert "Read 'say' aloud" in instructions
