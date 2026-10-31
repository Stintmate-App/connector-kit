# Pitwall write API (v1)

The only way data enters Pitwall's database. A **connector** reads its simulator and sends
messages; Pitwall validates them against the [data model](data-model.md), applies the order
rules and stores them. Guide for connector authors: [connectors.md](connectors.md). JSON Schema
of every message: [write-api.schema.json](write-api.schema.json).

## Transports

One validator and one writer behind both:

- **Pitwall's write methods** (the [connector protocol](connector-protocol.md#31-the-write-methods),
  over Stintmate IPC v2, any language; how connectors write): each message is one method,
  `pitwall.write.<type>`, with the message's fields (no `type`) plus the connector's
  `session_key` (and `seq`) and an idempotency key stored with the write; the result is the
  reply (without `line`), a refusal is the error `bad_request` with the reply in
  `details.reply`. The write API's `hello` is not sent: Pitwall builds it from the connection's
  hello metadata. Pitwall fills `session_id` from the `session_key` and runs one writer per
  connector, in the order received.
- **`pitwall-ingest`** (JSON Lines on stdin/stdout): for imports, tests and dry runs, run by
  whoever operates Pitwall rather than by a connector. **While Pitwall's core runs it is the
  only writer**, so a run that would write is refused at once with one reply
  `{"ok": false, "line": 0, "error": "writer_busy: ..."}` and exit code 2; `--dry-run` always
  works.

```
python -m pitwall.ingest [--db PATH] [--dry-run] [--import] [--outlines DIR | --no-outlines]
    < messages.jsonl
python -m pitwall.ingest --schema          # print the JSON Schema
```

One JSON object per line on stdin (UTF-8; blank lines ignored), one reply per line on stdout,
in order, flushed after each. Exit code 0 = every message accepted, 1 = at least one refused,
2 = it could not write (`writer_busy`: the core or another writer is running; or a database
that is not a Pitwall database of this schema), with one error reply at `"line": 0`; 3 = the
workspace could not be resolved.

## Replies

```json
{"ok": true, "line": 3, "type": "session_start", "session_id": 28}
{"ok": true, "line": 4, "type": "lap", "session_id": 28, "lap": 1, "lap_id": 160}
{"ok": false, "line": 5, "type": "lap", "error": "lap.sectors[2].time_s: must be a number >= 0"}
```

`line` exists only in `pitwall-ingest`. Errors start with the field's path (list indexes are
0-based). **A refused message changes nothing**; the connection goes on with the next message.
In a dry run every reply also has `"dry_run": true`.

| Message | Reply fields |
|---|---|
| `hello` | `model_version`, `contract_version` |
| `catalog` | `classes`, `cars`, `tracks`, `layouts` (entries received) |
| `session_start` | `session_id` |
| `lap` | `session_id`, `lap`, `lap_id`, `stint` (the stint Pitwall stored it in) |
| `pit_exit` | `session_id`, `stint` (the current one), `new_stint` (true: this exit started it) |
| `setup` | `session_id`, `setup` (1..n in the session), `stint`, `stored` |
| `participant_laps` | `session_id`, `rows`, `stored` |
| `session_end` | `session_id`, `stored` (false = no lap: nothing kept), `laps`, `discarded` |

## Messages

Every message has `"type"`. Absent and `null` mean "not given". Full field list, types and
ranges: the JSON Schema.

- **`hello`**: first, once per connection: `sim` (lowercase id, e.g. `ams2`; part of every
  catalogue key), `sim_name`, `connector`, optional `connector_version`, `model_version` (2).
- **`catalog`**: repeatable; upserts by id: `classes` `[{class_id, name}]`, `cars`
  `[{car_id, name, class_id?, fuel_capacity_l?}]` (`fuel_capacity_l`: the tank, L, > 0),
  `tracks` `[{track_id, name}]`, `layouts` `[{track_id, layout_id, name, length_m?}]`. A
  car's class and a layout's track must be catalogued (earlier or in the same message). A
  later upsert keeps a car's class and tank capacity and a layout's length when it does not
  give them.
- **`session_start`**: `started_at` (ISO 8601 with UTC offset), `session_type`, optional
  `car_id`, `track_id` + `layout_id` (both or neither; catalogued), `external_ref`,
  `conditions` `{ambient_c, track_c, rain, wind_speed_ms, cloud}`. Reply: the `session_id` to
  use in the session's messages.
