# Writing a Pitwall connector

For an agent (or a person) building the connector that brings a simulator's data into Pitwall.
Pitwall knows no simulator: the connector is a separate program, in any language, that reads
the simulator, maps what it reads onto Pitwall's [data model](data-model.md) and talks to
**pitwall-core** through the [connector protocol](connector-protocol.md). Everything
simulator-specific stays in the connector, and so do its own docs, settings and control app.
The [guide](guide.md) covers the other half: the descriptor, being started by the hub and the
control endpoint.

## 1. Read first

- [connector-protocol.md](connector-protocol.md) (over the Stintmate IPC v2 contract,
  [contract.md](contract.md)): the connection and its hello, what a connection declares, the
  write methods, captures, announcements, telemetry, the live frame and error codes. This guide
  does not repeat them.
- [data-model.md](data-model.md): entities, vocabularies, the channel catalogue with units and
  signs.
- [write-api.md](write-api.md) and [write-api.schema.json](write-api.schema.json): the
  messages a connector writes (one `pitwall.write.<type>` method each), replies, order rules.
- The examples: the protocol's worked example (`examplesim`,
  [section 9](connector-protocol.md#9-worked-example-python-the-reference-package)) and the [`skeleton/`](../skeleton/README.md)
  connector, which writes a complete synthetic session (catalog, pit exits, laps with channels,
  one invalid lap, two stints).
- The reference connector: the **AMS2 connector**, its own repository
  [`Stintmate-App/ams2-connector`](https://github.com/Stintmate-App/ams2-connector) (Rust:
  recorder and control app, with its docs and tests): how ids, names, vocabularies, lap
  detection, stints, pending lap times, setups, rivals, the live frame, captures and telemetry
  were solved for one simulator at 50 Hz without disturbing the game.

## 2. What to find out about the simulator

Before writing code, find (and write down in the connector's own docs, with how you checked):

1. **How to read it** (shared memory, UDP, an SDK, log files) and at what rate. Live
   connectors must cost the simulator nothing noticeable: low priority, sleep between samples,
   buffer in memory, send once per lap.
2. **Whether it runs**: a sure sign (its process), not only that its data channel exists (other
   programs may keep it open after the game closed). This is the connector's job; Pitwall
   believes `game_running`.
3. **Ids and names**: the car, class, track and layout ids the simulator gives, and readable
   names (from the simulator, or a names file in the connector). Ids are opaque to Pitwall but
   must be stable: the same car on the same layout must always get the same ids.
4. **Session**: start and end, its type, the player's car and the layout.
5. **Laps**: when the line is crossed, the official lap time (and when it is published),
   validity (track limits), sector times, pit lane and garage states.
6. **Units and signs** of every value you map to a channel (speed, accelerations, steering,
   positions, tyre data). Test them; do not guess. Leave out a channel you cannot verify.
7. **Setup**: what the simulator exposes of the setup the player chose (often only brake bias,
   compounds, fuel; cold pressures only when they are really cold).

## 3. Mapping to the model

- `sim`: a short lowercase id for the simulator (`acc`, `rf2`, `iracing`...), the same in every
  run, matching `^[a-z0-9_-]{1,32}$` (the connector id: `^[a-z0-9][a-z0-9_-]{0,31}$`); `sim_name`: its display name. Both go in the hello
  metadata (`connector_id`, `connector_name`, `sim`, `sim_name`, `service`).
- **Catalogue**: send a `catalog` before the session that uses a car or a layout (repeat it
  whenever you see a new one; it upserts). Give `length_m` for layouts and `fuel_capacity_l`
  (the tank) for cars when known: the fuel plan uses it.
- **Session type**: map the simulator's types onto `practice`, `qualifying`, `race`, `warmup`,
  `other` (test / hot lap → `practice`; a race's formation phase is part of the `race`
  session; anything unclear → `other`).
- **Stints**: Pitwall numbers them. Send a `pit_exit` when the car leaves the pit lane or the
  garage under its own power (after writing the laps that ended before it) and `on_track` on
  every lap (false when the car never left the pit lane); Pitwall starts a new stint at an exit
  after the car has driven on track.
- **Lap type**: `formation` (before a race start), `out` (starts in the pits or the garage),
  `in` (ends in the pits or the garage, also when the player returns to the garage from a
  menu), else `flying`. Only flying laps that are timed and valid are *clean* (used for bests).
- **Lap time**: `time_s` only for a lap driven line to line; `time_source: official` when the
  simulator published it, `measured` when it is your own clock. Partial laps (to or from the
  pits, an aborted lap) have no `time_s`. Drop fragments of a few seconds.
- **Sectors**: as many as the simulator has, in order; leave out `time_s` of a sector that was
  not timed (do not send 0 or a running time).
- **Conditions**: only values in the model's units (`rain` and `cloud` 0..1). A value on
  another scale is not conditions: leave it out.
- **Setup**: `source: telemetry`, `reason: session_start` / `pit_exit`; only what the player
  sets. Never hot pressures, temperatures or ambient. Other parameters go in `extra` with your
  own stable keys, a `label` and a `group`.
- **Rivals** (optional): one `participant_laps` row per participant and lap the simulator
  counts, the player included (`is_player`). Batch them (with each lap, every ~30 s and at
  the end).
- **Channels**: the catalogue's names and units exactly; your own extras as `ext_<name>`.
  Coded channels use Pitwall's codes, never the simulator's: map its surface materials to
  `<wheel>_surface` (road, kerb, grass, gravel, sand, run-off, track-limit strip, wall, other
  off-track) and its pit states to `pit_mode` (on track, into the pits, in the box, out, in
  the garage); see the data model. Keep the mapping tables in the connector.
- **Live frame** (if you implement `live_frame`): the same ids and vocabulary as what you
  write, times in seconds and `null` when the simulator does not give a value (never `-1` or
  `0` as "unknown"), the safety car marked.

## 4. The minimum useful lap

```json
{"type": "lap", "session_id": 1, "lap_type": "flying", "time_s": 92.481,
 "channels": {"t": [...], "dist": [...], "speed": [...], "x": [...], "z": [...]}}
```

With `t`, `dist`, `speed` (required when a lap has channels) Pitwall can list laps and compare
them along the lap; with the position `x`, `z` it can draw the track. Corner detection needs
`g_lat`, and each further channel (`throttle`, `brake`, `steer`, tyres...) enables more of the
analysis. A lap without channels (times only) is accepted too.

## 5. Talking to the core

**Process model.** The connector runs on its own (started by the hub, by its own control app
or by hand) whether or not Pitwall runs. Each of its processes (a recorder, a second service...)
opens its own connection to the core's endpoint `pitwall` with the `stintmate-ipc` package (or
any implementation of the protocol) and a distinct `service`; the connector is connected while
any of them is. The package's client connects in the background, retries with the protocol's
backoff and re-subscribes after every reconnect.

**Hello.** `peer.app` `connector-<id>`, `peer.process` the service, and `peer.meta`
`{connector_id, connector_name, sim, sim_name, service}`. A `bye` with `connector_busy`
(another connector is connected) or `busy` (the same service is connected) closes the
connection: log it and retry later (a write refused for `connector_busy` carries
`retry_ms` 10000). A hello whose metadata is missing a field, or whose `connector_id` is not
the slug of `peer.app`, or whose `sim` is not a slug, gets `bad_request`.

**Writing.** Each write API message goes as one method, `pitwall.write.<type>`, with the
message's fields (no `type`), the session's `session_key` (16 random hex digits chosen by the
connector at `session_start`) and a `seq` where the write method takes one
([protocol section 3.1](connector-protocol.md#31-the-write-methods)); always with the
contract's idempotency key. The result is the Writer's reply. Check every answer:
`bad_request` carries the Writer's error and changes nothing; `writer_busy` means another
writer holds the database (an import): nothing was stored, send the same call again after
`retry_ms` (5000 from the core). Writes of one connection are handled in order. A session
continues across reconnections and Pitwall restarts (same key). Keeping the writes that could
not be delivered is the connector's job: store each write, in order, with its idempotency key,
and send it again after a reconnect until Pitwall answers, so that laps finished while Pitwall
is down are stored when it comes back ([protocol section 3.3](connector-protocol.md#33-laps-while-pitwall-is-down-the-outbox);
the skeleton resends in memory, a real recorder writes to disk on another thread). You do not
compute track outlines: the core refreshes a layout's outline after your
`pitwall.write.session_end` when the best clean lap of the layout changed.

**Channels as bin.** Send every channel as `{"encoding": "f32", "data": <bytes>}` (the
little-endian float32 samples as raw bytes; MessagePack `bin`). Number arrays stay accepted.

**Status.** Every connection declares the state channel `connector.status` and sets it on
every change (no periodic resend: the connection is the liveness): `game_running`, `phase`,
what it is writing (`session_key`, `stint`, `lap`), the car and layout on track now (`live`),
the outbox backlog, its own `error_code` and a short `message`. Pitwall shows it in the
state channel `pitwall.connectors` ([events.md](events.md#pitwallconnectors)), the Stintmate
control apps and the engineer's `connector_status` tool.

**Live functions** (declare only those you implement; the engineer's tools get
`not_implemented_by_connector`, naming your connector, for the others):

- `connector.live_frame`: answer with the frame now (aim for a few ms: answer from memory),
  or `game_not_running` / `busy` / `internal`; in the menus a frame with `"phase": "menus"`.
  Build it only when asked: nothing is streamed. The core also asks for one frame after each
  stored lap, with a 0.3 s limit, for the lap event's `position`.
- `connector.capture_target` (optional): name the game's window for the setup screen capture,
  `{hwnd, pid, title?, crop?}`, or `game_not_running` / `window_minimized` / `not_capturable`.
  Pitwall grabs the window itself and owns the capture button; without this method it grabs
  the foreground window ([connector-protocol.md section 4](connector-protocol.md#4-captures-pitwall-grabs-the-connector-names-the-window)).
- `connector.telemetry`: the 32-byte neutral stream Pitwall's brake cues use
  ([protocol section 6](connector-protocol.md#6-telemetry-for-brake-cues-connectortelemetry-stream)); produce
  it only while Pitwall subscribes.

**Announcements.** Declare your text catalog (`pitwall.catalog.declare`, in your own
language) at every connection and send codes with params (`pitwall.announce`); Pitwall
composes and translates the texts. The core publishes the session, stint, lap and capture
events itself ([events.md](events.md)).

## 6. Testing

Everything here runs outside Stintmate's own source: you need Windows, [uv](https://docs.astral.sh/uv/)
for a Python connector, and a copy of [`skeleton/`](../skeleton/README.md) as a worked case.

1. **Run the skeleton's tests** to see the harness working, from a copy of `skeleton/`:

   ```
   powershell -ExecutionPolicy Bypass -File .\fetch-ipc.ps1
   uv sync
   uv run pytest -q
   ```

   (`-ExecutionPolicy Bypass` allows that one run and changes no setting.)
   `fetch-ipc.ps1` puts the `stintmate-ipc` wheel in `vendor/`; the tests run the recorder
   against a fake Pitwall, serve the control endpoint as the hub uses it, run
   `stintmate-ipc-check server` against it and check the descriptor. Keep that file as the
   model for your own conformance tests: its fake Pitwall is built only from the names in these
   documents, and it checks that the recorder's writes follow the protocol.
2. **Test the Pitwall side of your connection** with a fake Pitwall in your own tests, as the
   skeleton does: check the hello, that `connector.status` is declared, that each write
   carries its `session_key` and idempotency key, that a refused write is sent again, and
   that your connector survives the endpoint going away and coming back.
3. **Test the protocol side with the checker.** `stintmate-ipc-check client <pipe> --key-file
   <key>` serves a test endpoint and checks the handshake, reconnection and messages of the
   client that connects to it; `stintmate-ipc-check server <pipe> --key-file <key>` checks
   your control endpoint (`--fast` skips the checks that wait for timeouts). Both are installed
   by the `stintmate-ipc` wheel (`uv run stintmate-ipc-check --help` inside the skeleton).
4. **Run against a real core with a throw-away database.** Use a temporary folder, never the
   user's: exit Pitwall first (there is one core per user), then start the core of the
   Stintmate installation with its own data folder, `pitwall-core --data-dir <temporary
   folder>`; its key is `<temporary folder>\ipc\key`, created if missing, so pass that folder's
   key to your connector. Connect, drive or replay, and read the result back with the
   Stintmate data browser or the engineer's tools: your laps, types, stints and best laps
   should be as you drove them. Subscribe to the core's channels ([events.md](events.md)) to
   see the events and your status.
5. **Compare with a known stream.** The skeleton covers out, in and flying laps, an invalid
   lap, two stints and a session that ends; the worked example in
   [protocol section 9](connector-protocol.md#9-worked-example-python-the-reference-package) is the smallest write
   sequence. Diff what your connector writes against the shape of those.
6. **In the car**: one practice session; check laps, stints and times against the simulator's
   own timing screen, and the connector's state in the Stintmate hub and through the engineer's
   `connector_status` tool.
