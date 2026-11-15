# SPDX-License-Identifier: GPL-3.0-or-later
"""The phone's position answers one question and is written nowhere.

Each question runs from the tool down to the real source, with a pretend
host. Afterwards every file under the test's own folder is read for the
position: the saved copy, its tags, the log and the temporary files.
"""

import asyncio
import logging
import tempfile

import pytest
from test_drivstoffpriser import NOW, Clock, Host

from fuel_mcp.server import build_server
from fuel_mcp.service import FuelService
from fuel_mcp.sources.drivstoffpriser import DrivstoffpriserSource

#: A position near the made-up stations, with digits no other number in the tests has.
LAT, LON = 0.0012345, 0.0054321
#: The ways it could be written: in full, and rounded as a log or a key might round it.
WRITTEN = ("0.0012345", "0.0054321", "0.00123", "0.00543", "1.2345e-03", "5.4321e-03")


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """The test's folder, where the copy, a full log and all temporary files go."""
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
def test_no_file_written_after_a_question_holds_the_position(folder, caplog, down):
    caplog.set_level(logging.DEBUG)
    answer = _ask(folder, Host(down=down))
    assert answer["success"] is not down  # the question really ran, both ways
    files = _written(folder)
    assert "fuel-mcp.log" in files
    assert down or any(name.startswith("cache") for name in files)  # the copy was written
    for name, body in files.items():
        for form in WRITTEN:
            assert form.encode() not in body, f"{name} holds the position as {form}"
            assert form not in name, f"a file is named by the position: {name}"
    for form in WRITTEN:
        assert form not in caplog.text
