# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone's position answers one question and is written nowhere.

Each question runs from the tool down to the real source, with a pretend
host. Home, the settings, cache and state folders, the temporary folder and
a full log all point into the test's own folder. Afterwards every file there
is read for the position, and so is everything printed. Only that folder is
read: a write to a fixed path outside it would not be seen.
"""

import asyncio
import dataclasses
import json
import logging
import tempfile

import pytest
from test_drivstoffpriser import NOW, PRICES, STATIONS, Clock, Host

from fuel_mcp.server import build_server
from fuel_mcp.service import FuelService
from fuel_mcp.sources.drivstoffpriser import DrivstoffpriserSource

#: A position whose rounded forms are distinctive too, which a point near 0,0 is not. It is out
#: in the Atlantic, so it is nobody's place, and outside Norway, where exact points are refused.
LAT, LON = 41.23456, -32.65432
#: The ways it could be written: in full, and rounded to 4, 3 or 2 decimals as a log, a file name
#: or a cache grid might round it. One decimal is left out: every station within 5 km shares it.
WRITTEN = ("41.23456", "32.65432", "41.2346", "32.6543", "41.235", "32.654", "41.23", "32.65")
#: The made-up stations moved about 2.5 km from the position, to places sharing none of its forms.
PLACES = {"osm_1": (41.25, -32.62), "ck_2": (41.21, -32.69)}


class _HostNearby(Host):
    """The pretend host, with the made-up stations moved near the position."""

    def __call__(self, url, headers, timeout):
        reply = super().__call__(url, headers, timeout)
        if not url.endswith("stations.json") or reply.status != 200:
            return reply
        stations = json.loads(json.dumps(STATIONS))
        for station in stations["stations"]:
            station["latitude"], station["longitude"] = PLACES[station["id"]]
        return dataclasses.replace(reply, body=json.dumps(stations).encode())


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """The test's folder, where home, the copy, a full log and all temporary files go."""
    for variable, name in [
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
        ("XDG_DATA_HOME", "data"),
    ]:
        (tmp_path / name).mkdir()
        monkeypatch.setenv(variable, str(tmp_path / name))
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    monkeypatch.setenv("TMPDIR", str(temporary))
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    log = logging.FileHandler(tmp_path / "fuel-mcp.log", encoding="utf-8")
    log.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(log)
    old_level, root.level = root.level, logging.DEBUG
    yield tmp_path
    root.removeHandler(log)
    root.setLevel(old_level)
    log.close()


def _ask(folder, host):
    source = DrivstoffpriserSource(folder / "cache", fetch=host, now=Clock())
    server = build_server(FuelService(source, now=lambda: NOW))
    result = asyncio.run(server.call_tool("fuel_price", {"lat": LAT, "lon": LON, "accuracy_m": 30}))
    return result.structured_content


def _written(folder):
    """Every file under `folder`, by name, with what it holds."""
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("down", [False, True], ids=["answered", "host down"])
def test_no_file_written_after_a_question_holds_the_position(folder, caplog, capsys, down):
    caplog.set_level(logging.DEBUG)
    answer = _ask(folder, _HostNearby(down=down))
    assert answer["success"] is not down  # the question really ran, both ways
    files = _written(folder)
    assert "fuel-mcp.log" in files
    assert down or any(name.startswith("cache") for name in files)  # the copy was written
    for name, body in files.items():
        for form in WRITTEN:
            assert form.encode() not in body, f"{name} holds the position as {form}"
            assert form not in name, f"a file is named by the position: {name}"
    printed = capsys.readouterr()
    for form in WRITTEN:
        assert form not in caplog.text
        assert form not in printed.out + printed.err


def test_no_fixture_number_looks_like_the_position():
    """A station or price written like the position would make the test above fail for nothing."""
    fixtures = json.dumps([STATIONS, PRICES, PLACES])
    assert not [form for form in WRITTEN if form in fixtures]
