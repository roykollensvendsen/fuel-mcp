# SPDX-License-Identifier: GPL-3.0-or-later
"""Made-up stations around the made-up point 0,0, far out at sea.

The fake keeps the real export's limits: some stations lack a fuel, and
prices are hours or a day old. One is ten days old, as so much of the real
export is. Ages are counted back from `now`, so an example's output does not
drift with the date.
"""

from __future__ import annotations

import datetime as dt

from fuel_mcp.geo import distance_km
from fuel_mcp.model import Fuel, SourceUnavailableError, Station, StationPrice

#: How much further than asked the fake looks, as a real source's coarse box would.
_SLACK_KM = 20.0

#: name, lat, lon, then each fuel's (price, hours old).
_STATIONS: tuple[tuple[str, float, float, dict[Fuel, tuple[float, float]]], ...] = (
    ("Alpha", 0.009, 0.0, {Fuel.DIESEL: (19.84, 3), Fuel.PETROL95: (20.49, 3), Fuel.PETROL98: (21.29, 3)}),
    ("Beta", 0.0, 0.018, {Fuel.DIESEL: (19.79, 30), Fuel.PETROL95: (20.49, 30)}),
    ("Gamma", 0.0, -0.002, {Fuel.DIESEL: (20.15, 1), Fuel.PETROL95: (20.49, 1), Fuel.PETROL98: (21.99, 1)}),
    ("Delta", 0.09, 0.0, {Fuel.DIESEL: (18.99, 2), Fuel.PETROL95: (19.99, 2)}),
    # The cheapest diesel nearby, but ten days old, as so much of the real export is.
    ("Epsilon", 0.0, 0.03, {Fuel.DIESEL: (18.49, 240)}),
    # No price at all, and 220 m from Gamma: a report between the two must say which.
    ("Zeta", 0.0, -0.004, {}),
)


class FakeSource:
    """A pretend Drivstoffpriser that touches no network."""

    label = "Drivstoffpriser"

    def __init__(self, *, now: dt.datetime | None = None, failing: bool = False) -> None:
        """Ages count back from `now`; `failing` makes every lookup fail."""
        self._now = now or dt.datetime.now(dt.UTC)
        self._failing = failing

    def prices_near(self, lat: float, lon: float, radius_km: float, fuel: Fuel) -> list[StationPrice]:
        """Every made-up price for `fuel`, at any distance.

        A source may give more than was asked, and this one gives everything
        within a generous box, so the service's own radius is what is tested.
        """
        if self._failing:
            msg = "the fake was told to fail"
            raise SourceUnavailableError(msg)
        found = []
        for name, s_lat, s_lon, fuels in _STATIONS:
            if fuel not in fuels or distance_km(lat, lon, s_lat, s_lon) > radius_km + _SLACK_KM:
                continue
            price, hours = fuels[fuel]
            found.append(
                StationPrice(
                    station_id=f"fake_{name.lower()}",
                    name=f"Fake Fuel {name}",
                    brand="Fake Fuel",
                    lat=s_lat,
                    lon=s_lon,
                    fuel=fuel,
                    price=price,
                    updated_at=self._now - dt.timedelta(hours=hours),
                    source=self.label,
                )
            )
        return found

    def stations(self) -> list[Station]:
        """Every made-up station, priced or not."""
        if self._failing:
            msg = "the fake was told to fail"
            raise SourceUnavailableError(msg)
        return [
            Station(f"fake_{name.lower()}", f"Fake Fuel {name}", "Fake Fuel", s_lat, s_lon)
            for name, s_lat, s_lon, _ in _STATIONS
        ]
