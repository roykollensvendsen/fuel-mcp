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

## The tools

| Tool | What it does |
| --- | --- |
| `fuel_price` | Names the cheapest station near you, its price, distance and age. It reads only. |
| `fuel_report` | Saves a pump price you say as your own report, on this machine. |
| `fuel_report_undo` | Takes your latest report back, within 10 minutes. |
| `fuel_report_confirm` | Sends the report on, only after your yes to its question. |

`claude-voice-plugin.toml` tells the voice's installer how to start the server
and which tools it serves. A test holds it to the installer's format and to the
tools the server really offers.

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

## Reporting a price by voice

Say the price you see at the pump, such as "diesel is 21.49 here". The voice
calls `fuel_report` with your position. It saves at once and says it back:
"Saved: diesel twenty-one forty-nine at …, as your own report. Say 'undo' to
take it back." Nothing is sent anywhere by this.

The station is the one within 300 m of you, widened by the position's
accuracy. When two are that close, it asks which, and saves nothing yet. A
position rougher than 300 m is not used: say the station's name instead. A name
is matched without minding case or accents, against your own names first, then
the station list. A price outside `price_low` to `price_high` is said back as
looking wrong, and not saved.

Ask for a price afterwards and your report counts beside the source's prices.
The newest price for a station wins. Yours is said as your own, with its age,
until `own_report_hours` have passed. A price the source only estimates is said
apart, and never chosen over a reported one.

With a sender of your own set up, the voice then asks "Send it to … too?". Only
your yes sends it, once, within 2 minutes. "Only <service>" narrows it. One
service refusing leaves the others sent. A sender silent past `send_timeout_s`
is said as "may have been sent". Undo never takes back what was sent.

```toml
send_timeout_s = 15         # how long one send may take, 1 to 60 seconds; 15 when not set
[station_names]             # your own names for stations, matched first
"my usual" = "osm_1"        # a station's id from the list; this one is made up
```

## Your own reports

A price you report yourself is kept on your own list, on this machine only.
The list is `~/.local/state/fuel-mcp/own-reports.jsonl`, readable by you
alone. Each line holds the station's id and name, the fuel, the price and the
time. It never holds a position. Stations and times still show where you were,
so a report is removed once its keep time has passed. These settings go in the
same file as the ones above:

```toml
sinks = ["local"]          # your own list; it is always on, even if left out
own_report_hours = 12      # how long your report counts, 1 to 168 hours; 12 when not set
own_report_keep_hours = 12 # how long it is kept, at least the hours above, up to 2160; 12
price_low = 10             # a price below this per litre is not believed; 10 when not set
price_high = 40            # a price above this is not believed; 40 when not set
```

This repository holds no code that sends a report anywhere, and its tests
check that. A sender of your own lives in a private repository of yours. It is
named only in your local settings, under `[[private_sinks]]`, with a folder and
a `fuel_mcp_private.<module>:<Class>`. The loader in `src/fuel_mcp/sinks/`
checks that folder before it imports anything. If any check fails, nothing is
sent, and the report is still kept on your list.

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
