# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's local settings: home, and which price sources are switched on."""

import datetime as dt
import pathlib
import subprocess
import sys

import pytest

from fuel_mcp import cli
from fuel_mcp.model import Fuel
from fuel_mcp.settings import (
    SettingsError,
    build_source,
    cache_folder,
    config_folder,
    load_settings,
    state_folder,
)
from fuel_mcp.simulator import FakeSource

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)


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


def test_the_owners_default_fuel_is_read_from_the_config(tmp_path):
    assert load_settings(tmp_path / "absent").fuel is Fuel.DIESEL
    folder = _config(tmp_path, 'fuel = "petrol95"\n[home]\nlat = 0.0\nlon = 0.0\n')
    assert load_settings(folder).fuel is Fuel.PETROL95


@pytest.mark.parametrize("line", ['fuel = "petrol"', "fuel = 95"])
def test_an_unknown_default_fuel_names_the_known_ones_and_the_file(tmp_path, line):
    with pytest.raises(SettingsError, match=r"diesel, petrol95, petrol98\. Fix fuel in .*config\.toml"):
        load_settings(_config(tmp_path, line + "\n"))


def test_the_owners_default_fuel_reaches_the_answer(tmp_path, monkeypatch, capsys):
    folder = _config(tmp_path, 'fuel = "petrol98"\nsources = []\n')
    monkeypatch.setattr(cli, "config_folder", lambda _env: folder)
    monkeypatch.setattr(cli, "build_source", lambda _s, _c: FakeSource(now=NOW))
    assert cli.main(["price", "--lat", "0", "--lon", "0"]) == 0
    assert capsys.readouterr().out.startswith("Petrol 98 is cheapest")


def test_the_farthest_search_and_the_old_price_limit_are_read_from_the_config(tmp_path):
    defaults = load_settings(tmp_path / "absent")
    assert (defaults.farthest_km, defaults.old_after_days) == (20.0, 30)
    folder = _config(tmp_path, "farthest_km = 35\nold_after_days = 60\n")
    chosen = load_settings(folder)
    assert (chosen.farthest_km, chosen.old_after_days) == (35.0, 60)


@pytest.mark.parametrize(
    ("line", "key", "fix"),
    [
        ("farthest_km = 0", "farthest_km", "a number from 1 to 100"),
        ('farthest_km = "far"', "farthest_km", "a number from 1 to 100"),
        ("farthest_km = true", "farthest_km", "a number from 1 to 100"),
        ("farthest_km = inf", "farthest_km", "a number from 1 to 100"),
        ("farthest_km = nan", "farthest_km", "a number from 1 to 100"),
        ("farthest_km = 101", "farthest_km", "a number from 1 to 100"),
        ("old_after_days = 6", "old_after_days", "a whole number from 7 to 3650"),
        ("old_after_days = -1", "old_after_days", "a whole number from 7 to 3650"),
        ("old_after_days = 2.5", "old_after_days", "a whole number from 7 to 3650"),
    ],
)
def test_a_search_setting_outside_its_limits_names_the_fix_and_the_file(tmp_path, line, key, fix):
    # A farthest_km of inf would step out for ever, and nan matches no station. An old limit under
    # a week would never call a price old, since a price is current for a week.
    with pytest.raises(SettingsError, match=rf"{key} must be {fix}.*Fix {key} in .*config\.toml"):
        load_settings(_config(tmp_path, line + "\n"))


def test_the_search_limits_have_one_default():
    from fuel_mcp.service import Choices  # noqa: PLC0415

    defaults = load_settings(pathlib.Path("/nonexistent"))
    assert (defaults.farthest_km, defaults.old_after_days) == (
        Choices().farthest_km,
        Choices().old_after_days,
    )


# -- the owner's own reports ---------------------------------------------------------------


def test_the_report_settings_are_read_and_have_defaults(tmp_path):
    defaults = load_settings(tmp_path / "absent")
    assert defaults.sinks == ("local",)
    assert (defaults.own_report_hours, defaults.own_report_keep_hours) == (12, 12)
    assert (defaults.price_low, defaults.price_high) == (10.0, 40.0)
    assert defaults.private_sinks == ()
    chosen = load_settings(
        _config(
            tmp_path,
            "own_report_hours = 6\nown_report_keep_hours = 48\nprice_low = 12\nprice_high = 35.5\n",
        )
    )
    assert (chosen.own_report_hours, chosen.own_report_keep_hours) == (6, 48)
    assert (chosen.price_low, chosen.price_high) == (12.0, 35.5)


