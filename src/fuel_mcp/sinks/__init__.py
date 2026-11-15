# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the owner's own reports go: his list here first, then any private sender of his.

The public package holds no sender. A private one lives in the owner's own
repository, found only through his local config:

    [[private_sinks]]
    folder = "~/src/fuel-mcp-private"   # a checkout of it, made up here
    sink = "fuel_mcp_private.sinks.example:ExampleSink"

Importing a package runs every module it imports, so everything is checked
before anything is imported, and any failed check means that sink is not loaded:

1. The whole tree is the owner's. The folder, and every folder and file under
   its ``src``, must be a plain folder or file, not a link, owned by the owner,
   and writable by no one else. Every folder above it must be the owner's or
   root's, and writable by no one else. The one exception is a folder owned by
   root with the sticky bit, such as ``/tmp``: anyone may write in it, but no one
   may remove or rename what another user put there.
2. One named package. ``src`` holds ``fuel_mcp_private`` and nothing else, and no
   package of that name is importable already. Anything else on ``sys.path``
   would stay in ``sys.modules`` for the whole process, shadowing other modules.
3. ``src`` is put first on ``sys.path`` only while the module imports, then
   taken off. So a sink imports everything it needs when it loads.
4. The class is built with no arguments, and must be a sink: a ``label`` of its
   own, ``needs_yes`` exactly True, and a ``take``.

Every entry must name the same folder. Python keeps the first package it
imports under a name, so a second ``fuel_mcp_private`` from another folder
would quietly be the first one's modules. An entry naming another folder is
left out, and the others stay.

A sink that fails a check is left out for the whole run, and nothing is sent
to it. A failed tree check leaves them all out. The log line names the check
and the fix, and never holds a price, a station or a position.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import stat
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fuel_mcp.model import OwnReport, PriceSink, SinkRefusedError
from fuel_mcp.sinks.local import LocalSink

if TYPE_CHECKING:
    import pathlib
    from collections.abc import Collection, Sequence

PACKAGE = "fuel_mcp_private"
NAME = re.compile(r"^fuel_mcp_private(\.[a-z_][a-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*$")
WRITABLE_BY_OTHERS = 0o022
log = logging.getLogger("fuel_mcp")


@dataclass(frozen=True)
class PrivateSinkEntry:
    """One ``[[private_sinks]]`` entry: the folder it sits in, and its ``module:Class``."""

    folder: pathlib.Path
    sink: str


class NotLoadedError(Exception):
    """A check failed; the message names it and the fix, as a sentence for the log."""


def load_private_sinks(entries: Sequence[PrivateSinkEntry]) -> list[PriceSink]:
    """Every listed sink that passes every check; one that fails is logged and left out."""
    if not entries:
        return []  # none listed: the usual case, and nothing to say
    folder = entries[0].folder
    try:
        check_tree(folder)
    except NotLoadedError as error:
        log.warning("fuel-mcp: no private sink is used: %s", error)
        # RULE: a private sink that fails any check sends nothing, and the report is still saved
        return []
    sinks: list[PriceSink] = []
    for entry in entries:
        try:
            sinks.append(_load(entry, folder, {s.label for s in sinks}))
        except NotLoadedError as error:
            log.warning("fuel-mcp: private sink %s is left out: %s", entry.sink, error)
    return sinks


def check_tree(folder: pathlib.Path) -> None:
    """Refuse a private folder that anyone but the owner could have put code in."""
    if not folder.is_absolute():
        msg = f"{folder} is not a full path. Write it from / or ~ in [[private_sinks]]"
        raise NotLoadedError(msg)
    for parent in reversed(folder.parents):
        _check_place(parent, parent=True)
    _check_place(folder, parent=False)
    src = folder / "src"
    _check_place(src, parent=False)
    for top, folders, files in os.walk(src):
        for name in [*folders, *files]:
            _check_place(src.joinpath(top, name), parent=False)
    names = sorted(p.name for p in src.iterdir())
    # RULE: the loader refuses a top-level name other than fuel_mcp_private
    if names != [PACKAGE]:
        msg = f"{src} must hold {PACKAGE} and nothing else; it holds {', '.join(names) or 'nothing'}"
        raise NotLoadedError(msg)
    if importlib.util.find_spec(PACKAGE) is not None:
        msg = f"a {PACKAGE} is importable already, so the one in {src} would not be the one used"
        raise NotLoadedError(msg)


