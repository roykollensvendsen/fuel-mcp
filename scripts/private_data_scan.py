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
are checked; the commit hook is what checks home and the list. A file that
exists but cannot be read stops the scan, so its words never go unchecked.

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


class PrivateFileError(Exception):
    """A private file exists but cannot be used, so what it holds would go unchecked."""

    def __init__(self, path: pathlib.Path) -> None:
        """`path` is named in the message; its contents never are."""
        super().__init__(f"{path} is not in the form this check reads, so nothing was checked against it")
        self.path = path


def _toml(path: pathlib.Path) -> dict[str, object]:
    """The file's table; a missing file is empty, and a broken one refuses.

    A link whose target has moved is not a missing file: it is broken, and refuses.
    """
    if not os.path.lexists(path):
        return {}
    try:
        with path.open("rb") as file:
            return tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        raise PrivateFileError(path) from None


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _home_forms(value: float) -> set[str]:
    """Home to about 100 m, cut short and rounded, since either may be written."""
    scale = 10**HOME_DECIMALS
    cut = math.trunc(value * scale) / scale
    return {f"{cut:.{HOME_DECIMALS}f}", f"{value:.{HOME_DECIMALS}f}"}


def private_values(folder: pathlib.Path) -> set[str]:
    """Home, to about 100 m either way, and the owner's private words, from files outside the repository.

    A file that exists but cannot be read as expected raises PrivateFileError: a
    broken list must stop the commit, never let its words through unchecked.
    """
    out: set[str] = set()
    words = folder / "private.toml"
    listed = _toml(words).get("words", [])
    if not isinstance(listed, list) or not all(isinstance(w, str) for w in listed):
        raise PrivateFileError(words)
    out |= {w for w in listed if len(w) >= MIN_WORD}
    config = folder / "config.toml"
    home = _toml(config).get("home")
    if home is not None:
        if not isinstance(home, dict) or not all(_number(home.get(k)) for k in ("lat", "lon")):
            raise PrivateFileError(config)
        out |= {form for k in ("lat", "lon") for form in _home_forms(float(home[k]))}
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
    """Scan the staged files, every tracked file, or the history; 1 on a finding, 2 when it cannot."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--tracked", action="store_true", help="scan every tracked file")
    parser.add_argument("--history", action="store_true", help="scan every commit on every branch")
    parser.add_argument(
        "--config-dir", help="where config.toml and private.toml are (default ~/.config/fuel-mcp)"
    )
    args = parser.parse_args(argv)
    folder = pathlib.Path(args.config_dir).expanduser() if args.config_dir else config_folder(os.environ)
    try:
        private = private_values(folder)
    except PrivateFileError as error:
        sys.stderr.write(f"private data scan: {error}; fix it, or move it aside.\n")
        return 2
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
