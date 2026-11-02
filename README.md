# Stintmate connector kit

A connector can be written in any language that loads a DLL. The transport between a connector
and Stintmate is `stintmate_ipc.dll`, a native library with a C ABI (`native/` says where to
download it, with its header). The skeleton in `skeleton/` is a Python example: read it as a
reference in any language, and copy it if you write Python.

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
| `docs/catalogs.md` | The announcement catalogs |
| `docs/data-model.md` | The data model a connector writes into |
| `docs/write-api.md`, `docs/write-api.schema.json` | The write API and its JSON schema |
| `docs/events.md` | The event feed |
| `contract.json` | The connector-facing names, codes, vocabularies and limits, machine readable (also in the release zip) |
| `checker/` | `connector-check`: starts your connector from its descriptor against a fake Pitwall and reports the checks of [levels.md](docs/levels.md). Python; needs the wheel that `skeleton/fetch-ipc.ps1` downloads; works for a connector in any language |
| `native/` | For a connector in any other language: where the C header and the native DLL come from (the stintmate-ipc release) |
| `skeleton/` | The smallest working connector (a fake simulator that plays one synthetic session through the real contract): copy it, rename it, replace the fake recorder with the reading of your game. Python, an example to read and copy; its README is the step by step. `fetch-ipc.ps1` downloads the `stintmate-ipc` wheel (the Python binding, with the native DLL; an asset of the stintmate-ipc-python release) into `vendor/`; `install.ps1` registers it, `tests/test_conformance.py` checks it |

The kit carries no binary and no C header: they are assets of the stintmate-ipc release (`stintmate_ipc.dll`, `stintmate_ipc.h`, `stintmate-ipc-check.exe`). The Python wheel is an asset of the stintmate-ipc-python release.

## Releases

Each release is a GitHub release of this repository, tagged `vMAJOR.MINOR.PATCH`, with one asset,
`connector-kit-<version>.zip`, and a `SHA256SUMS` file (`<sha256>  <name>`, one line per asset;
verify with `Get-FileHash -Algorithm SHA256 <file>`). The zip holds `README.md`, `LICENSE`,
`NOTICE`, `contract.json`, `docs/`, `native/` and `skeleton/` under a `connector-kit-<version>/` folder. The notes
of a release are its section of `CHANGELOG.md`; the tag must equal the version of the first
`## <version>` heading of that file (`scripts/release-check.sh` compares the first word of that
heading). A section named `X.Y.Z+docs.N` records a documentation-only correction of release
`X.Y.Z`: it has no release and no assets, and `release-check.sh` refuses any tag containing `+`. Before
tagging the next release, put its own `## <version>` section on top.

The `ci` and `release` workflows (Actions) run by hand only. `scripts/check-docs.sh` is the
documentation check both run, together with the skeleton's tests and the JSON validity of
`contract.json` and `docs/write-api.schema.json`; run it locally from the repository root.

## Where Stintmate comes from

Stintmate (the hub, Pitwall and its `pitwall-core`) is downloaded from https://stintmate.app and
installed on Windows. The installation and the hub's first run create the user's workspace, the
`ipc\key` file inside it and the folder the kit is copied to. The skeleton's tests and the
conformance check run without it (they use a fake Pitwall); the hand test in `skeleton/README.md`
and steps 4 and 6 of `docs/connectors.md` need an installed Stintmate with a workspace.

## Where this kit is read

Stintmate's developer session downloads the release of this kit that matches the installed
Stintmate into the user's work folder. Read the files there, never edit them; work in a copy,
for example `dev/connector-<sim>/`.

## Contributing

Issues are welcome. Pull requests are welcome too, but there is no promise that they are
reviewed or merged. For a larger change, open an issue first. Report a security problem
privately, as described in [`SECURITY.md`](SECURITY.md).
