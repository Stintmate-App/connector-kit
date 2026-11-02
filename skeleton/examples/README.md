# Examples: what to add to the skeleton for each feature

The skeleton does the minimum ([levels.md](../../docs/levels.md)). Each module here shows one
optional piece, small enough to read in a minute. They are importable, and the skeleton does not
import them: copy what you need into `src/skeleton_connector/`. Each file starts with the lines
to add.

| File | Shows | Contract |
|---|---|---|
| `live_frame.py` | `connector.live_frame` declared inline, and a frame that validates against [live-frame.schema.json](../../docs/live-frame.schema.json) with one `is_player` | [connector-protocol.md section 7](../../docs/connector-protocol.md) |
| `capture_target.py` | `connector.capture_target` and its three answers (a window, `game_not_running`, `window_minimized`) | [section 4](../../docs/connector-protocol.md) |
| `setups.py` | `pitwall.write.setup` at the session start and at a pit exit, with `extra` | [write-api.md](../../docs/write-api.md) |
| `options.py` | A `settings.json` read at start, defaults in code | [guide.md](../../docs/guide.md) |

Run `uv run pytest -q` in `skeleton/`: `tests/test_examples.py` loads every module and checks the
frame against the schema.
