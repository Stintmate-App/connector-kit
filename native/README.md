# Native interface (any language)

A connector can be written in any language that loads a DLL. The transport between a connector
and Pitwall is Stintmate IPC v2, and its C ABI is the one interface for every language:
`stintmate_ipc.h` declares it, and the comment at the top of the header holds the ABI rules
(ownership, threading, error codes). ABI 1.0. The specification is public:
https://github.com/Stintmate-App/stintmate-ipc (its `SPEC.md`).

This kit carries no header and no binary. Download them directly from the stintmate-ipc release
`v0.3.3` (https://github.com/Stintmate-App/stintmate-ipc/releases/tag/v0.3.3); no Python and no
`uv` are involved:

- `stintmate_ipc.dll`: the library the header describes; ship it beside your connector's
  executable;
- `stintmate_ipc.h`: the header;
- `stintmate-ipc-check.exe`: the conformance tool (below);
- `SHA256SUMS`: the checksums of every file of the release, one `<sha256>  <name>` line each.
  Verify what you download, for example with `Get-FileHash -Algorithm SHA256 <file>`.

`stintmate_ipc-0.3.3-py3-none-win_amd64.whl`, the Python binding (a zip file that carries the same
DLL and tool under `stintmate_ipc/_bin/`), is an asset of the release `v0.3.3` of the
stintmate-ipc-python repository
(https://github.com/Stintmate-App/stintmate-ipc-python/releases/tag/v0.3.3), with its own
`SHA256SUMS`. Only Python connectors need it; `skeleton/fetch-ipc.ps1` downloads and verifies it
into `skeleton/vendor/`, the environment variable `STINTMATE_IPC_RELEASE` replaces the source (a
base URL or a local folder).

## Testing a connector, in any language

`stintmate-ipc-check.exe` is how any connector tests itself, whatever its language: it speaks the
protocol from the other side. `stintmate-ipc-check --help` lists:

    stintmate-ipc-check server <pipe>   check a server endpoint
    stintmate-ipc-check client <pipe>   serve a test endpoint and check the client that connects to it
    stintmate-ipc-check list            list the endpoints in the workspace's registry
    stintmate-ipc-check keygen [path]   create a key file if it does not exist

`<pipe>` is `\\.\pipe\stintmate-...` or the short name. Options of `server` and `client`:
`--key-file PATH`, `--key-hex HEX`, `--workspace DIR`, `--json`. `server` also takes `--listen
SECONDS`, `--channel NAME`, `--call METHOD[=JSON]` and `--fast`; `client` takes `--timeout`,
`--serve` and `--reconnect`. The exit status is 0 when every check passed (warnings allowed), 1
when one failed and 2 on a usage error. The skeleton's `tests/test_conformance.py` runs
`stintmate-ipc-check server` against its endpoint; do the same in your language's test suite.
