# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheapest station near a point, chosen and said in one sentence.

The position is used for this one call and kept nowhere. The answer names
the station, never the point it was asked from.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

from fuel_mcp.geo import distance_km
from fuel_mcp.model import CREDIT, Fuel, PriceSource, SourceUnavailableError, StationPrice
from fuel_mcp.speech import spoken_age, spoken_date, spoken_distance, spoken_name, spoken_price

if TYPE_CHECKING:
    from collections.abc import Callable

#: How far a reading searches first, before the position's own accuracy is added. With no
#: current price there, it steps out, doubling each time, up to the farthest the owner set.
SEARCH_KM = 5.0
#: How far the search steps out at most, unless the owner sets farthest_km.
FARTHEST_KM = 20.0
#: How old the latest price may be before it is said as only an old price, unless set.
OLD_AFTER = dt.timedelta(days=30)
#: The accuracy assumed when the voice gives none; the phone's precise mode reports about this.
DEFAULT_ACCURACY_M = 100.0
#: The most a poor position may widen the search. A network fix 50 km out would otherwise search 55 km.
WIDEST_ACCURACY_M = 2000.0
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

#: To stderr, which the voice never hears; stdout carries the MCP conversation.
log = logging.getLogger("fuel_mcp")


@dataclasses.dataclass(frozen=True)
class Choices:
    """The owner's choices: the usual fuel, how far the search steps out, and when a price is old."""

    fuel: Fuel = Fuel.DIESEL
    farthest_km: float = FARTHEST_KM
    old_after_days: int = OLD_AFTER.days


class FuelService:
    """Reads prices from one source and answers in one sentence."""

    def __init__(
        self,
        source: PriceSource | None,
        *,
        now: Callable[[], dt.datetime] | None = None,
        home: tuple[float, float] | None = None,
        config_file: str = CONFIG_FILE,
        choices: Choices | None = None,
    ) -> None:
        """`home` is used when the voice gives no position, and the chosen fuel when it names none.

        No `source` means none is switched on.
        """
        choices = choices or Choices()
        self._fuel = choices.fuel
        self._farthest_km = choices.farthest_km
        self._old_after = dt.timedelta(days=choices.old_after_days)
        self._source = source
        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self._home = home
        self._config_file = config_file

    def _chosen(self, fuel: Fuel | None) -> Fuel:
        """The fuel named, or the owner's usual one when none is."""
        # RULE: the owner's usual fuel is used when none is named
        return fuel or self._fuel

    def price(
        self,
        fuel: Fuel | None = None,
        lat: float | None = None,
        lon: float | None = None,
        accuracy_m: float | None = None,
    ) -> Answer:
        """The cheapest `fuel` near the point, or near home, as an answer to read aloud."""
        fuel = self._chosen(fuel)
        if self._source is None:
            return _answer(NO_SOURCE.format(file=self._config_file))
        point = (lat, lon) if lat is not None and lon is not None else self._home
        if point is None:
            return _answer(WHERE.format(file=self._config_file))
        accuracy = DEFAULT_ACCURACY_M if accuracy_m is None else accuracy_m
        # RULE: a rough position widens the search by two kilometres at most
        widen_m = min(max(accuracy, 0.0), WIDEST_ACCURACY_M)
        rough = "Your position is rough. " if accuracy > WIDEST_ACCURACY_M else ""
        steps = search_steps(self._farthest_km, widen_m / 1000)
        radius_km = steps[-1]
        try:
            prices = self._source.prices_near(point[0], point[1], radius_km, fuel)
        except SourceUnavailableError as error:
            log.warning("%s: %s", self._source.label, error)  # the reason only; never the position
            return _answer(
                f"{self._source.label}'s data isn't available right now. Try again in a few minutes."
            )
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
        found = _first_step(current, steps)
        if found is None:
            latest, km = max(near, key=lambda pk: (pk[0].updated_at, -pk[1]))
            say = self._only_old(fuel, latest, km, radius_km, now)
            return _answer(rough + say, station=_station(latest, km, current=False))
        widened, in_step = found
        best, km = min(in_step, key=lambda pk: (round(pk[0].price, 2), pk[1]))
        name = spoken_name(best.name, best.brand)
        say = (
            widened
            + f"{fuel.spoken} is cheapest at {name}, {spoken_price(best.price)}, {spoken_distance(km)}. "
            f"{best.source}'s price is {spoken_age(best.updated_at, now)}."
        )
        return _answer(rough + say, station=_station(best, km, current=True))

    def _only_old(
        self, fuel: Fuel, latest: StationPrice, km: float, radius_km: float, now: dt.datetime
    ) -> str:
        """The sentence for when no price within `radius_km` is from the last week."""
        name = spoken_name(latest.name, latest.brand)
        fuel_word, within = fuel.spoken.lower(), f"within {round(radius_km)} kilometres"
        # RULE: a price older than the old limit is said as only an old price
        if now - latest.updated_at > self._old_after:
            opening = (
                f"Only an old price: no {fuel_word} price {within} in the last {self._old_after.days} days. "
            )
        else:
            opening = f"No {fuel_word} price {within} is from the last week. "
        return (
            opening + f"The latest was at {name}, {spoken_price(latest.price)}, {spoken_distance(km)}, "
            f"last reported {spoken_date(latest.updated_at, now)}."
        )


def search_steps(farthest_km: float, widen_km: float = 0.0) -> list[float]:
    """The distances searched in turn: 5 km, doubling, and last the farthest the owner set.

    Each is widened by `widen_km`, for a position that is not exact.
    """
    steps, step = [], SEARCH_KM
    while step < farthest_km:
        steps.append(step)
        step *= 2
    # RULE: the search never goes past the farthest distance set
    steps.append(farthest_km)
    return [s + widen_km for s in steps]


def _first_step(
    current: list[tuple[StationPrice, float]], steps: list[float]
) -> tuple[str, list[tuple[StationPrice, float]]] | None:
    """The current prices in the first step that has any, said with how far it had to look."""
    passed = ""
    # RULE: the search steps out until a current price turns up
    for step in steps:
        in_step = [(p, km) for p, km in current if km <= step]
        if in_step:
            return passed, in_step
        passed = f"Nothing within {round(step)} kilometres. "
    return None


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
