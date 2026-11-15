#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refuse a commit that would publish where the owner lives or fills up.

This repository is public. Nothing in it may give the owner's home, their street
or the stations near them. The checks, on every file given (by default the files
staged for commit), or on the whole history:

- no home-network address: a private IPv4 address (examples use 192.0.2.x);
- no street address, such as a Norwegian street name followed by a number;
- no coordinates in Norway more exact than two decimals;
- not home, as ``[home]`` in the owner's ``config.toml`` gives it;
- no word from the owner's private list, such as a station near home.

Both files live in ``~/.config/fuel-mcp/``, outside the repository, so the list
itself is never published. The private list is ``private.toml``, holding
``words = ["...", ...]``. In CI neither file exists, so there only the patterns
are checked; the commit hook is what checks home and the list.

What is found is reported by kind and line, never quoted, since the report may
land in a public log.

    python3 scripts/private_data_scan.py            # the staged files (pre-commit)
    python3 scripts/private_data_scan.py --tracked  # every tracked file
    python3 scripts/private_data_scan.py --history  # every commit on every branch
"""

from __future__ import annotations

import argparse
import math
import os
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Shorter words would match ordinary text.
MIN_WORD = 4
#: Home is matched to this many decimals, about 100 m: close enough to find the street.
HOME_DECIMALS = 3

PRIVATE_IPV4 = re.compile(
    r"(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])"
)
STREET = re.compile(
    r"\b[A-ZÆØÅ][a-zæøå]+(?:gata|gaten|gate|veien|vegen|vei|veg|torget|plassen|allé|alleen)"
    r"\s+\d{1,4}[A-Za-z]?\b"
)
#: Norway lies within about 57-72 north and 4-32 east.
COORDINATES = re.compile(r"\b(?:5[7-9]|6\d|7[0-2])\.\d{3,}\s*,\s*(?:[4-9]|[12]\d|3[0-2])\.\d{3,}\b")
NUMBER = re.compile(r"-?\d+\.\d+")


def config_folder(env: dict[str, str] | os._Environ[str]) -> pathlib.Path:
    """The owner's settings folder, as the plugin itself finds it."""
    base = env.get("XDG_CONFIG_HOME") or str(pathlib.Path(env.get("HOME", "~")) / ".config")
    return pathlib.Path(base).expanduser() / "fuel-mcp"


def _toml(path: pathlib.Path) -> dict[str, object]:
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def private_values(folder: pathlib.Path) -> set[str]:
    """Home, to about 100 m, and the owner's private words, from files outside the repository."""
    out: set[str] = set()
    listed = _toml(folder / "private.toml").get("words", [])
    if isinstance(listed, list):
        out |= {w for w in listed if isinstance(w, str) and len(w) >= MIN_WORD}
    home = _toml(folder / "config.toml").get("home")
    if isinstance(home, dict):
        for key in ("lat", "lon"):
            value = home.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                scale = 10**HOME_DECIMALS
                out.add(f"{math.trunc(value * scale) / scale:.{HOME_DECIMALS}f}")
    return out


def offences(text: str, private: set[str]) -> list[str]:
    """What in ``text`` would say where the owner lives or fills up, by kind only."""
    found = [f"line {_line(text, m.start())}: a home-network address" for m in PRIVATE_IPV4.finditer(text)]
    found += [f"line {_line(text, m.start())}: a street address" for m in STREET.finditer(text)]
    found += [f"line {_line(text, m.start())}: exact coordinates" for m in COORDINATES.finditer(text)]
    lowered = text.lower()
    for value in private:
        if NUMBER.fullmatch(value):
            match = re.search(rf"(?<![\d.]){re.escape(value)}", text)
            at = match.start() if match else -1
        else:
            at = lowered.find(value.lower())
        if at >= 0:
            found.append(f"line {_line(text, at)}: a private word or home")
    return found


def _line(text: str, at: int) -> int:
    return text.count("\n", 0, at) + 1


def _git(*args: str) -> str:
    command = ["git", *args]
    done = subprocess.run(command, cwd=ROOT, capture_output=True, encoding="utf-8", check=True)  # noqa: S603
    return done.stdout


def _texts(*, tracked: bool, history: bool) -> list[tuple[str, str]]:
    """Each place to scan, as (what it is, its text)."""
    if history:
        return [("the history", _git("log", "-p", "--all", "--no-color", "--format=commit %H%n%B"))]
    names = _git("ls-files") if tracked else _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    out = []
    for name in names.splitlines():
        path = ROOT / name
        try:
            out.append((name, path.read_text(encoding="utf-8")))
        except (FileNotFoundError, IsADirectoryError, UnicodeDecodeError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    """Scan the staged files, every tracked file, or the history; 1 when anything is found."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tracked", action="store_true", help="scan every tracked file")
    parser.add_argument("--history", action="store_true", help="scan every commit on every branch")
    parser.add_argument(
        "--config-dir", help="where config.toml and private.toml are (default ~/.config/fuel-mcp)"
    )
    args = parser.parse_args(argv)
    folder = pathlib.Path(args.config_dir).expanduser() if args.config_dir else config_folder(os.environ)
    private = private_values(folder)
    bad = 0
    for where, text in _texts(tracked=args.tracked, history=args.history):
        for offence in offences(text, private):
            sys.stderr.write(f"{where}, {offence}\n")
            bad += 1
    if bad:
        sys.stderr.write("Use made-up names, points near 0,0, 192.0.2.x, and two decimals at most.\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
