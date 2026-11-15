# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: one price read on the fake, and the verbs it offers."""

import pytest

from fuel_mcp import cli


def test_the_command_offers_serve_and_price():
    assert cli.COMMANDS == ("serve", "price")


def test_a_simulated_price_at_the_made_up_point_is_printed(capsys):
    assert cli.main(["price", "--simulate", "--lat", "0", "--lon", "0"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Simulated: Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine")
    assert out.rstrip().endswith("Data: Drivstoffpriser (ODbL), stations © OpenStreetMap contributors")


def test_a_simulated_price_with_no_position_asks_where(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert cli.main(["price", "--simulate"]) == 1
    assert capsys.readouterr().out.startswith("Simulated: Where are you?")


def test_an_unknown_fuel_on_the_command_line_names_the_known_ones(capsys):
    with pytest.raises(SystemExit):
        cli.main(["price", "--simulate", "--fuel", "petrol"])
    assert "diesel, petrol95, petrol98" in capsys.readouterr().err


def _settings_home(tmp_path, monkeypatch, config=""):
    """Every folder the command reads or writes, in the test's own folder."""
    for variable, name in [
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_CACHE_HOME", "cache"),
    ]:
        monkeypatch.setenv(variable, str(tmp_path / name))
    (tmp_path / "config" / "fuel-mcp").mkdir(parents=True)
    (tmp_path / "config" / "fuel-mcp" / "config.toml").write_text(config)


def test_a_report_saved_through_the_command_s_parts_is_said_by_its_price(tmp_path, monkeypatch):
    _settings_home(tmp_path, monkeypatch)
    service, reporter = cli.parts(simulate=True)
    assert reporter.report(19.00, lat=0.0, lon=0.0, accuracy_m=10)["success"] is True
    say = service.price(None, lat=0.0, lon=0.0, accuracy_m=10)["say"]
    assert say == (
        "Diesel is cheapest at Fake Fuel Gamma, nineteen kroner, 200 metres away. "
        "That's your own report, under an hour old."
    )
    assert not (tmp_path / "state").exists()  # a simulated report never touches the owner's list


def test_the_real_parts_keep_reports_in_the_state_folder(tmp_path, monkeypatch):
    _settings_home(tmp_path, monkeypatch)
    service, reporter = cli.parts(simulate=False)
    assert reporter.sinks.private == []
    assert reporter.report(19.00, lat=0.0, lon=0.0)["say"].startswith("No fuel price source is switched on")
    assert service.price(None, lat=0.0, lon=0.0)["say"].startswith("No fuel price source is switched on")
