# SPDX-License-Identifier: GPL-3.0-or-later
"""No test reaches the network: every socket is refused before it opens."""

import socket

import pytest


class NetworkRefusedError(RuntimeError):
    """A test tried to open a socket."""


def _refuse(*_args, **_kwargs):
    msg = "a test tried to reach the network; feed it a recorded response instead"
    raise NetworkRefusedError(msg)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
