# SPDX-License-Identifier: GPL-3.0-or-later
"""The owner's own list: written whole and private, holding no position, kept only a while."""

import dataclasses
import datetime as dt
import json
import os
import stat

import pytest

from fuel_mcp import safe_file
from fuel_mcp.model import Fuel, OwnReport, SinkRefusedError
from fuel_mcp.safe_file import PrivateFileError, read_private, write_private
from fuel_mcp.sinks.local import FIELDS, FILE_NAME, LocalSink

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)
KEEP = dt.timedelta(hours=12)


def _report(minutes_ago=0, *, fuel=Fuel.DIESEL, price=21.49):
    return OwnReport(
        station_id="osm_1",
        station_name="Test Station North",
        fuel=fuel,
        price=price,
        reported_at=NOW - dt.timedelta(minutes=minutes_ago),
    )


# -- the safe write ------------------------------------------------------------------------


def test_a_private_file_is_readable_by_its_owner_only(tmp_path):
    path = tmp_path / "state" / "file"
    old = os.umask(0o022)
    try:
        write_private(path, b"one")
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert read_private(path) == b"one"


def test_a_private_write_refuses_a_link_where_the_file_goes(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"theirs")
    path = tmp_path / "file"
    path.symlink_to(elsewhere)
    with pytest.raises(PrivateFileError, match="is a link"):
        write_private(path, b"mine")
    assert path.is_symlink()
    assert elsewhere.read_bytes() == b"theirs"


def test_a_failed_private_write_leaves_the_old_file_and_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "file"
    write_private(path, b"old")

    def refused(self, target):
        raise OSError(28, "disk full")

    monkeypatch.setattr(type(path), "replace", refused)
    with pytest.raises(OSError, match="disk full"):
        write_private(path, b"new")
    monkeypatch.undo()
    assert path.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["file"]


def test_a_private_file_another_user_owns_is_not_read(tmp_path, monkeypatch):
    path = tmp_path / "file"
    write_private(path, b"words")
    someone_else = os.getuid() + 1
    monkeypatch.setattr(safe_file.os, "getuid", lambda: someone_else)
    with pytest.raises(PrivateFileError, match="isn't a file of yours"):
        read_private(path)


def test_a_link_is_not_read_as_a_private_file(tmp_path):
    (tmp_path / "theirs").write_bytes(b"words")
    (tmp_path / "file").symlink_to(tmp_path / "theirs")
    with pytest.raises(PrivateFileError, match="can't be read safely"):
        read_private(tmp_path / "file")


def test_a_missing_private_file_reads_as_none(tmp_path):
    assert read_private(tmp_path / "absent") is None


def test_an_open_private_folder_is_closed_to_others(tmp_path, caplog):
    # The owner's own folder, left open by a umask or by hand, is closed before anything is put in it.
    folder = tmp_path / "state"
    folder.mkdir()
    folder.chmod(0o777)
    write_private(folder / "file", b"words")
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert str(folder) in caplog.text
    assert "words" not in caplog.text


def test_a_private_file_s_folder_is_the_owner_s_and_closed_to_others(tmp_path, monkeypatch):
    folder = tmp_path / "state"
    folder.mkdir(mode=0o700)
    someone_else = os.getuid() + 1
    monkeypatch.setattr(safe_file.os, "getuid", lambda: someone_else)
    with pytest.raises(PrivateFileError, match=rf"{folder} isn't a folder of yours"):
        write_private(folder / "file", b"words")
    monkeypatch.undo()
    assert list(folder.iterdir()) == []


def test_a_private_file_s_folder_that_is_a_link_is_refused(tmp_path):
    (tmp_path / "real").mkdir(mode=0o700)
    (tmp_path / "state").symlink_to(tmp_path / "real")
    with pytest.raises(PrivateFileError, match="state"):
        write_private(tmp_path / "state" / "file", b"words")
    assert list((tmp_path / "real").iterdir()) == []


# -- the local sink ------------------------------------------------------------------------


def test_a_report_is_kept_and_read_back(tmp_path):
    sink = LocalSink(tmp_path, keep=KEEP)
    sink.take(_report(5))
    sink.take(_report(1, fuel=Fuel.PETROL95, price=22.10))
    assert sink.recent(Fuel.DIESEL, NOW) == [_report(5)]
    assert sink.recent(Fuel.PETROL95, NOW) == [_report(1, fuel=Fuel.PETROL95, price=22.10)]
    assert stat.S_IMODE((tmp_path / FILE_NAME).stat().st_mode) == 0o600


def test_an_own_report_is_removed_once_its_keep_time_has_passed(tmp_path):
    # Stations and times are a trail of where the owner was, so nothing stays past its keep time.
    sink = LocalSink(tmp_path, keep=KEEP)
    sink.take(_report(13 * 60))
    sink.take(_report(5))
    assert sink.recent(Fuel.DIESEL, NOW) == [_report(5)]
    lines = (tmp_path / FILE_NAME).read_text().splitlines()
    assert len(lines) == 1, "the old report must be gone from the file, not only from the answer"


@dataclasses.dataclass(frozen=True)
class _WithPosition(OwnReport):
    lat: float = 41.2
    lon: float = -32.6


def test_the_own_reports_file_holds_no_position(tmp_path):
    # Even a report that carries a position writes only the five fields.
    sink = LocalSink(tmp_path, keep=KEEP)
    sink.take(_WithPosition(**dataclasses.asdict(_report())))
    line = json.loads((tmp_path / FILE_NAME).read_text())
    assert tuple(line) == FIELDS
    assert "41.2" not in (tmp_path / FILE_NAME).read_text()


def test_a_broken_line_is_left_out_and_the_rest_read(tmp_path, caplog):
    sink = LocalSink(tmp_path, keep=KEEP)
    sink.take(_report(5))
    with (tmp_path / FILE_NAME).open("a") as file:
        file.write("not a report\n")
    assert sink.recent(Fuel.DIESEL, NOW) == [_report(5)]
    assert "isn't a report" in caplog.text
    assert "21.49" not in caplog.text


def test_a_garbled_list_is_replaced_at_the_next_save_and_the_log_says_so(tmp_path, caplog):
    (tmp_path / FILE_NAME).write_text("not a report\n")
    (tmp_path / FILE_NAME).chmod(0o600)
    sink = LocalSink(tmp_path, keep=KEEP)
    sink.take(_report(5))
    assert (tmp_path / FILE_NAME).read_text().count("\n") == 1
    assert "replaced" in caplog.text
    assert "21.49" not in caplog.text


def test_an_unwritable_list_refuses_the_report_with_a_sentence(tmp_path):
    (tmp_path / FILE_NAME).symlink_to(tmp_path / "elsewhere")
    with pytest.raises(SinkRefusedError, match="Not saved: your own list can't be written"):
        LocalSink(tmp_path, keep=KEEP).take(_report())
