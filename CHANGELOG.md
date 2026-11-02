# Changelog

## 0.2.0 (unreleased)

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
- New in the guide: settings of a connector, the hub's launch timings, `--allow-synced`, what an official connector's setup reports, and licences. Protocol: the two odd cases of a closing game or a stopped connector, the conventional actions `test.ping` and `test.announce`, and write messages refusing unknown fields. `catalogs.md` lists suggested announcement codes.
- Fixed: the README, `contract.md` and `data-model.md` said the release zip does not carry `contract.json`; it does.
- The skeleton connector follows the minimum: it links to Pitwall when its recorder starts and checks the game every 2 s (`game.py`, with a fake game file), reads nothing with the game closed, writes through an outbox on disk (`outbox.py`), plays laps with the six required channels plus `steer`, `x` and `z` (`session.py`), shows `refused` in `control.state.pitwall`, runs below normal priority, caps its log, and honours `STINTMATE_DEV_INSTANCE`. While managed, only the managing process or its children can make its control app quit.
- The skeleton's `install.ps1` copies itself into the install folder and writes the descriptor's `uninstall`, `window` and `features`; `-Update` and `-Uninstall` hold the connector through the hub (`setup_hold.py`) and release it, `-Uninstall` removes the descriptor only if it names the install, and the environment is made with `uv venv --clear`. `fetch-ipc.ps1` computes the SHA-256 without `Get-FileHash`. The skeleton loads the user's language pack for its window texts, and `skeleton/examples/` shows the live frame, `connector.capture_target`, setups and options.

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
