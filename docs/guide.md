# Connector author's guide

For developers (and their AI agents) who write a **connector**: the program that reads a
simulator and feeds Pitwall, and that the Stintmate hub lists, starts, adopts and shows. This
page is the map; it links the normative documents instead of copying them. Where a link and
this page differ, the link wins.

| You need | Read |
|---|---|
| The Pitwall side (hello, status, write methods, live functions) | [connector-protocol.md](connector-protocol.md) |
| The control endpoint and the connector profile | [contract.md sections 3 and 5.6](contract.md) |
| The descriptor `<id>.json` in the machine folder | [descriptor.md](descriptor.md) |
| Text catalogs and language | [catalogs.md](catalogs.md) |
| Laps, stints and the telemetry study | [connector-protocol.md section 11](connector-protocol.md#11-laps-and-stints-what-the-connector-sends), [section 10 here](#10-the-telemetry-study-and-the-findings-document) |
| Mapping a simulator onto Pitwall's model, and testing | [connectors.md](connectors.md) |
| A minimal connector to copy | [`skeleton/`](../skeleton/README.md) |
| The transport | [stintmate-ipc](https://github.com/Stintmate-App/stintmate-ipc) (its `SPEC.md`) and the Python package `stintmate-ipc` (the DLL, the C header and the check tool are assets of each stintmate-ipc release; the wheel is an asset of each [stintmate-ipc-python](https://github.com/Stintmate-App/stintmate-ipc-python) release, which the skeleton fetches with `fetch-ipc.ps1`, for the Python skeleton; a connector in any language loads `stintmate_ipc.dll` through the C ABI described in [`native/`](../native/README.md)) |

## Words used in these documents

- **Stintmate** is a Windows application the user downloads from https://stintmate.app. It
  installs the **hub** (the app that lists, starts and shows connectors) and **Pitwall** (the
  database and `pitwall-core`, which connectors write to), and creates the user's **workspace**
  (the user's folder, with `ipc\key` inside it). A connector cannot be tried end to end
  without it; see [the README](../README.md#where-stintmate-comes-from).
- **The engineer** is the user's AI agent. It reads and works with Pitwall through the tools of
  Pitwall's MCP server; a connector never talks to it directly. The tools a connector author
  meets are `connector_status` (what is connected), `get_connector_guidance` (the guidance the
  connector declared), `list_connector_actions` and `send_connector_action` (the actions it
  declared, and sending one) and `get_live_state` (the live frame).

## 1. What a connector is

One or more processes that know a simulator and nothing else Stintmate-specific:

- at least one **service** (for example a recorder) that reads the simulator and connects to
  `pitwall-core` as `connector-<id>`;
- usually a **control app** (tray icon, window) that serves the endpoint `connector-<id>` for
  the hub and supervises the services. A connector without a window still serves the endpoint.

The program lives in its own install folder (the folder of the `launcher` your descriptor names,
for example under `%LOCALAPPDATA%\Programs\`); `<workspace>\connectors\<id>\` is only its data
and logs folder. A connector is responsible for **everything it needs to run**: its executable,
or for a script connector its own Python environment inside its install folder.
Stintmate does not install a connector, does not repair it, and does not provide or choose a
Python (or any runtime) for it: the hub runs the `launcher` named in your descriptor and
expects to reach the connector's channels; if it cannot, the connector does not work. A
connector composes no user text in a language it does not have ([catalogs.md](catalogs.md#6-languages-and-fallback)), and reads no file
of another app except the language setting (`hub\settings.json`).

## 2. The folder and the descriptor

`%LOCALAPPDATA%\Stintmate\connectors\<id>.json` (schema 1) is the only thing the hub needs to
find you; write it when you install and remove it when you uninstall. Your data and logs stay in
`<workspace>\connectors\<id>\`. The field table and rules are in
[descriptor.md](descriptor.md); the points that trip authors:

- `id` equals the file name without `.json` (else the file is ignored); `launcher` is an
  **absolute** path (the hub shows the connector as not installed when the file is missing);
  `control_protocol` is `"stintmate-ipc/2"` (any other value is refused); `endpoint` is
  `connector-<id>`; `mutex` is a `Local\...` single-instance mutex your control app holds while
  it runs (the liveness check); `services` lists the ids `control.service.start/stop` accept.
  Whether the hub may keep a service on or off across restarts is declared in `control.state`,
  not here
  ([the protocol, section 1.1](connector-protocol.md#11-letting-stintmate-remember-your-services-optional)).
- The contract is the outcome only: the file must be in place and correct. How it gets there
  (an installer, a file you ship, a registration command) is yours. The skeleton's
  `install.ps1` is one way; no managing app calls any registration command of yours and none is
  required.
- Only you write the file, atomically; the hub never does and ignores unknown keys.

## 3. Being started and adopted

The hub runs `launcher` + `args` + the trailing options

```
--workspace <workspace> --managed --parent-pid <hub pid>
```

Your launcher must **tolerate these options** (parse them or ignore them; `--allow-synced`
is also appended after `--workspace` when the workspace folder is synced and that was allowed).
Behaviour expected:

1. Hold the descriptor's `mutex` for as long as the control app runs; a second launch must not
   start a second control app.
2. With `--managed`, watch `--parent-pid`; when it disappears, carry on standalone (the hub
   also sends `control.managed` to an instance that was already running: that is the
   **adoption**, and answers `{managed: true, parent_pid}`).
3. `control.quit` returns at once and the process exits (services stopped) **within 20 s**,
   after which the pipe is gone; the hub terminates the process named by `control.state.pid`
   otherwise.
4. The hub starts your process in a new process group, not in a job, so it is not killed when
   the hub closes. It starts `launcher` with the launcher's own folder as the working
   directory; find your files from your executable's path, not from the working directory.

## 4. The workspace and the read-only key

Find the workspace from `--workspace` first, else `STINTMATE_WORKSPACE`, else the `workspace`
field of `%APPDATA%\Stintmate\settings.json`
([SPEC 4.3](https://github.com/Stintmate-App/stintmate-ipc/blob/main/SPEC.md#43-the-workspace);
`stintmate_ipc.find_workspace()` does the last two). The key is `<workspace>\ipc\key`
(`stintmate_ipc.default_key_path()`). **A third-party connector only reads the key**: it never
creates or replaces it. While the workspace or key is missing, log it and retry; do not fail
for good.

## 5. The control profile

Your control endpoint `connector-<id>` serves the control interface
([contract.md section 3](contract.md)).
The authoritative table is the connector profile in
[section 5.6](contract.md); in short:

| Required | Optional |
|---|---|
| `control.state` (common fields + `connector`, `game_running`, `pitwall`, `outbox`, `start_with_app`), `control.quit`, `control.managed`, `control.service.start`, `control.service.stop` | `control.notices`, `control.activity` (+ `control.activity.act`), `control.catalog.get`, `control.buttons.*` |
| `control.show`, `control.hide`, `control.autostart.set` if you have a window or a start entry | |

A method you do not serve answers `unknown_method` (and a channel `unknown_channel`). The hub
treats each optional one as "not there": a connector that does not serve `control.catalog.get`
is skipped when the hub translates catalogs, and one that answers `unknown_method` to
`control.buttons.*` owns no button. The contract is that no notices or activity means nothing in
the notice bar, a missing `control.catalog.get` means the window stays in your language, and
nothing is shown as an error or retried. Anything required and missing makes the connector show
as not working.

## 6. Connecting to Pitwall

Your services connect to `pitwall` with the `stintmate-ipc` package (it reconnects with the
protocol backoff), say hello as `connector-<id>`, declare `connector.status` and write sessions
and laps. Optional live functions (`connector.live_frame`, `connector.capture_target`,
`connector.telemetry`) each enable a Pitwall function. Everything is in
[connector-protocol.md](connector-protocol.md); laps finished while Pitwall is down are yours
to keep (the outbox) or lose, logged.

## 7. Catalogs and language

One translation path: **the hub translates every catalog**; you declare only your own text in
your own language (English usually) and never translate.

- *Announcements* (optional): declare your announcement catalog with
  `pitwall.catalog.declare` and send codes with `pitwall.announce` (optional phrase variants)
  ([contract.md sections 4.4 and 6](contract.md)).
- *Guidance* (optional): declare rules for the race engineer about your simulator with `pitwall.guidance.declare` (English; topics, limits and what makes good guidance: [connector-protocol.md section 5.1](connector-protocol.md#51-guidance-for-the-engineer)). It is not translated.
- *Actions* (optional): declare the commands only your simulator can carry out, sent by the engineer's agent, with `pitwall.actions.declare`, and serve them with `connector.action` (data and answer formats in a JSON Schema subset, `requires_confirmation` for what the player must approve; writing guide and a full example: [connector-protocol.md section 5.2](connector-protocol.md#52-actions-the-engineer-sends-you)).
- *Window texts* (optional): serve `control.catalog.get` with your built-in catalog, named
  `<id>-connector` ([catalogs.md section 1](catalogs.md#1-files-and-places)); the hub
  translates it into a language pack your app loads from `<workspace>\locales\`.
- The user's language is `language` in `<workspace>\hub\settings.json` (languages and
  fallback: [catalogs.md section 6](catalogs.md#6-languages-and-fallback)); read it
  only to pick the pack for your own windows, and texts you put in `control.state` (service
  `name` and `label`) must already be in that language.

## 8. Conformance

`stintmate-ipc-check` (inside the `stintmate-ipc` wheel, under `stintmate_ipc/_bin/`, and
installed as the console command `stintmate-ipc-check`) tests an endpoint in any
language. The wheel and a `SHA256SUMS` file are the release assets of the
stintmate-ipc-python repository (https://github.com/Stintmate-App/stintmate-ipc-python,
`releases/download/v<version>/`); `skeleton/fetch-ipc.ps1` downloads them into
`vendor/` and verifies the wheel, and `STINTMATE_IPC_RELEASE` (a base URL or a local folder)
replaces the source. The C header and the standalone tool are assets of the
stintmate-ipc releases (see [`native/`](../native/README.md)):

```
stintmate-ipc-check server <pipe> --key-file <workspace>\ipc\key
```

Run it against your running control endpoint (`stintmate_ipc.pipe_path("connector-<id>")`);
`--call control.show={}` style options exercise methods that have side effects, and `--fast`
skips the checks that wait for timeouts (`stintmate-ipc-check --help` lists all options). The
skeleton's test does this against its own endpoint:
[`skeleton/tests/test_conformance.py`](../skeleton/tests/test_conformance.py). The AMS2
connector (Rust, its own repository
[`Stintmate-App/ams2-connector`](https://github.com/Stintmate-App/ams2-connector)) runs it in
its own tests: `bins/ams2-connector-ui/tests/endpoint.rs` in that repository. For the Pitwall
side, see [connectors.md section 6](connectors.md#6-testing): never test against the user's
data.

## 9. Checklist

- [ ] `%LOCALAPPDATA%\Stintmate\connectors\<id>.json` in place, `id` = file name, absolute `launcher`, schema 1.
- [ ] The launcher accepts `--workspace`, `--managed`, `--parent-pid` (and `--allow-synced`).
- [ ] It ships everything it needs (own runtime or Python environment in its folder).
- [ ] Single-instance `mutex` held while the control app runs.
- [ ] Endpoint `connector-<id>` serves the required profile; `stintmate-ipc-check server` passes.
- [ ] `control.quit` ends the process within 20 s; managed mode follows the parent pid.
- [ ] Reads the key, never writes it; retries while workspace, key or Pitwall are missing.
- [ ] Pitwall side per connector-protocol.md (hello, `connector.status`, writes).
- [ ] Optional: catalogs declared in your own language; the hub translates them.

## Becoming an official connector

The hub can also list your connector as an **official connector** and install it with one
click: the Stintmate team adds a setup program to its catalog. For that the setup
must honour the unattended contract: it accepts `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART
/PROGRESS="<file>" /LOG="<file>"`; it appends to the progress file JSON lines with the stages
(`prepare`, `replace`, `files` with `done` and `total` counts, `register`) and a last line with
stage `done` carrying `ok`, `reason` and `installed` (that line is the authority, the exit
status is the backup); it writes the descriptor ([descriptor.md](descriptor.md)) as its last step; and when it
replaces a running connector it asks the hub to hold it (protocol section 1.2) instead of
stopping it by itself. The AMS2 connector's installer is the worked example. Nothing else
changes for a connector that is installed by hand.

## The skeleton as the smallest example

[`skeleton/`](../skeleton/README.md) is a complete
connector with a fake simulator, using only the `stintmate-ipc` package (the release wheel of stintmate-ipc-python that `fetch-ipc.ps1` downloads; it needs only `uv`): its
`install.ps1` creates the connector's own Python environment in its folder and writes the
descriptor with the absolute launcher; it serves the required control profile with one delegable
service, declares an announcement catalog and a guidance item, writes a synthetic session with
laps and pit exits as protocol section 11 says, and its tests run `stintmate-ipc-check server`
against a fake Pitwall built from the documents. Start there, then read the real one below.

## The AMS2 connector as the worked example

| Concern | Where (in the [`ams2-connector`](https://github.com/Stintmate-App/ams2-connector) repository) |
|---|---|
| Control endpoint and profile | `bins/ams2-connector-ui/src/endpoint.rs`, `app.rs` |
| Connection to Pitwall, key | `bins/ams2-recorder/src/link.rs` |
| Descriptor and `--register` | `bins/ams2-connector-ui/src/descriptor.rs`, its `docs/ARCHITECTURE.md` |
| Conformance | `bins/ams2-connector-ui/tests/endpoint.rs` |

The skeleton is the minimal connector in Python; the AMS2 connector in Rust is the full reference.

## 10. The telemetry study and the FINDINGS document

Before the connector writes laps, record real sessions and keep what you learn in
`FINDINGS.md` next to the connector's docs, modelled on the
[AMS2 findings](https://github.com/Stintmate-App/ams2-connector/blob/main/docs/FINDINGS.md). The eight experiments to run and
how laps and stints are reported are in
[connector-protocol.md section 11](connector-protocol.md#11-laps-and-stints-what-the-connector-sends).
Template:

```markdown
# <Simulator> findings

## Access
How the data is read (shared memory, UDP, plugin), versions tested, update rate, units.

## Laps
| Experiment | Fields read | Values observed |
|---|---|---|
| Out lap from the garage | | |
| Line after the out lap | | |
| Flying lap, valid / invalidated | | |
| Pits and out again | | |
| Back to the pits by menu or reset | | |
| Session restart | | |
| Race start (formation, grid) | | |
| Session ends mid-lap | | |

Counter kind (started / completed / none): ...  HUD number versus counter: ...

## Validity
Flag name, when it is set, causes the simulator reports.

## Pits and garage
Flags and states (pit lane, box, garage), what marks a pit exit.

## Quirks
Anything that surprised you, with the date and the session.

## Validation
Recorded sessions compared with the HUD: lap numbers, times, stints.
```
