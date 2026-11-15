# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing committed says where the owner lives or fills up."""

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import private_data_scan

ROOT = pathlib.Path(__file__).parent.parent


def test_a_home_network_address_is_never_committed():
    # Built at run time, so this file does not itself hold what it forbids.
    for address in (".".join(p) for p in (("10", "0", "0", "7"), ("192", "168", "1", "50"))):
        assert private_data_scan.offences(f"bridge at http://{address}:8000", set()), address


def test_a_street_address_is_never_committed():
    assert private_data_scan.offences("the pump in " + "Storgata" + " 12", set())
    assert private_data_scan.offences("Kirke" + "veien 3B", set())


def test_coordinates_more_exact_than_two_decimals_are_never_committed():
    assert private_data_scan.offences("home = " + "59.91" + "38, 10.75" + "22", set())
    assert private_data_scan.offences("home = " + "59.91" + ", 10.75", set()) == []


def test_a_station_name_near_home_is_never_committed():
    station = "Made-up " + "Pump Nordbyen"
    assert private_data_scan.offences(f"cheapest at {station.lower()}", {station})


def test_home_from_local_config_is_never_committed(tmp_path):
    folder = tmp_path / "fuel-mcp"
    folder.mkdir()
    (folder / "config.toml").write_text("[home]\nlat = " + "58.46" + "127\nlon = " + "8.77" + "241\n")
    found = private_data_scan.private_values(folder)
    assert private_data_scan.offences("lat = " + "58.46" + "12", found)
    assert private_data_scan.offences("lon: " + "8.77" + "2", found)


def test_home_written_rounded_is_never_committed(tmp_path):
    (tmp_path / "config.toml").write_text("[home]\nlat = " + "58.62" + "19\nlon = 0.0\n")
    found = private_data_scan.private_values(tmp_path)
    assert private_data_scan.offences("lat = " + "58.62" + "2", found)
    assert private_data_scan.offences("lat = " + "58.62" + "1", found)


def test_the_private_list_is_read_from_local_config(tmp_path):
    folder = tmp_path / "fuel-mcp"
    folder.mkdir()
    (folder / "private.toml").write_text('words = ["Made-up Pump Nordbyen", "ab"]\n')
    # A word too short to be told from ordinary text is left out.
    assert private_data_scan.private_values(folder) == {"Made-up Pump Nordbyen"}


def test_missing_config_files_hold_nothing(tmp_path):
    assert private_data_scan.private_values(tmp_path / "absent") == set()


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("private.toml", "words = [not toml"),
        ("private.toml", "words = 3"),
        ("private.toml", 'words = ["fine", 4]'),
        ("config.toml", "[home]\nlat = [broken"),
        ("config.toml", '[home]\nlat = "north"\nlon = 0.0\n'),
    ],
    ids=["list not toml", "words not a list", "a word not text", "config not toml", "home not numbers"],
)
def test_a_broken_private_file_refuses_the_commit_never_skips_the_check(tmp_path, capsys, name, text):
    (tmp_path / name).write_text(text)
    assert private_data_scan.main(["--config-dir", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1
    assert str(tmp_path / name) in err
    assert "north" not in err
    assert "not toml" not in err


@pytest.mark.parametrize("name", ["private.toml", "config.toml"])
def test_a_link_to_a_moved_private_file_refuses_the_commit(tmp_path, capsys, name):
    (tmp_path / name).symlink_to(tmp_path / "moved-away.toml")
    assert private_data_scan.main(["--config-dir", str(tmp_path)]) == 2
    assert str(tmp_path / name) in capsys.readouterr().err


def test_ordinary_text_and_documentation_addresses_are_allowed():
    text = "Fake Fuel Beta, 192.0.2.10, version 1.2.3.4, 19.79 kroner, lat 0.009, 2026-11-15"
    assert private_data_scan.offences(text, set()) == []


def test_a_finding_names_the_kind_and_line_never_the_value():
    station = "Made-up " + "Pump Nordbyen"
    found = private_data_scan.offences(f"first\nat {station}\n", {station})
    assert found == ["line 2: a private word or home"]


def _clean_run(tmp_path, *flags):
    return private_data_scan.main([*flags, "--config-dir", str(tmp_path / "absent")])


def test_the_tracked_tree_is_clean(tmp_path):
    assert _clean_run(tmp_path, "--tracked") == 0


def test_the_history_is_clean(tmp_path):
    assert _clean_run(tmp_path, "--history") == 0


def _repo_with_a_branch(tmp_path, *, on_main):
    """A repository whose side branch holds exact coordinates; main holds `on_main`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git = [
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@example.org"],
        ["config", "user.name", "t"],
    ]
    for args in git:
        subprocess.run(["git", *args], cwd=repo, check=True)

    def commit(text):
        (repo / "notes.txt").write_text(text)
        subprocess.run(["git", "add", "notes.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "notes"], cwd=repo, check=True)

    commit(on_main)
    subprocess.run(["git", "switch", "-q", "-c", "side"], cwd=repo, check=True)
    commit("home = " + "61.98" + "765, 7.12" + "345\n")
    subprocess.run(["git", "switch", "-q", "main"], cwd=repo, check=True)
    return repo


def test_the_history_scanned_is_what_lands_on_the_branch(tmp_path, monkeypatch):
    """A branch under review holds its own made-up numbers; main's scan must not read them."""
    monkeypatch.setattr(private_data_scan, "ROOT", _repo_with_a_branch(tmp_path, on_main="nothing\n"))
    assert _clean_run(tmp_path, "--history") == 0


def test_the_branch_own_history_is_still_scanned(tmp_path, monkeypatch):
    on_main = "home = " + "61.97" + "654, 7.23" + "456\n"
    monkeypatch.setattr(private_data_scan, "ROOT", _repo_with_a_branch(tmp_path, on_main=on_main))
    assert _clean_run(tmp_path, "--history") == 1


def test_the_commit_hook_runs_the_scan_on_what_is_staged():
    hook = (ROOT / ".githooks" / "pre-commit").read_text()
    assert "private_data_scan.py" in hook
    assert subprocess.run(["sh", "-n", str(ROOT / ".githooks" / "pre-commit")], check=False).returncode == 0
