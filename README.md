# Stintmate connector kit

What you need to build a Stintmate connector for a game that has none: the documents that say
what a connector is and what it must do, and the smallest working connector to start from. The
transport between a connector and Stintmate (Stintmate IPC) is public too:
https://github.com/Stintmate-App/stintmate-ipc (its `SPEC.md`).

Licensed under the Apache License 2.0 (see `LICENSE` and `NOTICE`).

| Path | What |
|---|---|
| `docs/guide.md` | The connector author's guide: what a connector is, the descriptor, the control endpoint, the service, texts, laps and stints, the telemetry study |
| `docs/connector-protocol.md` | The Pitwall side: hello, status, the write methods, live functions, laps and stints |
| `docs/connectors.md` | How Pitwall treats connectors |
| `docs/contract.md` | The parts of the IPC contract a connector uses |
| `docs/descriptor.md` | The connector descriptor |
| `docs/catalogs.md` | The announcement and guidance catalogs |
| `docs/data-model.md` | The data model a connector writes into |
| `docs/write-api.md`, `docs/write-api.schema.json` | The write API and its JSON schema |
| `docs/events.md` | The event feed |
| `contract.json` | The connector-facing names, codes, vocabularies and limits, machine readable |
| `native/` | For a connector in any other language: where the C header and the native DLL come from (the stintmate-ipc release) |
| `skeleton/` | The smallest working connector (a fake simulator that plays one synthetic session through the real contract): copy it, rename it, replace the fake recorder with the reading of your game. Its README is the step by step; `fetch-ipc.ps1` downloads the `stintmate-ipc` wheel (the transport, with its native DLL) into `vendor/`, so a Python connector needs only `uv`; `install.ps1` registers it, `tests/test_conformance.py` checks it |

The kit carries no binary and no C header: they are assets of the stintmate-ipc release.

## Installed with Stintmate

Stintmate installs a copy of this kit in the user's work folder, under `connector-kit/`. Read the
files there, never edit them; work in a copy, for example `dev/connector-<sim>/`.
