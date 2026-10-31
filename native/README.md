# Native interface (any language)

The transport between a connector and Pitwall is Stintmate IPC v2. Its C ABI is the one
interface for every language: `stintmate_ipc.h` declares it, and the comment at the top of the
header holds the ABI rules (ownership, threading, error codes). ABI 1.0. The specification is
public: https://github.com/Stintmate-App/stintmate-ipc (its `SPEC.md`).

This kit carries no header and no binary. They are assets of the stintmate-ipc release
`v0.3.1`: https://github.com/Stintmate-App/stintmate-ipc/releases/tag/v0.3.1

- `stintmate_ipc.h`: the header;
- `stintmate_ipc-0.3.1-py3-none-win_amd64.whl`: the wheel (a zip file); the DLL and the
  conformance tool are inside it under `stintmate_ipc/_bin/`:
  `stintmate_ipc.dll` (the library the header describes) and `stintmate-ipc-check.exe`;
- `SHA256SUMS`: the checksums of both, one `<sha256>  <name>` line each.

`skeleton/fetch-ipc.ps1` downloads and verifies them into `skeleton/vendor/` (copy that script
if you start from another language). The environment variable `STINTMATE_IPC_RELEASE` replaces
the source: a base URL or a local folder that holds the three files.

Take the DLL out of the wheel with any zip tool, for example:

    uv run python -c "import zipfile,glob; zipfile.ZipFile(glob.glob('skeleton/vendor/*.whl')[0]).extractall('ipc-wheel')"

then use `ipc-wheel/stintmate_ipc/_bin/`. Ship the DLL beside your connector's executable.
