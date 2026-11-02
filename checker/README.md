# connector-check

Checks a connector against the minimum level of the kit ([docs/levels.md](../docs/levels.md)). It
starts the connector from its descriptor, serves a fake Pitwall to it and prints one line per check.
It imports nothing of Stintmate and works for a connector in any language: it only starts the
descriptor's launcher as a process and talks to it over the contract.

## Run it

Once, download the transport wheel the checker depends on (`stintmate-ipc`):

```
skeleton\fetch-ipc.ps1
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

With a fake game file for the skeleton, use `--fake-game-file "{workspace}\connectors\skeleton\fake_game"`.

## What it does to your machine

It makes a temporary workspace with a key of its own, picks a random tag, and starts the connector
with `STINTMATE_DEV_INSTANCE=<tag>`, with an `AppData` and a descriptor folder
(`STINTMATE_CONNECTORS_DIR`) of its own in the temporary folder, and the options a managing app
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
| `M1.schema`, `M1.identity`, `M17.uninstall` | The descriptor against its schema; `id` equal to the file name, `endpoint`, `mutex` starting `Local\`, an existing absolute launcher; an absolute, existing `uninstall` command |
| `M7.key` | Started with no key: still running after 5 s, the key not created; linked once the key appears |
| `M2.options`, `M19.instance` | Starts with the managed options and an unknown one and serves `control.state`; the tagged pipe is served and the tagged mutex is held |
| `M3.single` | A second start exits within 10 s and the first still answers |
| `M4.transport`, `M4.state` | `stintmate-ipc-check server --fast` passes; `control.state` against its schema |
| `M5.services`, `M5.managed`, `M5.stranger`, `M5.quit` | `control.service.stop` and `start` change `running`; `control.managed` follows; a caller that is neither the parent nor its child is refused with `managed`; the parent's `control.quit` ends the tree within 20 s, with the pipe and the mutex gone |
| `M8.hello`, `M9.link`, `M7.absent`, `M9.state` | The hello and `connector.status`; linked with the game closed and still linked; linked again after Pitwall comes back; `control.state.pitwall` is `waiting`, `connected`, and `refused` after `unauthorized` or `connector_busy`, with new attempts |
| `M18.texts` | A language pack made by the checker from `control.catalog.get` reaches the service names and labels |
| `M6.recorder`, `M8.game` | The service `recorder` is `waiting`, then `recording`; `game_running` is false, true before the first write, false again |
| `M10.order`, `M10.laps`, `M11.channels`, `M12.sanity`, `M13.keys`, `M16.logs` | The session as the write API and the data model say; the sanity rules of [levels.md](../docs/levels.md) section 9; every key starts `<id>/` and is stable; the descriptor's logs exist |
| `M13.retry`, `M13.refused` | Scripted `busy`, `writer_busy`, `timeout`, `shutting_down`, `unavailable` and `connector_busy` keep the write and its key; a `bad_request` is set aside and the next writes go on. Needs a fake game file |
| `M14.outbox` | `outbox.pending` rises while Pitwall is away and the writes arrive in order afterwards; writes pending when the connector is killed arrive after its restart. Needs a fake game file |
| `M15.cpu`, `M15.priority` | At most 0.5 % of a core over `--cpu-seconds` for the whole process tree, game closed and linked; every process of the tree that used CPU is below normal priority |
| `F.names`, `F.announcements`, `F.guidance`, `F.actions` | At `--level features`: the declared names are known, and a declared catalog, guidance or action list reached the fake Pitwall (`test.ping` is called) |

`M2.runtime`, `M12.findings`, `M15.game`, `M16.cap` and `M17.hold` are manual: they print as
`MANUAL` and never fail. The features `live_frame`, `capture_target` and `brake_cues` are not checked.

One thing differs from the contract's wording: after the key appears the checker waits up to 30 s
for the link (the transport re-reads a key that was missing only now and then; the skeleton took up
to about 20 s), and the evidence line says how long it took.

## Tests

`uv run --project checker pytest checker/tests -q`. `test_planted_faults.py` plants a fault in a
copy of the skeleton, or in its descriptor, for each automatic check and requires the checker to fail
that check; `test_skeleton_passes.py` runs the unchanged skeleton while any real Stintmate apps run
and requires every automatic check to pass and the real pipes, key and settings to stay untouched.
