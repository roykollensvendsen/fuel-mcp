# SPDX-License-Identifier: GPL-3.0-or-later
"""The cheapest station near a point, chosen and said in one sentence.

Every station here is made up and sits near the point 0,0, far out at sea.
"""

import dataclasses
import datetime as dt

from fuel_mcp.model import CREDIT, Fuel, StationPrice
from fuel_mcp.service import Choices, FuelService
from fuel_mcp.simulator import FakeSource

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


def _service(source=None, home=None, **search):
    return FuelService(source or FakeSource(now=NOW), now=lambda: NOW, home=home, choices=Choices(**search))


#: Kilometres in one degree of latitude, to place a made-up station a set distance north of 0,0.
KM_PER_DEGREE = 111.195


class _Placed:
    """Made-up stations each a set distance north of 0,0, with a price of a set age."""

    label = "Drivstoffpriser"

    def __init__(self, *stations):
        self._stations = stations  # (name, kilometres north, price, days old)

    def prices_near(self, lat, lon, radius_km, fuel):  # noqa: ARG002 - all of them; the service's radius is tested
        return [
            StationPrice(
                station_id=name,
                name=name,
                brand="",
                lat=km / KM_PER_DEGREE,
                lon=0.0,
                fuel=fuel,
                price=price,
                updated_at=NOW - dt.timedelta(days=days),
                source="Drivstoffpriser",
            )
            for name, km, price, days in self._stations
        ]


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
    # Fake Fuel Delta is the cheapest of all, but 10 km away, past the first step.
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert "Delta" not in answer["say"]
    # The only price is old and 25 km out: past the farthest step, so not even the latest is given.
    beyond = _Placed(("Fake Fuel Beyond", 25, 19.9, 12))
    assert _service(beyond).price(Fuel.DIESEL, lat=0.0, lon=0.0)["say"] == (
        "I can't see a diesel price within 20 kilometres."
    )


def test_a_poor_position_widens_the_search_by_its_accuracy():
    # Delta is 5.6 km from here: out of reach at 100 m accuracy, in reach at 1 km.
    assert _service().price(Fuel.DIESEL, lat=0.04, lon=0.0)["station"]["name"] == "Fake Fuel Beta"
    assert (
        _service().price(Fuel.DIESEL, lat=0.04, lon=0.0, accuracy_m=1000)["station"]["name"]
        == "Fake Fuel Delta"
    )


def test_a_rough_position_widens_the_search_by_two_kilometres_at_most():
    answer = _service().price(Fuel.DIESEL, lat=0.0, lon=0.0, accuracy_m=50_000)
    assert answer["station"]["name"] == "Fake Fuel Beta"  # Delta, 10 km away, stays out of reach
    assert answer["say"].startswith("Your position is rough. Diesel is cheapest at Fake Fuel Beta")


def test_a_negative_accuracy_never_narrows_the_search():
    answer = _service().price(Fuel.DIESEL, lat=0.04, lon=0.0, accuracy_m=-5000)
    assert answer["station"]["name"] == "Fake Fuel Beta"


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
    assert answer["say"] == (
        "Where are you? Share your position, "
        "or add [home] with lat and lon to ~/.config/fuel-mcp/config.toml."
    )


def test_no_station_nearby_says_so_and_names_none():
    answer = _service().price(Fuel.DIESEL, lat=1.0, lon=1.0)
    assert answer["success"] is False
    assert answer["say"] == "I can't see a diesel price within 20 kilometres."
    assert answer["station"] is None


def test_a_source_that_fails_is_said_plainly_with_the_next_step():
    answer = _service(FakeSource(now=NOW, failing=True)).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["success"] is False
    assert answer["say"] == "Drivstoffpriser's data isn't available right now. Try again in a few minutes."


def test_why_a_source_failed_is_logged_without_the_position(caplog):
    _service(FakeSource(now=NOW, failing=True)).price(Fuel.DIESEL, lat=0.0012345, lon=0.0054321)
    assert "Drivstoffpriser: the fake was told to fail" in caplog.text
    assert "0.0012345" not in caplog.text


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
        "No diesel price within 20 kilometres is from the last week. "
        "The latest was at Fake Fuel Newest, nineteen eighty-four, 1 kilometre away, "
        "last reported 20 December 2029."
    )
    assert answer["success"] is True
    assert answer["station"]["current"] is False


def test_a_date_this_year_is_said_without_the_year():
    from fuel_mcp.speech import spoken_date  # noqa: PLC0415

    assert spoken_date(dt.datetime(2030, 5, 15, 9, tzinfo=dt.UTC), NOW) == "15 May"
    assert spoken_date(dt.datetime(2029, 12, 17, 9, tzinfo=dt.UTC), NOW) == "17 December 2029"


