# SPDX-License-Identifier: GPL-3.0-or-later
"""Fuel prices as an MCP tool, for a voice session.

``fuel_price`` reads only. Every answer has ``say``, to read aloud as it is,
``success``, which a program can read, and ``credit``, the open data's credit.
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
    from fuel_mcp.service import FuelService

INSTRUCTIONS = (
    "Fuel prices near a place, from Drivstoffpriser's open data. Every answer has 'say': "
    "Read 'say' aloud as it is, and never read a list. Call fuel_price with the phone's position "
    "as lat and lon, and its accuracy in metres as accuracy_m, when you have them. Without them "
    "it uses home, or asks where. If the user is or may be driving, say only the one sentence."
)

SIMULATED = "SIMULATED: these are made-up stations, not real ones. Say so when you report on them. "


def build_server(service: FuelService, *, simulated: bool = False) -> MCPServer[Any]:
    """The MCP server with the fuel price tool."""
    server: MCPServer[Any] = MCPServer(
        name="fuel-simulated" if simulated else "fuel",
        instructions=(SIMULATED + INSTRUCTIONS) if simulated else INSTRUCTIONS,
    )

    @server.tool(
        annotations=ToolAnnotations(read_only_hint=True),
        description=(
            "Find the cheapest fuel near a point; changes nothing.\n"
            "Names the cheapest station within about 5 km, its price, how far it is and how old the "
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
        lat: Annotated[float | None, Field(description="The phone's latitude, if known.")] = None,
        lon: Annotated[float | None, Field(description="The phone's longitude, if known.")] = None,
        accuracy_m: Annotated[
            float | None, Field(description="How exact the position is, in metres; 100 if not given.")
        ] = None,
    ) -> dict[str, Any]:
        ask = functools.partial(
            service.price, Fuel(fuel) if fuel else None, lat=lat, lon=lon, accuracy_m=accuracy_m
        )
        answer = await asyncio.to_thread(ask)
        return simulated_answer(answer) if simulated else answer

    return server


def simulated_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """`answer` with "Simulated: " before what is said, so the fake is never taken for real."""
    return {**answer, "say": f"Simulated: {answer['say']}", "simulated": True}
