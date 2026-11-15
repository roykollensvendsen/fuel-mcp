# SPDX-License-Identifier: GPL-3.0-or-later
"""The private-sink loader checks everything before it imports anything, and fails closed.

Every private sink here is made up, written into the test's own folder by the test.
"""

import datetime as dt
import importlib
import os
import stat
import sys

import pytest

from fuel_mcp import sinks as loader
from fuel_mcp.model import Fuel, OwnReport, SinkRefusedError
from fuel_mcp.sinks import PrivateSinkEntry, Sinks, check_tree, load_private_sinks
from fuel_mcp.sinks.local import LocalSink

NOW = dt.datetime(2030, 1, 1, 12, 0, tzinfo=dt.UTC)
NAME = "fuel_mcp_private.example:ExampleSink"
LABEL = "Example service"
REPORT = OwnReport("osm_1", "Test Station North", Fuel.DIESEL, 21.49, NOW)

SINK = """
import pathlib

from fuel_mcp_private import helper

pathlib.Path({mark!r}).write_text("imported")


class ExampleSink:
    label = {label!r}
    needs_yes = {needs_yes!r}

    def __init__(self):
        self.sent = []

    def take(self, report):
        self.sent.append(helper.shown(report))
"""


@pytest.fixture(autouse=True)
def _forget_the_private_package():
    """Each test's made-up package is its own: none stays in sys.modules for the next."""
    yield
    for name in [n for n in sys.modules if n.split(".")[0] == "fuel_mcp_private"]:
        del sys.modules[name]
    importlib.invalidate_caches()


def _private(tmp_path, *, label=LABEL, needs_yes=True, body=None):
    """A made-up private checkout under `tmp_path`, every part the owner's and 0755 or 0644."""
    folder = tmp_path / "private"
    package = folder / "src" / "fuel_mcp_private"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "helper.py").write_text("def shown(report):\n    return report.station_id\n")
    mark = tmp_path / "imported"
    text = body if body is not None else SINK.format(mark=str(mark), label=label, needs_yes=needs_yes)
    (package / "example.py").write_text(text)
    for place in [folder, *folder.rglob("*")]:
        place.chmod(0o755 if place.is_dir() else 0o644)
    return folder


def _entry(folder, sink=NAME):
    return PrivateSinkEntry(folder, sink)


def _loaded(folder, sink=NAME):
    return load_private_sinks([_entry(folder, sink)])


def test_a_private_sink_in_order_is_loaded_and_sends_after_loading(tmp_path):
    # It imports its helper while it loads, so sending works once src is off sys.path again.
    folder = _private(tmp_path)
    [sink] = _loaded(folder)
    assert (sink.label, sink.needs_yes) == (LABEL, True)
    said = Sinks(LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12)), [sink]).take(REPORT, {LABEL})
    assert said == {LABEL: f"Sent to {LABEL}."}
    assert sink.sent == ["osm_1"]


def test_a_private_sink_that_fails_any_check_sends_nothing_and_the_report_is_still_saved(tmp_path):
    folder = _private(tmp_path)
    (folder / "src" / "fuel_mcp_private" / "helper.py").chmod(0o664)
    loaded = _loaded(folder)
    assert loaded == []
    assert not (tmp_path / "imported").exists(), "nothing of a refused tree may be imported"
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    assert Sinks(local, loaded).take(REPORT, {LABEL}) == {}
    assert local.recent(Fuel.DIESEL, NOW) == [REPORT]


def test_the_loader_leaves_sys_path_as_it_found_it(tmp_path):
    folder = _private(tmp_path)
    before = list(sys.path)
    assert _loaded(folder)
    assert sys.path == before
    broken = _private(tmp_path / "second", body="raise RuntimeError('no')\n")
    assert load_private_sinks([_entry(broken)]) == []
    assert sys.path == before


