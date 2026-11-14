# SPDX-License-Identifier: GPL-3.0-or-later
"""The command line: one price read on the fake, and the verbs it offers."""

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
