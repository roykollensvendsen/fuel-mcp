# SPDX-License-Identifier: GPL-3.0-or-later
"""What a fuel price is, and the one seam every data source fits behind.

A source only fetches and translates. The service does all the choosing:
the radius, the cheapest station, the sentence and the age.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import datetime as dt

#: The credit the open data asks for, in every answer and in the README.
CREDIT = "Data: Drivstoffpriser (ODbL), stations © OpenStreetMap contributors"


class Fuel(StrEnum):
    """A fuel, by the name Drivstoffpriser's export gives it."""

    DIESEL = "diesel"
    PETROL95 = "petrol95"
    PETROL98 = "petrol98"

    @property
    def spoken(self) -> str:
        """The fuel as it starts a sentence: "Petrol 95"."""
        return {"diesel": "Diesel", "petrol95": "Petrol 95", "petrol98": "Petrol 98"}[self.value]


@dataclass(frozen=True)
class StationPrice:
    """One station's price for one fuel, as a source reports it."""

    station_id: str
    name: str
    brand: str
    lat: float
    lon: float
    fuel: Fuel
    price: float
    updated_at: dt.datetime
    #: The source's name as it is said: "Drivstoffpriser".
    source: str
    report_count: int = 1


class SourceUnavailableError(Exception):
    """The source could not give prices; the message says why, for a log."""


class PriceSource(Protocol):
    """Where prices come from. One module per source, switched on in local config."""

    #: The source's name as it is said in an answer: "Drivstoffpriser".
    label: str

    def prices_near(self, lat: float, lon: float, radius_km: float, fuel: Fuel) -> list[StationPrice]:
        """Every price for `fuel` within `radius_km` of the point; raises SourceUnavailableError."""
        ...
