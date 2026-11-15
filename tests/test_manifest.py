# SPDX-License-Identifier: GPL-3.0-or-later
"""The manifest the voice's installer reads lists exactly the tools the server offers.

``plugin-manifest-v1.json`` beside this test is the bridge's schema, copied
byte for byte from claude-voice's ``docs/plugin-manifest/v1.json`` (commit
a18c8ee439bc). A copy, so the check needs no network; when the bridge's format
changes, the copy is replaced and this test says what no longer fits.
"""

import asyncio
import json
import pathlib
import tomllib

import jsonschema

from fuel_mcp.server import build_server
from fuel_mcp.service import FuelService
from fuel_mcp.simulator import FakeSource

ROOT = pathlib.Path(__file__).parent.parent
MANIFEST = ROOT / "claude-voice-plugin.toml"
SCHEMA = pathlib.Path(__file__).with_name("plugin-manifest-v1.json")


def _manifest():
    with MANIFEST.open("rb") as file:
        return tomllib.load(file)


def test_the_manifest_fits_the_bridge_s_format():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(_manifest())


def test_the_manifest_lists_exactly_the_tools_the_server_offers():
    served = [t.name for t in asyncio.run(build_server(FuelService(FakeSource())).list_tools())]
    assert _manifest()["tools"] == served


def test_the_manifest_starts_the_server_the_readme_names():
    manifest = _manifest()
    assert (manifest["name"], manifest["command"], manifest["args"]) == (
        "fuel",
        "uv",
        ["run", "fuel-mcp", "serve"],
    )