@pytest.mark.parametrize(
    "change",
    [
        "group-writable folder",
        "group-writable parent",
        "others-writable py",
        "link",
        "foreign owner",
    ],
)
def test_the_loader_refuses_a_private_tree_with_anything_not_the_owner_s_or_writable_by_others(
    tmp_path, monkeypatch, change, caplog
):
    folder = _private(tmp_path / "home")
    example = folder / "src" / "fuel_mcp_private" / "example.py"
    if change == "group-writable folder":
        (folder / "src").chmod(0o775)
    elif change == "group-writable parent":
        (tmp_path / "home").chmod(0o775)
    elif change == "others-writable py":
        example.chmod(0o646)
    elif change == "link":
        (folder / "src" / "fuel_mcp_private" / "linked.py").symlink_to(example)
    else:
        real = os.lstat
        theirs = os.getuid() + 1

        def lstat(path, *args, **kwargs):
            facts = real(path, *args, **kwargs)
            if str(path) == str(example):
                return os.stat_result((facts.st_mode, *facts[1:4], theirs, *facts[5:]))
            return facts

        monkeypatch.setattr(loader.os, "lstat", lstat)
    assert _loaded(folder) == []
    assert not (tmp_path / "home" / "imported").exists()
    assert "Fix:" in caplog.text or "real path" in caplog.text


def _parent_as(monkeypatch, parent, mode):
    """Make `parent` look owned by root with `mode`, as /tmp is."""
    real = os.lstat

    def lstat(path, *args, **kwargs):
        facts = real(path, *args, **kwargs)
        if str(path) == str(parent):
            return os.stat_result((stat.S_IFDIR | mode, *facts[1:4], 0, *facts[5:]))
        return facts

    monkeypatch.setattr(loader.os, "lstat", lstat)


def test_a_root_owned_sticky_parent_writable_by_all_is_accepted(tmp_path, monkeypatch):
    # /tmp is one: anyone may write in it, but no one may move what another user put there.
    folder = _private(tmp_path / "shared")
    _parent_as(monkeypatch, tmp_path / "shared", 0o1777)
    check_tree(folder)


def test_a_root_owned_parent_writable_by_all_without_the_sticky_bit_is_refused(tmp_path, monkeypatch):
    folder = _private(tmp_path / "shared")
    _parent_as(monkeypatch, tmp_path / "shared", 0o777)
    with pytest.raises(loader.NotLoadedError, match="others can write it"):
        check_tree(folder)


def test_the_loader_refuses_a_top_level_name_other_than_fuel_mcp_private(tmp_path):
    # A second name on sys.path would stay in sys.modules for the whole process: "json" would
    # shadow the standard library's for every module imported after it.
    folder = _private(tmp_path)
    (folder / "src" / "json.py").write_text("")
    (folder / "src" / "json.py").chmod(0o644)
    assert _loaded(folder) == []
    assert not (tmp_path / "imported").exists()


def test_a_private_package_importable_already_is_refused(tmp_path, monkeypatch):
    first = _private(tmp_path / "first")
    monkeypatch.syspath_prepend(str(first / "src"))
    second = _private(tmp_path / "second")
    with pytest.raises(loader.NotLoadedError, match="importable already"):
        check_tree(second)


@pytest.mark.parametrize("needs_yes", [False, 1, "yes", None])
def test_the_loader_refuses_a_private_sink_whose_needs_yes_isn_t_true(tmp_path, needs_yes, caplog):
    folder = _private(tmp_path, needs_yes=needs_yes)
    assert _loaded(folder) == []
    assert "needs_yes must be True" in caplog.text


@pytest.mark.parametrize(
    ("sink", "said"),
    [
        ("fuel_mcp_private:ExampleSink", "must be written"),
        ("fuel_mcp.sources:ExampleSink", "must be written"),
        ("fuel_mcp_private.example:Absent", "has no class"),
        ("fuel_mcp_private.absent:ExampleSink", "raised ModuleNotFoundError"),
    ],
)
def test_a_badly_named_or_missing_sink_is_refused(tmp_path, sink, said, caplog):
    assert _loaded(_private(tmp_path), sink) == []
    assert said in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "import sys\nsys.exit(3)\n",
        "import sys\nclass ExampleSink:\n    def __init__(self):\n        sys.exit(3)\n",
        (
            "import sys\nclass ExampleSink:\n    needs_yes = True\n    take = print\n"
            "    @property\n    def label(self):\n        sys.exit(3)\n"
        ),
    ],
    ids=["on import", "when built", "when checked"],
)
def test_a_sink_that_exits_is_refused_and_the_server_lives_on(tmp_path, body, caplog):
    # SystemExit isn't an Exception: caught as one, it would end the whole plugin.
    loaded = _loaded(_private(tmp_path, body=body))
    assert loaded == []
    assert "SystemExit" in caplog.text
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    Sinks(local, loaded).take(REPORT, {LABEL})
    assert local.recent(Fuel.DIESEL, NOW) == [REPORT]


