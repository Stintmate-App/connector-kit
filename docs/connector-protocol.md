# Connector protocol

How a **connector** (the program that reads a simulator) talks to **Pitwall**, the Stintmate
telemetry database and analysis service. Its process, `pitwall-core`, owns the database,
publishes what it stores and relays live requests. This document is for the author of a
connector, and for the agent that helps write one. It stands alone with the other files of this
kit.

The transport is **Stintmate IPC v2**, an open protocol with its own repository,
[`Stintmate-App/stintmate-ipc`](https://github.com/Stintmate-App/stintmate-ipc): the
specification (`SPEC.md`), the Rust implementation, a native library with a C ABI
(`stintmate_ipc.dll`, loadable from any language that can load a C library) and the Python
package `stintmate-ipc` (module `stintmate_ipc`, ctypes over that same library). The examples
below use the Python package. This document never contradicts [contract.md](contract.md), which
is the application contract of the `pitwall` endpoint; where they seem to differ, the contract
wins.

For the whole job of writing a connector (folder, descriptor, being managed by the Stintmate
hub, language, checklist) start at the [guide](guide.md). Read with this document:

- [data-model.md](data-model.md): ids, vocabularies (session types, lap types, pit mode and
  surface codes) and units;
- [write-api.md](write-api.md) and [write-api.schema.json](write-api.schema.json): the messages
  that store sessions, laps, setups and rival timing, and their order rules (the write methods
  carry them);
- [connectors.md](connectors.md): how to map a simulator onto the model;
- [events.md](events.md): the channels the core publishes;
- [catalogs.md](catalogs.md): the format of the text catalog of section 5;
- [descriptor.md](descriptor.md): the file that lets the hub find your connector;
- section 11 below: laps, stints and the simulator's counters (what to send, in what order).

Pitwall knows no simulator. The connector does everything simulator-specific: reading the game,
knowing whether it runs, mapping its values to the model's vocabulary and units. Examples use an
invented simulator, **examplesim**, whose connector id is `examplesim`.

**Reference implementations.** The [`skeleton/`](../skeleton/) folder of this kit is the
smallest working connector (a fake simulator, synthetic laps). The AMS2 connector, written in
Rust, is a complete one: handshake, writes and outbox, live frame, messages, captures and the
control endpoint. It lives in
[`Stintmate-App/ams2-connector`](https://github.com/Stintmate-App/ams2-connector).

## 1. Connecting

- The endpoint is the named pipe `\\.\pipe\stintmate-pitwall-<user>`
  (`stintmate_ipc.pipe_path("pitwall")`).
- **The workspace and the key.** The workspace is the user's Stintmate folder. Find it as
  section 4.3 of the IPC `SPEC.md` says: your own configuration or command line, then the
  environment variable `STINTMATE_WORKSPACE`, then the `workspace` field of
  `%APPDATA%\Stintmate\settings.json` (`stintmate_ipc.find_workspace()` does the last two). The
  key is `<workspace>\ipc\key` (`stintmate_ipc.default_key_path()`; see [contract.md](contract.md)).
  **A connector only reads the key**: it never creates or replaces it (Stintmate's
  own apps create it when missing). While the workspace or the key is missing, say so in your
  log and retry; do not fail for good. Pitwall may not be running either: connect in the
  background and retry with the protocol's backoff. The library's client does it and
  re-subscribes after every reconnect. In the Python package, `Client.wait_connected(timeout)`
  raises `NotConnected` when the time is up, and its `__cause__` is the last connection error
  (for example `PipeNotFound` while nobody serves the pipe).
- **The link rule.**
  > Connect to Pitwall when your recorder starts, whatever the game does, and stay connected. Never
  > disconnect on purpose while your recorder runs. Pitwall listens to every connector. One is
  > **active**: the first whose `game_running` is true, until that game closes. The active connector
  > drives live state, radio calls, brake cues, captures and actions. The others still store their
  > writes, and their events and announcements are not published. Set `game_running` as soon as the
  > process exists and before the first write of a session.
- **Being found by the hub.** This document covers the Pitwall side only. For the Stintmate hub
  to list and manage your connector, write the descriptor
  `%LOCALAPPDATA%\Stintmate\connectors\<id>.json` when you install ([descriptor.md](descriptor.md);
  your control endpoint is `connector-<id>`, see [contract.md](contract.md)). Write it
  atomically (a temporary file in the same folder, then a replace), and remove it when you
  uninstall, only if its `launcher` names your install. If you ship more than one build of the
  same connector, offer `uninstall` (the command line that removes your install silently) so
  another build's setup can replace yours. Your logs go in `<workspace>\connectors\<id>\` (list
  their names in `logs`). You need no workspace to install. You are responsible for everything
  your connector needs to run (its executable, or its own Python environment inside its install folder);
  the hub only launches the `launcher` you name.
- **One connection per process** of the connector (each service: the recorder, a helper...).
  Its hello (`auth.peer`):

  | Field | Value |
  |---|---|
  | `app` | `connector-<id>` (`connector-examplesim`) |
  | `process` | the service: `recorder`... |
  | `name`, `version` | the connector's display name and version |
  | `meta` | REQUIRED `{connector_id: "<id>", connector_name, sim, sim_name, service, product}`, every value a non-empty string; `connector_id` is the slug of `app` and matches `^[a-z0-9][a-z0-9_-]{0,31}$`; `sim` matches `^[a-z0-9_-]{1,32}$`; `service` equals `process`; `product` is a UUID version 4, lowercase, with hyphens, matching `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` |

  A connection whose `meta` fails these (a missing or malformed `product` included) is closed
  with `bye` `bad_request` (the message names the problem).
- **The product key** identifies your connector, apart from its `id`. Generate it once
  (`[guid]::NewGuid()` in PowerShell), write it in your descriptor too ([descriptor.md](descriptor.md))
  and send the same value in every connection of every version and flavour of the connector.
  Pitwall remembers the `product` it first saw for an `id`. A connection that names another
  `product` for an `id` already seen, or while another linked connection of that `id` names a
  different one, is closed with `bye` `connector_id_taken` (section 8): two connectors cannot
  share an id. Choosing a `sim` is a different matter, see
  [connectors.md section 7](connectors.md#7-several-connectors-for-one-game).
- **Many connectors at once, one active.** Pitwall accepts the connections of every `connector-*`
  app at the same time; the link rule above says which one is active. A second connection of the
  same service is closed with `busy`; every connection of a connector names the same `sim`
  (otherwise `bad_request`). A connector is *linked* while any of its connections is open (there
  is no status heartbeat: the connection is the liveness); its *functions* are what its
  connections declare. A Pitwall older than contract 2 (`contract_version` in
  [`contract.json`](../contract.json)) serves one connector at a time and closes the connection
  of a second one with `bye` `connector_busy` (the message names the connected one): retry
  after 10 s, like any other retryable close. The current Pitwall never sends that code.
- **The service that records has the id `recorder`** (`process` in the hello, `id` in
  `control.state`); further services have other ids. What `control.state.pitwall` and a
  service's `state` mean is in [contract.md section 3.2](contract.md).
- Requests may follow the handshake at once: the core registers the connection first (a request
  that arrives before the registration is finished waits up to 2 s for it).

### 1.1 Letting Stintmate remember your services (optional)

Everything here is optional: a connector without it works, and the hub shows its services (state
and label) but keeps nothing for them. To let the hub keep a service on or off across restarts,
mark it in the `services` of your control endpoint's `control.state` (see
[contract.md](contract.md)); all four fields are additive:

| Field of a `services[]` entry | Meaning |
|---|---|
| `delegable` (bool, absent = false) | The hub may keep this service on or off: it remembers the user's choice (in the user's workspace, never sent anywhere) and restores it. A switch is offered only for these. |
| `default` (`"on"` or `"off"`, absent = `"off"`) | The state the hub restores while the user has made no choice. |
| `always_on` (bool) | You keep it running whenever your app runs. The hub shows it without a switch, starts it when it finds it stopped (unless its last start was refused) and never stops it. A service with `always_on` is not `delegable`. |
| `refusal` (`{code, label}` or null) | The last start could not happen for a reason the user must fix; `label` is in the user's language. Set when a start fails, clear it when a start is tried again or the service runs. The hub shows it with "Try again" and never retries by itself. |

