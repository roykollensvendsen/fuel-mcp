# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's local settings: home, the usual fuel, and which price sources are switched on.

They live in ``$XDG_CONFIG_HOME/fuel-mcp/config.toml``, normally
``~/.config/fuel-mcp/config.toml``, and never in the repository:

    sources = ["drivstoffpriser"]   # none is on until it is listed here
    fuel = "petrol95"               # when a question names none; diesel if not set
    farthest_km = 20                # how far the search steps out, 1 to 100 km; 20 if not set
    old_after_days = 30             # older than this is said as only an old price, 7 to 3650; 30
    sinks = ["local"]               # where own reports go; "local" is always among them
    own_report_hours = 12           # how long an own report counts, 1 to 168 hours; 12
    own_report_keep_hours = 12      # how long it is kept, from the hours above to 2160; 12
    price_low = 10                  # a price below this is not believed, per litre; 10
    price_high = 40                 # a price above this is not believed; 40
    send_timeout_s = 20             # how long all the sends after one yes may take, 1 to 25 seconds; 20
    [home]                          # used when the voice gives no position
    lat = 0.0
    lon = 0.0
    [[private_sinks]]               # only for the owner's own sender; see fuel_mcp.sinks
    folder = "~/src/fuel-mcp-private"
    sink = "fuel_mcp_private.sinks.example:ExampleSink"
    [station_names]                 # the owner's own names for stations, matched first
    "my usual" = "osm_1"            # a station's id from the source's list, made up here

A source not listed is fully off: its module is never imported. A private sink
is only listed here; nothing of it is loaded while the settings are read.
"""

from __future__ import annotations

import importlib
import logging
import math
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fuel_mcp.model import Fuel
from fuel_mcp.service import Choices
from fuel_mcp.sinks import PrivateSinkEntry

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fuel_mcp.model import PriceSource

#: Each source by its name in config, and the module that holds it.
KNOWN_SOURCES = {"drivstoffpriser": ("fuel_mcp.sources.drivstoffpriser", "DrivstoffpriserSource")}
# RULE: no price source is on until the owner lists one
DEFAULT_SOURCES: tuple[str, ...] = ()


class SettingsError(Exception):
    """The settings cannot be used; the message names the file and the fix."""


@dataclass(frozen=True)
class Settings:
    """Where home is, the usual fuel, and which sources to ask. Home never appears in a repr."""

    home: tuple[float, float] | None = field(default=None, repr=False)
    sources: tuple[str, ...] = DEFAULT_SOURCES
    fuel: Fuel = Fuel.DIESEL
    farthest_km: float = Choices.farthest_km
    old_after_days: int = Choices.old_after_days
    sinks: tuple[str, ...] = ("local",)
    own_report_hours: int = 12
    own_report_keep_hours: int = 12
    price_low: float = 10.0
    price_high: float = 40.0
    send_timeout_s: float = 20.0
    #: The owner's own names for stations: they say where he goes, so never in a repr.
    station_names: Mapping[str, str] = field(default_factory=dict, repr=False)
    #: The owner's own senders, as listed; their folders are private, so never in a repr.
    private_sinks: tuple[PrivateSinkEntry, ...] = field(default=(), repr=False)


def _folder(env: Mapping[str, str], variable: str, fallback: str) -> pathlib.Path:
    base = env.get(variable) or str(pathlib.Path(env.get("HOME", "~")) / fallback)
    return pathlib.Path(base).expanduser() / "fuel-mcp"


def config_folder(env: Mapping[str, str]) -> pathlib.Path:
    """The settings folder, following XDG_CONFIG_HOME."""
    return _folder(env, "XDG_CONFIG_HOME", ".config")


def cache_folder(env: Mapping[str, str]) -> pathlib.Path:
    """Where the copy of the export is kept, following XDG_CACHE_HOME."""
    return _folder(env, "XDG_CACHE_HOME", ".cache")


def state_folder(env: Mapping[str, str]) -> pathlib.Path:
    """Where the owner's own reports are kept, following XDG_STATE_HOME."""
    return _folder(env, "XDG_STATE_HOME", ".local/state")


