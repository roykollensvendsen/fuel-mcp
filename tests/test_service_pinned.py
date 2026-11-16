# SPDX-License-Identifier: GPL-3.0-or-later
"""Every way the price question can be answered, pinned word for word before price() was split.

Each case is one branch of FuelService.price: no source, no position, a rough position, a host
that is down, nothing near, only old prices, a widened search, ties, estimates and own reports.
The answers were taken from the code as it was, so a split that changes any of them fails here.
Every station is made up and sits north of the point 0,0, far out at sea.
"""

import datetime as dt

import pytest

from fuel_mcp.model import Fuel, SourceUnavailableError, StationPrice
from fuel_mcp.service import Choices, FuelService

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)
#: Kilometres in one degree of latitude, to place a made-up station a set distance north of 0,0.
KM_PER_DEGREE = 111.195


def _at(name, km, price, *, days=1.0, fuel=Fuel.DIESEL, **more):
    """A made-up station `km` north of 0,0 with a price `days` old."""
    return StationPrice(
        station_id=name,
        name=name,
        brand="",
        lat=km / KM_PER_DEGREE,
        lon=0.0,
        fuel=fuel,
        price=price,
        updated_at=NOW - dt.timedelta(days=days),
        source="Drivstoffpriser",
        **more,
    )


class _Listed:
    """A source that gives back the stations it was made with, or is down."""

    label = "Drivstoffpriser"

    def __init__(self, *stations, down=False):
        self._stations, self._down = stations, down

    def prices_near(self, lat, lon, radius_km, fuel):  # all of them; the service picks
        if self._down:
            msg = "the host did not answer"
            raise SourceUnavailableError(msg)
        return list(self._stations)


def _ask(source, *, home=None, **asked):
    service = FuelService(source, now=lambda: NOW, home=home, choices=Choices(), config_file="CONFIG")
    return service.price(**asked)


