# SPDX-License-Identifier: GPL-3.0-or-later
"""The public repository holds no code that sends a report anywhere.

Any real sender is the owner's own, for his own use, kept in a private repository.
So nobody can take this one and use it to automate reports. Three checks hold it:

* the module list, the strong one: the sinks package has the loader and the
  local list, and neither imports a network client;
* the dependencies: no reporting client is installed with the plugin;
* the host scan, a tripwire and not a proof: a URL can be put together from
  pieces, so it only catches the plain case.
"""

import ast
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).parent.parent
SINKS = ROOT / "src" / "fuel_mcp" / "sinks"
NETWORK = {"httpx", "urllib", "socket", "requests", "http", "aiohttp", "ssl", "grpc"}
#: Reporting clients and hosts, split so this file doesn't trip its own scan.
CLIENTS = re.compile("^(" + "fire" + "base.*|google-cloud-.*|pyre" + "base.*|gcloud.*)$")
HOSTS = ["fire" + "store", "fire" + "base", "app" + "check", "api." + "drivstoffpriser"]


def _imported(path):
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_the_public_package_holds_no_sender():
    modules = sorted(p.relative_to(SINKS).as_posix() for p in SINKS.rglob("*.py"))
    assert modules == ["__init__.py", "local.py"], "a sender belongs in the owner's private repository"
    for module in modules:
        assert not _imported(SINKS / module) & NETWORK, f"sinks/{module} imports a network client"


def test_no_reporting_client_is_a_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    declared = [*project.get("dependencies", [])]
    for extra in project.get("optional-dependencies", {}).values():
        declared += extra
    names = {re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].lower() for d in declared}
    locked = {p["name"].lower() for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]}
    assert not {n for n in names | locked if CLIENTS.match(n)}


def _scanned():
    for folder in ("src", "scripts", "tests"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".toml", ".md", ".txt", ".json"}:
                yield path, path.read_text(errors="replace")
    for path in sorted(ROOT.glob("*.md")):
        blocks = re.findall(r"^```.*?$(.*?)^```", path.read_text(), flags=re.MULTILINE | re.DOTALL)
        yield path, "\n".join(blocks)


def test_no_reporting_host_is_named_anywhere():
    """A tripwire, not a proof: a host built from pieces, as HOSTS is, passes it."""
    named = [
        f"{path.relative_to(ROOT)} names {host}"
        for path, text in _scanned()
        for host in HOSTS
        if host in text.lower()
    ]
    assert named == []
