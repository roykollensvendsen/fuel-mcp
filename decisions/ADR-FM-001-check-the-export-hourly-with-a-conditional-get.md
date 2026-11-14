# ADR-FM-001: Check the open export at most hourly, with a conditional GET

## Status

Accepted, 2026-11-14, by the claude-voice manager, in the handover for this
plugin's first build. The design had said every 12 hours; the manager chose
hourly instead.

## Context

Prices come from Drivstoffpriser's open export: two static files,
`stations.json` and `prices.json`, on GitHub Pages
([the data page](https://drivstoffpriser.github.io/Drivstoffpriser-App/data/index.html)).
The page says the files are "updated every 12 hours", and gives no schedule.

On 2026-11-14 one header request to each file showed this:

- Both carry an `ETag` and a `Last-Modified` date.
- A request sending the `ETag` back as `If-None-Match` got `304 Not Modified`,
  with no body.
- `stations.json` is about 420 kB and `prices.json` about 35 kB.
- GitHub Pages sends `Cache-Control: max-age=600`, so a copy is fresh for ten
  minutes.

Without a schedule, a 12-hour timer can fall just before an export. The
plugin would then say prices up to a day old while newer ones sat unread.

## Options considered

- **Fetch both files in full every 12 hours.** The design's first choice. It
  is cheap, but out of step with the export it lags by up to 12 hours more.
- **Fetch on every question.** Freshest, but it sends about 450 kB each time,
  and a voice on a phone waits for it. A volunteer project's host pays for it.
- **Do nothing: fetch once at start.** A server left running for days would
  say ever older prices.
- **Check at most hourly, with a conditional GET.** Chosen.

## Decision

When a question comes, the plugin uses its local copy if it was checked
within the last hour. Otherwise it asks for each file with `If-None-Match`
and `If-Modified-Since` from the last answer. A `304` costs no body and
restarts the hour. A `200` replaces the copy. If the check fails and a copy
exists, the copy is used, and the answer still says how old the price is.
There is no timer: nothing is fetched unless someone asks.

## Consequences

- A new export reaches an answer within an hour of the next question.
- Most checks are a `304` of a few hundred bytes, so 24 a day cost less than
  one full fetch.
- The plugin keeps a copy and its tags under the user's cache folder, which
  is one more thing on disk.
- Worse: the first question after an hour waits for two round trips, even
  when nothing changed.
- If the host stops sending `ETag` and `Last-Modified`, every hourly check
  becomes a full fetch. That is about 11 MB a day for a busy user, and
  nothing warns about it.

## Related

- The source module, `src/fuel_mcp/sources/drivstoffpriser.py`.
- The design note this departs from, kept outside the repository.