def load_settings(folder: pathlib.Path) -> Settings:
    """Read the settings in `folder`; raises SettingsError naming what to fix."""
    path = folder / "config.toml"
    try:
        if path.is_file():
            with path.open("rb") as file:  # bytes, so the bridge's missing locale cannot misread it
                table = tomllib.load(file)
        else:
            table = {}
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        msg = f"{path} is not valid TOML. Fix it, or move it aside to use the defaults: mv {path} {path}.bad"
        raise SettingsError(msg) from None
    sources = table.get("sources", list(DEFAULT_SOURCES))
    if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
        msg = f'sources in {path} must be a list of names, such as: sources = ["drivstoffpriser"]'
        raise SettingsError(msg)
    for name in sources:
        if name not in KNOWN_SOURCES:
            known = ", ".join(KNOWN_SOURCES)
            msg = f"unknown source '{name}'; the known ones are {known}. Fix sources in {path}"
            raise SettingsError(msg)
    if len(sources) > 1:
        msg = (
            f"only one source is supported until merging is written; keep one of: {', '.join(sources)}. "
            f"Fix sources in {path}"
        )
        raise SettingsError(msg)
    return Settings(
        home=_home(table.get("home"), path),
        sources=tuple(sources),
        fuel=_fuel(table, path),
        farthest_km=float(_within(table, "farthest_km", FARTHEST_KM_LIMITS, path, whole=False)),
        old_after_days=int(_within(table, "old_after_days", OLD_AFTER_DAYS_LIMITS, path, whole=True)),
        **_reports(table, path),
    )


#: The farthest search an owner may set, in km. Past 100 the nearest price is no answer to a driver.
FARTHEST_KM_LIMITS = (1, 100)
#: The old-price limit an owner may set, in days. Under 7 nothing would be called old, since a
#: price is current for a week; past ten years the limit means nothing.
OLD_AFTER_DAYS_LIMITS = (7, 3650)
#: How long an own report may count, in hours: past a week a pump price is long gone.
OWN_REPORT_HOURS_LIMITS = (1, 168)
#: How long it may be kept, in hours: up to 90 days, for a "what did I pay" not built yet.
OWN_REPORT_KEEP_HOURS_LIMITS = (1, 2160)
#: The believable price bounds an owner may set, per litre.
PRICE_LIMITS = (1, 100)
#: How long all the sends after one yes may take together, in seconds: below the voice's 30 s.
SEND_TIMEOUT_LIMITS = (1, 25)
#: Each number's default when the owner sets none.
DEFAULTS: dict[str, float] = {
    "farthest_km": Choices.farthest_km,
    "old_after_days": Choices.old_after_days,
    "own_report_hours": 12,
    "own_report_keep_hours": 12,
    "price_low": 10,
    "price_high": 40,
    "send_timeout_s": 20,
}
#: Every sink the public package has. A private sender goes in [[private_sinks]].
KNOWN_SINKS = ("local",)
log = logging.getLogger("fuel_mcp")


def _reports(table: Mapping[str, object], path: pathlib.Path) -> dict[str, Any]:
    """The settings for the owner's own reports, each checked, as Settings fields."""
    hours = int(_within(table, "own_report_hours", OWN_REPORT_HOURS_LIMITS, path, whole=True))
    keep = int(_within(table, "own_report_keep_hours", OWN_REPORT_KEEP_HOURS_LIMITS, path, whole=True))
    # RULE: an own report is kept at least as long as it counts
    if keep < hours:
        msg = f"own_report_keep_hours must be at least own_report_hours, {hours}. Fix it in {path}"
        raise SettingsError(msg)
    low = float(_within(table, "price_low", PRICE_LIMITS, path, whole=False))
    high = float(_within(table, "price_high", PRICE_LIMITS, path, whole=False))
    # RULE: the lowest believable price is below the highest
    if low >= high:
        msg = f"price_low must be below price_high, such as 10 and 40. Fix them in {path}"
        raise SettingsError(msg)
    return {
        "sinks": _sinks(table, path),
        "own_report_hours": hours,
        "own_report_keep_hours": keep,
        "price_low": low,
        "price_high": high,
        "send_timeout_s": float(_within(table, "send_timeout_s", SEND_TIMEOUT_LIMITS, path, whole=False)),
        "station_names": _station_names(table.get("station_names", {}), path),
        "private_sinks": _private_sinks(table.get("private_sinks", []), path),
    }


