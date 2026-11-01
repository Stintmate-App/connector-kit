# Pitwall data model (model version 2)

Pitwall's own, simulator-neutral model of what a driver does on track. It is stored natively in
SQLite, knows no simulator, and receives data only through the [write API](write-api.md) from
**connectors**, one per simulator ([guide](guide.md), [connectors](connectors.md)). A connector
never opens the database: this page tells you what the messages you send turn into, which
vocabularies you map your simulator onto and which channels exist.

## Principles

- **Opaque ids, readable names.** Classes, cars, tracks and layouts are keyed by
  `(sim, id)`; the connector gives the id and a display name. Pitwall never parses an id.
- **Fixed vocabulary and units** where Pitwall reasons (session type, lap type, setup basics,
  channels); free text where it only shows (names, compounds, a rival's race state).
- **Optional means absent.** Weather, fuel, sectors, setups, rivals and every channel except
  `t`, `dist`, `speed` are optional; analysis says what is missing instead of failing.
- **Pitwall's ids:** `session_id`, lap numbers 1..n per session and `lap_id` are assigned by
  Pitwall. Only an importer of old data (the write API's import mode) gives them explicitly.

## Entities

| Entity (table) | Key | Fields (optional unless marked) |
|---|---|---|
| Source (`sources`) | `sim` | `sim_name` (required), `connector`, `connector_version` (last `hello`) |
| Car class (`car_classes`) | `(sim, class_id)` | `name` (required) |
| Car (`cars`) | `(sim, car_id)` | `name` (required), `class_id`, `fuel_capacity_l` (tank, L) |
| Track (`tracks`) | `(sim, track_id)` | `name` (required) |
| Layout (`layouts`) | `(sim, track_id, layout_id)` | `name` (required), `length_m` |
| Session (`sessions`) | `session_id` | `sim`, `connector`, `started_at`, `type` (required); `external_ref`, `ended_at` (absent = live), `car_id`, `track_id` + `layout_id`, conditions at the start |
| Lap (`laps`) | `lap_id`; unique `(session_id, lap)` | `lap` (1..n), `stint` (Pitwall's), `type`, `valid`, `on_track` (the car was on the track, outside the pit lane; default true), `samples` (required); `time_s` (absent = not timed), `time_source`, `measured_time_s`, `started_at`, `sim_lap`, `fuel_start_l`, `fuel_used_l`, `max_speed_kmh`, conditions at the end |
| Sector (`lap_sectors`) | `(lap_id, sector)` | `time_s` (NULL = not timed); sectors 1..n, as many as the simulator has |
| Channel (`lap_channels`) | `(lap_id, channel)` | `data`: zlib(float32 little-endian samples) |
| Stint (`stints`) | `(session_id, stint)` | `reason` (`session_start` / `pit_exit` / `engineer`, the last reserved), `started_at`, `fuel_l` (fuel on board at the start, if known) |
| Setup (`setups`) | `(session_id, setup)` | `stint`, `taken_at`, `reason`, `source`, `extra` (required); the basic parameters |
| Participant lap (`participant_laps`) | `row_id`; indexed `(session_id, participant, lap)` | `participant`, `lap`, `name`, `is_player`, `valid`, `pit`, `from_line` (required); `car_id`, `class_id`, `time_s`, `time_source`, `sectors` (JSON array), `pit_stops`, `position`, `crossed_at_s`, `sim_best_lap_s`, `state` |
| Meta (`meta`) | `key` | `model_version` = 2, `contract_version` = 1 |

Pitwall also keeps a track outline per layout, computed from the layout's best clean lap; you
send nothing for it.

Times are ISO 8601 text **with a UTC offset** (`2026-10-18T18:00:00+02:00`).

## Vocabularies

Connectors map their simulator's own values onto these. The exact lists are in
[`contract.json`](../contract.json) (in the repository; the release zip does not carry it).

- **Session type:** `practice`, `qualifying`, `race`, `warmup`, `other`.
- **Lap type:** `formation` (before a race start, never timed), `out` (leaves the pits or the
  garage), `in` (ends in the pits), `flying` (line to line on track).
- **Clean lap** = `flying`, timed (`time_s` not NULL) and `valid`. Best laps, stints' bests
  and best sectors use clean laps only.
- **Time source:** `official` (the simulator's time) or `measured` (the connector's clock);
  rival laps may also be `sectors` (summed from the sector times). A lap may also keep
  `measured_time_s`, the connector's own measurement next to an official time (a sanity
  check, shown only). A lap is *timed* when it has `time_s` (there is no separate "complete"
  flag) and its fuel at the end is `fuel_start_l - fuel_used_l` (not stored).
- **Setup reason:** `session_start`, `pit_exit`, `engineer`. **Setup source:** `telemetry`
  (read from the simulator), `engineer` (written by the race engineer).
- **Phase** (the live frame and `connector.status`, [protocol](connector-protocol.md)):
  `menus`, `garage`, `on_track`, `paused`.
- **Conditions** (session at its start, lap at its end): `ambient_c` C, `track_c` C, `rain`
  0..1, `wind_speed_ms` m/s, `cloud` 0..1 (cloud cover). A value your simulator gives on
  another scale is left out rather than converted into something it is not.
- **Surface** (`<wheel>_surface` channels): `0` road (the racing surface, pit lane
  included), `1` kerb, `2` grass, `3` gravel, `4` sand, `5` run-off (paved area beyond the
  track limits), `6` track-limit strip (a painted strip marking the limits), `7` wall
  (touching a barrier), `8` other off-track (dirt, verge, bank). Off track = codes 2..8; two
  or more wheels off track is an excursion; 7 is wall contact. A simulator material that is
  not clearly one of these is road.
- **Pit mode** (`pit_mode` channel): `0` on track, `1` driving into the pits, `2` in the pit
  box, `3` driving out of the pits (or the garage), `4` in the garage. Only 0 is on track.

### Stints

Pitwall decides the stints; the connector only reports the fact (`pit_exit`, the car left
the pit lane or the garage under its own power) and per lap `on_track`. The rule, the same for
every simulator:

> Stint 1 starts with the session. A pit exit after the car has been on track in the current
> stint starts the next stint. The first exit of a session (leaving the garage) stays in
> stint 1. Laps never go back to an earlier stint.

A lap and a setup are stored with the current stint. The `stints` table has a row from the
pit exit (`reason` `pit_exit`, `started_at` = the exit's `at`, `fuel_l`), so a stint with no
lap yet survives a restart; stint 1's row is written with the session (`session_start`).
Pitwall keeps the state per open session (`stint`, whether a lap of it was on track); a
reopened session takes it from the tables. `laps.on_track` is false for a lap that never left
the pit lane (the `in` lap of a stop the car did not drive out of, a teleport to the pits).

### Setups

A setup holds what the user **sets**, never readings (hot pressures, carcass temperatures and
ambient are telemetry and conditions). Basic parameters are columns, all optional:

| Column | Meaning |
|---|---|
| `brake_bias_front_pct` | brake bias to the front, % (0..100) |
| `compound_front`, `compound_rear` | tyre compound names, as the simulator shows them |
| `pressure_fl_kpa` ... `pressure_rr_kpa` | cold pressures as set in the garage, kPa |
| `fuel_l` | fuel loaded, L |
| `wing_front`, `wing_rear`, `abs`, `tc` | settings on the simulator's own scale |

Everything else goes in `extra`: a JSON object keyed by the connector's own key, each entry
`{"label", "group", "value", "unit"}` (springs, bars, dampers, camber, toe, ride heights,
differential, gears, a simulator's own electronics). Readers show `extra` as label + value
lists, grouped. A stint's setup is its last setup; changes between stints are found by
comparing the columns and the `extra` entries.

### Rival timing

One row per participant (the player included, `is_player`) and lap **counted by the
simulator** (`lap` = laps completed after the crossing). `pit` = the lap touched the pit lane;
`pit_stops` = pit visits seen up to the crossing; `from_line` = the crossing that started the
lap was seen; `crossed_at_s` = the connector's clock at the crossing, seconds since the session
started (race gaps are differences of crossing times at the same lap); `state` = the
simulator's race state label, shown only. A participant index can be reused by another driver
in the same session, so readers group by `(participant, name)`.

## Channel catalogue

A lap's channels are equally long float32 arrays, one value per sample in time order (a
connector chooses its rate; the reference recorders sample at about 50 Hz). `t`, `dist` and
`speed` are **required** when a lap has channels; `x` and `z` are needed for track outlines,
track maps and corners; everything else is optional. Connector-specific channels use the
prefix `ext_` (stored, never analysed): `ext_` plus 1..60 ASCII letters, digits or
underscores, at most 64 characters in all.

| Channel | Unit | Meaning |
|---|---|---|
| `t` | s | lap clock since the line; -1 while not timed (grid, formation lap, pits/garage) |
| `dist` | m | distance from the start/finish line, 0..layout length |
| `speed` | km/h | car speed |
| `throttle`, `brake`, `clutch` | 0..1 | pedals, unfiltered driver input |
| `steer` | -1..1 | steering, > 0 = right |
| `gear` | gear | -1 reverse, 0 neutral, 1..n |
| `rpm` | rpm | engine speed |
| `g_lat` | g | lateral, > 0 = cornering to the right (same sign as `steer`) |
| `g_long` | g | longitudinal, > 0 accelerating, < 0 braking |
| `g_vert` | g | vertical |
| `x`, `y`, `z` | m | world position; `y` up; `(x, z)` plan view |
| `fuel` | L | fuel in the tank |
| `abs`, `tc` | 0/1 | 1 while ABS / traction control intervene |
| `invalid` | 0/1 | 1 once the simulator flagged the lap invalid |
| `pit_mode` | code | pit mode code (above) |

Per wheel, prefix `fl_`, `fr_`, `rl_`, `rr_`:

| Channel | Unit | Meaning |
|---|---|---|
| `t_in`, `t_mid`, `t_out` | C | tread temperature, inner (towards the centreline) / middle / outer |
| `t_carcass`, `t_air` | C | carcass and internal air temperature |
| `pressure` | kPa | live (hot) tyre pressure |
| `wear` | 0..1 | tyre wear |
| `brake_t` | C | brake temperature |
| `susp` | mm | suspension travel |
| `susp_vel` | sim units | damper velocity |
| `ride` | cm | ride height |
| `surface` | code | surface code (above) |
| `rps` | rev/s | wheel rotation speed |

**Uncertain or simulator-specific.** Simulators do not always document these, so the analysis
does not depend on them:

- `clutch`: which end is "pressed" may not be documented; not used by the analysis.
- `g_vert`: sign and whether gravity is included may not be documented; not used.
- `wear`: 0..1 without saying which end is a new tyre; reports show it as given.
- `susp`: zero reference and sign may not be documented; reports show its range.
- `susp_vel`: often "rate of change of pushrod deflection", units may not be documented;
  reports treat > 0 as bump and compare values relatively.
- `rps`: sign as the simulator gives it (may be negative when rolling forward); the analysis
  uses only ratios between wheels.
- `surface` and `pit_mode` are Pitwall's own codes: each connector maps its simulator's
  materials and pit states onto them.
