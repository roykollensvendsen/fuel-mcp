# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's local settings: home, the usual fuel, and which price sources are switched on.

They live in ``$XDG_CONFIG_HOME/fuel-mcp/config.toml``, normally
``~/.config/fuel-mcp/config.toml``, and never in the repository:

    sources = ["drivstoffpriser"]   # none is on until it is listed here
    fuel = "petrol95"               # when a question names none; diesel if not set
    farthest_km = 20                # how far the search steps out at most; 20 if not set
    old_after_days = 30             # a latest price older than this is said as only an old price
    [home]                          # used when the voice gives no position
    lat = 0.0
    lon = 0.0

A source not listed is fully off: its module is never imported.
"""

from __future__ import annotations

import importlib
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fuel_mcp.model import Fuel

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
    farthest_km: float = 20.0
    old_after_days: int = 30


def _folder(env: Mapping[str, str], variable: str, fallback: str) -> pathlib.Path:
    base = env.get(variable) or str(pathlib.Path(env.get("HOME", "~")) / fallback)
    return pathlib.Path(base).expanduser() / "fuel-mcp"


def config_folder(env: Mapping[str, str]) -> pathlib.Path:
    """The settings folder, following XDG_CONFIG_HOME."""
    return _folder(env, "XDG_CONFIG_HOME", ".config")


def cache_folder(env: Mapping[str, str]) -> pathlib.Path:
    """Where the copy of the export is kept, following XDG_CACHE_HOME."""
    return _folder(env, "XDG_CACHE_HOME", ".cache")


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
        farthest_km=float(_positive(table, "farthest_km", 20.0, path, whole=False)),
        old_after_days=int(_positive(table, "old_after_days", 30, path, whole=True)),
    )


def _positive(
    table: Mapping[str, object], key: str, default: float, path: pathlib.Path, *, whole: bool
) -> float:
    """The number set for `key`, or `default`; anything but a positive number names the fix."""
    value = table.get(key, default)
    kinds = (int,) if whole else (int, float)
    # RULE: a search setting that is not a positive number names the fix and the file
    if isinstance(value, bool) or not isinstance(value, kinds) or value <= 0:
        kind = "a whole number" if whole else "a number"
        msg = f"{key} must be {kind} above 0, such as {key} = {default:g}. Fix {key} in {path}"
        raise SettingsError(msg)
    return value


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
