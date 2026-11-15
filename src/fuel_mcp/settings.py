# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's local settings: home, the usual fuel, and which price sources are switched on.

They live in ``$XDG_CONFIG_HOME/fuel-mcp/config.toml``, normally
``~/.config/fuel-mcp/config.toml``, and never in the repository:

    sources = ["drivstoffpriser"]   # none is on until it is listed here
    fuel = "petrol95"               # when a question names none; diesel if not set
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
    return Settings(home=_home(table.get("home"), path), sources=tuple(sources), fuel=_fuel(table, path))


def _fuel(table: Mapping[str, object], path: pathlib.Path) -> Fuel:
    """The fuel a question means when it names none; diesel unless the owner chose another."""
    chosen = table.get("fuel", Fuel.DIESEL.value)
    known = [f.value for f in Fuel]
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