@pytest.mark.parametrize(
    ("line", "key", "fix"),
    [
        ("own_report_hours = 0", "own_report_hours", "a whole number from 1 to 168"),
        ("own_report_hours = 1.5", "own_report_hours", "a whole number from 1 to 168"),
        ("own_report_keep_hours = 2161", "own_report_keep_hours", "a whole number from 1 to 2160"),
        ("price_low = 0", "price_low", "a number from 1 to 100"),
        ("price_high = nan", "price_high", "a number from 1 to 100"),
    ],
)
def test_a_report_setting_outside_its_limits_names_the_fix_and_the_file(tmp_path, line, key, fix):
    with pytest.raises(SettingsError, match=rf"{key} must be {fix}.*Fix {key} in .*config\.toml"):
        load_settings(_config(tmp_path, line + "\n"))


def test_an_own_report_is_kept_at_least_as_long_as_it_counts(tmp_path):
    # Kept for less time than it counts, a report would vanish while it still should be said.
    with pytest.raises(SettingsError, match=r"own_report_keep_hours must be at least .*config\.toml"):
        load_settings(_config(tmp_path, "own_report_hours = 24\nown_report_keep_hours = 12\n"))


def test_the_lowest_believable_price_is_below_the_highest(tmp_path):
    with pytest.raises(SettingsError, match=r"price_low must be below price_high.*config\.toml"):
        load_settings(_config(tmp_path, "price_low = 30\nprice_high = 30\n"))


def test_local_is_always_among_the_sinks(tmp_path, caplog):
    # A private sender must never hold the only copy of a report.
    settings = load_settings(_config(tmp_path, "sinks = []\n"))
    assert settings.sinks == ("local",)
    assert "always on" in caplog.text


@pytest.mark.parametrize("line", ['sinks = ["elsewhere"]', 'sinks = "local"', "sinks = [1]"])
def test_an_unknown_sink_names_the_fix_and_the_file(tmp_path, line):
    with pytest.raises(SettingsError, match=r"sink.*config\.toml"):
        load_settings(_config(tmp_path, line + "\n"))


def test_a_private_sink_is_listed_and_nothing_of_it_is_loaded(tmp_path):
    folder = tmp_path / "private"
    settings = load_settings(
        _config(
            tmp_path,
            f'[[private_sinks]]\nfolder = "{folder}"\nsink = "fuel_mcp_private.example:ExampleSink"\n',
        )
    )
    assert [(e.folder, e.sink) for e in settings.private_sinks] == [
        (folder, "fuel_mcp_private.example:ExampleSink")
    ]
    assert str(folder) not in repr(settings)
    assert "fuel_mcp_private" not in sys.modules


@pytest.mark.parametrize(
    "text",
    [
        '[[private_sinks]]\nfolder = "/x"\n',
        '[[private_sinks]]\nfolder = "/x"\nsink = 3\n',
        '[[private_sinks]]\nfolder = "/x"\nsink = "fuel_mcp_private.a:B"\nkey = "secret"\n',
        'private_sinks = "nothing"\n',
    ],
)
def test_a_private_sink_entry_of_the_wrong_shape_names_the_fix(tmp_path, text):
    with pytest.raises(SettingsError, match=r"\[\[private_sinks\]\] in .*config\.toml needs exactly"):
        load_settings(_config(tmp_path, text))


def test_the_state_folder_follows_xdg():
    assert state_folder({"XDG_STATE_HOME": "/st"}) == pathlib.Path("/st/fuel-mcp")
    assert state_folder({"HOME": "/h"}) == pathlib.Path("/h/.local/state/fuel-mcp")


def test_the_send_time_limit_and_the_owner_s_station_names_are_read(tmp_path):
    defaults = load_settings(tmp_path / "nothing-here")
    assert (defaults.send_timeout_s, dict(defaults.station_names)) == (15.0, {})
    chosen = load_settings(_config(tmp_path, 'send_timeout_s = 5\n[station_names]\n"my usual" = "osm_1"\n'))
    assert (chosen.send_timeout_s, dict(chosen.station_names)) == (5.0, {"my usual": "osm_1"})


@pytest.mark.parametrize("line", ["send_timeout_s = 0", "send_timeout_s = 61", 'send_timeout_s = "5"'])
def test_a_send_time_limit_outside_1_to_60_seconds_names_the_fix(tmp_path, line):
    with pytest.raises(SettingsError, match="send_timeout_s must be a number from 1 to 60"):
        load_settings(_config(tmp_path, line + "\n"))


@pytest.mark.parametrize("table", ["[station_names]\nusual = 3\n", 'station_names = "usual"\n'])
def test_station_names_must_map_a_name_to_a_station_id(tmp_path, table):
    with pytest.raises(SettingsError, match=r'\[station_names\] .* "my usual" = "<station id>"'):
        load_settings(_config(tmp_path, table))
