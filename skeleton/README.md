# Skeleton connector

The smallest working [Stintmate connector](../docs/guide.md), for authors of new
ones (and their agents). It reads no simulator: a fake recorder plays one synthetic session
(garage exit, out lap, flying laps, one invalid, pit stop, a second stint) through the real
contract. Copy this folder, rename it, replace `game.py` (is the game running?) and `session.py`
(what a session of your simulator holds), and keep the rest.

It depends only on `stintmate-ipc`, the Python binding of the Rust implementation of Stintmate
IPC (`pyproject.toml`: a path source; it is the wheel `fetch-ipc.ps1` puts in `vendor/`, so nothing is
built). Connectors in other languages use the same implementation through its C ABI (`native/` of this repository),
or the protocol spec directly. It imports nothing from any Stintmate app, and a test
enforces it.

This is an example to read and copy; a connector in another language follows the same steps
with the same contract. You need [uv](https://docs.astral.sh/uv/) only to run its tests or to
develop on it (the project asks for Python `>=3.12`, `requires-python` in `pyproject.toml`; uv
fetches a matching Python). Install uv with:

```
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

It needs Windows; no git, no Rust.

A default Windows refuses `.\fetch-ipc.ps1` and `.\install.ps1`, so the commands below are run as
`powershell -ExecutionPolicy Bypass -File .\<script>.ps1`. That allows that one run and changes
no setting.

## What is in it

| File | What it shows | Guide section |
|---|---|---|
| `fetch-ipc.ps1` | Downloads the `stintmate-ipc` wheel from the release of the `stintmate-ipc-python` repository into `vendor/` and checks its SHA-256 (`STINTMATE_IPC_RELEASE`: a base URL or a local folder instead); `install.ps1` runs it first | 1 |
| `install.ps1` | Creates the connector's own Python environment in its install folder and writes the descriptor `%LOCALAPPDATA%\Stintmate\connectors\skeleton.json` with the **absolute** launcher (`-Update` holds the running connector first, `-Uninstall` removes both; see section 2) | 1, 2 |
| `src/skeleton_connector/setup_hold.py` | Keep. What `install.ps1` asks of the running hub: `hold`, `release` and, with no hub, `stop` (section 2) | 1 |
| `src/skeleton_connector/__init__.py` | The names, and `dev_tag()` and `pipe(app)`: with `STINTMATE_DEV_INSTANCE=<tag>` every pipe takes the tag, so a test run never meets a real Stintmate | 3 |
| `src/skeleton_connector/main.py` | The launcher: runs below normal priority, accepts `--workspace --managed --parent-pid --allow-synced`, holds the single-instance mutex (with the tag when there is one), logs to `control.log` (folder made on the first run, capped at about 1 MB and two older copies) | 3, 4 |
| `src/skeleton_connector/control.py` | The endpoint `connector-skeleton`: `control.state` (one delegable service `recorder`: `stopped`, `waiting`, `recording`), `control.quit` (while managed, only the managing process or its children may ask), `control.managed`, `control.service.start/stop`, `control.catalog.get` | 5 |
| `src/skeleton_connector/game.py` | **Replace first.** `Game.running()`: is the game running? Check its process, never its data channel. The fake: the file `<workspace>\connectors\skeleton\fake_game` exists | |
| `src/skeleton_connector/session.py` | **Replace next.** The catalog, the synthetic session and the nine channels of a lap, with their units and signs | |
| `src/skeleton_connector/outbox.py` | Keep. Every write goes to disk first and is sent in order with its key; retryable errors keep the file, the six refusals move it to `rejected/` | |
| `src/skeleton_connector/recorder.py` | Links to Pitwall when it starts, whatever the game does; checks the game every 2 s; the hello, `connector.status`, per-connection declarations (catalog, guidance, actions), `connector.action` (`test.ping`), the writes through the outbox, an announcement (never queued) | 6 |
| `src/skeleton_connector/texts.py` | The announcement catalog, one guidance item, the sample action `test.ping` and the window catalog, all in English, and the loader of the user's language pack for the window's texts | 7 |
| `examples/` | One small module for each optional piece: the live frame, `connector.capture_target`, setups, options. Importable, not imported by the skeleton; each starts with the lines to add | 6 |
| `tests/test_conformance.py`, `tests/test_outbox.py`, `tests/test_setup.py`, `tests/test_examples.py` | A fake Pitwall built from the documents' names (it can refuse, vanish and come back), the outbox, `stintmate-ipc-check server`, the descriptor, a fake hub for the hold, install, update and uninstall in a temporary folder, the language pack, the examples | 8 |

### The fake game

There is no simulator. Create the file `<workspace>\connectors\skeleton\fake_game` and the
recorder, which checks it every 2 s, sets `game_running` and plays one session; delete the file
and it closes the session. With no file the recorder stays linked to Pitwall, writes nothing and
reads nothing. Your `Game.running()` takes the file's place.

## Step by step

1. **The workspace.** Your connector's data and logs live in the user's Stintmate workspace
   (`<workspace>\connectors\<id>\`) and it reads the key `<workspace>\ipc\key`; it never creates
   it (the hub's onboarding does). `main.py` takes `--workspace`, else
   `stintmate_ipc.find_workspace()`; if there is none it logs and retries. The recorder's
   `Client` takes `KeyFile(path)` (create off): a missing key is reported (`Recorder.message`)
   and retried.
2. **Install.** `powershell -ExecutionPolicy Bypass -File .\install.ps1` (it runs `fetch-ipc.ps1` first; optionally `-Destination <folder>`; it needs no workspace) makes
   `<Destination>\.venv` and installs this project into it (a copy, so the folder you
   cloned can go away), then writes `%LOCALAPPDATA%\Stintmate\connectors\skeleton.json`
   atomically: the launcher is `<Destination>\.venv\Scripts\skeleton-connector.exe`, absolute,
   and `logs` name files in `<workspace>\connectors\skeleton\`. The contract asks only for the
   file to be in place and correct; an installer, a file you ship or a command are all fine.
   The script copies itself to `<Destination>\install.ps1` and the descriptor's `uninstall` key
   runs that copy through Windows PowerShell (`-Uninstall -Destination <folder>`), so Stintmate can
   remove the install. `.\install.ps1 -Update` and `-Uninstall` first **hold** the connector
   ([connector-protocol.md section 1.2](../docs/connector-protocol.md)): `setup_hold hold` asks
   the hub to quit it and keep it stopped, and `release` ends the hold; with no hub it asks the
   connector to quit (`setup_hold stop`). If a session is being recorded, the script exits with
   code 1 and changes nothing. `-Uninstall` removes the descriptor only if its launcher is under
   the destination, releases without starting, and then removes the folder; a descriptor removed
   while held leaves the connector "updating" until it is released. Run `install.ps1` from
   Windows PowerShell or PowerShell 7, with a path that has spaces or not. Stintmate installs
   nothing for you and provides no Python.
3. **Being started.** The hub runs the launcher with `--workspace <w> --managed --parent-pid
   <pid>`. `main.py` takes the mutex `Local\SkeletonConnector.Control` (the descriptor names it:
   the hub's liveness check), serves the endpoint and waits. `control.quit` answers at once and
   the process ends a moment later. With `--managed`, `ControlApp` leaves the delegable service
   to the hub (it sends `control.service.start/stop` after the endpoint answers) and watches the parent
   process; alone, it starts the recorder itself.
4. **The control endpoint.** `ControlApp` serves the required profile (docs/contract.md
   5.6). `control.state` carries the common fields plus `connector`, `game_running`, `pitwall`,
   `outbox` and `start_with_app`, set only when it changes. The service `recorder` is marked
   `delegable` with `default: "on"` (protocol 1.1), so the hub offers a switch and remembers
   it. The skeleton has no window, so it serves no `control.show/hide/autostart.set`, no
   notices and no `control.services.desired` (publish that event only when your own window or
   tray switches a delegable service while you are managed).
5. **Pitwall.** `Recorder` connects to `pitwall` as `connector-skeleton` / process `recorder`
   with `meta` `{connector_id, connector_name, sim, sim_name, service}`, declares
   `connector.status`, and after every connection declares its announcement catalog
   (`connector.skeleton`), guidance and its action `test.ping` (`pitwall.actions.declare`; the
   engineer finds it with `list_connector_actions` and sends it with `send_connector_action`,
   and `connector.action` answers `{pong}`). Then it writes the car/track catalog, `session_start`
   with a random 16-hex `session_key`, and the facts of protocol section 11: `pit_exit` for
   each exit, `lap` with `lap_type`, `valid`, `time_s` only when timed, `sim_lap` and
   `on_track`, then `session_end`. Every write has its idempotency key
   (`<id>/<session_key>/lap/<seq>`), is stored in `<workspace>\connectors\skeleton\outbox\`
   first and is sent from there in order, again with the same key, until Pitwall answers; the
   link to Pitwall opens when the recorder starts, not when the game does ([link rule](../docs/levels.md)). The
   connector never sends a `stint`: Pitwall numbers laps and decides stints.
6. **Words.** Everything you say is English and declared, never translated: the announcement
   catalog (`pitwall.catalog.declare`, then `pitwall.announce` with a code and params), the
   guidance (`pitwall.guidance.declare`) and, for a window, `control.catalog.get`. The hub
   translates every catalog. The skeleton's `texts.py` also reads the language once at start from
   `<workspace>\hub\settings.json` and loads the pack
   `<workspace>\locales\<tag>\skeleton-connector.json` for the texts in `control.state`
   (`name`, `label`), per key along the chain (`es-CL`, `es`, `en`), the built-in English last; a
   file that is missing or bad is logged and skipped.
7. **Test.**
   ```
   powershell -ExecutionPolicy Bypass -File .\fetch-ipc.ps1    # once: the stintmate-ipc wheel into vendor/
   uv sync
   uv run pytest -q
   ```
   The tests run under a random `STINTMATE_DEV_INSTANCE`, with temporary workspaces and pipe
   names of their own. They run the recorder against a fake Pitwall, the endpoint as a hub uses it,
   `stintmate-ipc-check server` against the endpoint, `install.ps1` in a temporary folder (install, `-Update` and the
   `uninstall` command, against a fake hub), and the examples. Before the first real session also try your recorder against
   a throw-away `pitwall-core` in a temporary workspace, never the user's.

## Try it by hand

This needs an installed Stintmate (https://stintmate.app) with its workspace and `ipc\key`.
With the Stintmate hub installed and a workspace on disk, run `install.ps1` as above and start
the hub: the connector is listed, and its recorder plays the session as soon as Pitwall runs.
Without the hub, start `skeleton-connector.exe --workspace <workspace>` yourself (needs
`<workspace>\ipc\key`), and ask it to stop with `control.quit`.

## Check it with `connector-check`

After `fetch-ipc.ps1`, from the kit's root (the skeleton is registered by `install.ps1`):

```
uv run --project checker connector-check --descriptor %LOCALAPPDATA%\Stintmate\connectors\skeleton.json --fake-game-file "{workspace}\connectors\skeleton\fake_game"
```

The checker plays the fake game by creating that file in its own temporary workspace, so the
skeleton plays one session and the checker reports every check of the minimum level
([checker/README.md](../checker/README.md)). Run it again against your own descriptor.

## Using this as your own connector

Rename the package, `CONNECTOR_ID` (equal to the folder name; `^[a-z0-9][a-z0-9_-]{0,31}$`), the mutex, the
service ids and `sim`; keep `install.ps1` and the descriptor in step (the test fails when they
drift). Replace `game.py` and the script in `session.py` with the study of your simulator
([guide, section 10](../docs/guide.md#10-the-telemetry-study-and-the-findings-document)):
a real recorder keeps its sampling loop free, hands each finished lap to the outbox from another
thread ([protocol 3.3](../docs/connector-protocol.md)) and reads nothing from the game while it
is closed.
