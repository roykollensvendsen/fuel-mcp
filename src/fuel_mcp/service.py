# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheapest station near a point, chosen and said in one sentence.

The position is used for this one call and kept nowhere. The answer names
the station, never the point it was asked from.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from fuel_mcp.geo import distance_km
from fuel_mcp.model import CREDIT, Fuel, PriceSource, SourceUnavailableError, StationPrice
from fuel_mcp.speech import spoken_age, spoken_distance, spoken_price

if TYPE_CHECKING:
    from collections.abc import Callable

#: How far a reading searches around the point, before the position's own accuracy is added.
SEARCH_KM = 5.0
#: The accuracy assumed when the voice gives none; the phone's precise mode reports about this.
DEFAULT_ACCURACY_M = 100.0

NO_SOURCE = "No fuel price source is switched on; see the fuel plugin's README."
WHERE = "Where are you? Share your position, or set home in the fuel plugin's config."

Answer = dict[str, Any]


class FuelService:
    """Reads prices from one source and answers in one sentence."""

    def __init__(
        self,
        source: PriceSource | None,
        *,
        now: Callable[[], dt.datetime] | None = None,
        home: tuple[float, float] | None = None,
    ) -> None:
        """`home` is used when the voice gives no position; no `source` means none is switched on."""
        self._source = source
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._home = home

    def price(
        self,
        fuel: Fuel = Fuel.DIESEL,
        lat: float | None = None,
        lon: float | None = None,
        accuracy_m: float | None = None,
    ) -> Answer:
        """The cheapest `fuel` near the point, or near home, as an answer to read aloud."""
        if self._source is None:
            return _answer(NO_SOURCE)
        point = (lat, lon) if lat is not None and lon is not None else self._home
        if point is None:
            return _answer(WHERE)
        radius_km = SEARCH_KM + (accuracy_m if accuracy_m is not None else DEFAULT_ACCURACY_M) / 1000
        try:
            prices = self._source.prices_near(point[0], point[1], radius_km, fuel)
        except SourceUnavailableError:
            return _answer(f"{self._source.label}'s data isn't available right now.")
        near = [(p, distance_km(point[0], point[1], p.lat, p.lon)) for p in prices if p.fuel == fuel]
        # RULE: a station beyond the search radius is never chosen
        near = [(p, km) for p, km in near if km <= radius_km]
        if not near:
            return _answer(f"I can't see a {fuel.spoken.lower()} price within {round(radius_km)} kilometres.")
        best, km = min(near, key=lambda pk: (round(pk[0].price, 2), pk[1]))
        say = (
            f"{fuel.spoken} is cheapest at {best.name}, {spoken_price(best.price)}, {spoken_distance(km)}. "
            f"{best.source}'s price is {spoken_age(best.updated_at, self._now())}."
        )
        return _answer(say, station=_station(best, km))


def _station(price: StationPrice, km: float) -> dict[str, Any]:
    """The chosen station for a program to read; its coordinates are left out."""
    return {
        "name": price.name,
        "brand": price.brand,
        "fuel": price.fuel.value,
        "price": price.price,
        "distance_km": round(km, 1),
        "updated_at": price.updated_at.isoformat(),
        "report_count": price.report_count,
        "source": price.source,
    }


def _answer(say: str, station: dict[str, Any] | None = None) -> Answer:
    return {"success": station is not None, "say": say, "station": station, "credit": CREDIT}
