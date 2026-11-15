# SPDX-License-Identifier: GPL-3.0-or-later
"""Files only the owner may read, written whole or not at all.

A private file is written to a new file beside it, then moved into place with
``os.replace``. A reader sees the old file or the new one, never half of either.
The new file is made by this process alone, with ``O_EXCL`` and ``O_NOFOLLOW``,
and set to 0600 before anything is written to it.

Its folder must be the owner's own, not a link, and closed to everyone else.
A folder of his that is open to others is refused loudly, not quietly closed:
he should see that it was open. The refusal names the folder and the fix,
``chmod go-rwx <folder>``, with one log line. The log never holds what the file
holds.

The cache of public prices doesn't use this: it holds nothing private.
"""

from __future__ import annotations

import contextlib
import logging
import os
import secrets
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pathlib

# RULE: a private file is readable by its owner only
MODE = 0o600
FOLDER_MODE = 0o700
NEW = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
OPEN_MINE = os.O_RDONLY | os.O_NOFOLLOW
OPEN_FOLDER = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
log = logging.getLogger("fuel_mcp")


class PrivateFileError(OSError):
    """A private file can't be used safely; the message names the file and the fix."""


def write_private(path: pathlib.Path, data: bytes) -> None:
    """Put `data` at `path`, readable by the owner only; raises PrivateFileError."""
    path.parent.mkdir(mode=FOLDER_MODE, parents=True, exist_ok=True)
    _check_folder(path.parent)
    # RULE: a private write refuses a link where the file goes
    if path.is_symlink():
        msg = f"{path} is a link, so it isn't written. Remove the link: rm {path}"
        raise PrivateFileError(msg)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, NEW, MODE)
    try:
        os.fchmod(fd, MODE)  # the umask may have taken more away; never less
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temp.replace(path)
    except BaseException:
        # RULE: a failed private write leaves the old file and no temporary
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()
        raise


def _check_folder(folder: pathlib.Path) -> None:
    """Refuse a folder that is a link, someone else's, or open to others; each with its fix."""
    try:
        fd = os.open(folder, OPEN_FOLDER)
    except OSError as error:  # ELOOP: a link; ENOTDIR: not a folder
        msg = f"{folder} can't hold private files ({error.strerror}). Remove it: rm {folder}"
        log.warning("fuel-mcp: %s", msg)
        raise PrivateFileError(msg) from None
    try:
        facts = os.fstat(fd)
    finally:
        os.close(fd)
    mine = facts.st_uid == os.getuid()
    # RULE: a private file's folder is the owner's and closed to others
    if not mine or stat.S_IMODE(facts.st_mode) & 0o077:
        if mine:
            msg = f"{folder} is open to others, so nothing private goes in it. Fix: chmod go-rwx {folder}"
        else:
            msg = (
                f"{folder} isn't a folder of yours, so nothing private goes in it. Remove it: rm -r {folder}"
            )
        log.warning("fuel-mcp: %s", msg)
        raise PrivateFileError(msg)


def read_private(path: pathlib.Path) -> bytes | None:
    """The bytes at `path`, or None when there is no file; raises PrivateFileError.

    A link, or a file another user owns, is refused: either could be someone else's words.
    """
    try:
        fd = os.open(path, OPEN_MINE)
    except FileNotFoundError:
        return None
    except OSError as error:  # ELOOP: a link stands where the file should be
        msg = f"{path} can't be read safely ({error.strerror}). Remove it: rm {path}"
        raise PrivateFileError(msg) from None
    with os.fdopen(fd, "rb") as file:
        facts = os.fstat(file.fileno())
        # RULE: a private file another user owns is not read
        if facts.st_uid != os.getuid() or not stat.S_ISREG(facts.st_mode):
            msg = f"{path} isn't a file of yours, so it isn't read. Remove it: rm {path}"
            raise PrivateFileError(msg)
        return file.read()