```json
{"id": "recorder", "name": "Recorder", "state": "stopped", "label": "Stopped",
 "level": "off", "running": false, "delegable": true, "default": "on", "refusal": null}
```

- **Managed start.** When the hub launches you (`--managed --parent-pid <pid>`) or adopts you
  (`control.managed`), do not start your `delegable` services yourself: the hub sends
  `control.service.start` or `control.service.stop` for each, after your endpoint answers, every
  time your app starts. Your own startup setting applies only when your app runs alone.
  `control.service.*` changes the running state only; do not rewrite your own settings for it.
- **The user's own switch.** If your window or tray has a switch for a `delegable` service and
  you are managed, publish the event `control.services.desired` on your endpoint (declare it in
  your handshake like any channel):
  `{"service": "recorder", "on": false, "at": "2026-10-22T18:30:00Z"}`. Publish it only for the
  user's switch: never for a `control.service.*` call, an automatic restart, a crash or a
  refusal. The hub ignores it for a service that is not listed in your last `control.state` or
  not `delegable`; it saves the choice and sends nothing back.

### 1.2 Updating your connector while Stintmate runs

The Stintmate hub starts and supervises your connector. A setup that replaces its files while it
runs has two options: ask the user to close Stintmate, or ask the hub to **hold** the connector
for the length of the setup. Use the hold; keep the prompt only as the fallback when the hub
cannot be asked.

The bracket, run by your **setup** (never by the connector itself):

