# Changelog

## 0.4.0 (2026-11-07)

- **The descriptor has a new optional key, `icon`**: the absolute path of a square PNG icon (32 to 1024 pixels a side, at most 512 KiB, 256 x 256 recommended) that your setup installs. The managing app draws it wherever it shows your connector, and falls back to another icon or your connector's initials when there is none or it is not valid. A bad icon never breaks the descriptor. Paths must start with a drive letter: relative, UNC and device paths are refused. `descriptor.md` has the rules (a new "The icon" section and a table row) and `descriptor.schema.json` the property; `levels.md` section 7 lists it. `schema` stays 1.
- The checker has a new check, `F.icon`, at `--level features` only: `SKIP` when the descriptor has no `icon`, `PASS` or `FAIL` when it has. It is never a minimum requirement. The checker's version number follows the kit's (0.4.0).
- The skeleton ships a neutral `icon.png` (256 x 256); `install.ps1` installs it into the install folder and writes `icon` in the descriptor, and `-Uninstall` removes it with the folder.
- No protocol change: `contract_version` stays 3.

## 0.3.3 (2026-11-07)

- `descriptor.md`: the hub now reads `window`. It shows its "Open window" button for a connector only when the key is `true`; a connector with no window leaves the key out. No protocol change: `contract_version` stays 3.

## 0.3.2 (2026-11-05)

- The checker writes `{"schema": 1}` as `stintmate.json` into its temporary workspace. Without it, a connector that checks the workspace refused the folder, `M2.options` failed and every later check was skipped.
- The contract wording was made explicit: new `contract.md` section 8 states the workspace manifest (`stintmate.json`, integer `schema` of 1 or more) and the errors `workspace_uninitialized`, `manifest_invalid` and `manifest_unsupported`; the guide links to it. No protocol change: `contract_version` stays 3.
- The checker's version number follows the kit's (0.3.2).

## 0.3.1 (2026-11-04)

- The skeleton's tests no longer fail once the skeleton is copied out of the kit: the live-frame schema they validate against is shipped next to them (`skeleton/tests/live-frame.schema.json`), and a kit test keeps that copy equal to `docs/live-frame.schema.json`. No protocol or behaviour change.
- The checker's version number follows the kit's (0.3.1).

## 0.3.0 (2026-11-03)

- **The protocol changed (`contract_version` 3).** Every connector now has a product key, `product`: a UUID version 4, lowercase, with hyphens, that you generate once and never change.
  - The hello metadata has a new required field, `product`; a missing or malformed one is closed with `bad_request`. `connector-protocol.md` section 1 and `contract.md` section 4.1 say so.
  - The descriptor has a new required key, `product`, equal to the hello's. A descriptor without it is listed as broken. `descriptor.md` and `descriptor.schema.json` follow; `schema` stays 1.
  - New close code `connector_id_taken`: a connector id was first seen with another `product`, or one linked connection of the id already names another. the `bye` message names the id and the product it was first seen with; it is final until the user acts, so retry after 60 s, and report `control.state.pitwall` = `refused`. It is in `contract.md` section 3.2 and `contract.json` (`error_codes`).
  - The ownership rule: a setup or registration command never overwrites a descriptor that holds another `product`, and an uninstaller removes the file only when both `launcher` and `product` are its own.
- New minimum requirement M20 in `levels.md`: `product` in the hello and the descriptor, equal and stable.
- `contract.json` has `contract_version` 3, `hello_meta.product` with its pattern, and `connector_id_taken` under `error_codes`.
- New guide section, "Several connectors for one game" (`connectors.md` section 7, linked from the guide): several connectors can run for one game and each records its own sessions; the cost of your connector is yours to keep low; choosing a `sim` is choosing a data namespace, so share one only with identical ids; how to make and keep the product key. `data-model.md` says what Stintmate records about the connectors behind the catalog and the sessions.
- The skeleton connector has `PRODUCT` in its config (a fixed example UUID to replace once with `[guid]::NewGuid()`), sends it in the hello, writes it in the descriptor, shows `refused` after `connector_id_taken`, and its `install.ps1` refuses to overwrite a descriptor with another product (naming that connector) and removes the descriptor on `-Uninstall` only when `launcher` and `product` are its own.
- The checker's version number follows the kit's (0.3.0). Its fake Pitwall closes a hello without a valid `product` with `bad_request`, and three checks are new:
  - `M20.product`: `product` is valid and equal in the hello and the descriptor.
  - `M20.taken`: the fake Pitwall closes with `connector_id_taken`; the connector must show `refused` and not connect again within 60 s. It watches 65 s (`--taken-seconds`).
  - `M20.registration`: your registration command (`--register-command`) must refuse to overwrite a descriptor of another product; without the option the check is `MANUAL`.
- The skeleton waits at least 60 s before it connects again after `connector_id_taken`, whatever the IPC client's own backoff.

## 0.2.0 (2026-11-03)