@pytest.mark.parametrize(
    ("body", "said"),
    [
        ("raise RuntimeError('broken')\n", "raised RuntimeError"),
        ("class ExampleSink:\n    def __init__(self, key):\n        pass\n", "built with no arguments"),
        ("class ExampleSink:\n    label = 'Example service'\n    needs_yes = True\n", "no take"),
        ("class ExampleSink:\n    label = ''\n    needs_yes = True\n    take = print\n", "label"),
        (
            "class ExampleSink:\n    label = 'your own list'\n    needs_yes = True\n    take = print\n",
            "label",
        ),
        ("ExampleSink = object()\n", "has no class"),
    ],
)
def test_a_module_that_is_not_a_sink_is_refused(tmp_path, body, said, caplog):
    assert _loaded(_private(tmp_path, body=body)) == []
    assert said in caplog.text


def test_two_private_sinks_must_share_one_folder_and_have_labels_of_their_own(tmp_path, caplog):
    # Python keeps the first package it imports under a name, so a second folder would be
    # quietly the first one's code. The second entry is left out; the first stays.
    folder = _private(tmp_path / "one")
    other = _private(tmp_path / "two")
    loaded = load_private_sinks([_entry(folder), _entry(other), _entry(folder)])
    assert [s.label for s in loaded] == [LABEL]
    assert "must sit in one folder" in caplog.text
    assert "a name of its own" in caplog.text


def test_a_folder_that_is_not_a_full_path_is_refused(tmp_path):
    with pytest.raises(loader.NotLoadedError, match="not a full path"):
        check_tree(tmp_path.relative_to(tmp_path.parent))


def test_no_private_sink_listed_loads_nothing_and_says_nothing(caplog):
    assert load_private_sinks([]) == []
    assert caplog.text == ""


def test_a_log_line_never_holds_a_price_or_a_station(tmp_path, caplog):
    folder = _private(tmp_path)
    (folder / "src").chmod(0o777)
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    Sinks(local, _loaded(folder)).take(REPORT, {LABEL})
    assert caplog.text
    assert "21.49" not in caplog.text
    assert "Test Station" not in caplog.text


class _Recording:
    label = LABEL
    needs_yes = True

    def __init__(self, local, error=None):
        self.local, self.error, self.sent = local, error, []

    def take(self, report):
        assert self.local.recent(report.fuel, NOW) == [report], "sent before it was saved"
        if self.error:
            raise self.error
        self.sent.append(report)


def test_a_report_is_saved_locally_before_anything_is_sent(tmp_path):
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    sender = _Recording(local)
    assert Sinks(local, [sender]).take(REPORT, {LABEL}) == {LABEL: f"Sent to {LABEL}."}
    assert sender.sent == [REPORT]


def test_a_sink_not_named_gets_nothing(tmp_path):
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    sender = _Recording(local)
    assert Sinks(local, [sender]).take(REPORT) == {}
    assert sender.sent == []


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (SinkRefusedError("you've reported there 5 times today."), "you've reported there 5 times today."),
        (RuntimeError("anything"), "it failed. The log says why."),
    ],
)
def test_a_refusing_or_failing_sender_is_said_and_the_report_stays_local(tmp_path, error, said):
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    result = Sinks(local, [_Recording(local, error)]).take(REPORT, {LABEL})
    assert result == {LABEL: f"Not sent to {LABEL}: {said}"}
    assert local.recent(Fuel.DIESEL, NOW) == [REPORT]


def test_a_local_save_that_fails_sends_nothing(tmp_path):
    (tmp_path / "state").mkdir(mode=0o700)
    (tmp_path / "state" / "own-reports.jsonl").symlink_to(tmp_path / "elsewhere")
    local = LocalSink(tmp_path / "state", keep=dt.timedelta(hours=12))
    sender = _Recording(local)
    with pytest.raises(SinkRefusedError):
        Sinks(local, [sender]).take(REPORT, {LABEL})
    assert sender.sent == []
