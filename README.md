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
| `fuel_price` | Names the cheapest station within about 5 km, its price, distance and age. It reads only. |

It takes `fuel` (`diesel`, the default, `petrol95` or `petrol98`), and the
phone's position as `lat`, `lon` and `accuracy_m`. The search widens by the
position's accuracy, which is taken as 100 m when not given. With no
position, it asks where.

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
