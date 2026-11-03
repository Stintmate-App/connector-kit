# connector-check

Checks a connector against the minimum level of the kit ([docs/levels.md](../docs/levels.md)). It
starts the connector from its descriptor, serves a fake Pitwall to it and prints one line per check.
It imports nothing of Stintmate and works for a connector in any language: it only starts the
descriptor's launcher as a process and talks to it over the contract.

## Run it

Once, download the transport wheel the checker depends on (`stintmate-ipc`):

```
powershell -ExecutionPolicy Bypass -File skeleton\fetch-ipc.ps1
```

Then, from the kit's root:

```
uv run --project checker connector-check --descriptor <file>
```

A full run takes a few minutes: it plays several sessions, stops and starts the fake Pitwall, kills
the connector once and measures the CPU for `--cpu-seconds`.

| Option | Meaning |
|---|---|
| `--descriptor <file>` | The connector's descriptor (`<id>.json`). Required |
| `--level minimum\|features` | `features` also checks the names the descriptor declares (default `minimum`) |
| `--only M1,M5` | Run only these checks, by id (`M5.quit`) or by requirement (`M5`) |
| `--skip-session` | Do not play a session; the checks that need one are skipped |
| `--fake-game-file <path>` | A file whose existence means "the game runs". The checker creates and removes it. `{workspace}` and `{id}` are replaced; a relative path is relative to the checker's temporary workspace. Without it the checker asks you to play a session, and the checks that need further sessions are skipped |
| `--session-timeout 1800` | Seconds to wait for a session to end |
| `--cpu-seconds 60` | Length of the idle CPU measurement |
| `--instance-env NAME` | The variable that carries the tag (default `STINTMATE_DEV_INSTANCE`) |
| `--mutex <name>` | The tagged mutex name, for a connector whose rule differs from the descriptor's mutex plus `.<tag>` |
| `--no-position` | The game gives no position: `x` and `z` are not required |
| `--report <file.json>` | Also write the result as JSON |
| `--start-timeout 60` | Seconds to wait for `control.state` after the start |
| `--link-seconds 30` | Seconds the link must hold with the game closed |
| `--taken-seconds 65` | Seconds `M20.taken` watches the connector after `connector_id_taken` (at least 61) |
| `--register-command <cmd>` | Your registration command, for `M20.registration` (a JSON array or a command line; `{connectors_dir}`, `{workspace}` and `{id}` are replaced). Without it the check stays `MANUAL` |

With a fake game file for the skeleton, use `--fake-game-file "{workspace}\connectors\skeleton\fake_game"`.

### Your own connector

Close the game first: `M9.link` needs it closed, and the checker starts the connector with it closed.
A connector that reads a real game has three ways to meet the session checks:

1. **A test-only game switch and a synthetic session** (what the skeleton does): while
   `STINTMATE_DEV_INSTANCE` is set, the connector takes a file as "the game runs" and plays a scripted
   session. Give the file to `--fake-game-file`. Every check runs, and the exit code can be 0.
2. **You play.** Without `--fake-game-file` the checker asks you to start the game, drive two timed laps,
   make a pit stop and close the game, and waits up to `--session-timeout` (30 minutes by default). It
   plays only that first session, so `M13.retry`, `M13.refused` and `M14.outbox` are skipped and the
   exit code is 3 at best.
3. **No game at hand.** `--skip-session` skips every check that needs a session (exit 3 at best); the
   rest, from the descriptor to `M15`, still run.

A skipped check is not a pass: exit 3 says what was not proved. Run the skipped ones by hand, or add the
switch of the first way.

## What it does to your machine

It makes a temporary workspace with a key of its own, picks a random tag, and starts the connector
with `STINTMATE_DEV_INSTANCE=<tag>`, with an `AppData` and a descriptor folder
(`STINTMATE_CONNECTORS_DIR`) of its own in the temporary folder, without `STINTMATE_WORKSPACE` (the
workspace comes from `--workspace` only), and the options a managing app
passes (`--workspace`, `--managed`, `--parent-pid`) plus `--allow-synced` and `--future-option x`.
The fake Pitwall listens on the `pitwall` pipe with the tag in its user part, and the connector's
control endpoint is reached on its tagged pipe. A real Stintmate on the same machine is not
touched. Every process the checker starts, with its children, ends with it, also on failure and on
Ctrl+C, and the temporary folder is removed.

## Output and exit codes

```
PASS|FAIL|SKIP|MANUAL  <id>  <title>  <evidence>
```

then a summary with the `contract_version`, the declared features and what stays manual.

| Exit | Meaning |
|---|---|
| 0 | Every automatic check passed |
| 1 | One failed |
| 2 | It could not run: bad usage, the wheel or the descriptor missing |
| 3 | No failure, but some automatic checks were skipped (`--skip-session`, or a missing fake game file) |

## The checks

