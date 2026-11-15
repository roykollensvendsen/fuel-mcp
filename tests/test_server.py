# SPDX-License-Identifier: GPL-3.0-or-later
"""The tool the voice sees, called as the bridge calls it."""

import asyncio
import datetime as dt

from fuel_mcp.model import CREDIT, Fuel
from fuel_mcp.reports import Reporter
from fuel_mcp.server import build_server
from fuel_mcp.service import Choices, FuelService
from fuel_mcp.simulator import FakeSource
from fuel_mcp.sinks import Sinks
from fuel_mcp.sinks.local import LocalSink

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


def _server(*, simulated=False):
    return build_server(FuelService(FakeSource(now=NOW), now=lambda: NOW), simulated=simulated)


def _call(server, name, **arguments):
    result = asyncio.run(server.call_tool(name, arguments))
    assert not result.is_error, result
    return result.structured_content


TOOLS = ["fuel_price", "fuel_report", "fuel_report_undo", "fuel_report_confirm"]


def test_the_server_offers_exactly_the_price_and_the_three_report_tools():
    tools = {t.name: t for t in asyncio.run(_server().list_tools())}
    assert list(tools) == TOOLS
    assert tools["fuel_price"].annotations.read_only_hint is True
    assert set(tools["fuel_price"].input_schema["properties"]) == {"fuel", "lat", "lon", "accuracy_m"}
    assert set(tools["fuel_report"].input_schema["properties"]) == {
        "price", "fuel", "station", "lat", "lon", "accuracy_m",
    }  # fmt: skip
    assert set(tools["fuel_report_confirm"].input_schema["properties"]) == {"token", "only"}
    assert not tools["fuel_report"].annotations.read_only_hint


def test_the_report_tools_tell_the_voice_when_to_call_them():
    tools = {t.name: t.description for t in asyncio.run(_server().list_tools())}
    assert "only when the user states a price they see now" in tools["fuel_report"]
    assert "never call fuel_report_confirm without their clear yes" in tools["fuel_report"]
    assert "Never call it again for the same token" in tools["fuel_report_confirm"]
    assert "never un-sends" in tools["fuel_report_undo"]


def test_a_report_is_saved_and_undone_through_the_tools(tmp_path):
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    reporter = Reporter(FakeSource(now=NOW), Sinks(local, []), now=lambda: NOW)
    server = build_server(FuelService(FakeSource(now=NOW), now=lambda: NOW), reporter)
    saved = _call(server, "fuel_report", price=21.49, lat=0.0, lon=0.0, accuracy_m=10)
    assert saved["say"].startswith("Saved: diesel twenty-one forty-nine at Fake Fuel Gamma")
    confirmed = _call(server, "fuel_report_confirm", token="made-up")  # noqa: S106 - not a secret
    assert confirmed["say"].startswith("There's no sender set up")
    assert _call(server, "fuel_report_undo")["say"].startswith("Taken off your own list")


def test_without_reporting_set_up_the_report_tools_say_so():
    answer = _call(_server(), "fuel_report", price=21.49)
    assert answer == {"success": False, "say": "Reporting a price isn't set up here."}


def test_diesel_is_the_fuel_when_none_is_named():
    answer = _call(_server(), "fuel_price", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Diesel is cheapest at Fake Fuel Beta")
    assert answer["credit"] == CREDIT


def test_the_owner_s_usual_fuel_is_used_when_none_is_named():
    service = FuelService(FakeSource(now=NOW), now=lambda: NOW, choices=Choices(fuel=Fuel.PETROL95))
    answer = _call(build_server(service), "fuel_price", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Petrol 95 is cheapest")
    named = _call(build_server(service), "fuel_price", fuel="diesel", lat=0.0, lon=0.0)
    assert named["say"].startswith("Diesel is cheapest")


def test_the_simulated_answer_says_it_is_simulated():
    answer = _call(_server(simulated=True), "fuel_price", fuel="petrol95", lat=0.0, lon=0.0)
    assert answer["say"].startswith("Simulated: Petrol 95 is cheapest at Fake Fuel Gamma")
    assert answer["simulated"] is True


def test_the_instructions_tell_the_voice_to_pass_the_phone_position():
    instructions = _server().instructions
    assert "lat" in instructions
    assert "accuracy_m" in instructions
    assert "Read 'say' aloud" in instructions