HERE = {"lat": 0.0, "lon": 0.0}
#: (case, source, home, question, the answer's 'say', the chosen station's name or None)
CASES = [
    (
        "no source",
        None,
        None,
        HERE,
        (
            "No fuel price source is switched on. To use Drivstoffpriser, add sources = "
            '["drivstoffpriser"] to CONFIG.'
        ),
        None,
    ),
    (
        "no position and no home",
        _Listed(_at("Fake A", 1, 20.0)),
        None,
        {},
        "Where are you? Share your position, or add [home] with lat and lon to CONFIG.",
        None,
    ),
    (
        "home when no position",
        _Listed(_at("Fake A", 1, 20.0)),
        (0.0, 0.0),
        {},
        (
            "Diesel is cheapest at Fake A, twenty kroner, 1 kilometre away. Drivstoffpriser's price "
            "is about a day old."
        ),
        "Fake A",
    ),
    (
        "only a latitude",
        _Listed(_at("Fake A", 1, 20.0)),
        None,
        {"lat": 0.0},
        "Where are you? Share your position, or add [home] with lat and lon to CONFIG.",
        None,
    ),
    (
        "a rough position",
        _Listed(_at("Fake A", 6.5, 20.0)),
        None,
        {**HERE, "accuracy_m": 5000},
        (
            "Your position is rough. Diesel is cheapest at Fake A, twenty kroner, 6 kilometres away. "
            "Drivstoffpriser's price is about a day old."
        ),
        "Fake A",
    ),
    (
        "a negative accuracy",
        _Listed(_at("Fake A", 5.05, 20.0)),
        None,
        {**HERE, "accuracy_m": -50},
        (
            "Nothing within 5 kilometres. Diesel is cheapest at Fake A, twenty kroner, 5 kilometres "
            "away. Drivstoffpriser's price is about a day old."
        ),
        "Fake A",
    ),
    (
        "the host is down",
        _Listed(down=True),
        None,
        HERE,
        "Drivstoffpriser's data isn't available right now. Try again in a few minutes.",
        None,
    ),
    (
        "nothing near",
        _Listed(_at("Fake Far", 25, 20.0)),
        None,
        HERE,
        "I can't see a diesel price within 20 kilometres.",
        None,
    ),
    (
        "another fuel and unbelievable prices only",
        _Listed(
            _at("Fake Petrol", 1, 20.0, fuel=Fuel.PETROL95),
            _at("Fake Low", 1, 9.0),
            _at("Fake High", 1, 41.0),
        ),
        None,
        HERE,
        "I can't see a diesel price within 20 kilometres.",
        None,
    ),
    (
        "only a price from last week or so",
        _Listed(_at("Fake Week", 3, 20.0, days=10)),
        None,
        HERE,
        (
            "No diesel price within 20 kilometres is from the last week. The latest was at Fake "
            "Week, twenty kroner, 3 kilometres away, last reported 22 December 2029."
        ),
        "Fake Week",
    ),
    (
        "only an old price",
        _Listed(_at("Fake Old", 3, 20.0, days=40), _at("Fake Older", 2, 19.0, days=50)),
        None,
        HERE,
        (
            "Only an old price: no diesel price within 20 kilometres in the last 30 days. The latest "
            "was at Fake Old, twenty kroner, 3 kilometres away, last reported 22 November 2029."
        ),
        "Fake Old",
    ),
    (
        "the search steps out",
        _Listed(_at("Fake Ten", 8, 20.0), _at("Fake Stale", 1, 18.0, days=9)),
        None,
        HERE,
        (
            "Nothing within 5 kilometres. Diesel is cheapest at Fake Ten, twenty kroner, 8 "
            "kilometres away. Drivstoffpriser's price is about a day old."
        ),
        "Fake Ten",
    ),
    (
        "a tie goes to the nearer",
        _Listed(_at("Fake Far Tie", 4, 20.001), _at("Fake Near Tie", 2, 20.0)),
        None,
        HERE,
        (
            "Diesel is cheapest at Fake Near Tie, twenty kroner, 2 kilometres away. "
            "Drivstoffpriser's price is about a day old."
        ),
        "Fake Near Tie",
    ),
    (
        "a price from the future is taken as just now",
        _Listed(_at("Fake Future", 1, 20.0, days=-1)),
        None,
        HERE,
        (
            "Diesel is cheapest at Fake Future, twenty kroner, 1 kilometre away. Drivstoffpriser's "
            "price is under an hour old."
        ),
        "Fake Future",
    ),
    (
        "a cheaper estimate is said apart",
        _Listed(_at("Fake A", 1, 20.0), _at("Fake Guess", 2, 18.0, estimate=True)),
        None,
        HERE,
        (
            "Diesel is cheapest at Fake A, twenty kroner, 1 kilometre away. Drivstoffpriser's price "
            "is about a day old. Drivstoffpriser's estimate at Fake Guess is eighteen kroner."
        ),
        "Fake A",
    ),
    (
        "an estimate alone",
        _Listed(_at("Fake Guess", 2, 18.0, estimate=True)),
        None,
        HERE,
        (
            "I can't see a diesel price within 20 kilometres. Drivstoffpriser's estimate at Fake "
            "Guess is eighteen kroner."
        ),
        None,
    ),
    (
        "an own report",
        _Listed(_at("Fake A", 1, 20.0), _at("Fake A", 1, 19.5, days=0.1, own=True)),
        None,
        HERE,
        (
            "Diesel is cheapest at Fake A, nineteen fifty, 1 kilometre away. That's your own report, "
            "2 hours old."
        ),
        "Fake A",
    ),
]


@pytest.mark.parametrize(
    ("source", "home", "asked", "say", "station"), [c[1:] for c in CASES], ids=[c[0] for c in CASES]
)
def test_every_branch_of_the_price_answer_is_pinned(source, home, asked, say, station):
    answer = _ask(source, home=home, fuel=Fuel.DIESEL, **asked)
    assert answer["say"] == say
    assert answer["success"] is (station is not None)
    assert (answer["station"] or {}).get("name") == station