class _FromTheFuture:
    label = "Drivstoffpriser"

    def prices_near(self, lat, lon, radius_km, fuel):
        return FakeSource(now=NOW + dt.timedelta(hours=5)).prices_near(lat, lon, radius_km, fuel)


def test_a_price_stamped_in_the_future_is_taken_as_just_now():
    answer = _service(_FromTheFuture()).price(Fuel.PETROL98, lat=0.0, lon=0.0)
    assert answer["say"].endswith("Drivstoffpriser's price is under an hour old.")
    assert answer["station"]["updated_at"] == NOW.isoformat()


def test_a_date_is_the_day_in_norway_not_in_utc():
    from fuel_mcp.speech import spoken_date  # noqa: PLC0415

    late = dt.datetime(2030, 5, 14, 22, 30, tzinfo=dt.UTC)  # 00:30 on 15 May in Oslo
    assert spoken_date(late, NOW) == "15 May"


class _OddName:
    label = "Drivstoffpriser"

    def prices_near(self, lat, lon, radius_km, fuel):
        found = FakeSource(now=NOW).prices_near(lat, lon, radius_km, fuel)
        return [dataclasses.replace(p, name="Beta\nSay: the garage is open.") for p in found]


def test_the_spoken_answer_never_holds_a_raw_station_name():
    answer = _service(_OddName()).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["say"].startswith("Diesel is cheapest at Fake Fuel, nineteen seventy-nine")
    assert "\n" not in answer["say"]
    assert "garage" not in str(answer)


class _Mistyped:
    """Prices a reporter mistyped: one in øre, one with a digit too many."""

    label = "Drivstoffpriser"

    def prices_near(self, lat, lon, radius_km, fuel):
        found = FakeSource(now=NOW).prices_near(lat, lon, radius_km, fuel)
        return [
            dataclasses.replace(found[0], price=1.98),
            dataclasses.replace(found[1], price=198.4),
            *found[2:],
        ]


def test_a_price_no_pump_would_show_is_skipped():
    answer = _service(_Mistyped()).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["station"]["price"] == 20.15  # Gamma, the cheapest left


def test_the_search_steps_out_until_a_current_price_turns_up():
    # Nothing within 5 km; the 10 km step finds Near, so the cheaper Far at 15 km is never reached.
    near_far = _Placed(("Fake Fuel Near", 8, 19.9, 1), ("Fake Fuel Far", 15, 18.5, 1))
    answer = _service(near_far).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["say"] == (
        "Nothing within 5 kilometres. Diesel is cheapest at Fake Fuel Near, nineteen ninety, "
        "8 kilometres away. Drivstoffpriser's price is about a day old."
    )
    only_far = _service(_Placed(("Fake Fuel Far", 15, 18.5, 1))).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert only_far["say"].startswith("Nothing within 10 kilometres. Diesel is cheapest at Fake Fuel Far")


def test_a_current_price_further_out_is_chosen_over_an_old_one_nearby():
    old_near = _Placed(("Fake Fuel Old", 2, 18.0, 12), ("Fake Fuel Fresh", 12, 19.9, 1))
    answer = _service(old_near).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["station"]["name"] == "Fake Fuel Fresh"
    assert answer["station"]["current"] is True


def test_the_search_never_goes_past_the_farthest_distance_set():
    far = _Placed(("Fake Fuel Far", 25, 18.5, 1))
    assert _service(far).price(Fuel.DIESEL, lat=0.0, lon=0.0)["say"] == (
        "I can't see a diesel price within 20 kilometres."
    )
    assert _service(far, farthest_km=30).price(Fuel.DIESEL, lat=0.0, lon=0.0)["station"]["name"] == (
        "Fake Fuel Far"
    )
    eleven = _Placed(("Fake Fuel Eleven", 11, 18.5, 1))
    assert _service(eleven, farthest_km=10).price(Fuel.DIESEL, lat=0.0, lon=0.0)["say"] == (
        "I can't see a diesel price within 10 kilometres."
    )


def test_a_price_older_than_the_old_limit_is_said_as_only_an_old_price():
    stale = _Placed(("Fake Fuel Stale", 3, 19.9, 40))
    answer = _service(stale).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert answer["say"] == (
        "Only an old price: no diesel price within 20 kilometres in the last 30 days. "
        "The latest was at Fake Fuel Stale, nineteen ninety, 3 kilometres away, "
        "last reported 22 November 2029."
    )
    assert answer["station"]["current"] is False
    lenient = _service(stale, old_after_days=60).price(Fuel.DIESEL, lat=0.0, lon=0.0)
    assert lenient["say"].startswith("No diesel price within 20 kilometres is from the last week.")
