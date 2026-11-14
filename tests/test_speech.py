# SPDX-License-Identifier: GPL-3.0-or-later
"""Prices, distances and ages, as the voice says them."""

import datetime as dt

import pytest

from fuel_mcp.speech import spoken_age, spoken_distance, spoken_price

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("price", "said"),
    [
        (19.84, "nineteen eighty-four"),
        (20.05, "twenty oh five"),
        (19.80, "nineteen eighty"),
        (21.00, "twenty-one kroner"),
        (9.99, "nine ninety-nine"),
        (19.849, "nineteen eighty-five"),
    ],
)
def test_a_price_is_said_as_spoken(price, said):
    assert spoken_price(price) == said


@pytest.mark.parametrize(
    ("km", "said"),
    [(0.04, "right here"), (0.26, "300 metres away"), (1.04, "1 kilometre away"), (2.4, "2 kilometres away")],
)
def test_a_distance_is_rounded_as_spoken(km, said):
    assert spoken_distance(km) == said


@pytest.mark.parametrize(
    ("hours", "said"),
    [
        (0.2, "under an hour old"),
        (1.5, "1 hour old"),
        (5, "5 hours old"),
        (30, "about a day old"),
        (24 * 9, "about 9 days old"),
    ],
)
def test_a_price_older_than_a_day_is_said_as_about(hours, said):
    assert spoken_age(NOW - dt.timedelta(hours=hours), NOW) == said
