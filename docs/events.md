# Pitwall's channels (what pitwall-core publishes)

What Pitwall stores and what its connector says, published by **pitwall-core** on the endpoint
`pitwall` of Stintmate IPC v2. The normative list is the contract, [contract.md section 4.5](contract.md);
this page is the field reference for the channels a connector's author sees: the ones that
report the connector's own writes and status. Pitwall does not know who subscribes: a voice
app can call each lap and speak the announcements, the hub shows the recording in its tray,
a control app shows the connector. A subscriber needs nothing else from Pitwall (no imports, no
shared code, no files).

## Subscribing

- Connect to `pitwall` (`\\.\pipe\stintmate-pitwall-<user>`, key `<workspace>\ipc\key`) and
  `sub` the channels you need ([SPEC 13](https://github.com/Stintmate-App/stintmate-ipc/blob/main/SPEC.md#13-ordering)); the `stintmate-ipc` package's `Client` keeps the
  subscriptions across reconnections.
- **Event channels are live only**: a subscriber that was down misses what happened. **State
  channels** give the current value on every (re)subscribe.
- All event channels of one connection keep send order: a subscriber of both sees
  `pitwall.sessions.started` before that session's `pitwall.laps.completed`.
- `evt.ts` is the core's QPC time (judge staleness by it); `at` is the wall clock (ISO 8601
  with offset and milliseconds). Units: seconds, km/h, litres, °C, metres. Missing or unknown
  values are `null`. Receivers ignore unknown fields (fields are added within a version, never
  renamed).

## State channels

### `pitwall.connectors`

Session, lap and stint events are the followed connector's; the sessions of the other connectors show in `pitwall.connectors.connectors[].recording`.

The core and the connected connector (also the result of `pitwall.connectors.get`):
`{core: {pid, version, run, started_at, workspace, data_dir}, connector: {id, name, version,
sim, sim_name, connected_at, functions, services: [{service, pid, connected_at, status}],
recording} | null, connectors: [entry, ...], active: str | null, refused: [{app, sim?, at, code}]}`. `functions` are the names its connections
declare (`connector.live_frame`, `connector.capture_target`, `connector.telemetry`,
`connector.status`); `status` is each service's last `connector.status`
([connector-protocol.md 2.1](connector-protocol.md#21-connectorstatus-state)); `recording` is
the `pitwall.recording` value; `refused` the last 5 connections refused (a second connection of a service, a
sim mismatch, a bad hello). `data_dir` (the core's data folder) and `connector.recording` are Pitwall's
optional additions to the contract's payload. `connectors` lists every linked connector in connection order, each entry shaped like `connector` plus `game_running` and `active`; `active` is the id of the active connector or null; `connector` is the entry of the one Pitwall follows (the active one, else the one followed before if it is still linked, else the first linked), null only with nothing linked.

### `pitwall.recording`

`null`, or what the connector is writing: `{connector_id, session_key, session_id,
session_type, car_id, car, track_id, track, layout_id, layout, track_length_m, started_at,
stint, lap}` (`lap`: the number of the lap being driven, as the stored laps will number it).
`null` while no session is open or no connector is connected.

## Event channels

### `pitwall.sessions.started`

When the connector starts a session (`pitwall.write.session_start` accepted). The session is
stored with its first lap: one that ends with no lap is reported by `pitwall.sessions.ended`
with `discarded: true`. Fields, plus `session_key`, `connector_id`, `at`:

| Field | Meaning |
|---|---|
| `session_id` | Pitwall session id (the id used by the engineer's tools and the database). |
| `car`, `car_class` | Display names from the connector's catalogue (e.g. `MINI Cooper S 1965`, `TC60S2`); the ids if it gave no names. |
| `car_id`, `class_id` | Catalogue ids. |
| `track`, `layout` | Display names (or ids); `track_id`, `layout_id` the catalogue ids. |
| `track_length_m` | The layout's length from the catalogue, or `null`. |
| `session_type` | Pitwall's session type: `practice`, `qualifying`, `race`, `warmup`, `other`. |
| `ambient_c`, `track_c`, `rain` | Conditions when the session started (`rain` 0–1), or `null`. |

### `pitwall.sessions.ended`

`{session_id, session_key, laps, discarded, at}`: `laps` stored; `discarded` true if the
session had no laps and so was never stored (`session_id` is then `null`; e.g. a race loading
screen). A consumer that showed a `sessions.started` for a discarded session drops it. Sent at
`pitwall.write.session_end` and when a `session_start` with a new key closes the open session.
A connector that disconnects with a session open produces nothing: the session continues when
it comes back with the same key.

### `pitwall.stints.started`

A new stint begins when the car leaves the pits after having driven on track (the player may
have changed the setup). Pitwall decides it ([data model](data-model.md#stints)); stint 1 is
implicit in `sessions.started`. Sent **at the pit exit**, from the connector's `pit_exit`
fact (no wait for the stint's first lap or setup):
`{session_id, session_key, stint, fuel_l, at}` (`fuel_l`: the `pit_exit`'s fuel on board, L;
`null` if the connector did not give it).

### `pitwall.laps.completed`

Once per stored lap, after the core stores it and asks the connector for the position.
Fields, plus `session_key`, `connector_id`, `at`:

| Field | Meaning |
|---|---|
| `session_id`, `stint` | |
| `lap` | Pitwall lap number, 1..n within the session (includes out/in/formation laps); the same number as in the database. |
| `lap_id` | The lap's id in the database. |
| `label` | The lap's name for the player: `{kind, number, stint, text}`: flying laps by the simulator's counter (`lap 5`; `lap 3 of stint 2` when a flying lap of the session already had that number), `lap 7` by Pitwall's number without a counter, `out lap of stint 2`, `in lap of stint 2` (the stint is named from stint 2 on), `formation lap`. `text` is English; localize from `kind`, `number`, `stint`. Say this, never `lap`. |
| `game_lap` | The simulator's own lap counter (`sim_lap` of the write API), or `null`. |
| `kind` | `flying`, `out` (from the pits/garage), `in` (ends in the pits), `formation` (before a race start). |
| `complete` | The lap is timed (it has a lap time). |
| `valid` | Not invalidated by the simulator (track limits etc.). |
| `lap_time_s` | Lap time, or `null` for laps that are not timed. |
| `time_source` | `official` (the simulator's time) or `measured` (the connector's clock). |
| `sectors_s` | Sector times in order, as many as the simulator has (`[s1, s2, s3]` for most); items may be `null`. |
| `delta_session_best_s` | Lap time minus the best previous clean lap of the session (negative = faster). `null` for the first clean lap or for laps that are not clean (`data-model.md`: flying, timed, valid, no long four-wheel excursion). |
| `delta_stint_best_s` | Same, against the current stint only. |
| `session_best` | True if this lap is the new session best. |
| `fuel_used_l`, `fuel_left_l` | Fuel used on this lap and left at its end. |
| `fuel_laps_left` | `fuel_left_l` / average consumption of the session's timed flying laps. `null` until one exists. |
| `max_speed_kmh`, `track_c`, `ambient_c` | |
| `position` | The player's position when the lap is stored (the core reads it from the connector's live frame, if the connector has one), or `null`: no `live_frame` function, no answer within 0.3 s, or a frame with no player among its participants, as when the lap is closed in the menus (e.g. the in lap stored after quitting to the main menu, whose frame has no participants). |

Example:

```json
{"session_id": 8, "stint": 1, "lap": 3, "lap_id": 141, "label": {"kind": "flying", "number": 3, "stint": 1, "text": "lap 3"}, "game_lap": 3, "kind": "flying", "complete": true, "valid": true, "lap_time_s": 117.62, "time_source": "official", "sectors_s": [45.4, 47.1, 25.12], "delta_session_best_s": 1.623, "delta_stint_best_s": 1.623, "session_best": false, "fuel_used_l": 3.48, "fuel_left_l": 19.97, "fuel_laps_left": 5.8, "max_speed_kmh": 179.4, "track_c": 17.7, "ambient_c": 11.7, "position": 1, "session_key": "9f3c2a71b04e5d16", "connector_id": "examplesim", "at": "2026-10-07T19:47:29.512+02:00"}
```


### `pitwall.laps.noted`

A note was added to the lap being driven (by the engineer's tool). The payload is the note's
record as the core's note store returns it, plus `at`.

### `pitwall.captures.stored`

A capture of the game screen was stored: `{name, session_id, stint,
applies_to, source, label, at}` (`applies_to` as in the engineer's capture list, `label` null until the
engineer names it). `name`:
e.g. `12_1_3` (`<session>_<stint>_<n>`) or `nosession_20261008_121427_1`; `source`: `button`
(capture button or key), `agent` (the engineer's `capture_screen`) or `test` (the control
app's "Test capture"). No path: fetch the image with `pitwall.captures.get {name, size?}`.

### `pitwall.captures.changed`

A stored capture was labelled or deleted: `{name, change: "labelled" | "deleted", label?}`.
The hub refreshes its capture lists on it.

### `pitwall.connector_messages`

Every connector message that is not a `spotter` one: the connector sent a code with params
(`pitwall.announce`, [connector-protocol.md section 5](connector-protocol.md#5-messages-and-announcements)),
Pitwall composed it in the user's language. History: `pitwall.connector_messages.list` (by
default the last 60 minutes and 50 messages, at most 1440 minutes and 200; the core's private
store, which keeps them 30 days).

| Field | Meaning |
|---|---|
| `message_id` | `m<core start yyyymmddHHMMSS>-<n>`, unique across core runs. |
| `connector_id`, `connector_name`, `sim`, `sim_name` | Who sent it. |
| `code`, `params` | The code of the connector's declared catalog and its params. |
| `variant` | The phrase variant used. |
| `audience` | `driver` (spoken to the player) or `agent` (for the race engineer). |
| `priority` | `spotter`, `critical`, `high`, `normal`, `low` (from the catalog; default `normal`). |
| `max_wait_s`, `slot` | Only when the catalog sets them: seconds after the event within which the message must start to be worth saying; the slot a newer message replaces. |
| `text` | The written text in the user's language (the connector's own while there is no current translation). |
| `say` | The spoken text (numbers in words), player messages only; else `null`. |
| `language` | The language of `text` and `say`. |
| `data` | Facts from the connector (at most 2048 bytes as JSON), or `null`. |
| `session_id` | The session being recorded, or `null`. |
| `at` | When the core composed it. |

### `pitwall.announcements`

What a radio speaks: one per message with audience `driver`, `{id, source: "connector",
connector_id, connector_name, code, say, text, variant, language, priority, max_wait_s?, slot?,
at}` (`id` = the message's `message_id`). A message of priority `spotter` is announced here
but never stored or published on `pitwall.connector_messages`. A radio is expected to speak
`say` by level (`critical` with an alert tone, only `spotter` cutting what is playing) and to
drop a message that has not started within `max_wait_s` of `evt.ts`; without `max_wait_s` it
waits until it can be said.

## Live state

The channels carry stored laps and what the connector says. For what is happening right now
(position, gaps, lap in progress) the core serves the method `pitwall.live_state` (the
engineer's `get_live_state` tool).
