# Changelog

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
