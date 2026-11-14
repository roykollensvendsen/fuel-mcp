# SPDX-License-Identifier: GPL-3.0-or-later
"""Nothing committed holds a coordinate on land in Norway, nor a network test.

The fake stations sit near 0,0, far out at sea. A coordinate pair inside
Norway's box in a committed file is someone's home, route or local pump.
"""

import pathlib
import re
import socket
import subprocess

import pytest

ROOT = pathlib.Path(__file__).parent.parent
PAIR = re.compile(r"(-?\d{1,2}\.\d{2,})\s*[,;/ ]\s*(-?\d{1,3}\.\d{2,})")
# Norway's mainland and coast, as a box: latitude 57 to 72, longitude 4 to 32.
LAT, LON = (57.0, 72.0), (4.0, 32.0)


def norwegian_pairs(text: str) -> list[str]:
    """Each coordinate pair in `text` that falls inside Norway's box."""
    found = []
    for match in PAIR.finditer(text):
        lat, lon = float(match.group(1)), float(match.group(2))
        if LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]:
            found.append(match.group(0))
    return found


def test_a_coordinate_pair_in_norway_is_found():
    # Built at run time, so this file does not itself hold what it forbids.
    pair = ", ".join(("58." + "4612", "8." + "7724"))
    assert norwegian_pairs(f"home = {pair}") == [pair]
    assert norwegian_pairs("lat, lon = 0.009, 0.018") == []


def test_no_committed_file_holds_a_coordinate_in_norway():
    tracked = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    offences = []
    for name in tracked:
        path = ROOT / name
        if path.suffix in {".lock", ".png"} or name == "LICENSE" or not path.is_file():
            continue
        offences += [f"{name}: a coordinate in Norway" for _ in norwegian_pairs(path.read_text())]
    assert not offences, "\n".join(offences)


def test_a_test_cannot_reach_the_network():
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("example.org", 80))
