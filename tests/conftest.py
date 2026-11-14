# SPDX-License-Identifier: GPL-3.0-or-later
"""No test reaches the network: every internet connection is refused.

Local socket pairs stay allowed, because asyncio's event loop wakes itself
through one.
"""

import socket

import pytest


class NetworkRefusedError(RuntimeError):
    """A test tried to open an internet connection."""


def _refuse(*_args, **_kwargs):
    msg = "a test tried to reach the network; feed it a recorded response instead"
    raise NetworkRefusedError(msg)


_connect = socket.socket.connect


def _connect_unless_internet(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _refuse()
    return _connect(self, address)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _connect_unless_internet)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_unless_internet)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
