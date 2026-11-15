# fuel-mcp

Fuel prices near a place, said in one sentence: an MCP server and claude-voice plugin.

Ask for a fuel near a point, and it names the cheapest station in one
sentence. The made-up stations sit around the point 0,0, far out at sea.
They touch no network, and every answer says it is simulated:

```console
$ uv run fuel-mcp price --simulate --lat 0 --lon 0
Simulated: Diesel is cheapest at Fake Fuel Beta, nineteen seventy-nine, 2 kilometres away. Drivstoffpriser's price is about a day old.
Data: Drivstoffpriser (ODbL), stations © OpenStreetMap contributors
```

The same tool is served to a voice session over stdio, and the server stops
when its input closes:

```console
$ uv run fuel-mcp serve --simulate < /dev/null
```

## The tool

| Tool | What it does |
| --- | --- |
| `fuel_price` | Names the cheapest station near you, its price, distance and age. It reads only. |

It takes `fuel` (`diesel`, `petrol95` or `petrol98`), and the
phone's position as `lat`, `lon` and `accuracy_m`. The search widens by the
position's accuracy, which is taken as 100 m when not given.
It looks within 5 km first. With no price from the last week there, it
steps out to 10 km, then 20 km, and says how far it had to look. With none
from the last week at all, it gives the latest it found and its date. When
even that is older than 30 days, it starts with "Only an old price". With no
position, it asks where. A price more than a week old is never said as
current: it is given with the day it was last reported.

## Real prices

No price source is on until you list one. Settings live in
`~/.config/fuel-mcp/config.toml`, never in this repository. Home is used
when the voice gives no position, and `fuel` when a question names none:

```toml
sources = ["drivstoffpriser"]   # one source; with none listed, nothing is fetched
fuel = "petrol95"               # diesel when not set
farthest_km = 20                # how far the search steps out, 1 to 100 km; 20 when not set
old_after_days = 30             # older is said as only an old price, 7 to 3650 days; 30 when not set
[home]
lat = 0.0
lon = 0.0
```

With Drivstoffpriser listed, its export is checked at most once an hour,
and only when someone asks
([ADR-FM-001](decisions/ADR-FM-001-check-the-export-hourly-with-a-conditional-get.md)).
A question waits on the host for 8 seconds in all. After that it answers
from the copy, or says the data isn't available.
The copy is kept in `~/.cache/fuel-mcp/`. Its times are read as Norwegian
time, because the app stores the reporting phone's local time.

A mistake in the file is said with the fix:

```console
$ XDG_CONFIG_HOME=tests/settings-examples uv run fuel-mcp price
unknown source 'petrolbot'; the known ones are drivstoffpriser. Fix sources in tests/settings-examples/fuel-mcp/config.toml
```

## The data

Data: Drivstoffpriser (ODbL), stations © OpenStreetMap contributors.

Prices come from [Drivstoffpriser](https://github.com/Drivstoffpriser/Drivstoffpriser-App),
a community project whose open data is free to use for any purpose. Every
answer carries that credit in its `credit` field, and says how old its price is.

## The gates

Every one of these runs in continuous integration, and all of them run here:

```console
$ uv run ruff check .
All checks passed!
```

<!-- not run: the workflow runs each of these; nesting them here would run the suite inside itself -->
```bash
uv run ruff format --check .   # layout
uv run mypy src                # types
uv run pytest -q               # behaviour
python scripts/mutate.py       # that each rule's own test can fail
```

`CONTRIBUTING.md` says what each one is for, and
[`decisions/`](decisions/README.md) records the choices that shaped them,
including how a change is made here.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
