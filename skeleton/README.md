# Skeleton connector

The smallest working [Stintmate connector](../docs/guide.md), for authors of new
ones (and their agents). It reads no simulator: a fake recorder plays one synthetic session
(garage exit, out lap, flying laps, one invalid, pit stop, a second stint) through the real
contract. Copy this folder, rename it, and replace `Recorder._play` with the reading of your
simulator.

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
| `fetch-ipc.ps1` | Downloads the `stintmate-ipc` wheel from the release of the `stintmate-ipc-python` repository into `vendor/` and checks its SHA-256 (`STINTMATE_IPC_RELEASE`: a base URL or a local folder instead; `STINTMATE_IPC_READ_TOKEN`: a GitHub token that can read the release, only while that repository is private); `install.ps1` runs it first | 1 |
| `install.ps1` | Creates the connector's own Python environment in its install folder and writes the descriptor `%LOCALAPPDATA%\Stintmate\connectors\skeleton.json` with the **absolute** launcher (`-Uninstall` removes both) | 1, 2 |
| `src/skeleton_connector/main.py` | The launcher: accepts `--workspace --managed --parent-pid --allow-synced`, holds the single-instance mutex, logs to `control.log` | 3, 4 |
| `src/skeleton_connector/control.py` | The endpoint `connector-skeleton`: `control.state` (one delegable service `recorder`), `control.quit`, `control.managed`, `control.service.start/stop`, `control.catalog.get` | 5 |
| `src/skeleton_connector/recorder.py` | The hello, `connector.status`, per-connection declarations (catalog, guidance, actions), `connector.action` (`test.ping`), the writes in order with idempotency keys, an announcement | 6 |
| `src/skeleton_connector/texts.py` | The announcement catalog, one guidance item, the sample action `test.ping` and the window catalog, all in English | 7 |
| `tests/test_conformance.py` | A fake Pitwall built from the documents' names, `stintmate-ipc-check server`, the descriptor | 8 |

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
   `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall` removes the file (only if its launcher is under the destination)
   and the folder. Stintmate installs nothing for you and provides no Python.
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
   (`<id>/<session_key>/lap/<seq>`) and is sent again, same key, until Pitwall answers. The
   connector never sends a `stint`: Pitwall numbers laps and decides stints.
6. **Words.** Everything you say is English and declared, never translated: the announcement
   catalog (`pitwall.catalog.declare`, then `pitwall.announce` with a code and params), the
   guidance (`pitwall.guidance.declare`) and, for a window, `control.catalog.get`. The hub
   translates every catalog. The skeleton has no pack loader, so the texts in `control.state`
   (`name`, `label`) stay English; a connector with a window reads the language from
   `<workspace>\hub\settings.json` and loads the pack `<workspace>\locales\<tag>\skeleton-connector.json`.
7. **Test.**
   ```
   powershell -ExecutionPolicy Bypass -File .\fetch-ipc.ps1    # once: the stintmate-ipc wheel into vendor/
   uv sync
   uv run pytest -q
   ```
   The tests run the recorder against a fake Pitwall, the endpoint as a hub uses it,
   `stintmate-ipc-check server` against the endpoint, and `install.ps1 -SkipInstall` (which
   writes only the descriptor). Before the first real session also try your recorder against
   a throw-away `pitwall-core` in a temporary workspace, never the user's.

## Try it by hand

With the Stintmate hub installed and a workspace on disk, run `install.ps1` as above and start
the hub: the connector is listed, and its recorder plays the session as soon as Pitwall runs.
Without the hub, start `skeleton-connector.exe --workspace <workspace>` yourself (needs
`<workspace>\ipc\key`), and ask it to stop with `control.quit`.

## Using this as your own connector

Rename the package, `CONNECTOR_ID` (equal to the folder name; `^[a-z0-9_-]+$`), the mutex, the
service ids and `sim`; keep `install.ps1` and the descriptor in step (the test fails when they
drift). Replace the script in `recorder.py` with the study of your simulator
([guide, section 10](../docs/guide.md#10-the-telemetry-study-and-the-findings-document)):
a real recorder keeps its sampling loop free and writes through an outbox on another thread
([protocol 3.3](../docs/connector-protocol.md)).
