# The connector side of the Stintmate application contract

Stintmate's apps talk over Stintmate IPC v2 ([repository](https://github.com/Stintmate-App/stintmate-ipc),
[SPEC](https://github.com/Stintmate-App/stintmate-ipc/blob/main/SPEC.md)). The SPEC defines the
transport: handshake, methods, state channels, events, streams, flags and the protocol error
codes. This page is the application layer a connector meets on top of it: the names, params,
results and application error codes of the connector's two connections, to Pitwall and to the
managing app. How to use them step by step is in the [connector protocol](connector-protocol.md);
the machine-readable names are in [`contract.json`](../contract.json) (also in the release zip).

A connector has two sides:

- **To Pitwall** (endpoint `pitwall`): the connector connects as a client with its hello
  metadata, declares `connector.*` names, writes laps, declares catalogs, guidance and actions.
- **To the managing app** (the connector's own endpoint `connector-<id>`): the connector serves
  the control interface, section 3, so the managing app can show, start and stop it.

## 1. Error codes (application layer)

A receiver treats an unknown code as `internal`. Messages (`msg`) are English; a UI that shows an
error localises it by its code. A caller that gives up cancels the call; the owner honours
`cancel` as the SPEC says. The SPEC's own codes (`bad_request`, `unknown_method`, `busy`,
`timeout`, `too_large`, `internal`, ...) are listed in the SPEC. The application codes a
connector sends or receives:

| Code | Meaning | Retry |
|---|---|---|
| `game_not_running` | The simulator is not running (or frozen). | later |
| `not_on_track` | Needs the car on track or a session; `details` `{phase, game_running}`. | later |
| `not_configured` | A setting the user has not made yet; `msg` says which and where. | after the user acts |
| `connector_disconnected` | Pitwall has no connector connected. | later |
| `not_implemented_by_connector` | The connected connector does not declare that method or channel; `details` `{connector, sim, sim_name, method}`. | no |
| `connector_busy` | Sent only by a Pitwall older than contract 2 (in `bye` or as a write's answer, see 4.1), while another connector id was connected. | 10 s, like any retryable code |
| `writer_busy` | Pitwall's database is held by another writer (an import); nothing stored. **Does not consume the idempotency key.** | `retry_ms` |
| `engineer_only` | The method is for the engineer's tool server; another peer called it. | no |
| `unknown_action` | The connected connector did not declare that action code (`details` `{connector, codes}`); also what a connector answers for a code it no longer serves. | no |
| `invalid_data` | The data of an action does not match its `params`; `details.errors` `[{path, problem}]`. | after fixing it |
| `confirmation_required` | The action declares `requires_confirmation` and the send lacked `driver_confirmed: true`. | after the user's yes |
| `session_closed` | A write names a `session_key` Pitwall has closed. | no |
| `unknown_session` | A write names a `session_key` that was never started. | no |
| `rate_limited` | A per-sender rate limit; `retry_ms` set. | `retry_ms` |
| `unknown_code` | An announcement code not in the sender's declared catalog. | no |
| `managed` | A managed control app refuses `control.quit` from a caller that is not its manager; `details.managed_by`. | no |
| `not_found` | The thing named (an id, an activity item) does not exist (any more). | no |
| `no_wheel` | `control.buttons.detect` found no joystick or wheel. | after the user connects one |
| `window_minimized` | Capture: the game window is minimized; `msg` says how to fix it. | after the user acts |
| `not_capturable` | Capture: the connector cannot name a window (Pitwall then grabs the foreground window) or the window cannot be grabbed. | no |
| `blank_frame` | Capture: every grab method returned a black or flat frame. | later |

**`busy`.** With `retry_ms` it is the SPEC's: the owner is overloaded, retry after that long.
**Without `retry_ms` it is final for this call**: the app refuses because of a state that
retrying does not clear at once (it is exiting, a service is `starting` or `stopping`;
`details.state` says which). The caller shows the message and does not retry on its own.

## 2. Deadlines and producers

- Callers send a deadline `dl`; the default is 5 s, and each method below says its own.
- **Producers never block.** The connector's game loop never waits on a pipe: sampling feeds a
  queue, and a background thread sends.
- **Load once.** Catalogs and layouts are declared once per connection, never per sample or
  per lap.

## 3. The control interface (every control app)

Served on the connector's endpoint `connector-<id>`. The caller is logged with its process
chain. A command refused while the app is starting or exiting answers `busy` (`details.state`;
no `retry_ms`, so final for that call).

### 3.1 Methods

| Method | Params | Result | Notes |
|---|---|---|---|
| `control.show` v1 | `{}` | `{window: "shown"}` | Bring the app's window up. |
| `control.hide` v1 | `{}` | `{window: "hidden"}` | |
| `control.quit` v1 | `{}` | `{exiting: true}` | Returns at once; the app stops its services and exits. The caller waits for the endpoint to disappear (the pipe is gone) for up to 20 s, then terminates `control.state.pid`. Managed: only the managing process (or a child of it) may call it; others get the application code `managed` (final, not retried) with `details.managed_by`. |
| `control.managed` v1 | `{parent_pid: int}` | `{managed: true, parent_pid}` | Managed mode: the app watches that PID every 2 s and falls back to standalone when it ends. `parent_pid` is also checked against the caller's OS PID chain (logged if different). |
| `control.service.start` v1 | `{service: str}` | `{service, state}` | The services are those the descriptor lists. Unknown: `bad_request`. Services the app lists in `control.state.services[]` as `delegable` are the ones the managing app keeps on or off (3.2). A **managed** app (`--managed --parent-pid`, or adopted by `control.managed`) does not start its `delegable` services by itself: the managing app sends `control.service.start/stop` shortly after the endpoint answers (restoring the user's saved choice, else the service's `default`); `always_on` services the app starts itself. The app's own startup settings (`start_with_app`) apply only when it runs alone. These calls change the running state only and never rewrite the app's own settings. A call on a service with a `refusal` tries again. Busy service (`starting`, `stopping`...): `busy` with `details.state` and no `retry_ms`. |
| `control.service.stop` v1 | `{service: str}` | `{service, state}` | Clean stop, forced after the app's own timeout. |
| `control.autostart.set` v1 | `{enabled: bool}` | `{autostart: bool}` | The app's own "start with Windows" entry. |
| `control.activity.act` v1 | `{id: str, action: str}` | `{accepted: bool}` | The user pressed the button of a `control.activity` item (3.2). Errors: `not_found` (final: the item or action is gone), `busy`. Not ordered, not idempotent. The app re-checks the item's `enabled` itself: the managing app's greyed button is a hint, not the guard. |
| `control.catalog.get` v1 | `{}` | `{catalog: <the app's built-in English catalog document, catalogs.md section 2>, rev: str (the first 12 hex digits of the SHA-256 of its canonical JSON, sorted keys, no spaces, UTF-8)}` | The managing app's translate job reads it to learn which texts a language pack must hold; the apps' catalogs are not files it may read. `idempotent: true`, not ordered, deadline 5 s, result at most 1 MB. Served by a control app that has window texts; one without it answers `unknown_method` and its UI stays in its own language. The app reads the catalog and computes `rev` once. |

All are `ordered: true` (per connection), not idempotent; deadline 5 s. Exceptions:
`control.activity.act` and `control.catalog.get` are not ordered (and the latter is idempotent).

### 3.2 Channels

| Channel | Kind | Payload | Subscribers |
|---|---|---|---|
| `control.state` v1 | state | the app's state, below | the managing app |
| `control.services.desired` v1 | event (optional) | `{service: str, on: bool, at: str (ISO 8601 UTC)}` | the managing app. Published by a control app when the **user** switched a `delegable` service in the app's own window or tray, and the app is managed. Never for a `control.service.*` call, an automatic restart, a crash or a refusal. Declared only by apps that have such switches. The managing app subscribes when the endpoint declares it (an `unknown_channel` reply is not an error), saves the value and sends nothing back. Not delivered while the managing app is not connected: a change made while the app runs alone stays the app's own setting, and the saved state applies again the next time the managing app manages the app. |
| `control.notices` v1 | event | `{title: str, message: str, level: "info"\|"warn"\|"error", at: str, kind?: "info"\|"action"\|"progress"\|"warning"\|"error", code?: str, id?: str, lifetime?: "dismissable"\|"expires", ttl_s?: number}` | the managing app (tray balloon for errors, and the notice bar). The optional fields are additive (the version stays v1): without them `error` is an `error` and `warn` a `warning`, both `dismissable`, and `info` an `info` that `expires` (`ttl_s`, default 10). Events with the same `code` (else the same `title`) replace each other and the bar counts them ("x3"). `id` names the notice inside the app. |
| `control.activity` v1 | state | `{items: [{id: str, kind: "info"\|"action"\|"progress"\|"warning"\|"error", title: str, text?: str, priority?: int (-5..5, added to the kind's), lifetime: "held"\|"dismissable", progress?: {value?: float 0..1 \| null, done?: int, total?: int, stage?: str}, action?: {id: str, label: str, enabled: bool, reason?: str}, language?: str}]}` | the managing app (notice bar) |

`control.activity` is what an app holds over time: running jobs with progress, sticky notices,
one action each. Texts are composed by the app in the user's language. At most 20 items (the
managing app ignores the rest); the app updates a progress at most twice a second (coalesced);
the list is empty when idle, and an item is cleared by removing it. `value: null` is an
indeterminate progress. Dismissing is local to the managing app and never sent to the app.

`control.state` common fields (every control app):

| Field | Type | Meaning |
|---|---|---|
| `app` | str | The control app's name. |
| `pid` | int | The control app's PID. |
| `version` | str | |
| `managed`, `parent_pid` | bool, int? | |
| `window` | str | `shown` / `hidden`. |
| `exiting` | bool | |
| `autostart` | bool? | `nil` if unreadable. |
| `level` | str | The tray colour of the app as a whole: `off`, `ok`, `warn`, `error`. |
| `services` | array of map | One per service: `{id, name, state, label, level, running, ownership, pid?, forced_stop, restart_pending?, delegable?, default?, always_on?, refusal?}` (`id` and `state` are machine values; `name` and `label` are texts in the user's language, translated by the app from its own catalog, never English-only). The last four are optional and additive (the version stays v1): `delegable` (bool, absent = false): the managing app may keep the service on or off across restarts, remembering the user's choice and restoring it, and offers a switch; without it the service is shown but never started or stopped on its own. `default` (`"on"`\|`"off"`, absent = `"off"`): the state restored while the managing app has no saved choice. `always_on` (bool): the app keeps it running whenever it runs; shown with no switch, started once with `control.service.start` if found off when the app connects (and there is no `refusal`), and never stopped. `refusal` (`{code: str, label: str}` \| null): the last start could not happen for a reason the user must fix (`code` a machine value, `label` in the user's language); set when the start fails, cleared when a start is attempted again or the service runs; shown with "Try again" and never retried by itself. |

A connector adds `connector: {id, name, version, sim, sim_name}`, `game_running: bool`,
`pitwall: "connected"|"waiting"|"refused"` (from its services), `outbox: {pending: int,
oldest_at: str \| null}` and `start_with_app: [str]` (used only when the connector runs alone).

| `control.state.pitwall` | Meaning | Hub |
|---|---|---|
| `connected` | At least one running service has its connection open | nothing |
| `waiting` | No connection now and none refused: Pitwall absent, reconnecting, key missing, or every service off | "cannot reach Pitwall" only when the core is down and a service runs |
| `refused` | The last connection was closed for a reason a retry will not fix: `unauthorized`, `bad_request`, `busy`, or `connector_busy` from an older Pitwall | held error notice |

| `services[].state` | Meaning | Hub |
|---|---|---|
| `stopped` | Not running | |
| `starting`, `loading`, `connecting`, `waiting_models` | Getting ready | shown amber while the service's `level` is `ok` |
| `waiting` | Recorder running, no game or no session | shown amber while the service's `level` is `ok` |
| `recording` | A session is being written | shown as recording while `level` is `ok` |
| `running` | Any other service that runs | |
| others | Legal: shown by `label` | as `level` |

The service that records has the id `recorder`; further services have other ids. `ownership`,
`forced_stop` and `restart_pending` are informational: the hub reads none of them. The first part
of a service's `label`, up to the first `" · "`, is the text its chip shows.

### 3.3 Buttons (optional)

A control app that owns a wheel, controller or keyboard button the user assigns serves
`control.buttons.*`, so the managing app can show and set every button of every running app
without knowing which app owns which. An app that owns no button answers `unknown_method`,
which is read as "no buttons".

| Method | Params | Result | Notes |
|---|---|---|---|
| `control.buttons.list` v1 | `{}` | `{buttons: [{id, label, kinds: ["wheel"] \| ["wheel", "key"], wheel: {device, buttons, button, label} \| null, key: {vk, name} \| null}]}` | `label` and `wheel.label` in the user's language. Idempotent, not ordered. |
| `control.buttons.detect` v1 | `{id, timeout_s: 5..60 (default 20)}` | `{press: {device, buttons, button, label}}` | The owner pauses whatever reads the button, waits for the first press of any wheel or controller button, resumes. `prog {stage: "waiting", devices: [{name, buttons}], left_s}` at the start and every 5 s (each gives the caller a new deadline: time left + 10 s). `cancel` is honoured. Errors: `no_wheel` (no device), `timeout`, `busy` (another detect, the app's own screen, or the service is starting or stopping: `details.state`), `cancelled`, `bad_request` (unknown id or `timeout_s`). It saves nothing. Not ordered. |
| `control.buttons.set` v1 | `{id, wheel?: {device, buttons, button} \| null, key?: {vk, scan?} \| null}` | the button's `list` item, as stored | An omitted key is unchanged, `null` removes. `key` on a button without the `key` kind: `bad_request`. The owner applies it (restarts its listener when needed). Ordered. |

`device` is the device name as SDL reports it and `buttons` its button count (both are stored:
two devices can share a name); `button` is the number the owner stores and shows. The managing
app compares two presses by `(device, buttons, button)`.

## 4. Pitwall's endpoint, `pitwall`

Served by Pitwall's core, the only writer of the database. Heartbeat 5000 ms.

### 4.1 Who connects, and the hello metadata

| Peer | `peer.app` | `peer.process` | `peer.meta` |
|---|---|---|---|
| a connector process | `connector-<id>` | the service name | REQUIRED `{connector_id: str, connector_name: str, sim: str, sim_name: str, service: str}` (`connector_id` = `<id>`; `sim` `^[a-z0-9_-]{1,32}$`) |

The **connector registry**: every connector id may be linked at once, one connection per
service, every connection of a connector names the same `sim` (a second connection of a service
gets `bye` `busy`, a different `sim` gets `bad_request`). A connector is **linked** while any of
its connections is open; its **functions** are the union of what its connections declare
(`connector.live_frame`, `connector.capture_target`, `connector.telemetry`). Liveness is the
connection (no status heartbeat). One linked connector is **active**: the first whose
`game_running` is true, until that game closes ([levels.md](levels.md) section 3). A write never
makes a connector active. Single-connector answers (live state, captures, actions) follow the
active connector, else the last one that was active and is still linked, else the first linked.
A Pitwall older than contract 2 serves one connector id at a time and closes the connection of a
second one with `bye` `connector_busy`; the current one never sends that code.

### 4.2 Writes (connector to Pitwall)

One method per write API message type. The params are the [write API](write-api.md) message
**without `type`**, plus `session_key`; the result is the writer's reply (without `line`).
Channels are sent as `{"encoding": "f32", "data": bin}` (the little-endian float32 samples as
raw bytes); arrays of numbers stay accepted. The write API's `hello` is built from `peer.meta`.

| Method | Params (besides the write API fields) | Result | Idempotency key `ik` (convention) |
|---|---|---|---|
| `pitwall.write.catalog` v1 | none | `{ok, type, classes, cars, tracks, layouts}` (counts) | `<id>/catalog/<sha256-12 of params>` |
| `pitwall.write.session_start` v1 | `session_key: str` (16 hex digits, random per session, chosen by the connector) | `{ok, type, session_id}` | `<id>/<session_key>/start` |
| `pitwall.write.setup` v1 | `session_key`, `seq: int` (1.. per session) | `{ok, type, session_id, setup, stint, stored}` | `<id>/<session_key>/setup/<seq>` |
| `pitwall.write.lap` v1 | `session_key`, `seq: int` (the connector's lap sequence in the session, 1..) | `{ok, type, session_id, lap, lap_id, stint}` | `<id>/<session_key>/lap/<seq>` |
| `pitwall.write.participant_laps` v1 | `session_key`, `seq: int` | `{ok, type, session_id, rows, stored}` | `<id>/<session_key>/participants/<seq>` |
| `pitwall.write.pit_exit` v1 | `session_key`, `seq: int` (1.. per session) | `{ok, type, session_id, stint, new_stint}` | `<id>/<session_key>/pit_exit/<seq>` |
| `pitwall.write.session_end` v1 | `session_key` | `{ok, type, session_id, stored, laps, discarded}` | `<id>/<session_key>/end` |

- Flags: all `ordered: true`, `idempotent: true`. Deadline 30 s. A lap (about 1.2 MB as `bin`)
  is chunked by the library.
- **Idempotency:** the `ik` travels in the call. Pitwall stores each key **in the same database
  transaction** as the write and remembers it as long as the session exists (catalog keys:
  forever). A repeat gets the first answer. The forms above are the reference package's
  convention; what matters is that a write retried after a lost answer carries the same key and
  every other write a different one. `writer_busy`, `busy`, `timeout`, `shutting_down` do not
  consume the key.
- **Sessions by key, not by connection.** A session stays open across reconnections: writes
  carrying its `session_key` continue it. A `session_start` with a new key closes the
  connector's open session as the write API closes a session when its connection drops (stored
  if it has a lap, discarded if not). A later write to the closed key: `session_closed`. A key
  never started: `unknown_session`.
- Refused writes: `bad_request` with the writer's error in `msg` and its reply in
  `details.reply` (non-retryable: the connector moves the item out of its outbox, 5.4).
- After each stored lap Pitwall calls `connector.live_frame` (deadline 0.3 s) for the lap
  event's `position`, then publishes `pitwall.laps.completed`.

### 4.3 Captures (the connector's part)

Pitwall grabs and stores each image itself when the user presses the capture button or key or
the engineer asks; a connector never sends an image. Before each grab Pitwall asks the
connector which window with `connector.capture_target` (optional, 5.3). A capture belongs to the
stint that starts within 15 minutes after it, else to the session recorded when it was taken.

### 4.4 Connector messages and announcements

One mechanism for both: **the connector declares a catalog and sends codes with params;
Pitwall composes the text in the user's language and publishes it.** No free text crosses
from a connector (a connector that wants one declares a code with a `text` param).

| Method | Params | Result | Flags, deadline |
|---|---|---|---|
| `pitwall.catalog.declare` v1 | `{catalog: map}`: a catalog file in the [catalog format](catalogs.md#2-catalog-file), `catalog` = `connector.<id>`, `language` = the connector's, messages carrying `params`, `values`, and per message: `audience: "driver"\|"agent"`, `priority: "spotter"\|"critical"\|"high"\|"normal"\|"low"` (default `normal`; `spotter` only with `audience: "driver"` and `max_wait_s` <= 3, else `bad_priority` / `bad_max_wait`; `audience: "agent"` allows `low\|normal\|high`), `max_wait_s: 0.5..600` (OPTIONAL, number: seconds after the event within which the message must start, absent = waits until it can be said), `slot` (OPTIONAL, `^[a-z][a-z0-9_]{0,31}$`: a newer message of the same slot replaces a queued older one), `kind: "message"\|"announcement"` (OPTIONAL, informative), `group` (OPTIONAL): one of `laps`, `fuel`, `setup`, `session`, `flags`, `safety_car`, `penalties`, `race`, `rivals`, `car`, `spotter`, `test`, `other`; missing or any other value is stored as `other` (logged, not refused), `variants` (OPTIONAL): 1..8 further wordings of `text` (variants 1..n), each checked as the message with that text; a bad list refuses the entry (`bad_variants`, `variant <n>: <reason>`). An entry that carries `ttl_s` is refused (`bad_field: ttl_s`, logged) | `{catalog, codes: int, new: [str], changed: [str], removed: [str], translation: "current"\|"pending"\|"not_needed"\|"unavailable"}` | `idempotent` (`ik` `<id>/textcatalog/<sha12 of the catalog>`), 10 s |
| `pitwall.announce` v1 | `{code: str, params: map, variant: int?, data: map?}` (`data` at most 2 KB: facts for the engineer; `variant` 0..n picks the wording, missing or null is 0: a non-integer is `bad_request` with `data: {param: "variant", reason: "bad_variant"}`, one beyond the declared ones `bad_request` with `data: {param: "variant", reason: "unknown_variant", variants: int}`; both are checked before the rate budget and never count) | `{message_id: str, at: str}` | not idempotent (live only), 5 s |
| `pitwall.guidance.declare` v1 | `{items: [{id, topic, when, text}]}`, 0..16 items; `id` `^[a-z][a-z0-9_]{0,31}$`, unique; `topic` one of `setup`, `capture`, `telemetry`, `session`, `car`, `track`, `tyres`, `fuel`, `strategy`, `controls`, `other`; `when` 1..120 characters, one line, completing "Read it when ..."; `text` 1..1500 characters (Markdown allowed); `when` + `text` of all items at most 8,000 characters | `{items: int, refused: {id: reason}, changed: bool}` | `idempotent` (`ik` `<id>/guidance/<sha12 of the items>`), 5 s; a peer that is not a connector, `items` not a list, more than 16 items or over 8,000 characters: `bad_request` |

- **Guidance** (optional): rules about the connector's simulator that the agent cannot infer
  (how setups are read, what a capture shows). English only (it is for the agent: not
  translated, nothing checks the language). A bad item is dropped and named in `refused` (and
  logged); an unknown `topic` is a bad item. **Last declaration wins** per connector
  (`items: []` clears it). Pitwall stores it (it survives a restart) with the connector's
  `name` and `sim_name`. Declare at every connection, before or after the catalog. Writing
  guide: [connector-protocol.md section 5.1](connector-protocol.md#51-guidance-for-the-engineer).
- **Declare** at every connection, before any `pitwall.announce` (Pitwall refuses codes of an
  undeclared catalog with `unknown_code`). Pitwall validates the catalog ([catalogs.md section
  9](catalogs.md#9-validation-on-load); a refused entry is dropped and listed in the log),
  stores it privately, and compares each message's source hash with the stored copy and with
  the translation of the user's language. New or changed codes with a user language other than
  the catalog's start a translation (section 6): `translation: "pending"`; until it lands,
  those codes are composed from the connector's own texts (the catalog's language is the last
  fallback, [catalogs.md section 6](catalogs.md#6-languages-and-fallback)). **Catalog
  versioning** is per message by the source hash; no version number is needed.
- **Announce** validation: the code exists, `params` match the declared names and kinds
  (`bad_request` naming the param otherwise), rate at most **10 per connector per 60 s**, and
  **120 per 60 s for `spotter` messages** (`rate_limited` with `retry_ms`; refused ones do not
  count). Pitwall then composes `text` (written) and, for `audience: "driver"`, `say` (spoken,
  numbers in words, [catalogs.md section 7](catalogs.md#7-number-catalog-numbersjson)), stores
  the message (history, kept 30 days) and publishes:
  - `pitwall.connector_messages` (every message except `priority: "spotter"`), and
  - `pitwall.announcements` (driver messages only: what the radio speaks).
  - `spotter`-level messages are published on `pitwall.announcements` only: they are not stored
    in the history and not published on `pitwall.connector_messages` (hundreds per race would
    bury the engineer's history). A spotter calls on state changes only, gives its calls one
    `slot`, and uses no params so the radio can pre-synthesise them.
- Nothing is queued by a connector while Pitwall is down: an announcement is worth something
  only now.

### 4.5 Channels a connector may subscribe to

State channels give the current value on (re)subscribe; event channels are **live only**: a
subscriber that was down misses them. Event channels of one connection keep send order, so a
subscriber of both sees `pitwall.sessions.started` before that session's
`pitwall.laps.completed`.

| Channel | Kind | Payload (`d`) |
|---|---|---|
| `pitwall.connectors` v1 | state | `{core: {pid, version, run, started_at, workspace, data_dir}, connector: {id, name, version, sim, sim_name, connected_at, functions: [str], services: [{service, pid, connected_at, status: <connector.status>}], recording: <pitwall.recording>}?, refused: [{app, sim?, at}] (last 5)}`: what a connector UI shows as "Pitwall: connected" |
| `pitwall.recording` v1 | state | `nil`, or `{connector_id, session_key, session_id?, session_type, car_id, car, track_id, track, layout_id, layout, track_length_m?, started_at, stint, lap}` (`lap`: the lap being driven; `session_id` after `session_start`) |
| `pitwall.sessions.started` v1 | event | the session's fields + `session_key`, `connector_id`, `at` |
| `pitwall.sessions.ended` v1 | event | `{session_id?, session_key, laps, discarded, at}` |
| `pitwall.stints.started` v1 | event | `{session_id, session_key, stint, fuel_l?, at}` |
| `pitwall.laps.completed` v1 | event | the lap event ([events.md](events.md#pitwalllapscompleted)) + `session_key`, `connector_id`, `at` |
| `pitwall.captures.stored` v1 | event | `{name, session_id?, stint?, applies_to?, source: "button"\|"agent"\|"test", label?, at}` |
| `pitwall.connector_messages` v1 | event | `{message_id, connector_id, connector_name, sim, sim_name, code, params: map, audience, priority: "spotter"\|"critical"\|"high"\|"normal"\|"low", max_wait_s?: number, slot?: str, text: str (written, user's language), say: str? (spoken, driver only), variant: int (0 when none), language: str (of text/say), data: map?, session_id?, at}` |
| `pitwall.announcements` v1 | event | `{id: str, source: "connector"\|"pitwall", connector_id: str?, connector_name: str? (when source is "connector"), code: str, say: str (final sentence, user's language, numbers in words), text: str (written form), variant: int (0 when none), language: str, priority: "spotter"\|"critical"\|"high"\|"normal"\|"low", max_wait_s?: number, slot?: str, at}` (`max_wait_s` and `slot` only when the catalog sets them). The radio speaks it: ordered by level (`spotter` > `critical` > `high` > `normal` > `low`), then first come first served; only `spotter` cuts what is playing; a queued message is replaced by a newer one of its `slot`; dropped when not started within `max_wait_s`; without `max_wait_s` it waits until it can be said. |
| `pitwall.catalogs` v1 | state | `{rev: int, catalogs: [{catalog, connector_id, connected: bool, codes: int, declared_at}]}`; `rev` +1 on a declare that changes a code or its fields, a translation landing, a connector connecting or disconnecting |

### 4.6 Connector actions

Commands only the connected simulator can carry out (tests, and other things only that game
has), sent by the engineer's agent. The connector declares them with their data and answer
formats; the engineer's tool server offers them to the agent. The writing guide for connector
authors is [connector-protocol.md section 5.2](connector-protocol.md#52-actions-the-engineer-sends-you).

| Method | Params | Result | Flags, deadline | Errors |
|---|---|---|---|---|
| `pitwall.actions.declare` v1 (connector) | `{actions: [action]}`, 0..32 actions | `{actions: int, refused: {code: reason}, changed: bool}` | `idempotent` (`ik` `<id>/actions/<sha12 of the actions>`), no cache (last wins), 5 s | `bad_request`: not a connector's connection, `actions` not a list, more than 32 actions or over 128 KiB |
| `pitwall.actions.send` v1 (the engineer's tool server only) | `{code: str, data: map, driver_confirmed?: bool}` | `{code, connector_id, call_id, answer: map, answer_errors?: [{path, problem}], elapsed_ms}` | not idempotent (an action may have effects: never retried); the action's `timeout_s` + 2 s | below |

An **action** is `{code, description, params, result, requires_confirmation?, example?,
timeout_s?}`:

- `code`: `^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){0,2}$`, at most 64 characters, unique.
- `description`: 1..600 characters, English, for the agent.
- `params`, `result`: the format of the data and of the answer, a **subset of JSON Schema
  2020-12**, root `type: "object"`. Every node has `type` and a `description` (1..300 characters);
  `object` has `properties` and `additionalProperties: false` (or absent); `string` has
  `maxLength` (1..65,536) or an `enum` (1..64 strings of at most 64 characters); `number` has
  `unit` (at most 16 characters), `minimum` and `maximum`; `integer` has `minimum` and
  `maximum` (`unit`, `enum` optional); `array` has `items` and `maxItems` (1..1,000); optional
  `required`, `default`, `examples` (at most 3), `minLength`, `minItems`, `exclusiveMinimum`,
  `exclusiveMaximum`. Any other keyword (`$ref`, `anyOf`, `pattern`, `format`, `const`, ...)
  refuses the action. Property names `^[a-z][a-z0-9_]{0,63}$`; at most 64 properties per
  object, 256 nodes, 4 levels below the root. The full table is in the protocol's
  [section 5.2](connector-protocol.md#52-actions-the-engineer-sends-you).
- `requires_confirmation` (default `false`): the engineer needs the user's clear yes on the
  radio first; `pitwall.actions.send` refuses it without `driver_confirmed: true`.
- `example`: one `params` instance that must validate; `timeout_s`: 1..60, default 10.
- Limits: the declaration at most 128 KiB, each action at most 16 KiB. A bad action is dropped
  and named in `refused`; the others are kept. Last declaration wins per connector (`[]`
  clears it). Pitwall stores them (they survive a restart) with the connector's `name`,
  `sim_name`, `version` and `declared_at`.

`pitwall.actions.send` steps, each with its error (`details` where noted): the caller is the
engineer's tool server (`engineer_only`); a connector is connected (`connector_disconnected`);
it declared `code` (`unknown_action`, `details` `{connector, codes}`); `data` at most 64 KiB
(`too_large`) and valid against `params` (`invalid_data`, `details.errors` `[{path, problem}]`,
at most 20, JSON Pointer paths); the action has `requires_confirmation` and `driver_confirmed`
is not `true` (`confirmation_required`); at most 20 sends per 60 s (`rate_limited`,
`retry_ms`); then `connector.action` is relayed (`connector_disconnected`,
`not_implemented_by_connector`, `timeout`, or the connector's own error code unchanged). An
answer over 256 KiB is `too_large`; otherwise it is checked against `result` and any mismatch
is delivered with `answer_errors` (and logged). Every relayed call is kept 30 days (code,
sizes, outcome, `driver_confirmed`; data and answer cut at 4 KiB).

## 5. What a connector process declares

### 5.1 On its `pitwall` connection

| Name | Kind | Declared by | Called / subscribed by Pitwall |
|---|---|---|---|
| `connector.status` v1 | state | every service | at once, on every connection that declares it |
| `connector.live_frame` v1 | method | the recorder | on tool requests and after every stored lap |
| `connector.capture_target` v1 | method (optional) | the recorder | before each capture (4.3) |
| `connector.action` v1 | method (optional) | the recorder | when the engineer sends an action the connector declared (4.6); deadline: the action's `timeout_s` |
| `connector.telemetry` v1 | stream | the recorder | while brake cues can fire |

A third-party connector needs only `connector.status` and the write methods; each other name
is optional and its absence gives `not_implemented_by_connector` to the tool that needs it.

### 5.2 `connector.status` v1 (state)

Set on every change; no periodic resend (the connection is the liveness).

| Field | Type | Meaning |
|---|---|---|
| `game_running` | bool | The simulator's process runs (checked on the process, never only the data channel). |
| `phase` | str? | `menus`, `garage`, `on_track`, `paused`; nil when the game is not running. |
| `session_key` | str? | The session this service writes (recorder), else nil. |
| `stint`, `lap` | int? | What it writes now (Pitwall's numbers as last answered). |
| `live` | map? | The car and layout on track now: `{session_type: "practice"\|"test"\|"qualifying"\|"warmup"\|"race"\|"other", car_id, track_id, layout_id, track_length_m: float?}`; nil outside a session. |
| `outbox` | map? | Recorder: `{pending: int, oldest_at: str?, rejected: int}` (5.4). |
| `error_code`, `message` | str? | The service's own problem (`unsupported_sim_version`, `no_data`, `not_configured`...), `message` a short English diagnostic for logs and the agent: no app shows it as it is. |

### 5.3 `connector.live_frame` and `connector.capture_target`

- `connector.live_frame` v1: params `{}`, result = the live frame
  ([connector-protocol.md section 7](connector-protocol.md#7-the-live-frame-connectorlive_frame)).
  Errors `game_not_running`, `busy` (no consistent read; Pitwall retries once), `internal`.
  Pitwall's deadline: 1 s for tools, 0.3 s after a lap.
- `connector.capture_target` v1 (optional): params `{}`, result `{hwnd: int, pid: int, title?: str,
  crop?: {x, y, w, h}}`: the simulator's top-level window, which Pitwall grabs itself
  ([connector-protocol.md section 4](connector-protocol.md#4-captures-pitwall-grabs-the-connector-names-the-window)).
  Deadline 1 s, asked before every capture; it only names the window. Errors:
  `game_not_running`, `window_minimized` (both refuse the capture), `not_capturable` (Pitwall
  grabs the foreground window).

### 5.4 Laps while Pitwall is down: the outbox

Every write (4.2) goes through the connector's **outbox**, a folder private to the connector:

1. Before sending, the write is stored as one file `<20-digit sequence>.msgpack` holding
   `{method, v, ik, p}` (atomic: temporary file, then rename). One file write per lap and per
   session message: the file is written by the background writer, never by the sampling loop.
2. Sent in sequence order, **one at a time** (ordered methods; each waits for its answer).
3. `ok`: the file is deleted. One of the six non-retryable codes (`bad_request`,
   `session_closed`, `unknown_session`, `too_large`, `unknown_method`, `version_unsupported`):
   moved to `outbox\rejected\` and logged. **Anything else** keeps the file and retries with the
   same key after `retry_ms`, 1 s by default and at most 10 s. For example `connector_busy`,
   `unavailable`, `busy`, `writer_busy`, `timeout`, `shutting_down` and a lost connection.
4. While Pitwall is down the recorder keeps recording into the outbox; after the reconnect it
   replays oldest first, before any new write. Pitwall's idempotency makes a write sent twice
   (answer lost) stored once.
5. `connector.status.outbox` shows the backlog; the control app can show "N laps waiting for
   Pitwall".

### 5.5 `connector.telemetry` v1 (stream): neutral data for brake cues

Optional. Declared by the recorder; `rate_hz: 100` (declared; default). Layout `telemetry`, 32
bytes, little-endian, no padding, aligned:

| Offset | Field | Type | Unit, meaning |
|---|---|---|---|
| 0 | `lap_distance_m` | f32 | Distance along the lap from the line, m (the viewed car); -1 unknown. |
| 4 | `speed_ms` | f32 | Car speed, m/s. |
| 8 | `accel_long_ms2` | f32 | Longitudinal acceleration in the car's frame, m/s², positive forward. |
| 12 | `track_length_m` | f32 | Layout length, m; 0 unknown. |
| 16 | `lap_time_s` | f32 | Current lap clock, s; -1 when not running (before a start, in the garage). |
| 20 | `frame` | u32 | The connector's counter of distinct game frames (increments when the simulator produced new data). |
| 24 | `sim_lap` | u16 | The simulator's lap counter; 0 unknown. |
| 26 | `session_type` | u8 | 0 unknown, 1 practice, 2 test, 3 qualifying, 4 warmup, 5 race, 6 other. |
| 27 | `game_state` | u8 | 0 not running, 1 menus, 2 loading, 3 playing, 4 paused, 5 replay. |
| 28 | `flags` | u16 | Bits below. |
| 30 | `reserved` | u16 | 0. |

`flags`: bit 0 `on_track` (on the circuit, not in the pit lane or garage); bit 1 `in_pit_lane`;
bit 2 `in_garage`; bit 3 `timed` (the lap clock runs); bit 4 `flying` (the connector's view:
a timed lap begun on track, not an out lap); bit 5 `lap_invalid` (the simulator invalidated
the lap); bit 6 `player_view` (the viewed car is the player's, not spectating or a replay);
bit 7 `fresh` (this sample carries a new game frame; 0 = a keep-alive repeat). Bits 8-15: 0.

Sampling rules:

- **`t_ns` is taken when the connector first sees the frame**, never at send time.
- A sample is sent **only when a new game frame is seen**, at most `rate_hz` per second; while
  no new frame comes (menus, pause) one repeat with `fresh` = 0 every 250 ms keeps `game_state`
  current.
- Produced **only while subscribed**: the connector polls for it only then. Never blocks: the
  ring drops the oldest.
- Subscription option `o: {rate_hz: int}` (10-200) MAY lower the cap; the connector may answer
  `bad_request` outside its range.

### 5.6 The connector's own endpoint (`connector-<id>`)

Serves the control interface (section 3) for the managing app. Each service may also open the
control app's endpoint when it runs and declare a state channel `service.status` v1 (the
`connector.status` payload plus `{service, pid, started_at}`), which the control app subscribes
to. Services still start without the control app: they just retry the connection.

**Connector profile (what an outside connector declares).** The connector is responsible for
everything it needs to run and to meet this table; Stintmate installs and repairs nothing for
it. The managing app reads the [descriptor](descriptor.md), runs its absolute `launcher`, and
expects to reach the endpoint; if it cannot, the connector does not work (shown as such).

| Name | Where | Status | Notes |
|---|---|---|---|
| `%LOCALAPPDATA%\Stintmate\connectors\<id>.json` | file | required | In place and correct; how it gets there is the connector's choice (installer, shipped file, a registration command; no command is required of any connector). |
| endpoint `connector-<id>` serving section 3 | managing app | required | Held while the control app runs; the descriptor's `mutex` is the liveness check. |
| `control.state` v1 (state, 3.2) | channel | required | The common fields and `connector: {id, name, version, sim, sim_name}`, `game_running`, `pitwall`, `outbox`, `start_with_app`. |
| `control.quit`, `control.managed`, `control.service.start`, `control.service.stop` | methods | required | 3.1; the services are those the descriptor lists. |
| `control.show`, `control.hide`, `control.autostart.set` | methods | as the connector has a window or a start entry | 3.1. |
| `control.notices`, `control.activity` (+ `control.activity.act`) | channels / method | optional | 3.2: what the connector wants the user to see. |
| `services[].delegable/default/always_on/refusal`, `control.services.desired` | state fields / event | optional | 3.2; [the protocol's section 1.1](connector-protocol.md#11-letting-stintmate-remember-your-services-optional). Without them the services are shown but no state is kept for them. |
| `control.catalog.get` v1 | method | optional | A connector with window texts serves its English catalog under the name `<id>-connector` ([catalogs.md section 1](catalogs.md#1-files-and-places)); without it the window stays in its own language. |
| `control.buttons.*` | methods | optional | 3.3; `unknown_method` means "no buttons". |
| `pitwall` side: the hello, `connector.status`, the write methods | sections 5.1 to 5.4 | required | [connector-protocol.md](connector-protocol.md). |
| `connector.live_frame`, `connector.capture_target`, `connector.telemetry` | 5.3, 5.5 | optional | Each enables a Pitwall function (live tools, captures, brake cues). |
| `pitwall.catalog.declare` + `pitwall.announce` | 4.4 | optional | The announcement catalog, in the connector's own language (English usually). **The connector translates nothing**: the managing app translates every catalog, these included (section 6). |
| `pitwall.guidance.declare` | 4.4 | optional | Rules for the agent about your simulator (English): what it must know before reading a setup, a capture, ... |
| `pitwall.actions.declare` + `connector.action` | 4.6 | optional | Commands only your simulator can carry out, sent by the engineer's agent: each with its data and answer format (a JSON Schema subset). |

The user's language (`language` in the managing app's `hub\settings.json` in the workspace) is
read by a connector only for its own windows.

## 6. A connector's announcement catalog and its translation

1. The connector connects and calls `pitwall.catalog.declare` (its catalog `connector.<id>`).
2. Pitwall stores it, bumps `rev` of the state `pitwall.catalogs` and answers
   `translation: "pending"` when the user's language is not the catalog's and codes have no
   current translation. Pitwall starts no translation of the catalog.
3. **The managing app translates it (one translation path):** it watches the state
   `pitwall.catalogs` (a new `rev`), reads `pitwall.catalogs.get` (per message the `source`
   object: `text`, `params`, `values`, `plural`, `note`, `audience`, `source_hash`; per catalog
   `source_language`; a message's variants are translated as the keys `<code>.v<n>`), diffs
   with the language pack `connector.<id>.json` by source hash, translates the missing texts,
   writes the pack, then calls **`pitwall.catalogs.reload {catalog: "connector.<id>"}`**
   (result `{catalog, language, translation}`; `not_found` for an unknown catalog). Pitwall
   reads the pack again and validates it (a bad entry falls back to the connector's text),
   marks the catalog current and bumps `rev`. The managing app writes every catalog pack,
   including `connector.<id>.json`; Pitwall writes none.
4. At run time: `pitwall.announce {code: "flag_yellow", params: {sector: 2}}`: Pitwall composes
   `say` = "Bandera amarilla, sector dos." for a Spanish-speaking user, publishes it on
   `pitwall.announcements` and the radio speaks it.

## 7. The hub's endpoint, `hub` (connector setup)

A connector's setup program uses two methods of the managing app to stop the connector while it
replaces its files ([connector-protocol.md section 1.2](connector-protocol.md#12-updating-your-connector-while-stintmate-runs)).
Both are ordered. An answer `busy` carries `details.state`: `starting` or `exiting` (the managing app is not
ready or is closing) or `busy` (it did not answer within 2 s).

| Method | Params | Result | Errors |
|---|---|---|---|
| `hub.connector.hold` v1 | `{id, purpose?: "update" \| "uninstall" (default "update"), ttl_s?: int 30..1800 (default 300)}` | `{id, state: "held" \| "stopping", restart: bool, expires_in_s}` | `bad_request`; `not_found` (no such connector); `busy` with `details.recording` true (a driving session is being recorded) |
| `hub.connector.release` v1 | `{id, start?: bool (default true)}` | `{id, released: bool, starting: bool}` | `bad_request` |

A hold stops the connector and keeps it stopped until a release, the rewrite of its descriptor,
the deadline, or the user's Start or Stop. Repeating a hold moves the deadline to the later one.
Releasing starts the connector again with the descriptor's current launcher if it was wanted
before; repeating a release is safe.