- **`lap`**: `session_id`, `lap_type`, optional `time_s` (absent = not timed), `valid`
  (default true), `time_source` (default `official` when timed), `measured_time_s` (the
  connector's own measurement kept next to an official time; shown only), `started_at`,
  `sim_lap`, `fuel_start_l`, `fuel_used_l`, `max_speed_kmh` (default: the maximum of `speed`),
  `conditions` (at the end of the lap), `sectors` `[{time_s}]` in order (a sector with no
  `time_s` is not timed), `channels`, `on_track` (the car was on the track, outside the pit
  lane, during the lap; when absent Pitwall derives it from the `pit_mode` channel: true if any
  sample is 0; with neither, true). Pitwall numbers the laps 1..n and the stints: a lap has no
  `stint` field (import mode accepts one, see below).
- **`pit_exit`**: `session_id`, `at` (ISO 8601 with UTC offset), optional `fuel_l` (fuel on
  board at the exit, L): the car has just left the pit lane, or driven out of the garage,
  under its own power. Send every lap that ended before the exit first; Pitwall starts the
  next stint if a lap with the car on track came since the previous exit (see
  [Stints](data-model.md#stints)).
- **`setup`**: `session_id`, `taken_at`, `reason` (`session_start` / `pit_exit` /
  `engineer`), `source` (`telemetry` / `engineer`), stored in the current stint (send a
  `pit_exit` setup after the `pit_exit`, so it lands in the new stint), the basic parameters
  (`brake_bias_front_pct`, `compound_front`, `compound_rear`, `pressure_fl_kpa` ...
  `pressure_rr_kpa` cold as set, `fuel_l`, `wing_front`, `wing_rear`, `abs`, `tc`) and `extra`
  `{key: {label, value, group?, unit?}}`. Only what the user sets; never readings.
- **`participant_laps`**: `session_id`, `laps`: rival timing rows `{participant, lap, name,
  car_id?, class_id?, is_player, time_s?, time_source?, sectors?, valid, pit, pit_stops?,
  from_line, position?, crossed_at_s?, sim_best_lap_s?, state?}` (see
  [Rival timing](data-model.md#rival-timing)).
- **`session_end`**: `session_id`, `ended_at`.

### Channels

`channels` maps catalogue names (or `ext_<name>`) to samples; all equally long, at least one
sample; `t`, `dist` and `speed` required when any channel is given. In JSON each channel is
either an array of numbers or, smaller and faster,

```json
{"encoding": "f32", "data": "<base64 of the little-endian float32 samples>"}
```

They are stored as float32. **Send f32**: it is smaller and quicker to parse than an array of
JSON numbers. Over IPC the same channel travels as `{"encoding": "f32", "data": <bytes>}`
with raw bytes instead of base64.

## Order rules

1. `hello` first, once. Anything else before it: `<type>: send hello first`.
2. One session open at a time: `session_start` while one is open is refused; `lap`,
   `setup`, `participant_laps` and `session_end` must carry the open session's id (`lap` with
   no open session: `lap.session_id: no open session (send session_start first)`).
3. References: `car_id` and `track_id`/`layout_id` must be catalogued for the connection's
   `sim`.
4. Pitwall owns the stint: `lap` and `setup` carry none (import mode only: an explicit
   `stint` that may not go back, and its `stints` row is created if missing). A `pit_exit`
   before the session's first lap is kept in the session's memory and starts nothing: no
   lap has been on track yet.
5. **A session is stored with its first lap.** Until then the writer keeps it, and its setups
   and rival rows, in memory; the first lap's transaction writes them all. A `session_end`
   with no lap discards it (`"stored": false`). Its id stays reserved for the writer's life
   (the connector may have published it), so a discarded session leaves a gap; a new writer
   process starts again from the largest id in the database.
6. One transaction per message: a lap (row, sectors, channels) is written whole or not at all.
7. If the connector stops without `session_end`, a stored session stays live (`ended_at` NULL).

Writes over IPC add rules of their own: see
[the write methods](connector-protocol.md#31-the-write-methods) and [contract.md](contract.md).
