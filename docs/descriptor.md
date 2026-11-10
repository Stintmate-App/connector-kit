# The connector descriptor

How Stintmate's managing app (the hub) finds the connectors installed on a machine without
knowing any simulator. Each connector's setup writes **its own** descriptor, one file per
connector id, in the per-user machine folder. The hub lists the `*.json` files there and runs
every connector's control app the same way, over the control interface ([contract.md section
3](contract.md#3-the-control-interface-every-control-app)). The descriptor describes this
machine's install, so a new or moved workspace sees every installed connector.
`<workspace>\connectors\<id>\` stays the connector's data and logs folder.

**The folder** is `%LOCALAPPDATA%\Stintmate\connectors` (the LocalAppData known folder; if
`LOCALAPPDATA` is not set, the `AppData\Local` folder of the user's home). The environment
variable `STINTMATE_CONNECTORS_DIR` (a folder path), when set and not empty, replaces it: use it
in tests. The writer creates the folder; a reader that finds none lists nothing. A reader only
looks at files named `*.json`, so the temporary file of an atomic write is never read.

```json
{
  "schema": 1,
  "id": "example",
  "name": "Example connector",
  "product": "6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43",
  "sim": "example",
  "sim_name": "Example Simulator",
  "version": "1.0.0",
  "flavour": "dev",
  "launcher": "C:\\Program Files\\Example\\example-connector.exe",
  "args": [],
  "control_protocol": "stintmate-ipc/2",
  "endpoint": "connector-example",
  "mutex": "Local\\ExampleConnector.UI",
  "services": [{"id": "recorder", "name": "Recorder"}],
  "logs": ["recorder.log", "ui.log"],
  "icon": "C:\\Program Files\\Example\\icon.png",
  "written_at": "2026-10-19T01:30:00+0200",
  "written_by": "example-connector 1.0.0"
}
```

| Field | Type | Meaning |
|---|---|---|
| `schema` | int, required | The descriptor's version, `1`. A higher one is shown as needing a newer Stintmate. |
| `id` | string, required | The connector's id: the same as in its `hello` metadata (`connector_id`) and **equal to the file name without `.json`**; a descriptor whose `id` differs from its file name is ignored (logged). It is also a file name and a folder name, so it matches `^[a-z0-9][a-z0-9_-]{0,31}$` (the pattern of the hello metadata and of [the schema](descriptor.schema.json)): none of `\ / : * ? " < > |` and no trailing dot. |
| `name` | string, required | The connector's display name. |
| `product` | string, required | The connector's product key: a UUID version 4, lowercase, with hyphens (`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`), **equal to the `product` in the hello metadata** ([connector protocol, section 1](connector-protocol.md#1-connecting)). Generate it once and never change it. A descriptor without it, or with a malformed one, is listed as broken ("written for an older Stintmate") and never launched. |
| `version` | string, required | The connector's version. |
| `sim`, `sim_name` | string, recommended | The simulator as the connector names it (its hello metadata's `sim`, `sim_name`). The hub does not read them: shown by other tools, never branched on. |
| `flavour` | string, optional | Which build of this connector is installed, for example `"dev"` or `"python"`. Leave it out for the release build. The hub shows it after the version (`1.2.0 (dev)`); nothing branches on it. |
| `launcher` | string, required | Absolute path of the control app's executable. A descriptor whose `launcher` does not exist is listed as not installed and never launched. |
| `args` | array of strings, required | Arguments always passed to it (may be empty). |
| `control_protocol` | string, required | `"stintmate-ipc/2"`: the control interface of the [contract](contract.md). Any other value is refused. |
| `endpoint` | string, required | The connector's control endpoint id, `connector-<id>`; the pipe name is derived from it by the IPC package. |
| `mutex` | string, required | The control app's single-instance mutex (`Local\...`): held while it runs; **the liveness check**. |
| `services` | array, required | The connector's services in display order: `id` (what `control.service.start`/`stop` take; required) and `name`. The live state of each is in the endpoint's `control.state`. |
| `logs` | array of strings, optional | Log files (plain names, no path) in **`<workspace>\connectors\<id>\`**. The hub copies the last 512 KB of each, anonymised, into the problem report the user previews and saves. |
| `uninstall` | array of strings, optional | The command line (argv) that removes this install with no window and no prompts; the first element must be an absolute path, else it is ignored. Another build's setup runs it before installing, and the hub's Settings runs it for Uninstall. Leave it out when nothing installed the connector (a development checkout). |
| `window` | bool, optional | `true` when the control app serves `control.show` and `control.hide`. The hub shows its "Open window" button for the connector only when the key is `true`; leave it out (or `false`) when the connector has no window. |
| `features` | array of strings, optional | What the connector declares it offers, from the `features` list of `vocabularies` in [`contract.json`](../contract.json): `live_frame`, `lap_status`, `capture_target`, `brake_cues`, `setups`, `sim_data`, `fuel`, `sectors`, `conditions`, `rivals`, `wheel_channels`, `off_track`, `announcements`, `spotter`, `guidance`, `actions`, `window`, `notices`, `translated_texts`, `remembered_services`, `options` ([levels.md](levels.md) section 6 says what each one means). The hub does not read the key, so it ignores any name; the kit's checker flags a name that is not in the list. It is a declaration of the developer. |
| `icon` | string, optional | Absolute path of a PNG file your setup installed: the connector's icon, drawn by the managing app wherever it shows the connector. Square, 32 to 1024 pixels a side (256 x 256 recommended: the hub draws it at up to about 110 pixels on a high-density screen), at most 512 KiB, on a local drive (`C:\...`). Leave it out when there is none: the managing app then draws another icon or the connector's initials. The rules are under "The icon" below. |
| `written_at`, `written_by` | string, optional | When and by what the file was written. |

A broken descriptor (missing required field, wrong types) is listed with its error and never
launched.

## Rules

- **The contract is the outcome:** `%LOCALAPPDATA%\Stintmate\connectors\<id>.json` is in place
  and correct, so a managing app finds the connector. How it gets there is the connector's
  choice: its setup, a registration command or a script such as the skeleton's `install.ps1`.
  Nothing needs a workspace to install a connector.
- **One file per id, one owner:** the `product` says whose file it is. A setup or registration
  command that finds `<id>.json` holding **another `product` does not overwrite it**: it fails
  with a message that names the other connector (its `name`) and says the id is taken. Only a
  descriptor with the same `product` is replaced.
- **One file per id:** installing a second build of the same connector replaces the first. The
  setup of one build removes the other through its `uninstall` command, so the two flavours
  never share the file. When the file's `launcher` changes while the connector runs, the hub
  restarts it so the new build runs.
- **Updating a running connector:** a setup that replaces a connector Stintmate manages holds it
  through the hub instead of asking the user to close Stintmate; see the [connector protocol,
  section 1.2](connector-protocol.md#12-updating-your-connector-while-stintmate-runs).
- **Written by the connector only,** from its setup or registration command, atomically
  (a temporary file in the same folder, then a replace), UTF-8 without a BOM, and left as it
  is when nothing but `written_at` would change. A control app does not rewrite it at start.
- **Removed by the connector's uninstaller,** and only when both the file's `launcher` names that
  install (absolute paths, compared case-insensitive, `/` read as `\`) and its `product` is the
  connector's own. A file that names another install or another product, or is not the
  connector's, is left as it is.
- **The connector is responsible for everything it needs to run and to meet the protocol.** A
  managing app does not install, repair or choose interpreters for it. A connector that is a
  script ships its own Python environment (or runtime) inside its folder, and `launcher` names
  that environment's executable.
- **A managing app** starts `launcher` + `args` and expects to reach the connector's control
  endpoint; if it cannot, the connector does not work and is shown as such. The command line it
  runs is `launcher` + `args` + `--workspace <its workspace>` `--managed --parent-pid <its
  pid>`: the connector's `launcher` must tolerate those trailing options (and `--allow-synced`
  when the folder is synced); it may ignore them. It adopts a running one (mutex held) with
  `control.managed`; ignores unknown keys; and never writes the file.
- Paths inside (`launcher`) are absolute; file names (`logs`) are relative to
  `<workspace>\connectors\<id>\`.
- Additive changes (new optional keys) keep `schema` 1; a changed meaning bumps it. `window`,
  `features` and `icon` are such keys. A [JSON Schema](descriptor.schema.json) of the descriptor is in the kit.

## The icon

- **The path** starts with a drive letter and a separator (`^[A-Za-z]:[\\/]`) and ends in `.png`
  (any case). A relative path, a UNC path (`\\server\share\...`) and a device or long-path prefix
  (`\\?\`, `\\.\`) are refused and read as if the key were absent. UNC paths are refused so
  that a managing app never touches the network, or sends the user's Windows credentials, because
  a descriptor says so.
- **The file** is a regular file of at most 524 288 bytes that starts with the PNG signature
  (`89 50 4E 47 0D 0A 1A 0A`) and whose image is square, 32 to 1024 pixels a side.
- **A bad icon never breaks the descriptor.** The descriptor stays valid and the connector is
  managed as usual; the managing app ignores the icon (and may log why). This differs from the
  required keys on purpose: an icon is cosmetic.
- **The install owns it.** Your setup installs the PNG with the connector (the skeleton: `icon.png`
  in the install folder), the registration writes `icon`, and the uninstaller removes both. A
  managing app never copies, caches or changes the file; it reads it when it draws the connector.
- A writer that leaves the file alone when "nothing but `written_at` would change" compares the
  whole descriptor, so a new `icon` value rewrites it.
- `connector-check --level features` checks it as `F.icon`: `SKIP` when the key is absent,
  `PASS` or `FAIL` otherwise.

## Languages (`locales\`) and the language setting

- **The language** is `language` in `<workspace>\hub\settings.json`: a BCP 47 tag (`en`, `es`,
  `es-CL`), default `en`. Written only by the hub; read by every app once at start. The file
  missing, unreadable, not a JSON object, the field missing or not a valid tag: `en`. A
  connector (any process, any language) reads `language` the same way for its own windows, and
  declares only its English (or own-language) catalogs: the hub translates all of them.
- **Language packs** are `<workspace>\locales\<tag>\`, one folder per language tag, holding any
  of: `<catalog>.json` per app (`<id>-connector` for a connector's own control app),
  `numbers.json` (spoken numbers and the plural rules), `glossary.json` and
  `connector.<id>.json` (a connector's announcement catalog, translated by the hub). Formats,
  validation and the fallback chain (`es-CL`, `es`, `en`, the app's built-in English, per key):
  [catalogs.md](catalogs.md).
- **Who writes which file:** the hub's translate job writes `<catalog>.json` of every app UI
  catalog and of every connector's announcement catalog (`connector.<id>.json`, held by
  Pitwall); Pitwall's core writes `numbers.json`; `glossary.json` is the user's or the user's
  agent's. Each file has one automatic writer; a hand edit survives the job while its English
  source is unchanged. Nothing here is required: an empty or missing `locales\` means English
  everywhere.
