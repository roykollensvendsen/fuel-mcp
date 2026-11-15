# SPDX-License-Identifier: GPL-3.0-or-later
"""Fuel prices as MCP tools, for a voice session.

``fuel_price`` reads only. ``fuel_report`` saves the owner's own report on this
machine, ``fuel_report_undo`` takes it back, and ``fuel_report_confirm`` sends it
on, only after his yes. Every answer has ``say``, to read aloud as it is, and
``success``, which a program can read. A price answer also has ``credit``.
"""

from __future__ import annotations

import asyncio
import functools
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from fuel_mcp.model import Fuel

if TYPE_CHECKING:
    from fuel_mcp.reports import Reporter
    from fuel_mcp.service import FuelService

INSTRUCTIONS = (
    "Fuel prices near a place, from Drivstoffpriser's open data. Every answer has 'say': "
    "Read 'say' aloud as it is, and never read a list. Call fuel_price with the phone's position "
    "as lat and lon, and its accuracy in metres as accuracy_m, when you have them. Without them "
    "it uses home, or asks where. If the user is or may be driving, say only the one sentence. "
    "When the user states a pump price, call fuel_report with the position. Call "
    "fuel_report_confirm only on their clear yes to its question, once."
)

#: The phone's position, as both the price and the report tools take it.
Lat = Annotated[float | None, Field(description="The phone's latitude, if known.")]
Lon = Annotated[float | None, Field(description="The phone's longitude, if known.")]
Accuracy = Annotated[
    float | None, Field(description="How exact the position is, in metres; 100 if not given.")
]

SIMULATED = "SIMULATED: these are made-up stations, not real ones. Say so when you report on them. "


def build_server(
    service: FuelService, reporter: Reporter | None = None, *, simulated: bool = False
) -> MCPServer[Any]:
    """The MCP server with the fuel price tool and the report tools; no `reporter`, no reporting."""
    server: MCPServer[Any] = MCPServer(
        name="fuel-simulated" if simulated else "fuel",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True),
        description=(
            "Find the cheapest fuel near a point; changes nothing.\n"
            "Names the cheapest station within 5 km, looking further (20 km unless the owner set "
            "another) when none has a current price. Says its price, how far it is and how old the "
            "price is, in one sentence. Read 'say' aloud."
        ),
    )
    async def fuel_price(
        fuel: Annotated[
            Literal["diesel", "petrol95", "petrol98"] | None,
            Field(
                description="The fuel: diesel, petrol95 or petrol98. Leave it out when the user names none."
            ),
        ] = None,
        lat: Lat = None,
        lon: Lon = None,
        accuracy_m: Accuracy = None,
    ) -> dict[str, Any]:
        ask = functools.partial(
            service.price, Fuel(fuel) if fuel else None, lat=lat, lon=lon, accuracy_m=accuracy_m
        )
        answer = await asyncio.to_thread(ask)
        return simulated_answer(answer) if simulated else answer

    _report_tools(server, reporter, simulated=simulated)
    return server


Fuels = Literal["diesel", "petrol95", "petrol98"]
NOT_SET_UP = {"success": False, "say": "Reporting a price isn't set up here."}


def _report_tools(server: MCPServer[Any], reporter: Reporter | None, *, simulated: bool) -> None:
    """The three report tools. They are always offered, so the voice's list never changes."""

    def said(answer: dict[str, Any]) -> dict[str, Any]:
        return simulated_answer(answer) if simulated else answer

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False),
        description=(
            "Save a pump price as the user's own report, on this machine only.\n"
            "Call it only when the user states a price they see now. It saves at once, names the "
            "station and says how to undo it. When it asks 'Send it to … too?', never call "
            "fuel_report_confirm without their clear yes. Read 'say' aloud."
        ),
    )
    async def fuel_report(  # noqa: PLR0913, PLR0917 - the arguments the voice sends
        price: Annotated[float, Field(description="The price per litre the user says, in kroner.")],
        fuel: Annotated[
            Fuels | None, Field(description="diesel, petrol95 or petrol98. Leave it out when none is named.")
        ] = None,
        station: Annotated[str | None, Field(description="The station's name, if the user says one.")] = None,
        lat: Lat = None,
        lon: Lon = None,
        accuracy_m: Accuracy = None,
    ) -> dict[str, Any]:
        if reporter is None:
            return NOT_SET_UP
        ask = functools.partial(
            reporter.report,
            price,
            Fuel(fuel) if fuel else None,
            station,
            lat=lat,
            lon=lon,
            accuracy_m=accuracy_m,
        )
        return said(await asyncio.to_thread(ask))

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False),
        description=(
            "Take the user's latest own report off their list, if it is under 10 minutes old.\n"
            "Call it when the user says undo, or to take it back. It never un-sends a report "
            "already sent. Read 'say' aloud."
        ),
    )
    async def fuel_report_undo() -> dict[str, Any]:
        if reporter is None:
            return NOT_SET_UP
        return said(await asyncio.to_thread(reporter.undo))

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True),
        description=(
            "Send the report just saved to the services its question named, after the user's yes.\n"
            "Call it only on a plain yes, or a clear 'only <service>', given as only. Anything "
            "else means no: don't call it. Never call it again for the same token. Read 'say' aloud."
        ),
    )
    async def fuel_report_confirm(
        token: Annotated[str, Field(description="The token from fuel_report's question.")],
        only: Annotated[
            list[str] | None, Field(description="The services the user narrowed it to, if they said 'only'.")
        ] = None,
    ) -> dict[str, Any]:
        if reporter is None:
            return NOT_SET_UP
        return said(await asyncio.to_thread(reporter.confirm, token, only))


def simulated_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """`answer` with "Simulated: " before what is said, so the fake is never taken for real."""
    return {**answer, "say": f"Simulated: {answer['say']}", "simulated": True}