1. **Hold** before replacing any file: `hub.connector.hold {id, purpose?: "update" | "uninstall",
   ttl_s?: 30..1800 (an integer, default 300)}` on the hub's endpoint `hub`. The hub quits your
   connector (its `control.quit`), shows it as "Updating" and does not start it. The reply is
   `{id, state: "stopping" | "held", restart, expires_in_s}`: repeat the call (every 0.5 s)
   until `held`, then wait until your own mutexes are free. Repeating it refreshes the deadline.
   The hub refuses a hold with `busy` and `details.recording` true while a session is being
   recorded, and it reads that from `connector.status.phase` being `on_track` ([section
   2.1](#21-connectorstatus-state)): a connector that never sets `phase` is stopped in the
   middle of a session.
2. Copy the files and **register** (rewrite your descriptor, [descriptor.md](descriptor.md)).
3. **Release**: `hub.connector.release {id, start?: true}`. The reply is
   `{id, released, starting}`. The hub starts your connector from the **current** descriptor's
   launcher. Release also when the install failed or was cancelled: the hub then starts the launcher
   the descriptor names. If your update replaces the files in place and fails halfway, the old
   build may be gone: say so to the user, exit with a non-zero code, and still release when you
   can; when you cannot, the hold ends at its `ttl_s`. `released: false` (nothing was held) is not an error.

```python
import time
from stintmate_ipc import KeyFile, PeerInfo, connect, default_key_path, pipe_path

info = PeerInfo("connector-examplesim", name="ExampleSim setup", version="1.2.0", process="setup")
hub = connect(pipe_path("hub"), info, KeyFile(default_key_path()), timeout=3)
try:
    while hub.call("hub.connector.hold", {"id": "examplesim"}, timeout=5)["state"] != "held":
        time.sleep(0.5)
    ...  # copy the files, register
finally:
    hub.call("hub.connector.release", {"id": "examplesim"}, timeout=5)
    hub.close()
```

Errors: `not_found` (the hub does not know your connector: no descriptor yet; install without a
hold), `busy` with `details: {recording: true}` (the user is recording a session with your
connector: it is never stopped; tell the user to finish it and retry), `busy` with
`details: {state: "starting" | "exiting"}` (the hub itself is starting or quitting),
`unknown_method` (an older hub), `bad_request`. Treat anything but success as "no hold": fall
back to asking the user to close Stintmate.

If the setup dies the hold ends by itself: when the descriptor file is rewritten, or after
`ttl_s` (the hub then starts the current launcher if it still exists). The user pressing Start or
Stop in Stintmate ends it too. Do not hold from your connector, only from its setup; a setup
finds the workspace and key the same way as in section 1.

## 2. What a connection declares

On its own connection, each service declares (IPC `SPEC.md` section 2.2) what Pitwall may call
or subscribe to. Pitwall finds them by the connection, not by a name: a connector needs no
server of its own for Pitwall.

| Name | Kind | Who declares it | Pitwall uses it |
|---|---|---|---|
| `connector.status` v1 | state | every service | subscribed at once on every connection that declares it |
| `connector.live_frame` v1 | method | the service that reads the game (recorder) | on tool requests (`pitwall.live_frame`, `pitwall.live_state`), and after every stored lap for the lap event's `position` (deadline 0.3 s) |
| `connector.capture_target` v1 | method (optional) | the service that knows the game (recorder) | before each capture; deadline 1 s; section 4 |
| `connector.action` v1 | method (optional) | the service that can carry out actions (recorder) | when the engineer sends an action you declared with `pitwall.actions.declare`; deadline: the action's `timeout_s`; section 5.2 |
| `connector.telemetry` v1 | stream | the recorder | subscribed only while brake cues can fire (section 6) |

Only `connector.status` and the write methods are needed. A missing name gives
`not_implemented_by_connector` (with `details` `{connector, sim, sim_name, method}`) to the tool
that needs it.

### 2.1 `connector.status` (state)

Set on every change, never resent periodically:

| Field | Type | Meaning |
|---|---|---|
| `game_running` | bool | The simulator's process runs (check the process, never only the data channel). |
| `phase` | str? | `menus`, `garage`, `on_track`, `paused`; nil when the game is not running. |
| `session_key` | str? | The session this service writes (recorder), else nil. |
| `live` | map? | The car and layout on track now: `{session_type, car_id, track_id, layout_id, track_length_m?}` (catalog ids); nil outside a session. Pitwall's brake cues use it ("the car on track now"). |
| `stint`, `lap`, `outbox` | | Informational, for the user's screens: `stint` and `lap` are what the service writes now (the numbers Pitwall last answered: `stint` from the `pit_exit` and `lap` replies); `outbox` is `{pending, oldest_at?, rejected}` (section 3.3). The core does not read them. |
| `error_code`, `message` | str? | The service's own problem, a short English text. |

The core logs a change of `game_running`, `phase`, `session_key`, `error_code` or `message`, and
uses `live` for the brake cues; `connector_status` shows the whole last status.

## 3. Writing data

### 3.1 The write methods

One method per [write API](write-api.md) message type. The params are the write API message
**without `type`**, plus `session_key` (and `seq`); the result is the write API's reply. The
write API's `hello` is not sent: Pitwall builds it from `peer.meta`.

| Method | Extra params | Result | Idempotency key (`ik`) |
|---|---|---|---|
| `pitwall.write.catalog` | none | `{ok, type, classes, cars, tracks, layouts}` (counts) | `<id>/catalog/<sha256-12 of params>` |
| `pitwall.write.session_start` | `session_key` (16 hex digits, random per session, chosen by the connector) | `{ok, type, session_id}` | `<id>/<session_key>/start` |
| `pitwall.write.setup` | `session_key`, `seq` (1.. per session) | `{ok, type, session_id, setup, stint, stored}` | `<id>/<session_key>/setup/<seq>` |
| `pitwall.write.lap` | `session_key`, `seq` (the connector's lap sequence in the session, 1..) | `{ok, type, session_id, lap, lap_id, stint}` | `<id>/<session_key>/lap/<seq>` |
| `pitwall.write.pit_exit` | `session_key`, `seq` (1.. per session) | `{ok, type, session_id, stint, new_stint}` | `<id>/<session_key>/pit_exit/<seq>` |
| `pitwall.write.participant_laps` | `session_key`, `seq` | `{ok, type, session_id, rows, stored}` | `<id>/<session_key>/participants/<seq>` |
| `pitwall.write.session_end` | `session_key` | `{ok, type, session_id, stored, laps, discarded}` | `<id>/<session_key>/end` |

- All are `ordered` and `idempotent`; send `ik` on every call (a write without one is
  `bad_request`). Deadline 30 s. A message too large for one frame is sent in chunks by the
  library (IPC `SPEC.md` section 5.4); a lap with its channels is the large one.
- Pitwall treats `ik` as an opaque string and keeps it with the database across restarts, for
  every connector together: make it stable for one logical write and unique among your writes,
  as the pattern above does with your id and the session key.
- The writes after `session_start` name the session by its `session_key` (case-insensitive),
  never by `session_id`: Pitwall fills the write API's `session_id` itself (so a lap can be
  written before the connector knows the id, for example from its outbox while Pitwall was
  down).
- **Channels** of a lap go as `{"encoding": "f32", "data": <bytes>}`: the little-endian float32
  samples as raw bytes (arrays of numbers are still accepted).
- **Sessions by key, not by connection.** A session stays open across reconnections and restarts
  of Pitwall: writes carrying its key continue it. A `session_start` with a new key closes the
  connector's open session (stored if it has a lap, discarded if not). Reusing a key that was
  already started is `bad_request`. A write to a closed key: `session_closed`; to a key never
  started: `unknown_session`.
- **Idempotency.** Pitwall stores each key in the same database transaction as the write and
  keeps it with the database: a repeat gets the first answer and stores nothing. `writer_busy`
  (the database is held by an import; `retry_ms` is 5000), `busy`, `timeout` and
  `shutting_down` do not consume the key: send the same call again. A connection not yet
  registered gets `unavailable` (`retry_ms` 1000), which keeps the key too. A Pitwall older than
  contract 2 also answers `connector_busy` (`retry_ms` 10000) to a write while another connector
  is connected; the key is kept then as well.
- **Refused writes**: `bad_request` with the write API's error in `msg` (it names the field)
  and its reply in `details.reply`. Not retryable: nothing was stored, the session goes on. A write with a field the write API does
  not define is refused the same way, naming the field (`unknown field`): put your own values in
  `extra` or in `ext_` channels. In `connector.status`, the live frame and `control.state` unknown
  fields are kept or ignored, never refused.

### 3.2 After a lap

Pitwall stores the lap, asks the connector's `connector.live_frame` (0.3 s) for the player's
position, publishes `pitwall.laps.completed` and answers. The session, stint and lap events are
Pitwall's own (computed from the writes, the same for every simulator): [events.md](events.md).

### 3.3 Laps while Pitwall is down: the outbox

The rule for every connector: every write goes through the connector's outbox (one file per
write, written by a background thread, never by the sampling loop), sent in order one at a time;
deleted on `ok`, moved aside on a non-retryable error, kept and retried (same `ik`) on a
retryable one or after the reconnect. Pitwall's idempotency makes a write sent twice stored
once. Show the backlog in `connector.status.outbox`.

## 4. Captures: Pitwall grabs, the connector names the window

The engineer looks at what the simulator shows (a setup screen, timing, results) through
captures. **Pitwall takes them itself** (a wheel button or a key the user assigns, or the
agent's `capture_screen` tool; one grab, one image each): a connector never captures, sends or
stores an image. The only thing it can add is to say **which window** to grab, with one small,
**optional** method.

| Method | Direction | Params | Result |
|---|---|---|---|
| `connector.capture_target` | Pitwall to connector | `{}` | `{hwnd: int, pid: int, title?: str, crop?: {x, y, w, h}}` |

- `hwnd`: the Windows handle (`HWND`, as an integer) of the simulator's top-level window. `pid`:
  the process id that owns it; Pitwall checks the window still belongs to `pid` before grabbing,
  so a closed window whose handle was reused is never captured. `title`: the window title, only
  to label the capture. `crop`: the rectangle to keep, in pixels of the window's **client area**
  (`x`, `y` from its top-left corner); omit it to keep the whole client area (use it when the
  game mirrors a larger view and the part worth reading is smaller). An answer without an
  integer `hwnd` and an integer `pid` is ignored and the foreground window is used.
- Pitwall asks **before every capture** (again, once, if the window turns out stale) with a
  deadline of 1 s. Answer well inside it, from a thread that is not the sampling loop
  (enumerating windows is enough); the method has no side effects.
- Declare it on the connection of the service that knows whether the game runs (normally the
  recorder).

Example (the request has no params; the answer for a game window at 1920x1080 whose lower half
shows the menu):

```json
{"method": "connector.capture_target", "params": {}}
{"hwnd": 1313826, "pid": 18344, "title": "ExampleSim", "crop": {"x": 0, "y": 540, "w": 1920, "h": 540}}
```

Errors the connector answers, and what Pitwall does:

| Code | Use it when | Pitwall |
|---|---|---|
| `game_not_running` | You know the simulator is not running (no window of its process). | Refuses: the tool or the log says so. No capture. |
| `window_minimized` | The window exists but is minimized (nothing to grab). Say in `msg` how to fix it ("restore the window; it does not need focus"). | Refuses with your `msg`. No capture. |
| `not_capturable` | Any other reason you cannot name a window; `msg` says why. | Falls back to the foreground window, as if the method did not exist. |

**What if the connector does not implement it?** Nothing breaks. With no connector connected, a
connector that does not declare `connector.capture_target` (or answers `unknown_method`) or one
that answers `not_capturable`, Pitwall grabs **the foreground window**: while the user drives,
that is the game. Implement the method when the foreground window is not reliable (a VR runtime
in front, a launcher) or to crop, and answer `game_not_running` / `window_minimized` so that a
capture with the game closed or minimized is refused instead of grabbing whatever is in front.
Any other error (`timeout`, `internal`...) is relayed to the tool unchanged and nothing is
captured.

Pitwall's own `pitwall.capture` (the agent's request) is not called by connectors. It answers
`game_not_running` / `window_minimized` (your refusals), `blank_frame` (the grab returned a
black or flat frame), `not_capturable`, `busy` (another grab runs) or `internal`. Pitwall stores
the image (with a thumbnail) and publishes `pitwall.captures.stored`. The capture's details
record the window (`target`: `{title, pid, source: "connector" | "foreground"}`) and the
connector's last `connector.status.phase`, so the engineer can judge what the image shows.

## 5. Messages and announcements

The connector **declares its text catalog** and then **sends codes with params**; Pitwall
composes the text in the user's language and publishes it. No free text crosses from a connector
(a connector that wants one declares a code with a `text` param).

1. At every connection, before any announcement: `pitwall.catalog.declare {catalog}` with a
   catalog file in the [catalog format](catalogs.md): `catalog` = `connector.<id>`, `language` =
   the connector's own, and per message:
   - `audience`: `driver` (the radio speaks it) or `agent` (the engineer reads it);
   - `priority`: `spotter`, `critical`, `high`, `normal` or `low` (default `normal`);
   - optionally `max_wait_s` and `slot` (both in [5.3](#53-priority-and-maximum-wait));
   - optionally `kind` (`message`, `announcement`);
   - optionally `group`, the category: one of `laps`, `fuel`, `setup`, `session`, `flags`,
     `safety_car`, `penalties`, `race`, `rivals`, `car`, `spotter`, `test`, `other` (missing or
     unknown is stored as `other` and logged);
   - optionally `variants`: a list of 1 to 8 more wordings of the same message, so a message
     said often does not always sound the same. `text` is variant 0, `variants[0]` is variant
     1, and so on. Every variant is a plain string with exactly the placeholders of `text` (the
     same `params`, `plural` and `values`); none may repeat `text` or another variant. A
     `plural` message's variant is one wording used for every count, so word it to read right
     with any number (or without the count). A bad list drops the whole entry (`bad_variants`,
     or `variant <n>: <reason>` for one text), logged. Each variant is translated on its own
     (language pack key `<code>.v<n>`) and falls back to your text on its own. Variants are
     wordings, not meanings: the user mutes a message with one switch, whatever the variant, so
     never put a different fact in a variant (a new fact is a new code).

   Codes are one segment (`^[a-z][a-z0-9_]{0,31}$`); numbers are params with their kinds
   (`int`, `decimal`, `gap`, `lap_time`, `ordinal`, `distance_m`, `speed`, `temperature`,
   `litres`, `laps`, `enum`, `text`). An entry that does not validate is dropped and logged (the
   others are kept); the answer is `{catalog, codes, new, changed, removed, translation}`, where
   `translation` is `current`, `pending`, `not_needed` or `unavailable`. A catalog that is not a
   map, whose `catalog` is not `connector.<id>` or whose `language` is not a BCP 47 tag is
   refused whole with `bad_request`. Send `ik` `<id>/textcatalog/<sha12 of the catalog>`; the
   core re-evaluates every declaration (its answer is not cached), so the same catalog declared
   again is a safe retry.
2. `pitwall.announce {code, params, variant?, data?}` (`data`: a map of facts for the engineer,
   at most 2,048 bytes as compact JSON):
   - `unknown_code` for a code not declared (or nothing declared yet);
   - `bad_request` naming the param when the params do not match the declared names and kinds;
   - `rate_limited` (with `retry_ms`) beyond 10 per connector in any 60 s (`spotter` messages
     have their own budget of 120 in any 60 s).

   An announcement is not idempotent: it is worth something only now, so do not queue it while
   Pitwall is down. `variant` (integer, default 0) picks the wording; the connector chooses it
   (Pitwall and the radio never rotate). A variant your catalog does not declare, or one that is
   not an integer, is `bad_request` with `data: {param: "variant", reason: "unknown_variant" |
   "bad_variant"}`; it is not spoken and not counted in the rate. A good way to choose: at
   random, never the one sent last for that code. For `spotter` messages the radio makes every
   variant ahead of time, so variants cost nothing at call time.

Pitwall composes `text` (written) and, for the player, `say` (spoken: numbers in words, from the
user's number catalog, see [catalogs.md](catalogs.md)) in the user's language, from the
translation file `<workspace>\locales\<tag>\connector.<id>.json` when it is current, else from
the connector's own texts. A missing or stale translation is produced in the background by the
Stintmate hub (the one translation path: it reads the catalog from Pitwall with
`pitwall.catalogs.get`, translates it with the user's agent, writes the pack and tells Pitwall
with `pitwall.catalogs.reload`). **A connector only declares its catalog in its own language
(English is the usual choice) and sends codes; it translates nothing and writes no language
pack.** The user's language is `language` in `<workspace>\hub\settings.json` (a BCP 47 tag,
default `en`). Your own windows, if you have any, read it once at start (the hub restarts a
managed connector when it changes). **The connector's catalog is versioned per message by its
source hash**, so change a text and its translation is redone; keep a code's meaning (a new
meaning is a new code).

### 5.1 Guidance for the engineer

Some facts about your simulator the agent cannot infer and would get wrong: that setup files are
encrypted and must be read from a screen capture, what a game screen shows, which units the
telemetry uses. Declare them once per connection with **`pitwall.guidance.declare {items}`**. It
is not an announcement channel: nothing here is spoken or shown to the player, and nothing is
sent while you run.

| Field | Rules |
|---|---|
| `id` | `^[a-z][a-z0-9_]{0,31}$`, unique within the declaration |
| `topic` | one of `setup`, `capture`, `telemetry`, `session`, `car`, `track`, `tyres`, `fuel`, `strategy`, `controls`, `other` |
| `when` | 1..120 characters, one line: the condition that makes the agent need it, phrased to complete "Read it when ..." (for example "you read, log or change a car setup") |
| `text` | 1..1500 characters, Markdown allowed: the guidance itself, addressed to the agent |

- Limits: 0..16 items; `when` + `text` of all items at most 8,000 characters. More items, more
  characters, or `items` not a list is refused whole with `bad_request`. A single bad item (bad
  `id`, unknown `topic`, a `when` with a line break, a duplicate `id`, a length) is dropped,
  named in the answer `{items, refused: {id: reason}, changed}` and logged.
- **Last declaration wins**: each call replaces your whole guidance; `items: []` clears it. Send
  `ik` `<id>/guidance/<sha12 of the items>` (the answer is not cached, so the same items
  declared again are processed again). Declare at every connection, in any order relative to the
  catalog. Optional: a core that predates it answers `unknown_method`; log it once and carry on.
- **English only.** It is written for the agent, never translated, and nothing checks the
  language.
- How it reaches the agent: Pitwall stores it (it survives a restart) and its MCP server lists,
  per connector, each topic with its `when` in the server instructions, serves the full texts
  through the tool `get_connector_guidance(topic?)`, and offers one prompt per topic,
  `guidance_<connector id>_<topic>`. The agent reads the `when` lines at the start and fetches a
  topic's text before acting on it.
  The instructions carry only about the first 400 characters of a connector's `topic - when` list
  (and at most four connectors), so put the important topics first and keep each `when` short;
  the full texts are fetched on demand.

A good item states a fact and what to do about it:

```json
{"id": "setup_from_capture", "topic": "setup", "when": "you read, log or compare a car setup",
 "text": "ExampleSim encrypts its setup files (*.set): never try to read them. Ask the player to open the setup screen tab you need and press the capture button, then read the values from the image. One tab per capture."}
```

A bad one (dropped or useless): `"when": "always"` (not a condition), `"text": "ExampleSim is the
best simulator!"` (marketing, nothing to act on), a text longer than the agent needs, rules that
are not about your simulator, or non-English text.

### 5.2 Actions the engineer sends you

Some things only your simulator can do: play a radio check to prove the link works, read the
setup screen that is open, write a car setting, run a self-test. The engineer's agent asks for
them with **actions**. You declare the actions you serve and the exact format of their data and
of their answer; Pitwall checks the data before you ever see it and checks your answer after you
give it. It is **optional**: a connector without actions loses nothing.

What actions are not: announcements to the player (section 5), rules for the agent (section 5.1)
or live data (section 7). Only the engineer's MCP server can send an action: any other client is
refused with `engineer_only`, so a script of yours cannot stand in for the agent (see "Testing"
below).

#### Declaring: `pitwall.actions.declare {actions}`

```json
{"actions": [
  {"code": "test.ping",
   "description": "Radio round trip: the connector answers and says its radio check on the player's radio.",
   "timeout_s": 5,
   "requires_confirmation": false,
   "params": {"type": "object", "additionalProperties": false, "properties": {
       "say": {"type": "boolean", "description": "Also play the radio check on the player's radio (default true)."}}},
   "result": {"type": "object", "additionalProperties": false,
       "required": ["pong", "announced"], "properties": {
       "pong": {"type": "string", "maxLength": 64, "description": "Always 'pong'."},
       "announced": {"type": "boolean", "description": "Pitwall accepted the radio check announcement."}}},
   "example": {"say": true}}
]}
```

| Field | Type | Rules |
|---|---|---|
| `code` | str | `^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){0,2}$`, at most 64 characters, unique in the declaration. Dots group related actions (`test.ping`, `setup.get`). |
| `description` | str | 1..600 characters, English, written for the agent: what it does, when to use it, its side effects and any error codes you answer. |
| `params` | schema | The data the action accepts (the subset below); root `type: "object"`. An action with no data declares `{"type": "object", "additionalProperties": false, "properties": {}}`. |
| `result` | schema | The format of your answer, same subset, root `type: "object"`. |
| `requires_confirmation` | bool | Optional, default `false`. `true`: the engineer must have the player's clear yes on the radio before sending it (Pitwall refuses the send with `confirmation_required` unless the engineer says the player confirmed). Use it for anything the player would notice: a setup written, a car setting changed. |
| `example` | object | Optional: one `params` instance. It must validate against `params`, else the action is refused. |
| `timeout_s` | int | Optional, 1..60, default 10: how long Pitwall waits for your answer. |

Limits: 0..32 actions per declaration, the declaration at most 128 KiB of JSON, each action at
most 16 KiB. `actions: []` clears yours. `actions` not a list, more than 32 actions or over 128
KiB refuses the whole call with `bad_request`; **a single bad action is dropped**, named in the
answer `{actions, refused: {code: reason}, changed}` (`actions` is the number kept; the reason
names the keyword and the path) and logged, and the others are kept. Send `ik`
`<id>/actions/<sha12 of the actions>`; the last declaration wins and the answer is not cached.

#### The format: a subset of JSON Schema 2020-12

`params` and `result` are JSON Schema documents restricted to the keywords below. The subset is
small on purpose: the agent builds the data from it, so every field must say what it is, in
which unit and within which range. Any other keyword (`$ref`, `$defs`, `anyOf`, `oneOf`,
`allOf`, `not`, `if`, `pattern`, `format`, `const`, `patternProperties`, ...) refuses the action.

| `type` | Mandatory keywords | Optional keywords |
|---|---|---|
| every node except the root | `type` (one string: never a list, never `null`), `description` (1..300 characters) | `default` (must validate against the node), `examples` (at most 3, each must validate) |
| `object` | `properties` (a map, may be empty); `additionalProperties`, when present, must be `false` (absent means `false`: an unknown field never passes) | `required` (unique names of existing properties) |
| `string` | `maxLength` (1..65,536) | `minLength` (0..`maxLength`), `enum` (1..64 unique strings of at most 64 characters; with an `enum`, `maxLength` may be omitted) |
| `number` | `unit`, `minimum`, `maximum` (finite, `minimum` <= `maximum`) | `exclusiveMinimum`, `exclusiveMaximum` (finite numbers) |
| `integer` | `minimum`, `maximum` (integers) | `unit`, `enum` (1..64 unique integers) |
| `boolean` | none | none |
| `array` | `items` (one node), `maxItems` (1..1,000) | `minItems` (0..`maxItems`) |

- The root is `type: "object"`; its `description` is optional (the action's says it).
- `unit` is not a JSON Schema keyword (2020-12 treats unknown keywords as annotations, so your
  schema stays valid JSON Schema): a non-empty string of at most 16 characters (`"mm"`, `"kPa"`,
  `"°C"`, `"s"`, `"clicks"`, `"%"`, `"1"` for a ratio).
- Property names: `^[a-z][a-z0-9_]{0,63}$`; at most 64 properties per object, 256 nodes per
  schema, at most 4 levels of `object` / `array` below the root.
- `integer` accepts integral values (`3` and `3.0`, not `3.5`); a boolean is never a number.

A good field:

```json
"brake_bias_pct": {"type": "number", "unit": "%", "minimum": 40, "maximum": 80,
                   "description": "Front brake bias, percent of the braking force on the front axle."}
```

Bad fields, each refused: `{"type": "number", "description": "bias"}` (no `unit`, no bounds, and
a description that says nothing: say what the value is, in which form and what happens with it);
`{"type": "string", "description": "Note"}` (no `maxLength`); `{"type": ["string", "null"]}` (a
list of types, no description); `{"anyOf": [...]}` (not in the subset: declare two optional
properties instead).

#### How a call arrives

Declare **`connector.action` v1** (section 2) on the connection of the service that can carry the
actions out, not `inline`: the handler runs on a pool thread. **Never run an action on the game
loop** (the 50 Hz sampler, the live frame): do the work on the handler's thread, or hand it to
your own worker and wait for it, and keep the loop's cost unchanged. Not `ordered`: calls are
independent; if yours need an order, serialise inside the handler.

| Params | Result | Deadline |
|---|---|---|
| `{code: str, data: map, call_id: str}` (`call_id` is 16 hex characters, for your log) | your answer: a map in the action's `result` format | the action's `timeout_s` |

- **`data` is already valid.** Pitwall validated it against your `params` (types, `required`, no
  unknown field, `enum`, bounds, lengths) before relaying it; you still do the semantic checks (a
  value the game refuses, a screen that is not open) and refuse with `bad_request`.
- **Answer within `timeout_s`**, exactly in the `result` format. If it does not match, the
  engineer still receives your answer, flagged with `answer_errors` (`[{path, problem}]`), and
  Pitwall logs a warning: a bug for you to fix. An answer over 256 KiB is refused with
  `too_large`. Pitwall never retries a call: an action may have an effect, so the engineer does
  not repeat a timeout either.
- **Errors you answer** (the engineer receives them unchanged and tells the player in a
  sentence, so make `msg` plain English): `unknown_action` (a code you do not serve, only after a
  version change race), `game_not_running`, `not_on_track`, `busy` (with `retry_ms`),
  `unavailable`, `bad_request` (your own semantic refusal: `msg` says why, for example "no setup
  screen is open"), `internal`, and any other code you document in the action's `description`.
- Limits on the engineer's side: `data` at most 64 KiB, 20 sends per 60 s.

#### Declare at every connection

Declare again after every (re)connection, in any order relative to the catalog (section 5) and
the guidance (section 5.1). Pitwall stores the last declaration per connector id (so the engineer
can list your actions while you are not connected; the list says whether you are) and replaces
it. When a new version of your connector changes the actions, just declare the new set: calls in
flight at that moment still get their answers. Descriptions are English only and are never shown
to the player.

#### A full example: `test.ping`

```python
from stintmate_ipc import Client, KeyFile, PeerInfo, RemoteError, default_key_path, pipe_path

ID = "examplesim"
info = PeerInfo(f"connector-{ID}", name="ExampleSim connector", version="1.2.0",
                process="recorder",
                meta={"connector_id": ID, "connector_name": "ExampleSim connector",
                      "sim": "examplesim", "sim_name": "ExampleSim", "service": "recorder",
                      "product": "6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43"})
c = Client(pipe_path("pitwall"), info, KeyFile(default_key_path()))

ACTIONS = [{
    "code": "test.ping",
    "description": "Round trip check: answers pong at once and, unless say is false, plays "
                   "the radio check on the player's radio.",
    "timeout_s": 5,
    "params": {"type": "object", "additionalProperties": False, "properties": {
        "say": {"type": "boolean", "description": "Also play the radio check (default true)."}}},
    "result": {"type": "object", "additionalProperties": False, "required": ["pong"],
               "properties": {"pong": {"type": "string", "maxLength": 8,
                                       "description": "Always 'pong'."}}},
    "example": {"say": False},
}]


def action(ctx, p):                       # runs on a pool thread, never on the game loop
    if p["code"] != "test.ping":
        raise RemoteError("unknown_action", f"{p['code']} is not served here")
    if p["data"].get("say", True):
        announce_radio_check()            # your own pitwall.announce call (section 5)
    return {"pong": "pong"}


def declare(peer):                        # runs after every connection
    r = peer.call("pitwall.actions.declare", {"actions": ACTIONS}, timeout=5,
                  idempotency_key=f"{ID}/actions/{sha12(ACTIONS)}")
    if r["refused"]:
        log.error("actions refused: %s", r["refused"])


c.method("connector.action", action)      # default: a pool thread, not inline
c.on_connect = declare
c.start()
```

`sha12` is any stable short hash of the value (section 9 defines one); `announce_radio_check`
and `log` are yours.

#### Testing

1. Check the declaration before connecting: the [skeleton](../skeleton/) carries a conformance
   test that checks the keywords, descriptions and bounds of the declared actions, for every type
   of this section. It does not name the refused field or check the size limits: copy it and
   extend it for those.
2. Connect your connector to a running Pitwall, then ask the agent: in a session with the
   Pitwall MCP server, `list_connector_actions("ping")` shows what you declared and
   `send_connector_action("test.ping", {"say": false})` sends it. `pitwall.actions.send` is
   refused to any caller but Pitwall's MCP server (`engineer_only`), so a script cannot stand in
   for the agent. Pitwall logs one line per call (`Action <code> -> <id>: ok|<error> in <ms>
   ms ...`, never the data) and keeps the history for 30 days.

#### Conventional action codes

Two codes are a convention, so that the engineer's tools can test any connector the same way.
Declare them (the AMS2 connector does) and answer these formats:

| Code | Params | Result | Timeout |
|---|---|---|---|
| `test.ping` | `{say?: bool}` (default true: also play the connector's radio check) | `{pong: "pong"}`, plus `announced: bool, message_id?: str` when it plays the radio check (declare them in the result format) | 5 s |
| `test.announce` | `{code: str, params?: [{name, value}] (at most 8, values as text), variant?: int 0..7}`; `code` is one of the connector's catalog codes | `{announced: bool, message_id?: str, refused?: str}` | 5 s |

### 5.3 Priority and maximum wait

Two catalog fields tell the radio how urgent a message is and how long it stays worth saying.
You set them per message, in the catalog; the user cannot change them and neither can Pitwall.
Pitwall copies them onto what it publishes, and the radio orders its one queue by them.

**Levels** (`priority`, default `normal`), highest first:

| Level | Use it for | Example |
|---|---|---|
| `spotter` | what the player must hear within a second or two, or it is useless; the only level that cuts what is being said | "Car left." |
| `critical` | safety, right now; goes before everything queued and is announced with the alert tone, but never cuts a sentence | "Red flag." |
| `high` | the player must act soon | "Blue flag.", "Drive-through penalty." |
| `normal` | ordinary race information | "Final lap.", "Fuel for three laps." |
| `low` | nice to know, said only when the radio is free | "Rival retired." |

Within a level the order is first come first served. A message that a `spotter` cuts is said
again afterwards if its maximum wait still allows.

**`spotter` limits.** It needs `audience: driver` and a `max_wait_s` of 3 or less (an entry with
no `max_wait_s`, or one above 3, or an agent audience, is refused). Use it only for calls worth
interrupting what is being said, and give them one `slot` (below) so the newest replaces the
last ("Clear" replaces an unsaid "Car left"). `audience: agent` messages use only `low`,
`normal` or `high`.

**`spotter` messages are not kept.** They reach the radio on `pitwall.announcements` but are not
stored in the message history nor published on `pitwall.connector_messages`, so the engineer's
`list_connector_messages` never shows them (hundreds per race would bury it). Guidance: a
spotter calls on **state changes only** (never repeat a call while the state holds), gives its
calls **one slot**, and sends **no params**, so the radio can synthesise each phrase once, ahead
of time. Give them the `spotter` `group` so the user can switch them as one.

**`max_wait_s`** (number, 0.5 to 600, optional): the seconds, counted **from the moment of the
event** (the time Pitwall published it, a few milliseconds after your `pitwall.announce`),
within which the message must **start** being said. If the radio is busy for longer, the message
is dropped: a yellow flag said 20 s late is noise. Once a message has started it is said to the
end unless a `spotter` cuts it. For an `agent` message it is the deadline for the engineer to be
given it. **Without `max_wait_s`** the message waits until it can be said, however long that is
(use that for things that stay true: a disqualification, a red flag).

**`slot`** (string, `^[a-z][a-z0-9_]{0,31}$`, optional): when a new message arrives and an older
one of the same connector and slot is still queued (not yet started), the new one replaces it.
Use one slot for messages that supersede each other (`local_flag` for yellow ahead, slow car and
clear; `blue_flag` for blue flags). Messages without a slot never replace anything.

**Rate budgets.** At most 10 announcements per connector in any 60 s; `spotter` messages have a
separate budget of 120 in any 60 s that does not count against the 10. Beyond a budget
`pitwall.announce` answers `rate_limited` with `retry_ms`. Stintmate never drops a message just
for coming shortly after another, so send each event once.

**Refusals.** An entry that does not validate is dropped from the catalog and logged by Pitwall
(the rest is kept): `bad_audience`, `bad_priority` (an unknown level, `spotter` on an agent
message, or `critical` on an agent message), `bad_max_wait` (not a number from 0.5 to 600, or a
`spotter` without `max_wait_s` or above 3), `bad_slot`, `bad_kind`, and `bad_field: ttl_s`
(**`ttl_s` no longer exists**; use `max_wait_s`, which is counted from the event, not from
delivery).

## 6. Telemetry for brake cues: `connector.telemetry` (stream)

Pitwall's brake cues are scheduled in `pitwall-core` on simulator-neutral data; the sound is
played by the voice app's audio engine. The recorder declares the stream `connector.telemetry`
v1, `rate_hz: 100`, layout `telemetry` (32 bytes, little-endian). The layout is declared in the
handshake, so a subscriber reads it from there:

| Offset | Field | Type | Meaning |
|---|---|---|---|
| 0 | `lap_distance_m` | f32 | Distance along the lap from the line (the viewed car); -1 unknown. |
| 4 | `speed_ms` | f32 | Car speed, m/s. |
| 8 | `accel_long_ms2` | f32 | Longitudinal acceleration in the car's frame, m/s², positive forward. |
| 12 | `track_length_m` | f32 | Layout length; 0 unknown. |
| 16 | `lap_time_s` | f32 | Current lap clock; -1 when not running. |
| 20 | `frame` | u32 | The connector's counter of distinct game frames. |
| 24 | `sim_lap` | u16 | The simulator's lap counter; 0 unknown. |
| 26 | `session_type` | u8 | 0 unknown, 1 practice, 2 test, 3 qualifying, 4 warmup, 5 race, 6 other. |
| 27 | `game_state` | u8 | 0 not running, 1 menus, 2 loading, 3 playing, 4 paused, 5 replay. |
| 28 | `flags` | u16 | bit 0 `on_track`, 1 `in_pit_lane`, 2 `in_garage`, 3 `timed`, 4 `flying`, 5 `lap_invalid`, 6 `player_view`, 7 `fresh` (a new game frame; 0 = a keep-alive repeat). |
| 30 | `reserved` | u16 | 0. |

Rules (the AMS2 connector follows them; Pitwall's scheduler relies on `t_ns` and `fresh`):
`t_ns` is taken when the connector first sees the frame (not at send time); send a sample only
for a new game frame, at most `rate_hz` per second, and one repeat with `fresh` = 0 every 250 ms
while nothing changes (menus, pause); produce samples **only while subscribed**; never block
(the stream drops the oldest). Pitwall subscribes only while brake cues can fire: the connector
declares the stream, `connector.status.live.session_type` is `practice` (the cues also accept
`test`, a live session type the connector reports for a test day; it is not a stored one) and there is an enabled reference for that car
and layout. `flying` is the connector's view (a timed lap begun on track); the line crossing
just ahead after an out lap is predicted by Pitwall.

<a id="the-live-frame"></a>

## 7. The live frame: `connector.live_frame`

Params `{}`; result: the live frame below. Errors `game_not_running`, `busy` (no consistent
read: Pitwall retries once), `internal`. It answers from memory (declare it `inline` in the
Python package). Pitwall's deadline is 1 s for tools and 0.3 s after a lap.

What happens on track now, in the data model's vocabulary and units. Pitwall computes everything
else from it and the database: deltas to the reference laps, gaps, the corner, fuel laps left,
laps to finish.

| Group | Fields (units) | When |
|---|---|---|
| top level | `sim`; `phase`: `menus` (not in a session: main menus, loading, replays, restarting), `garage` (in a session, the player's car in the garage: the simulator's garage or setup screen, not driving), `on_track` (driving, the pit lane included), `paused` (in a session out of the garage, the simulator paused or an in-session menu over the car on track); optional `taken_at` (ISO 8601 with offset), `data_age_s` (s since the simulator last updated its data); these two are informational | always |
| `session` | `type` (`practice`, `qualifying`, `race`, `warmup`, `other`); `time_remaining_s`; `laps_in_event` (lap race); `duration_s` (timed session); `additional_laps` (laps after the time runs out, 0 = none); optional `started` (false before a race start: grid, formation lap; true once started; `null` = not known or not a race) | in a session |
| `track` | `track_id`, `layout_id` (catalog ids), optional `track_name`, `layout_name`; `length_m` | in a session |
| `car` | `car_id`, optional `class_id` (`null` when the simulator gives none), `car_name`, `class_name`; `pit_mode` (code: 0 on track, 1 into the pits, 2 in the box, 3 out of the pits or garage, 4 in the garage); `speed_kmh`; optional `gear` (-1, 0, 1..n), `rpm`; `fuel_l`, `fuel_capacity_l` (L); optional `penalty` (a penalty pending for the player: `drive_through`, `stop_go`, `other`; `null` = none or not known); `wheels` `{fl, fr, rl, rr}`, each `tread_in_c`, `tread_mid_c`, `tread_out_c` (C; inner = towards the car's centreline), `pressure_kpa` (live), `carcass_c`, `wear` (0..1), `compound`, `brake_c` | in a session |
| `lap` | the player's lap now: `current_lap` (the simulator's number), `laps_completed`, `distance_m` (from the line), `time_s` (lap clock, `null` while not timed: out lap, pits, before the start), `valid`, `sector` (1-based), `last_lap_s`, `best_lap_s` (the player's session best by the simulator); optional, [why not timed or not valid](#71-why-a-lap-is-not-timed-or-not-valid): `lap_type`, `invalid_cause`, `invalid_cause_certain`, `invalid_at_m` | in a session, absent with no player car (spectating) |
| `splits` | `ahead_s`, `behind_s`: the simulator's own time gaps to the cars ahead and behind, if it has them (Pitwall uses them in races only) | optional |
| `conditions` | the write API's conditions: `ambient_c`, `track_c`, `rain` (0..1), `wind_speed_ms`, `cloud` (0..1 cover; leave out a value on another scale) | optional |
| `participants` | every car, the player included: `index` (the simulator's slot), `name`, `is_player`, `is_safety_car`, `active` (false = a listed slot not in the session now), `position` (`null` = unknown), `laps_completed`, `current_lap`, `distance_m`, `sector`, `pit_mode`, `car_id`, `class_id`, `last_lap_s`, `best_lap_s`. At least one entry has `is_player: true`, also for a game with one car: without it Pitwall answers its live tools with `not_on_track` | in a session |

"In a session": `phase` `garage`, `on_track` or `paused` (the groups are absent in `menus`).
Without a session on track Pitwall answers its tools with `not_on_track`. `lap.current_lap` and
`lap.laps_completed` are the simulator's own counters, sent as they are (some do not count laps
through the pits or the garage); Pitwall numbers the laps itself (the live state's
`player.current_lap` is the lap being driven in the stored laps' numbers).

Rules: times are seconds, `null` when the simulator does not give them (never `-1` or `0` as
"unknown"); ids are the same catalog ids the connector writes (so the frame matches the stored
sessions); a participant's `distance_m` may be slightly negative or past the length around the
line; mark the safety car (it is left out of the classification and the gaps).

### 7.1 Why a lap is not timed or not valid

Optional fields that let the live state say why the lap being driven does not count
(`player.lap_status`) instead of a bare "invalidated". Send only what the simulator really
tells; leave the rest out (or `null`) and Pitwall says `unknown`.

| Field | Meaning |
|---|---|
| `session.started` | `false` before a race start (grid, formation lap), `true` once started. |
| `lap.lap_type` | The data model's lap type of the lap being driven, as far as the connector knows it now: `formation`, `out` (left the pits or the garage, until the line), `in` (once the car turns into the pits), `flying`. |
| `lap.invalid_cause` | Why `valid` is `false`: `off_track`, `track_limits`, `cut` (a corner cut), `penalty` (the simulator invalidated the lap with a penalty), `unknown` (flagged, cause not known). |
| `lap.invalid_cause_certain` | `false` when `invalid_cause` is only the probable cause (for example all four wheels over a kerb just before the flag). Absent = certain. |
| `lap.invalid_at_m` | Lap distance where the simulator flagged the lap, m (Pitwall names the corner from its track map). |
| `car.penalty` | A penalty pending for the player: `drive_through`, `stop_go`, `other`. |

Pitwall's `player.lap_status.code`, first match wins: the pit mode (`in_garage`; `in_pit_lane` in
the box; `in_lap` driving into the pits; `out_lap` driving out), then `before_start`
(`session.started` false or `lap_type` `formation`), `out_lap` / `in_lap` (`lap_type`),
`unknown` when `time_s` is `null` with none of these, then for `valid` false `invalid_cause`
(`unknown` when absent); `null` on a timed, valid lap. `player.lap_invalidated` is `true` only in
the `valid` false case, `false` on a timed valid lap and `null` when the lap is not timed: an out
lap is never "invalidated", whatever `valid` says (some simulators flag every lap that is not
timed).

## 8. Error codes

The protocol's codes (IPC `SPEC.md` section 11) plus the application's, listed in
[contract.md](contract.md). The ones a connector meets or answers:

| Code | Where | Meaning |
|---|---|---|
| `bad_request` | writes, declare, announce, guidance, actions | Refused: `msg` names the field; not retryable. For `pitwall.announce`: also a `variant` out of range or not an integer. For `pitwall.guidance.declare`: `items` not a list, more than 16 items, or over 8,000 characters (a single bad item is only dropped, section 5.1). For `pitwall.actions.declare`: `actions` not a list, more than 32 actions or over 128 KiB (a single bad action is only dropped, section 5.2); also what you answer to `connector.action` for your own semantic refusal. |
| `connector_id_taken` | `bye` | The `connector_id` was first seen with another `product`, or another linked connection of the same id already names another `product`. `details` `{connector_id, product_seen}`. Final until the user acts (the user removes the other install, or you choose another id): retry after 60 s, no faster. Report `control.state.pitwall` = `refused`. |
| `writer_busy` | writes | The database is held by another writer (an import); `retry_ms`; the key is not consumed. |
| `session_closed`, `unknown_session` | writes | The `session_key` was closed by Pitwall, or never started. |
| `unknown_code` | announce | A code not in the declared catalog (or nothing declared). |
| `rate_limited` | announce | More than 10 in 60 s (120 for `spotter`); `retry_ms`. |
| `connector_busy` | `bye`, writes | Sent only by a Pitwall older than contract 2, while another connector id was connected. Retryable like any other code: retry after 10 s. |
| `game_not_running`, `not_on_track`, `not_configured`, `busy`, `internal` | the connector's answers | Relayed to the tool unchanged. |
| `window_minimized`, `not_capturable` | `connector.capture_target` | The window cannot be grabbed: `window_minimized` is refused with the connector's `msg`; `not_capturable` makes Pitwall use the foreground window (section 4). |
| `blank_frame` | `pitwall.capture` | The grab returned a black or flat frame (Pitwall's own). |
| `unknown_action`, `invalid_data`, `confirmation_required`, `too_large`, `engineer_only` | actions | Pitwall's refusals of a send: a code you did not declare (`unknown_action`, also what you answer for a code you no longer serve), data that does not match `params` (`invalid_data`), an action with `requires_confirmation` sent without the player's yes, data over 64 KiB or an answer over 256 KiB, a caller that is not the engineer's MCP server. The engineer gets them; you never see the first four (section 5.2). |
| `connector_disconnected`, `not_implemented_by_connector`, `timeout` | tools | What a tool gets when there is no connector, it does not declare the name, or it did not answer in time. |

## 9. Worked example (Python, the reference package)

A recorder that declares what it serves, writes a catalog, one session with an out lap and a
timed lap, and ends it. Every write carries its idempotency key. In a real connector the writes
go through the outbox (section 3.3) and are sent by a background thread; the sampling loop only
fills buffers.

```python
import hashlib
import json
import secrets
from array import array
from datetime import datetime

from stintmate_ipc import Client, KeyFile, Layout, PeerInfo, default_key_path, pipe_path

ID = "examplesim"
LENGTH_M = 5148.0


def sha12(value):                              # any stable short hash of the value
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:12]


def now():                                     # ISO 8601 with the UTC offset
    return datetime.now().astimezone().isoformat(timespec="seconds")


def f32(values):                               # a lap channel: little-endian float32 bytes
    return {"encoding": "f32", "data": array("f", values).tobytes()}


CATALOG = {"classes": [{"class_id": "gt3", "name": "GT3"}],
           "cars": [{"car_id": "gt3_a", "name": "GT3 A", "class_id": "gt3"}],
           "tracks": [{"track_id": "ring", "name": "The Ring"}],
           "layouts": [{"track_id": "ring", "layout_id": "gp", "name": "Grand Prix",
                        "length_m": LENGTH_M}]}
GUIDANCE = [{"id": "setup_from_capture", "topic": "setup", "when": "you read a car setup",
             "text": "Setups are encrypted: ask the player for a capture."}]
ACTIONS = [{"code": "test.ping", "description": "Round trip check: answers pong.",
            "params": {"type": "object", "additionalProperties": False, "properties": {}},
            "result": {"type": "object", "additionalProperties": False, "required": ["pong"],
                       "properties": {"pong": {"type": "string", "maxLength": 8,
                                               "description": "Always 'pong'."}}}}]
TELEMETRY = Layout([("lap_distance_m", "f32"), ("speed_ms", "f32"), ("accel_long_ms2", "f32"),
                    ("track_length_m", "f32"), ("lap_time_s", "f32"), ("frame", "u32"),
                    ("sim_lap", "u16"), ("session_type", "u8"), ("game_state", "u8"),
                    ("flags", "u16"), ("reserved", "u16")], name="telemetry")

info = PeerInfo(f"connector-{ID}", name="ExampleSim connector", version="1.2.0",
                process="recorder",
                meta={"connector_id": ID, "connector_name": "ExampleSim connector",
                      "sim": "examplesim", "sim_name": "ExampleSim", "service": "recorder",
                      "product": "6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43"})
c = Client(pipe_path("pitwall"), info, KeyFile(default_key_path()))
c.method("connector.live_frame", lambda ctx, p: {"sim": ID, "phase": "menus"}, inline=True)
# Optional: serve the engineer's actions (section 5.2), on a pool thread, never the game loop.
c.method("connector.action", lambda ctx, p: {"pong": "pong"})
status = c.state_channel("connector.status", {"game_running": True, "phase": "garage"})
tel = c.stream_channel("connector.telemetry", TELEMETRY, rate_hz=100)


def declare(peer):                             # after every connection (sections 5.1, 5.2)
    peer.call("pitwall.guidance.declare", {"items": GUIDANCE}, timeout=5,
              idempotency_key=f"{ID}/guidance/{sha12(GUIDANCE)}")
    peer.call("pitwall.actions.declare", {"actions": ACTIONS}, timeout=5,
              idempotency_key=f"{ID}/actions/{sha12(ACTIONS)}")


c.on_connect = declare
c.start()                                      # connects and reconnects by itself
c.wait_connected(5)

c.call("pitwall.write.catalog", CATALOG, timeout=30,
       idempotency_key=f"{ID}/catalog/{sha12(CATALOG)}")
key = secrets.token_hex(8)                     # 16 hex digits, one per session
c.call("pitwall.write.session_start", {"session_key": key, "started_at": now(),
       "session_type": "practice", "car_id": "gt3_a", "track_id": "ring", "layout_id": "gp"},
       timeout=30, idempotency_key=f"{ID}/{key}/start")
status.set({**status.value, "session_key": key,
            "live": {"session_type": "practice", "car_id": "gt3_a", "track_id": "ring",
                     "layout_id": "gp", "track_length_m": LENGTH_M}})
# Pitwall numbers laps and stints: send the facts (`on_track` per lap, `pit_exit` when the
# car leaves the pit lane or the garage, after the laps that ended before it).
c.call("pitwall.write.pit_exit", {"session_key": key, "seq": 1, "at": now()},
       timeout=30, idempotency_key=f"{ID}/{key}/pit_exit/1")
for seq, (lap_type, time_s) in enumerate([("out", None), ("flying", 92.5)], start=1):
    n = 20
    lap = {"session_key": key, "seq": seq, "lap_type": lap_type, "valid": True,
           "channels": {"t": f32([i * 92.5 / n for i in range(n)]),
                        "dist": f32([i * LENGTH_M / n for i in range(n)]),
                        "speed": f32([40.0 + i for i in range(n)])}}
    if time_s is not None:
        lap["time_s"] = time_s                 # only when the simulator timed the lap
    c.call("pitwall.write.lap", lap, timeout=30, idempotency_key=f"{ID}/{key}/lap/{seq}")
c.call("pitwall.write.session_end", {"session_key": key, "ended_at": now()},
       timeout=30, idempotency_key=f"{ID}/{key}/end")
c.close()
```

## 10. Testing a connector

- The [skeleton](../skeleton/) carries a conformance test (`skeleton/tests/test_conformance.py`)
  that stands up a fake Pitwall built from the names in these documents, runs the conformance
  checker against it and checks the descriptor; copy and adapt it for your connector.
- `stintmate-ipc-check client <pipe>` (the conformance checker of the Rust implementation, in
  the `stintmate-ipc` repository) checks a client's handshake, reconnection and
  well-formedness; `stintmate-ipc-check server` checks an endpoint.
- Before the first session in the car, run your connector against the installed Pitwall and
  check the stored data as [connectors.md](connectors.md) says.

## 11. Laps and stints: what the connector sends

**Pitwall numbers laps and decides stints; the connector never does.** The connector reports
what the simulator says about each lap and a few facts; Pitwall applies one rule for every
simulator. Field details are in [write-api.md](write-api.md) and the vocabularies in
[data-model.md](data-model.md); this section is the reasoning and the order.

### 11.1 Per lap

| Field | What to send |
|---|---|
| `lap_type` | `formation` (before a race start, never timed), `out` (left the pits or the garage, until the line), `in` (ended in the pits), `flying` (line to line on track). When one lap is several (out and in at once), `formation` wins, then `out`, then `in`. |
| `valid` | The simulator's validity flag; `true` when it has none. |
| `time_s` | Only when the simulator timed the lap; absent otherwise (never `0` or `-1`). |
| `sim_lap` | The lap number **the simulator's HUD shows for that lap**; absent when the simulator shows none. It is shown only: Pitwall's own lap number (the `lap` of the reply) is assigned by Pitwall in the order it stores the laps and is the lap's identity. |
| `on_track` | The car was on the track, outside the pit lane, during the lap. Absent: Pitwall derives it from the `pit_mode` channel (any sample `0`), else `true`. |

The connector never sends `stint`.

### 11.2 Facts, and the order

`pitwall.write.pit_exit` (`{session_key, seq, at, fuel_l?}`): the car has just left the pit lane
or the garage under its own power. Pitwall's rule: stint 1 starts with the session; a pit exit
after the car was on track in the current stint starts the next one; the first exit of a session
(leaving the garage) stays in stint 1; stints never go back. A reply carries `stint` and
`new_stint`.

Order, always through the outbox (section 3.3): **every lap that ended before the exit is
written first, then `pit_exit`, then the setup taken at the exit**, so the setup lands in the
new stint. Flush a lap still pending, and close the open lap as incomplete if the car had been
driven, before sending the exit.

### 11.3 Counters: the HUD number into `sim_lap`

Simulators count laps in three ways; find out which yours uses (section 11.5) and derive the HUD
number of each lap from it:

| The simulator exposes | `sim_lap` of a lap is |
|---|---|
| A **started-laps** counter (it shows 1 while driving the first lap) | The value during that lap |
| A **completed-laps** counter (0 while driving the first lap) | The value at the start of the lap, plus 1 |
| Its own lap type per lap | Use it for `lap_type`; the number rule above still applies |
| No counter | Leave `sim_lap` out; Pitwall's own lap number is then what is said |

Counters differ in what they do on the out lap, after a return to the pits and on a restart. Do
not assume: measure it, and treat every row above as something to verify with your own study.

### 11.4 Odd cases

| Case | What the connector does |
|---|---|
| Back to the pits by a menu or a reset (teleport) | Close the current lap as incomplete (`in`, not timed). No new session. The next drive out is a `pit_exit`: a new stint if the car had been on track. The counter may not change; ignore it for numbering and keep the HUD number in `sim_lap`. |
| Session restart (the simulator resets the session or its counters) | `session_end`, then `session_start` with a **new** `session_key`. |
| Lap counter jumps while in the pits | Ignore it for numbering; `sim_lap` stays what the HUD showed. |
| Race start | The laps before timing starts are `formation`, never timed. |
| Very short laps (a drive-through glitch, a teleport right after the line) | The connector may drop laps under a minimum duration, and logs it. |
| Session ends mid-lap | The lap is `flying` (or its type), not timed, stored as incomplete. |
| The game closes with a session open | Close the open lap as incomplete and send `session_end`, both through the outbox. |
| The connector is stopped (`control.quit`) with a session open | The same, within the 20 s `control.quit` allows. If it does not get that far, the session stays open and the next `session_start` closes it (section 3.1). |
| Two flying laps with the same `sim_lap` in one session | Send both as they are; Pitwall's labels tell them apart by stint. |

### 11.5 Study the simulator before writing laps

Before a connector writes laps, record real sessions and write down what you find in a findings
document (the [guide](guide.md) has a template). For each experiment note the field names and
values, not impressions:

1. First lap leaving the garage: the lap counter, lap type or pit flags on the out lap.
2. Crossing the line after the out lap: what the counter and last-lap time do.
3. A normal flying lap, and an invalidated one: when and how the validity flag changes.
4. Driving into the pits and out again (stop, no menu): the counter, the pit flags.
5. Back to the pits by a menu or a reset: counter, flags, last-lap time.
6. A session restart: counters, session identifiers.
7. A race start: formation lap, grid, when timing starts.
8. The end of the session mid-lap: what the last lap looks like.

Also note what the HUD shows on each lap: that is what `sim_lap` must equal.