def _check_place(path: pathlib.Path, *, parent: bool) -> None:
    """One folder or file: a plain one, the owner's (or root's, above), writable by no one else."""
    try:
        facts = os.lstat(path)
    except OSError as error:
        msg = f"{path} can't be checked ({error.strerror})"
        raise NotLoadedError(msg) from None
    plain = stat.S_ISDIR(facts.st_mode) or (not parent and stat.S_ISREG(facts.st_mode))
    if not plain:
        msg = f"{path} is a link or not a plain file. Use the real path: {path.resolve()}"
        raise NotLoadedError(msg)
    owners = {os.getuid(), 0} if parent else {os.getuid()}
    shared = parent and facts.st_uid == 0 and bool(facts.st_mode & stat.S_ISVTX)
    # RULE: the loader refuses a private tree with anything not the owner's or writable by others
    if facts.st_uid not in owners or (facts.st_mode & WRITABLE_BY_OTHERS and not shared):
        fix = f"chmod go-w {path}" if facts.st_uid in owners else f"move the private folder out of {path}"
        msg = f"{path} could hold someone else's code: it isn't yours, or others can write it. Fix: {fix}"
        raise NotLoadedError(msg)


def _load(entry: PrivateSinkEntry, folder: pathlib.Path, labels: set[str]) -> PriceSink:
    if entry.folder != folder:
        msg = f"every private sink must sit in one folder, {folder}; this one names {entry.folder}"
        raise NotLoadedError(msg)
    if not NAME.match(entry.sink):
        msg = f"sink must be written {PACKAGE}.<module>:<Class>, not {entry.sink!r}"
        raise NotLoadedError(msg)
    module_name, class_name = entry.sink.split(":")
    saved = list(sys.path)
    sys.path.insert(0, str(folder / "src"))
    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # noqa: BLE001 - any failure at all means not loaded
        by_hand = f"cd {folder}/src; python -c 'import {module_name}'"
        msg = f"importing it raised {type(error).__name__}. Try it by hand: {by_hand}"
        raise NotLoadedError(msg) from None
    finally:
        # RULE: the loader leaves sys.path as it found it
        sys.path[:] = saved
    kind = getattr(module, class_name, None)
    if not isinstance(kind, type):
        msg = f"{module_name} has no class {class_name}"
        raise NotLoadedError(msg)
    try:
        sink = kind()
    except Exception as error:  # noqa: BLE001 - any failure at all means not loaded
        msg = f"{class_name}() raised {type(error).__name__}; it is built with no arguments"
        raise NotLoadedError(msg) from None
    return _checked(sink, labels)


def _checked(sink: object, labels: set[str]) -> PriceSink:
    """`sink`, if it is a sink: its own label, needs_yes exactly True, and a take."""
    label = getattr(sink, "label", None)
    if not isinstance(label, str) or not label.strip() or label in labels or label == LocalSink.label:
        msg = f"its label must be a name of its own, not {label!r}"
        raise NotLoadedError(msg)
    # RULE: the loader refuses a private sink whose needs_yes isn't True
    if getattr(sink, "needs_yes", None) is not True:
        msg = "its needs_yes must be True: nothing leaves this machine without the owner's yes"
        raise NotLoadedError(msg)
    if not callable(getattr(sink, "take", None)):
        msg = "it has no take(report)"
        raise NotLoadedError(msg)
    return sink  # type: ignore[return-value]  # checked just above, field by field


@dataclass
class Sinks:
    """The owner's own list, and the private sinks that passed every check."""

    local: LocalSink
    private: list[PriceSink]

    def take(self, report: OwnReport, labels: Collection[str] = ()) -> dict[str, str]:
        """Save `report` on the owner's list, then send it to each private sink in `labels`.

        The caller holds the owner's yes for exactly `labels`. Returns one sentence per sink
        sent to. A local save that fails raises SinkRefusedError, and then nothing is sent.
        """
        # RULE: a report is saved locally before anything is sent
        self.local.take(report)
        said = {}
        for sink in self.private:
            if sink.label not in labels:
                continue
            try:
                sink.take(report)
            except SinkRefusedError as error:
                said[sink.label] = f"Not sent to {sink.label}: {error}"
            except Exception as error:  # noqa: BLE001 - a sender's crash is said, never raised
                log.warning("fuel-mcp: private sink %s failed: %s", sink.label, type(error).__name__)
                said[sink.label] = f"Not sent to {sink.label}: it failed. The log says why."
            else:
                said[sink.label] = f"Sent to {sink.label}."
        return said