- New [`docs/levels.md`](docs/levels.md): what a connector must do (minimum), what it may add (parity) and where it may extend the contract (beyond), what each analysis needs, and how the contract versions.
- The link rule: a connector connects when its recorder starts, whatever the game does, and stays connected. Pitwall listens to every connector; the one whose game runs is the active one. The rule is in `levels.md`, the protocol and the connector guide.
- The idle budget: with the game closed, at most 0.5 % of one core over 60 s for all the connector's processes, and the game's data channel is not read. `contract.json` has it under `budgets`.
- `STINTMATE_DEV_INSTANCE=<tag>` separates a connector under development from a real Stintmate on the same machine (pipe names and mutex name).
- Outbox: a write is rejected only for `bad_request`, `session_closed`, `unknown_session`, `too_large`, `unknown_method` or `version_unsupported`; any other error keeps the file and retries with the same key after `retry_ms` (1 s by default, at most 10 s). `contract.json` lists them under `write_errors`.
- `contract.md` defines `control.state.pitwall` and the service states, and says which of them the hub uses.
- New JSON Schemas: `docs/live-frame.schema.json` (with `docs/live-frame.sample.json`), `docs/control-state.schema.json` and `docs/descriptor.schema.json`. The live frame in a session requires `lap` and at least one participant with `is_player: true`.
- The descriptor has two optional keys, `window` and `features`; `schema` stays 1.
- `contract.json` has `contract_version` (2) and, under `vocabularies`, `control_pitwall`, `service_states`, `minimum_channels`, `position_channels` and `features`.
- Many connectors may be linked at once; `connector_busy` is sent only by a Pitwall older than contract 2 and is retried like any other retryable code. `pitwall.connectors` gains `connectors` and `active`.
- `contract.md` documents `hub.connector.hold` and `hub.connector.release` (also under `hub_methods` in `contract.json`); sections 4.7 and 4.8 became 4.5 and 4.6.
- The live frame needs at least one participant with `is_player: true`; the docs say which channels each analysis needs (`throttle`, `brake` and `g_lat` for lap analysis, comparison and corners).
- New in the guide: settings of a connector, the hub's launch timings, `--allow-synced` and licences. Protocol: the two odd cases of a closing game or a stopped connector, the conventional actions `test.ping` and `test.announce`, and write messages refusing unknown fields. `catalogs.md` lists suggested announcement codes.
- Fixed: the README, `contract.md` and `data-model.md` said the release zip does not carry `contract.json`; it does.
- Documentation tidied.
- The skeleton connector follows the minimum: it links to Pitwall when its recorder starts and checks the game every 2 s (`game.py`, with a fake game file), reads nothing with the game closed, writes through an outbox on disk (`outbox.py`), plays laps with the six required channels plus `steer`, `x` and `z` (`session.py`), shows `refused` in `control.state.pitwall`, runs below normal priority, caps its log, and honours `STINTMATE_DEV_INSTANCE`. While managed, only the managing process or its children can make its control app quit.
- The skeleton's `install.ps1` copies itself into the install folder and writes the descriptor's `uninstall`, `window` and `features`; `-Update` and `-Uninstall` hold the connector through the hub (`setup_hold.py`) and release it, `-Uninstall` removes the descriptor only if it names the install, and the environment is made with `uv venv --clear`. `fetch-ipc.ps1` computes the SHA-256 without `Get-FileHash`. The skeleton loads the user's language pack for its window texts, and `skeleton/examples/` shows the live frame, `connector.capture_target`, setups and options.
- New `checker/`: `connector-check` starts a connector from its descriptor, serves a fake Pitwall on pipes of its own (a temporary workspace, a random `STINTMATE_DEV_INSTANCE` tag) and reports every automatic check of the minimum level, one line each (`PASS`, `FAIL`, `SKIP`, `MANUAL`), with exit codes 0 to 3 and an optional JSON report. It works for a connector in any language and runs beside a real Stintmate. `--level features` also checks the declared names, announcements, guidance and actions. Its tests plant faults in a copy of the skeleton and require the matching check to fail.
- `levels.md` section 9 and the guide's checklist describe the checker; the release zip carries `checker/`, `CHANGELOG.md` and `SECURITY.md`, and the README lists them.
- Fixed in the skeleton: the outbox sender survived neither a call timeout nor an exception in a callback (the writes stayed on disk, but nothing was sent until a restart); it now sends again after any IPC error. `recording` in `control.state` ends with the session, status updates are locked, a failing game check is logged instead of ending the watcher, a stray file in `outbox/rejected/` no longer stops the start, and `GetLastError` is read through `WinDLL(use_last_error=True)`.
- `install.ps1`: `-Update` is the way to update while the connector runs; the update rebuilds the environment in place and is not atomic, so on a failed install the script says that the old build may be gone and exits non-zero; `-Uninstall` releases the hold even when removing the descriptor fails. `connector-protocol.md` section 1.2 says the same, and that a hold is refused with `busy` while `connector.status.phase` is `on_track`.
- `live-frame.schema.json`: `active` is required on a participant (Pitwall drops an inactive slot from rivals, gaps and neighbours), and `time_remaining_s` may be 0.
- Documentation: a launcher ignores every option it does not know; M10 asks for a correct `lap_type` and correct `valid`, `time_s`, `sim_lap` and `on_track` when they are sent; M18 is met by a connector in one language without `control.catalog.get`; `pitwall.connectors` lists `connectors`, `active` and `refused[].code`; `test.ping`'s result is `{pong}` plus `announced` and `message_id` when it plays the radio check; the connector that Pitwall follows is the active one, else the one followed before.
- The CI and release workflows run the schema tests (`jsonschema` is installed and imported plainly, so they cannot be skipped) and check all the JSON files; `scripts/check-docs.sh` now also catches a private user path and a personal name.
- `connector-check` no longer fails a correct connector that differs from the skeleton: a catalog sent once per connection, laps without `sim_lap`, `valid` or `on_track`, a timed `in` or `out` lap, a connector with no `control.catalog.get` (`M18.texts` is `MANUAL`) or no `uninstall` key (`M17.uninstall` is `MANUAL`), and a connector whose lap order differs. A lap with no braking is a note in `M12.sanity`, not a failure.
- `connector-check` checks more: `lap_type` against the pit exits, a process started for every game check (the CPU is the job object's own accounting), `retry_ms` and an unknown error code on a write, every refusal code, `refused` after `bad_request` and `busy`, `phase` `on_track` in `connector.status`, a connector that carries on when its managing process ends (`M5.parent`), and no service running before a managing process asks. It drops `STINTMATE_WORKSPACE` from the connector's environment, and a key may start with the connector id without a slash.

## 0.1.5 (2026-11-01)

- The zip carries `contract.json`, and a test ties the zip to the README's list.
- The skeleton's action test covers every type of the subset: `number`, `integer` and `array` too.
- `release-check.sh` says what to do when the top section of the changelog is not the tag.
- The skeleton and the native notes use `stintmate-ipc` 0.3.3.

## 0.1.4+docs.2 (2026-11-01)

- The repository is prepared for being public: the workflows run on GitHub-hosted Windows runners only (the `runner` input is gone), and `STINTMATE_IPC_READ_TOKEN` is gone from the workflows, from `skeleton/fetch-ipc.ps1` and from the docs, so the wheel is downloaded anonymously over HTTPS (`STINTMATE_IPC_RELEASE` stays). `SECURITY.md` says how to report a vulnerability, and the README has a "Contributing" section. The contract and the skeleton's behaviour are unchanged.

## 0.1.4+docs.1 (2026-11-01)

- Documentation corrected against the code; the code and the skeleton are unchanged (the kit's
  tests lost a personal word from a pattern).
  `contract.json` now lists, as the documents already did, the `control.buttons.*` methods and the error codes `managed`,
  `no_wheel`, `not_found`, `overloaded`, `shutting_down` and `unavailable`. The documents say where
  the program, the data and the logs of a connector live, what the release zip holds, what the
  engineer is, where Stintmate comes from, and what the skeleton's conformance test checks. No
  release is cut for this section.

## 0.1.4 (2026-11-01)

- The skeleton and the native notes, their release links included, use `stintmate-ipc` 0.3.2. The
  contract is unchanged.

## 0.1.3 (2026-11-01)

- The skeleton fetches the `stintmate-ipc` wheel from the `stintmate-ipc-python` release (the
  wheel and its `SHA256SUMS`); the C header is no longer downloaded. `STINTMATE_IPC_READ_TOKEN`
  reads the release while that repository is private. The native notes still point at the
  `stintmate-ipc` releases for the DLL, the header and the check tool.
- Release workflows (`ci`, `release`, run by hand): the checks, and a release with
  `connector-kit-<version>.zip` and `SHA256SUMS`. `scripts/check-docs.sh` is the documentation
  check.

## 0.1.2 (2026-10-31)

- The kit says first that a connector can be written in any language that loads a DLL; the
  skeleton is a Python example to read and copy.
- `native/README.md`: `stintmate_ipc.dll`, `stintmate_ipc.h` and `stintmate-ipc-check.exe` are
  downloaded directly from the release, and `stintmate-ipc-check` is how a connector in any
  language tests itself.
- The skeleton's README says `uv` is only for running its tests or developing on it, and gives its
  install command.
- The PowerShell scripts are run as `powershell -ExecutionPolicy Bypass -File .\<script>.ps1`: a
  default Windows refuses them typed directly.
- The connector protocol describes `test` as a live session type, not a stored one.

## 0.1.1 (2026-10-31)

- `contract.json` lists every method and event name the documents use.

## 0.1.0 (2026-10-31)

First release of the connector kit.

- The skeleton connector (`skeleton/`, Python, stintmate-ipc 0.3.1) with its conformance tests,
  the fetch script for the transport wheel and the notes on the native interface (`native/`).
- Documentation, each claim checked against the code of Pitwall and the managing app:
  the author's guide (`docs/guide.md`), the connector protocol, the connectors page, the
  channels Pitwall publishes, the data model, the write API with its JSON Schema, the
  connector side of the application contract, the connector descriptor and the catalogs format.
- `contract.json`: the connector-facing names, the error codes, the vocabularies and the limits
  the documents state, as values read from the code.
- The Apache-2.0 licence.
