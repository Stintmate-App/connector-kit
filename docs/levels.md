# Connector levels: minimum, parity and beyond

What a connector must do to work, what it may add to match the best-equipped connectors, and
how far it may extend the contract without breaking it. Read it before choosing what to build;
the normative text of each row is in the document it links to.

## 1. What a level is

| Level | Meaning |
|---|---|
| **Minimum** | The rules every connector follows. Below this line the connector does not work with Stintmate: sessions are not stored, analysis fails, or the apps show false errors. |
| **Parity** | Optional features that a fully equipped connector offers. Each one gives the user something specific and costs the developer something specific (section 6). The developer chooses at the start. |
| **Beyond** | Extensions of your own that the contract has room for, and the rules that must never be broken (section 7). |

The managing app (the hub) enforces none of this at this version of the kit: it lists, starts
and supervises whatever its [descriptor](descriptor.md) describes. The kit's checker,
`connector-check`, reports against the levels (section 9).

## 2. Minimum

| # | Requirement | What breaks without it | Where |
|---|---|---|---|
| M1 | A valid descriptor: `id` equal to the file name, an absolute launcher that exists, `control_protocol`, `endpoint`, `mutex`, `services`. | The hub cannot find the connector, or lists it as broken and never starts it. | [descriptor.md](descriptor.md) |
| M2 | The launcher accepts the options the hub appends (`--workspace`, `--managed`, `--parent-pid`, and `--allow-synced` for a synced folder) and ships its own runtime inside its folder. | The hub starts it with those options and installs no interpreter for it. | [descriptor.md](descriptor.md), [guide.md](guide.md) section 3 |
| M3 | The control app holds its single-instance mutex while it runs; a second start exits. | The hub uses the mutex to tell whether the connector runs. | [descriptor.md](descriptor.md) |
| M4 | The control endpoint serves `control.state` with every required field. | The hub cannot show the connector's status. | [contract.md](contract.md) section 3 |
| M5 | `control.quit` exits within 20 s; `control.managed`, and `control.service.start` and `control.service.stop` for every listed service. | The hub cannot stop, adopt or switch it. | [contract.md](contract.md) section 3.1 |
| M6 | The service that records has the id `recorder`; every `state` is a string, and the named states of [contract.md](contract.md) section 3.2 mean what they say there. | The recorder chip does not show the connector's recording. | [contract.md](contract.md) section 3.2 |
| M7 | Read the key; never create or replace it. Retry while the workspace, the key or Pitwall are missing. | The connection is refused, or Stintmate's own key is overwritten. | [guide.md](guide.md) section 4 |
| M8 | Hello with the required metadata; `connector.status` sent on every change; `game_running` from the game's process, never from the existence of its data channel. | Screens and the hold of an update show the wrong state. | [connector-protocol.md](connector-protocol.md) sections 1 and 2 |
| M9 | The link rule (section 3) and a correct `control.state.pitwall`. | False notices ("cannot reach Pitwall"), or a connector that is not heard. | [contract.md](contract.md) section 3.2 |
| M10 | A catalog with `length_m` on every layout and stable ids; `session_start` and `session_end`; laps with correct `lap_type`, `valid`, `time_s`, `sim_lap` and `on_track`; `pit_exit` in order; no `stint` (Pitwall numbers stints). | Laps are refused, or stored in the wrong stint. | [write-api.md](write-api.md), [data-model.md](data-model.md) |
| M11 | The channels `t`, `dist`, `speed`, `throttle`, `brake` and `g_lat` on every lap that has channels, plus `x` and `z` when the game gives a position. | Lap analysis, comparison and corners fail (section 5). | [data-model.md](data-model.md) |
| M12 | Units and signs checked against the game, and the findings written down. | Analysis runs on wrong numbers without any error. | [guide.md](guide.md) section 10 |
| M13 | Every write carries its idempotency key `ik`. A retryable error is retried with the same key; a refused write is kept aside. | A lost answer stores a lap twice, or a refused lap blocks all the later ones. | [contract.md](contract.md) section 5.4 |
| M14 | An outbox on disk. | A lap finished while Pitwall is not running is lost. | [contract.md](contract.md) section 5.4 |
| M15 | Sampling off the write path, below normal priority, a stated CPU budget, and the idle budget (section 4). | The game stutters, which matters most in VR. | [connectors.md](connectors.md) section 2 |
| M16 | Logs in `<workspace>\connectors\<id>\`, named in the descriptor's `logs` and capped in size. | A problem report has nothing to attach. | [descriptor.md](descriptor.md) |
| M17 | An `uninstall` command in the descriptor when anything installed the connector, and the hold of [connector-protocol.md](connector-protocol.md) section 1.2 when its setup replaces a running one. | Uninstall and update cannot be automatic. | [descriptor.md](descriptor.md) |
| M18 | No text composed in a language the connector does not have: service names and labels come from its own catalog. | Texts reach the user in the wrong language. | [contract.md](contract.md) section 3.2, [catalogs.md](catalogs.md) |
| M19 | While developing, set `STINTMATE_DEV_INSTANCE=<tag>` for every process of the connector ([guide.md](guide.md) section 3 says how). | Your test connector and a real Stintmate on the same machine share a pipe name and a mutex name, so one of them is refused or adopted. | [guide.md](guide.md) section 3 |

`length_m` is required: the layout length is what turns a lap's distance into a fraction of
the lap in the analysis and the live state. The outbox (M14) is required of every connector.

## 3. The link rule

> Connect to Pitwall when your recorder starts, whatever the game does, and stay connected. Never
> disconnect on purpose while your recorder runs. Pitwall listens to every connector. One is
> **active**: the first whose `game_running` is true, until that game closes. The active connector
> drives live state, radio calls, brake cues, captures and actions. The others still store their
> writes, and their events and announcements are not published. Set `game_running` as soon as the
> process exists and before the first write of a session.

## 4. The idle budget

> With the game closed and the connection open, a connector uses at most 0.5 % of one core,
> measured over 60 s, all its processes together. It reads nothing from the game's data channel:
> only a check of the game's process, at most every 2 s. The process runs below normal priority.

The figures are in `contract.json` under `budgets`. Measure the sum of the CPU time of all the
connector's processes over 60 s with the game closed and the connection open, and divide by 60
s.

## 5. What each analysis needs

Taken from the code of Pitwall's analysis. A function that lacks a required channel does not
guess: the lap analysis, comparison and corner detection raise "not found" for a missing `t`,
`dist`, `speed`, `throttle`, `brake` or `g_lat`.

| Function | Needs | Optional |
|---|---|---|
| Stored lap | `t`, `dist`, `speed` when the lap has channels (a write without them is refused) | |
| Lap analysis, comparison | `t`, `dist`, `speed`, `throttle`, `brake`, `g_lat`; the layout's `length_m` | `abs`, `tc`, the four `<wheel>_surface` (all four or none) |
| Corners | The same six channels and `length_m`; the detection reads `g_lat` and `speed` | |
| Outline, track map | `t`, `dist`, `x`, `z`; `g_lat` tells whether the plan view is mirrored | |
| Incidents | `t`, `dist`, `speed` | `invalid`, `pit_mode`, the four `<wheel>_surface` |
| Tyre report | `speed`, `g_lat`, the tyre channels | |
| Brake analysis | `brake`, `dist`; `<wheel>_rps` for lock-ups | |
| Fuel plan | `fuel_used_l` on timed flying laps, `fuel_start_l`, the car's `fuel_capacity_l` | |
| Fuel call after a lap | `fuel_start_l`, `fuel_used_l` | |
| Live state | `session`, `track`, `car`, `lap` and one participant with `is_player: true` in the [live frame](live-frame.schema.json) | |
| `position` of a lap event | `connector.live_frame` answering within 0.3 s | |
| Brake cues | The telemetry stream, a practice or test session, an enabled reference lap | |

## 6. Parity

Choose at the start. For each row, find out what the game exposes, decide yes, no or later,
write the result in your findings document, and list the names of the features you built in
the descriptor's `features` ([descriptor.md](descriptor.md)).

| Feature (`features` name) | The user gains | The developer supplies | Depends on |
|---|---|---|---|
| Live frame (`live_frame`) | Live state tools, deltas, the `position` of lap events, radio answers | `connector.live_frame` with the full frame and one participant at least | Game data |
| Lap status (`lap_status`) | "Why is this lap not valid" | `lap_type`, `invalid_cause` and the other fields of [connector-protocol.md](connector-protocol.md) section 7.1 | The live frame |
| Capture target (`capture_target`) | Captures of the right window, and a refusal when it is minimized | `connector.capture_target` | Finding the game's window |
| Brake cues (`brake_cues`) | Audible brake markers | `connector.telemetry`, `live` in the status, `lap_distance_m` | Live data, a `brake` channel, reference laps |
| Setups (`setups`) | Setup tools and stint comparison | `pitwall.write.setup` at the start and at each pit exit | What the game exposes |
| Fuel (`fuel`) | The fuel plan and fuel calls | `fuel_start_l` and `fuel_used_l` on laps, the tank capacity | Fuel readable |
| Sectors (`sectors`) | Sector analysis | `sectors` on laps | The game's timing |
| Conditions (`conditions`) | Weather in reports and radio | `conditions` on sessions and in the frame | A scale that matches the data model |
| Rivals (`rivals`) | Classification and pace comparison | `participant_laps` and the participants of the frame | The game exposes other cars |
| Wheel channels (`wheel_channels`) | Tyre and brake reports, lock-ups | The `<wheel>_*` channels | Game data |
| Off-track detail (`off_track`) | Incidents and kerbs | `<wheel>_surface`, `invalid`, `pit_mode`, `abs`, `tc` | Pitwall's codes |
| Announcements (`announcements`) | Radio calls for flags, penalties and the race | A catalog and a detector | The game's live state |
| Spotter (`spotter`) | Car-left and car-right calls | `spotter` entries, one slot, no params | Rival positions |
| Guidance (`guidance`) | The engineer does not guess about your game | `pitwall.guidance.declare` | Your findings |
| Actions (`actions`) | A radio test and game commands | `connector.action`, with `test.ping` | Declared formats |
| Window (`window`) | The connector's own window, logs and options | `control.show` and `control.hide`, and `window: true` in the descriptor | |
| Notices (`notices`) | Progress and problems in the notice bar | `control.notices`, `control.activity` | |
| Translated texts (`translated_texts`) | The connector in the user's language | `control.catalog.get` and a loader for the language packs | [catalogs.md](catalogs.md) |
| Remembered services (`remembered_services`) | The state of each service kept across restarts | `delegable`, `default` and `refusal` on services, `control.services.desired` | The control app |
| Options (`options`) | The user chooses behaviour | A settings file of its own | |
| Official install (`official_install`) | One click in the managing app | A setup that follows the unattended contract | The official catalog |

## 7. Beyond

Extend the contract in these places and only these:

- **Channels:** `ext_<name>`, at most 64 characters in all. Stored and shown, never analysed.
  Never redefine a name of the catalogue, and never send invented values for one: leave it
  out.
- **Setup:** your own keys in `extra`, each with `label`, `group`, `unit`, and a stable key.
- **Actions:** your own codes under your own dotted prefix, `requires_confirmation` for
  anything the player would notice, the work done off the game loop.
- **Texts:** your own codes with your own params. A new meaning is a new code; variants are
  wordings only. Use the group `other` when none fits.
- **Guidance:** your own items, in English, within the limits of `contract.json`.
- **Services:** further services, each with its own connection and service name.
- **Windows and tools:** anything of your own that reads only your own data.
- **Options:** your own keys in your own settings file.

Never break these:

1. Pitwall owns stints, lap numbers and the database. A connector never opens the database.
2. Never create or replace the key.
3. No unknown fields in a write message: Pitwall refuses them. Use `extra` and `ext_` channels.
   Elsewhere (`connector.status`, the live frame, `control.state`) unknown fields are kept or
   ignored.
4. Touch nothing of the game's folders beyond what the game allows, nothing of another
   application's data, and nothing of an anti-cheat.
5. No free text to the radio: codes only.
6. Stay within the rate budgets, the limits of `contract.json` and the idle budget.
7. Stable ids: the same car on the same layout keeps its ids across versions of your connector.
8. Import no Stintmate application.

## 8. Compatibility

`contract.json` carries `contract_version`, a whole number. It is the version of this kit's
contract; it is not the version of Pitwall's read contract.

- **Additive** changes keep the number: new optional fields, new optional methods, new states.
- Anything else raises it: a removed or renamed field, a new required field, a changed meaning.
- A connector built on an older contract keeps working with a newer Stintmate as long as the
  number is the same or the changes in between are additive. Read the changelog between the two.

## 9. How to check

`connector-check` runs a connector against a fake Pitwall and reports against these levels.
What it cannot see stays manual: the units and signs of your channels against the game (M12),
the CPU cost in a running game (M15) and the texts of your language (M18).
