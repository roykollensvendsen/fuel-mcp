# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheapest station near a point, chosen and said in one sentence.

The position is used for this one call and kept nowhere. The answer names
the station, never the point it was asked from.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import TYPE_CHECKING, Any

from fuel_mcp.geo import distance_km
from fuel_mcp.model import CREDIT, Fuel, PriceSource, SourceUnavailableError, StationPrice
from fuel_mcp.speech import spoken_age, spoken_date, spoken_distance, spoken_name, spoken_price

if TYPE_CHECKING:
    from collections.abc import Callable

#: How far a reading searches around the point, before the position's own accuracy is added.
SEARCH_KM = 5.0
#: The accuracy assumed when the voice gives none; the phone's precise mode reports about this.
DEFAULT_ACCURACY_M = 100.0
#: Kroner per litre a pump could show; a price outside was mistyped, in øre or with a digit too many.
PLAUSIBLE_KR = (10.0, 40.0)
#: How long a price may be said as today's; an older one is said with its date.
CURRENT_FOR = dt.timedelta(days=7)

#: Where the owner's settings are, when nothing more exact is known.
CONFIG_FILE = "~/.config/fuel-mcp/config.toml"
NO_SOURCE = (
    "No fuel price source is switched on. "
    'To use Drivstoffpriser, add sources = ["drivstoffpriser"] to {file}.'
)
WHERE = "Where are you? Share your position, or add [home] with lat and lon to {file}."

Answer = dict[str, Any]


class FuelService:
    """Reads prices from one source and answers in one sentence."""

    def __init__(
        self,
        source: PriceSource | None,
        *,
        now: Callable[[], dt.datetime] | None = None,
        home: tuple[float, float] | None = None,
        config_file: str = CONFIG_FILE,
    ) -> None:
        """`home` is used when the voice gives no position; no `source` means none is switched on."""
        self._source = source
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._home = home
        self._config_file = config_file

    def price(
        self,
        fuel: Fuel = Fuel.DIESEL,
        lat: float | None = None,
        lon: float | None = None,
        accuracy_m: float | None = None,
    ) -> Answer:
        """The cheapest `fuel` near the point, or near home, as an answer to read aloud."""
        if self._source is None:
            return _answer(NO_SOURCE.format(file=self._config_file))
        point = (lat, lon) if lat is not None and lon is not None else self._home
        if point is None:
            return _answer(WHERE.format(file=self._config_file))
        radius_km = SEARCH_KM + (accuracy_m if accuracy_m is not None else DEFAULT_ACCURACY_M) / 1000
        try:
            prices = self._source.prices_near(point[0], point[1], radius_km, fuel)
        except SourceUnavailableError:
            return _answer(f"{self._source.label}'s data isn't available right now.")
        low, high = PLAUSIBLE_KR
        near = [
            (p, distance_km(point[0], point[1], p.lat, p.lon))
            for p in prices
            if p.fuel == fuel and low <= p.price <= high
        ]
        # RULE: a station beyond the search radius is never chosen
        near = [(p, km) for p, km in near if km <= radius_km]
        if not near:
            return _answer(f"I can't see a {fuel.spoken.lower()} price within {round(radius_km)} kilometres.")
        now = self._now()
        # A phone with a fast clock can report a price from the future; it is taken as just now.
        near = [(dataclasses.replace(p, updated_at=min(p.updated_at, now)), km) for p, km in near]
        # RULE: a fuel price older than 7 days is never said as current
        current = [(p, km) for p, km in near if now - p.updated_at <= CURRENT_FOR]
        if not current:
            latest, km = max(near, key=lambda pk: (pk[0].updated_at, -pk[1]))
            name = spoken_name(latest.name, latest.brand)
            say = (
                f"No {fuel.spoken.lower()} price within {round(radius_km)} kilometres is from the last week. "
                f"The latest was at {name}, {spoken_price(latest.price)}, {spoken_distance(km)}, "
                f"last reported {spoken_date(latest.updated_at, now)}."
            )
            return _answer(say, station=_station(latest, km, current=False))
        best, km = min(current, key=lambda pk: (round(pk[0].price, 2), pk[1]))
        name = spoken_name(best.name, best.brand)
        say = (
            f"{fuel.spoken} is cheapest at {name}, {spoken_price(best.price)}, {spoken_distance(km)}. "
            f"{best.source}'s price is {spoken_age(best.updated_at, now)}."
        )
        return _answer(say, station=_station(best, km, current=True))


def _station(price: StationPrice, km: float, *, current: bool) -> dict[str, Any]:
    """The chosen station for a program to read; its coordinates are left out."""
    return {
        "current": current,
        "name": spoken_name(price.name, price.brand),
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