def _sinks(table: Mapping[str, object], path: pathlib.Path) -> tuple[str, ...]:
    """The sinks listed, with "local" always among them."""
    sinks = table.get("sinks", list(KNOWN_SINKS))
    if not isinstance(sinks, list) or not all(isinstance(s, str) for s in sinks):
        msg = f'sinks in {path} must be a list of names, such as: sinks = ["local"]'
        raise SettingsError(msg)
    for name in sinks:
        if name not in KNOWN_SINKS:
            msg = (
                f"unknown sink '{name}'; the known one is local. A sender goes in [[private_sinks]] in {path}"
            )
            raise SettingsError(msg)
    # RULE: "local" is always among the sinks
    if "local" not in sinks:
        log.warning("fuel-mcp: sinks in %s leaves out local; it is always on, so it was added", path)
        sinks = ["local", *sinks]
    return tuple(dict.fromkeys(sinks))


def _station_names(names: object, path: pathlib.Path) -> dict[str, str]:
    """The owner's own names for stations, each a name and a station id, both text."""
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        msg = (
            f'[station_names] in {path} maps a name to a station id, written as: "my usual" = "<station id>"'
        )
        raise SettingsError(msg)
    return {str(k): v for k, v in names.items()}


def _private_sinks(entries: object, path: pathlib.Path) -> tuple[PrivateSinkEntry, ...]:
    """The [[private_sinks]] listed, read only: nothing of them is loaded here."""
    if not isinstance(entries, list) or not all(_an_entry(e) for e in entries):
        msg = (
            f'each [[private_sinks]] in {path} needs exactly folder = "<a folder>" '
            'and sink = "fuel_mcp_private.<module>:<Class>"'
        )
        raise SettingsError(msg)
    return tuple(PrivateSinkEntry(pathlib.Path(e["folder"]).expanduser(), e["sink"]) for e in entries)


def _an_entry(entry: object) -> bool:
    """A [[private_sinks]] table: a folder and a sink, both text, and nothing else."""
    return (
        isinstance(entry, dict)
        and set(entry) == {"folder", "sink"}
        and all(isinstance(v, str) for v in entry.values())
    )


def _within(
    table: Mapping[str, object], key: str, limits: tuple[int, int], path: pathlib.Path, *, whole: bool
) -> float:
    """The number set for `key`, or its default; anything outside `limits` names the fix.

    TOML allows inf and nan. Either would pass a plain "above 0": inf would step out for ever.
    """
    default = DEFAULTS[key]
    value = table.get(key, default)
    kinds = (int,) if whole else (int, float)
    number = value if isinstance(value, kinds) and not isinstance(value, bool) else math.nan
    low, high = limits
    # RULE: a search setting outside its limits names the fix and the file
    if not (math.isfinite(number) and low <= number <= high):
        kind = "a whole number" if whole else "a number"
        msg = f"{key} must be {kind} from {low} to {high}, such as {key} = {default:g}. Fix {key} in {path}"
        raise SettingsError(msg)
    return number


def _fuel(table: Mapping[str, object], path: pathlib.Path) -> Fuel:
    """The fuel a question means when it names none; diesel unless the owner chose another."""
    chosen = table.get("fuel", Fuel.DIESEL.value)
    known = [f.value for f in Fuel]
    # RULE: an unknown default fuel names the known ones and the file
    if chosen not in known:
        msg = f"unknown fuel {chosen!r}; the known ones are {', '.join(known)}. Fix fuel in {path}"
        raise SettingsError(msg)
    return Fuel(chosen)


def _home(home: object, path: pathlib.Path) -> tuple[float, float] | None:
    if home is None:
        return None
    lat = home.get("lat") if isinstance(home, dict) else None
    lon = home.get("lon") if isinstance(home, dict) else None
    if not all(isinstance(x, int | float) and not isinstance(x, bool) for x in (lat, lon)):
        msg = f"[home] in {path} needs two numbers, written as: lat = 0.0 and lon = 0.0 on their own lines"
        raise SettingsError(msg)
    return float(lat), float(lon)  # type: ignore[arg-type]


def build_source(settings: Settings, cache: pathlib.Path) -> PriceSource | None:
    """The source listed, imported only now; None when no source is switched on."""
    if not settings.sources:
        return None
    module_name, class_name = KNOWN_SOURCES[settings.sources[0]]
    source: PriceSource = getattr(importlib.import_module(module_name), class_name)(cache)
    return source
