# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheapest station near a point, chosen and said in one sentence.

Every station here is made up and sits near the point 0,0, far out at sea.
"""

import dataclasses
import datetime as dt

from fuel_mcp.model import CREDIT, Fuel
from fuel_mcp.service import FuelService
from fuel_mcp.simulator import FakeSource

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


def _service(source=None, home=None):
    return FuelService(source or FakeSource(now=NOW), now=lambda: NOW, home=home)


def test_diesel_near_the_point_names_the_cheapest_station_its_price_distance_and_age():
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["success"] is True
    assert answer["say"] == (
        "Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine, 2 kilometres away. "
        "Drivstoffpriser's price is about a day old."
    )
    assert answer["station"]["name"] == "Fake Fuel Beta"
    assert answer["station"]["price"] == 19.79


def test_every_answer_carries_the_data_credit():
    assert CREDIT == "Data: Drivstoffpriser (ODbL), stations © OpenStreetMap contributors"
    assert _service().price(Fuel.DIESEL, lat=0.0, lon=0.0)["credit"] == CREDIT
    assert _service().price(Fuel.DIESEL)["credit"] == CREDIT


def test_a_station_beyond_the_search_radius_is_never_chosen():
    # Fake Fuel Delta is the cheapest of all, but 10 km away.
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert "Delta" not in answer["say"]


def test_a_poor_position_widens_the_search_by_its_accuracy():
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=6000)
    assert answer["station"]["name"] == "Fake Fuel Delta"


def test_a_fuel_a_station_does_not_sell_is_skipped():
    # Beta sells no 98, so the cheapest 98 is elsewhere.
    answer = _service().price(Fuel.PETROL98, lat=0.0, lon=0.0)
    assert answer["say"].startswith("Petrol 98 is cheapest at Fake Fuel Alpha, ")


def test_two_stations_at_the_same_price_give_the_nearer_one():
    source = FakeSource(now=NOW)
    answer = _service(source).price(Fuel.PETROL95, lat=0.0, lon=0.0)
    assert answer["station"]["name"] == "Fake Fuel Gamma"


def test_without_a_position_it_uses_home_from_local_config():
    answer = _service(home=(0.0, 0.0)).price(Fuel.DIESEL)
    assert answer["station"]["name"] == "Fake Fuel Beta"


def test_without_a_position_or_home_it_asks_where():
    answer = _service().price(Fuel.DIESEL)
    assert answer["success"] is False
    assert answer["say"] == "Where are you? Share your position, or set home in the fuel plugin's config."


def test_no_station_nearby_says_so_and_names_none():
    answer = _service().price(Fuel.DIESEL, lat=1.0, lon=1.0)
    assert answer["success"] is False
    assert answer["say"] == "I can't see a diesel price within 5 kilometres."
    assert answer["station"] is None


def test_a_source_that_fails_is_said_plainly():
    answer = _service(FakeSource(now=NOW, failing=True)).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["success"] is False
    assert answer["say"] == "Drivstoffpriser's data isn't available right now."


def test_the_answer_never_holds_the_position_it_was_given():
    answer = _service().price(Fuel.DIESEL, lat=0.0012345, lon=0.0054321)
    assert "0.0012345" not in str(answer)
    assert "0.0054321" not in str(answer)


class _OnlyOld:
    """A source whose every nearby price is weeks old, as the real export's often are."""

    label = "Drivstoffpriser"

    def prices_near(self, lat, lon, radius_km, fuel):
        made_up = FakeSource(now=NOW - dt.timedelta(days=40)).prices_near(lat, lon, radius_km, fuel)
        newest = FakeSource(now=NOW - dt.timedelta(days=12)).prices_near(lat, lon, radius_km, fuel)[:1]
        return made_up + [dataclasses.replace(p, name="Fake Fuel Newest") for p in newest]


def test_a_fuel_price_older_than_7_days_is_never_said_as_current():
    # Fake Fuel Epsilon has the lowest diesel price nearby, but it is ten days old.
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert "Epsilon" not in answer["say"]
    assert answer["station"]["current"] is True


def test_with_only_old_prices_nearby_the_latest_is_given_with_its_date():
    answer = _service(_OnlyOld()).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["say"] == (
        "No diesel price within 5 kilometres is from the last week. "
        "The latest was at Fake Fuel Newest, nineteen eighty-four, 1 kilometre away, "
        "last reported 20 December 2029."
    )
    assert answer["success"] is True
    assert answer["station"]["current"] is False


def test_a_date_this_year_is_said_without_the_year():
    from fuel_mcp.speech import spoken_date  # noqa: PLC0415

    assert spoken_date(dt.datetime(2030, 5, 15, 9, tzinfo=dt.UTC), NOW) == "15 May"
    assert spoken_date(dt.datetime(2029, 12, 17, 9, tzinfo=dt.UTC), NOW) == "17 December 2029"