| Id | What it checks |
|---|---|
| `M1.schema`, `M1.identity`, `M17.uninstall` | The descriptor against its schema; `id` equal to the file name, `endpoint`, `mutex` starting `Local\`, an existing absolute launcher; an absolute, existing `uninstall` command when the descriptor has one (without the key it stays `MANUAL`: a development checkout has none) |
| `M7.key` | Started with no key: still running after 5 s and the key not created, also when the checker knocks on its control endpoint; linked once the key appears; then, with the recorder running, the key is removed and Pitwall restarted: the key must not come back from the connector, and it links again once the checker puts the key back. The key file is watched from the start to the end of the check, so a connector that creates it or replaces its content fails |
| `M2.options`, `M19.instance` | Starts with the managed options and an unknown one and serves `control.state`; the tagged pipe is served and the tagged mutex is held |
| `M3.single` | A second start exits within 10 s and the first still answers |
| `M4.transport`, `M4.state` | `stintmate-ipc-check server --fast` passes; `control.state` against its schema |
| `M5.services`, `M5.managed`, `M5.parent`, `M5.stranger`, `M5.quit` | `control.service.stop` and `start` change `running`, and no service runs before the managing process asks; `control.managed` follows; when the managing process ends the connector keeps running and `managed` becomes false; a caller that is neither the parent nor its child is refused with `managed`; the parent's `control.quit` ends the tree within 20 s, with the pipe and the mutex gone |
| `M8.hello`, `M9.link`, `M7.absent`, `M9.state` | The hello and `connector.status`; linked with the game closed and still linked; linked again after Pitwall comes back; `control.state.pitwall` is `waiting`, `connected`, and `refused` after `unauthorized`, `bad_request`, `busy` or `connector_busy`, with new attempts |
| `M20.product` | `product` is in the hello and in the descriptor, a lowercase UUID version 4 (the pattern of `contract.json`), and equal in both. The fake Pitwall also closes a hello without a valid `product` with `bad_request` |
| `M20.taken` | Planted fault: the fake Pitwall closes every connection with `connector_id_taken`. `control.state.pitwall` must be `refused`, and the connector must not connect again within 60 s of the refused attempt. It watches `--taken-seconds` (65 by default), so this check adds about a minute to a run; a connector that retries sooner fails at once. Mind your IPC client's jitter: a 60 s delay spread by 20 % can come back after 48 s |
| `M20.registration` | Plants a descriptor of another `product` in a folder of its own in the temporary folder and runs `--register-command` with `STINTMATE_CONNECTORS_DIR`, `LOCALAPPDATA` and `APPDATA` aimed at the temporary folder. The command must exit non-zero and leave the descriptor byte for byte as it was. For the skeleton: `["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File","skeleton\\install.ps1","-Destination","{workspace}\\registered"]` |
| `M18.texts` | A language pack made by the checker from `control.catalog.get` reaches the service names and labels. A connector that does not serve `control.catalog.get` (`unknown_method`) stays `MANUAL`: its texts stay in English |
| `M6.recorder`, `M8.game` | The service `recorder` is `waiting`, then `recording`; `game_running` is false, true before the first write, false again; `connector.status` shows `phase` `on_track` in the session |
| `M10.order`, `M10.laps`, `M11.channels`, `M12.sanity`, `M13.keys`, `M16.logs` | The session as the write API and the data model say; the sanity rules of [levels.md](../docs/levels.md) section 9; every key starts with the connector's id and is stable; the descriptor's logs exist |
| `M13.retry`, `M13.refused` | Scripted `busy`, `writer_busy`, `timeout`, `shutting_down`, `unavailable`, `connector_busy` and a code nobody knows keep the write and its key, and the write is not sent before `retry_ms`; `bad_request`, `session_closed`, `unknown_session`, `too_large`, `unknown_method` and `version_unsupported` set a lap aside, never send it again, and the next writes go on. Needs a fake game file |
| `M14.outbox` | `outbox.pending` rises while Pitwall is away and the writes arrive in order afterwards; writes pending when the connector is killed arrive after its restart. Needs a fake game file |
| `M15.cpu`, `M15.priority` | At most 0.5 % of a core over `--cpu-seconds` for every process the checker started, ended ones included (the job object's own accounting, so a helper started for each game check counts), game closed and linked; every process of the tree that used CPU is below normal priority |
| `F.names`, `F.announcements`, `F.guidance`, `F.actions` | At `--level features`: the declared names are known, and a declared catalog, guidance or action list reached the fake Pitwall (`test.ping` is called) |
| `F.live_frame` | At `--level features`, if `live_frame` is declared: the connector offers `connector.live_frame`; with the game closed it answers a frame valid against [live-frame.schema.json](../docs/live-frame.schema.json) or `game_not_running`; with the game running it answers a valid frame in the phase `garage`, `on_track` or `paused` (it needs the fake game file, or you playing) |
| `F.capture_target`, `F.brake_cues` | At `--level features`: a declared `capture_target` means the connection offers `connector.capture_target`; a declared `brake_cues` means it offers the stream `connector.telemetry` |

`M2.runtime`, `M12.findings`, `M15.game`, `M16.cap` and `M17.hold` are manual: they print as
`MANUAL` and never fail.

## What `--level features` covers

It adds one check per feature the descriptor declares. A feature that is not declared passes as "not declared". What it proves is that the connector offers the thing it declared, and for the live frame that the frame has the right shape. It does not prove the content is true: that the speed, the position or the fuel in a frame match the game, that the window `connector.capture_target` names is the game's, or that the telemetry stream carries real samples is for you to check against the game. Every other known feature has no check beyond its name (`F.names`).

## Tests

`uv run --project checker pytest checker/tests -q`. `test_planted_faults.py` plants a fault in a
copy of the skeleton, or in its descriptor, for each automatic check and requires the checker to fail
that check; `test_skeleton_passes.py` runs the unchanged skeleton while any real Stintmate apps run
and requires every automatic check to pass and the real pipes, key and settings to stay untouched.
