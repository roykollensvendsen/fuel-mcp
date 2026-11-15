# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's local settings: home, and which price sources are switched on."""

import pathlib
import subprocess
import sys

import pytest

from fuel_mcp import cli
from fuel_mcp.settings import (
    SettingsError,
    build_source,
    cache_folder,
    config_folder,
    load_settings,
)


def _config(tmp_path, text):
    folder = tmp_path / "fuel-mcp"
    folder.mkdir(exist_ok=True)
    (folder / "config.toml").write_text(text)
    return folder


def test_no_price_source_is_on_until_the_owner_lists_one(tmp_path):
    settings = load_settings(tmp_path / "absent")
    assert settings.sources == ()
    assert settings.home is None
    assert load_settings(_config(tmp_path, "[home]\nlat = 0.0\nlon = 0.0\n")).sources == ()


def test_with_no_config_the_answer_says_exactly_what_to_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["price", "--lat", "0", "--lon", "0"]) == 1
    path = tmp_path / "fuel-mcp" / "config.toml"
    assert capsys.readouterr().out.startswith(
        "No fuel price source is switched on. "
        f'To use Drivstoffpriser, add sources = ["drivstoffpriser"] to {path}.'
    )


def test_without_a_position_or_home_it_says_where_home_is_written(tmp_path, monkeypatch, capsys):
    _config(tmp_path, 'sources = ["drivstoffpriser"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["price"]) == 1
    path = tmp_path / "fuel-mcp" / "config.toml"
    assert capsys.readouterr().out.startswith(
        f"Where are you? Share your position, or add [home] with lat and lon to {path}."
    )


def test_a_second_listed_source_is_refused_rather_than_ignored(tmp_path):
    folder = _config(tmp_path, 'sources = ["drivstoffpriser", "drivstoffpriser"]\n')
    with pytest.raises(
        SettingsError, match="only one source is supported until merging is written; keep one of"
    ):
        load_settings(folder)


def test_a_broken_tags_file_is_taken_as_no_tags(tmp_path):
    from test_drivstoffpriser import Host  # noqa: PLC0415

    from fuel_mcp.model import Fuel  # noqa: PLC0415

    source = build_source(load_settings(_config(tmp_path, 'sources = ["drivstoffpriser"]\n')), tmp_path / "c")
    source._fetch = Host()  # noqa: SLF001
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "prices.json").write_bytes(b"{}")
    (tmp_path / "c" / "prices.json.tags.json").write_bytes(b"\xff not json")
    assert len(source.prices_near(0.0, 0.0, 5.1, Fuel.DIESEL)) == 2


def test_home_is_read_from_the_config(tmp_path):
    settings = load_settings(_config(tmp_path, "[home]\nlat = 0.5\nlon = -0.25\n"))
    assert settings.home == (0.5, -0.25)


def test_a_home_without_both_numbers_names_the_file_and_the_fix(tmp_path):
    folder = _config(tmp_path, '[home]\nlat = "north"\n')
    with pytest.raises(SettingsError, match=r"config\.toml.*lat = 0\.0"):
        load_settings(folder)


def test_a_broken_config_names_the_file(tmp_path):
    with pytest.raises(SettingsError, match=r"config\.toml is not valid TOML"):
        load_settings(_config(tmp_path, "home = [not toml"))


def test_an_unknown_source_stops_the_start_and_names_the_known_ones(tmp_path):
    folder = _config(tmp_path, 'sources = ["petrolbot"]\n')
    with pytest.raises(SettingsError, match="unknown source 'petrolbot'; the known ones are drivstoffpriser"):
        load_settings(folder)


def test_the_folders_follow_xdg():
    assert config_folder({"XDG_CONFIG_HOME": "/cfg"}) == pathlib.Path("/cfg/fuel-mcp")
    assert cache_folder({"XDG_CACHE_HOME": "/cache"}) == pathlib.Path("/cache/fuel-mcp")
    assert config_folder({"HOME": "/h"}) == pathlib.Path("/h/.config/fuel-mcp")


def test_the_listed_source_is_built_with_its_cache(tmp_path):
    settings = load_settings(_config(tmp_path, 'sources = ["drivstoffpriser"]\n'))
    source = build_source(settings, tmp_path / "cache")
    assert source is not None
    assert source.label == "Drivstoffpriser"


def test_a_source_not_listed_is_never_imported(tmp_path):
    folder = _config(tmp_path, "sources = []\n")
    probe = (
        "import sys, pathlib\n"
        "from fuel_mcp.settings import build_source, load_settings\n"
        f"source = build_source(load_settings(pathlib.Path({str(folder)!r})), pathlib.Path('.'))\n"
        "print(source, 'fuel_mcp.sources.drivstoffpriser' in sys.modules)\n"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "None False"


def test_with_no_source_switched_on_it_says_so_and_sends_nothing(tmp_path, monkeypatch, capsys):
    _config(tmp_path, "sources = []\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["price", "--lat", "0", "--lon", "0"]) == 1
    assert capsys.readouterr().out.startswith("No fuel price source is switched on.")


def test_a_settings_error_is_printed_with_its_fix(tmp_path, monkeypatch, capsys):
    _config(tmp_path, 'sources = ["petrolbot"]\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["price"]) == 1
    assert "unknown source 'petrolbot'" in capsys.readouterr().out


def test_the_real_price_uses_home_and_the_hourly_copy(tmp_path, monkeypatch, capsys):
    # A copy checked just now means no request is made; the network stays blocked.
    from test_drivstoffpriser import BODIES  # noqa: PLC0415

    import fuel_mcp.sources.drivstoffpriser as source  # noqa: PLC0415

    _config(tmp_path, 'sources = ["drivstoffpriser"]\n[home]\nlat = 0.0\nlon = 0.0\n')
    cache = tmp_path / "cache" / "fuel-mcp"
    cache.mkdir(parents=True)
    stamp = source.dt.datetime.now(source.dt.UTC).isoformat()
    for name, body in BODIES.items():
        (cache / name).write_bytes(body)
        (cache / f"{name}.tags.json").write_text(f'{{"checked_at": "{stamp}"}}')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert cli.main(["price"]) == 0
    assert capsys.readouterr().out.startswith("Diesel is cheapest at Made-up East, nineteen seventy-nine")
