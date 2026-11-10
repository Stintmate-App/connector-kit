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
- **`pitwall-ingest`** (JSON Lines on stdin/stdout; an import tool of Pitwall, not part of this
  kit and not run by a connector author; over IPC use the write methods of
  [connector-protocol.md](connector-protocol.md)): for imports, tests and dry runs, run by
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
| `sim_data` | `session_id`, `record` (1..n in the session, all kinds together), `stint`, `stored` |
| `sim_data_kinds` | `kinds` (how many were given), `changed` (the kinds whose stored description is new or different) |
| `participant_laps` | `session_id`, `rows`, `stored` |
| `session_end` | `session_id`, `stored` (false = no lap: nothing kept), `laps`, `discarded` |

## Messages

Every message has `"type"`. Absent and `null` mean "not given". Full field list, types and
ranges: the JSON Schema. Ids are at most 200 characters and names at most 500. `session_id`,
`lap` and `lap_id` fields exist for import mode only.

- **`hello`**: first, once per connection: `sim` (lowercase id, `^[a-z0-9][a-z0-9_-]{0,31}$`, e.g. `ams2`; part of every
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
  (default true), `time_source` (`official` or `measured`; default `official` when timed), `measured_time_s` (the
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
  `{key: {label, value, group?, unit?}}`. Only what the user sets; never readings (those go in
  `sim_data`).
- **`sim_data`**: simulator data, for values the game gives **at a moment** (a pit stop, a
  reset, a garage visit, a penalty notice, a damage report...) that no typed message above
  carries. Fields: `session_id`, `taken_at` (ISO 8601 with UTC offset, when the simulator gave
  the data), `kind` (your name for this structure: `^[a-z][a-z0-9_]{0,31}$`; names are your own,
  scoped by the connection's `sim`) and `data`, a JSON object whose structure you design. Pitwall
  stores it as sent, in the current stint, with the number of the last lap it holds, and does
  not compare records: two equal messages are two records, so send one only when the simulator
  gave something new. Never invent a value the simulator did not give. It is **not for samples**:
  per-sample data goes in a lap's `channels` (a catalogue name, or `ext_<name>` for your game's
  own channel). Limits on `data` are listed under [Limits of `sim_data`](#limits-of-sim_data).
  Declare every kind with `sim_data_kinds` before sending records of it. See
  [Simulator data](data-model.md#simulator-data).
- **`sim_data_kinds`**: not session-bound (like `catalog`; send it at any time after `hello`).
  Describes each kind so that a person or an AI agent reading the data does not have to guess:
  `kinds`, 1 to 32 items with `kind` unique, each `{kind, description, topic?, fields}`.
  `description` (1 to 500 characters) says what a record is and when you send one. `topic`
  (optional) is one of `setup`, `capture`, `telemetry`, `session`, `car`, `track`, `tyres`,
  `fuel`, `strategy`, `controls`, `other`; it lets Pitwall's reports point at related data
  without knowing your game. `fields` (1 to 64 entries) maps a **field path** to `{meaning,
  unit?}`: a path is the dotted keys of `data` (`wheels.fl.tread_mid`), a `*` segment stands for
  any key or array index (`wheels.*.tread_mid`), at most 6 segments and 128 characters, each
  segment `[A-Za-z0-9_*-]` up to 64; `meaning` is 1 to 300 characters and `unit` 1 to 24 (`C`,
  `kPa`, `s`, `m`, `fraction`...). Pitwall stores the description per (`sim`, `kind`) and the
  latest one is the one read, so it is safe to send it again at every session start. **A kind's
  fields never change meaning**: adding or dropping fields is fine, but a field whose meaning or
  unit changes gets a new kind name (`stall_tyres_v2`), so records already stored keep a
  description that fits them. A `sim_data` whose kind was never described is still stored, and
  reported as undescribed.
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

### Limits of `sim_data`

Pitwall checks `data` in one pass and names the offending path in its error reply; the same
numbers are in [`contract.json`](../contract.json) under `limits`.

| Limit | Value | Error text |
|---|---|---|
| Type | a JSON object | `sim_data.data: must be an object` |
| Size | at most 16,384 bytes as compact UTF-8 JSON | `sim_data.data: larger than 16384 bytes` |
| Depth | at most 6 nested containers (`data` itself is 1) | `sim_data.data.<path>: nested deeper than 6` |
| Keys | at most 512 keys in all objects together; each `^[A-Za-z0-9_-]{1,64}$` (no dots: dots separate field paths) | `sim_data.data: more than 512 keys` / `sim_data.data.<path>: bad key` |
| Arrays | at most 256 items each | `sim_data.data.<path>: more than 256 items` |
| Values | string (at most 500 characters), number, boolean, null, object, array | `sim_data.data.<path>: not a JSON value` |
| Numbers | finite; integers within +-2^53 | `sim_data.data.<path>: not a finite number` |
| Per session | at most 5,000 records | `sim_data: the session already holds 5000 records` |

The JSON Schema can only say "an object with these key names"; the rest is Pitwall's check.

## Order rules

1. `hello` first, once. Anything else before it: `<type>: send hello first`.
2. One session open at a time. In the `pitwall-ingest` import tool `session_start` while one is
   open is refused; over IPC a `session_start` with a new key closes the open session (see
   [connector-protocol.md section 3.1](connector-protocol.md)). `lap`,
   `setup`, `sim_data`, `participant_laps` and `session_end` must carry the open session's id (`lap` with
   no open session: `lap.session_id: no open session (send session_start first)`).
3. References: `car_id` and `track_id`/`layout_id` must be catalogued for the connection's
   `sim`.
4. Pitwall owns the stint: `lap`, `setup` and `sim_data` carry none (import mode only: an explicit
   `stint` that may not go back, and its `stints` row is created if missing). A `pit_exit`
   before the session's first lap is kept in the session's memory and starts nothing: no
   lap has been on track yet. Send every lap that ended before a `sim_data` first, and a
   stop's data before that stop's `pit_exit` when it belongs to the stint that ends there.
   `sim_data_kinds` may come at any time after `hello`.
5. **A session is stored with its first lap.** Until then the writer keeps it, and its setups,
   simulator data and rival rows, in memory; the first lap's transaction writes them all. A `session_end`
   with no lap discards it (`"stored": false`). Its id stays reserved for the writer's life
   (the connector may have published it), so a discarded session leaves a gap; a new writer
   process starts again from the largest id in the database.
6. One transaction per message: a lap (row, sectors, channels) is written whole or not at all.
7. If the connector stops without `session_end`, a stored session stays live (`ended_at` NULL).

Writes over IPC add rules of their own: see
[the write methods](connector-protocol.md#31-the-write-methods) and [contract.md](contract.md).
