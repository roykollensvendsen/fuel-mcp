# SPDX-License-Identifier: GPL-3.0-or-later
"""Prices, distances and ages, as the voice reads them aloud.

A price is said the way people say it at the pump: 19.84 is "nineteen
eighty-four", never "nineteen point eight four".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import datetime as dt

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
HOURS_A_DAY = 24
_TEN, _HUNDRED = 10, 100


def number(n: int) -> str:
    """0 to 99 in words: 84 is "eighty-four"."""
    if not 0 <= n < _HUNDRED:
        return str(n)
    if n < len(_ONES):
        return _ONES[n]
    tens, ones = divmod(n, _TEN)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def spoken_price(price: float) -> str:
    """A price per litre as it is said: "nineteen eighty-four"."""
    kroner, ore = divmod(round(price * _HUNDRED), _HUNDRED)
    if ore == 0:
        return f"{number(kroner)} kroner"
    if ore < _TEN:
        return f"{number(kroner)} oh {number(ore)}"
    return f"{number(kroner)} {number(ore)}"


def spoken_distance(km: float) -> str:
    """A distance, rounded to what a driver can use: "300 metres away", "2 kilometres away"."""
    if km < 0.05:  # noqa: PLR2004 - within 50 m is the forecourt itself
        return "right here"
    metres = round(km * 10) * 100
    if km < 1 and metres < 1000:  # noqa: PLR2004
        return f"{metres} metres away"
    whole = max(1, round(km))
    return f"{whole} kilometre{'s' if whole != 1 else ''} away"


def spoken_age(updated_at: dt.datetime, now: dt.datetime) -> str:
    """How old a price is: "5 hours old", or "about 3 days old" once it is past a day."""
    hours = (now - updated_at).total_seconds() / 3600
    # RULE: a price older than a day is said as about
    if hours >= HOURS_A_DAY:
        days = round(hours / HOURS_A_DAY)
        return "about a day old" if days == 1 else f"about {days} days old"
    if hours < 1:
        return "under an hour old"
    whole = int(hours)
    return f"{whole} hour{'s' if whole != 1 else ''} old"
