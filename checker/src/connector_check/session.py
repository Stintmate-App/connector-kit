"""Playing a session: the game (a file the checker creates, or the person), and reading what the
connector wrote back from the fake core."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from .fakecore import Entry, FakeCore, decode_channel
from .sanity import Lap

MANUAL_STEPS = ("Play a session now: start the game, leave the garage, drive two timed laps, "
                "make a pit stop, then close the game.")


def game_path(ctx: Any) -> Path | None:
    """The fake game file, or None when the person plays."""
    raw = ctx.args.fake_game_file
    if not raw:
        return None
    path = Path(raw.format(workspace=ctx.workspace, id=ctx.cid))
    return path if path.is_absolute() else ctx.workspace / path


def game_on(ctx: Any) -> None:
    path = game_path(ctx)
    if path is None:
        print(MANUAL_STEPS, file=sys.stderr, flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def game_off(ctx: Any) -> None:
    path = game_path(ctx)
    if path is not None:
        path.unlink(missing_ok=True)


def settle(ctx: Any, timeout: float = 25.0) -> bool:
    """The game is closed and the connector says so: the next session can start cleanly."""
    core: FakeCore = ctx.core
    return core.wait(lambda: (core.last_status() or {}).get("game_running") is not True, timeout)


def wait_end(ctx: Any, ended_before: int, timeout: float | None = None) -> bool:
    core: FakeCore = ctx.core
    return core.wait(lambda: core.ended_count > ended_before,
                     ctx.args.session_timeout if timeout is None else timeout, 0.2)


def accepted(entries: list[Entry], kind: str) -> list[Entry]:
    return [e for e in entries if e.method == f"pitwall.write.{kind}" and e.error is None
            and not e.replayed]


def layouts(entries: list[Entry]) -> dict[tuple[str, str], float | None]:
    out: dict[tuple[str, str], float | None] = {}
    for e in accepted(entries, "catalog"):
        for row in e.params.get("layouts") or []:
            out[(row.get("track_id"), row.get("layout_id"))] = row.get("length_m")
    return out


def laps_of(entries: list[Entry], key: str) -> list[Entry]:
    return [e for e in accepted(entries, "lap") if e.params.get("session_key") == key]


def decoded(lap: Entry) -> dict[str, list[float]]:
    return {n: decode_channel(v) for n, v in (lap.params.get("channels") or {}).items()}


def eligible(laps: list[Entry], length_m: float) -> list[Lap]:
    """Flying, valid, timed laps with channels, as sanity checks them."""
    out = []
    for e in laps:
        p = e.params
        if p.get("lap_type") == "flying" and p.get("valid") is True and p.get("time_s") \
                and p.get("channels"):
            out.append(Lap(decoded(e), length_m, float(p["time_s"]), p.get("fuel_start_l"),
                           p.get("fuel_used_l"), name=f"lap {p.get('seq')}"))
    return out


def pending(ctx: Any) -> int:
    state = ctx.link.latest if ctx.link is not None else None
    box = (state or {}).get("outbox")
    return box.get("pending", 0) if isinstance(box, dict) else 0


def wait_pending(ctx: Any, minimum: int, timeout: float) -> int:
    """Wait until the control state shows at least `minimum` writes pending; the value, or -1."""
    ok = ctx.link.wait_for(
        lambda s: isinstance(s.get("outbox"), dict) and s["outbox"].get("pending", 0) >= minimum,
        timeout)
    return pending(ctx) if ok is not None else -1


def max_pending_since(ctx: Any, since: float) -> int:
    with ctx.link._cv:                                                  # noqa: SLF001
        return max((s.get("outbox", {}).get("pending", 0) for t, s in ctx.link.history
                    if t >= since and isinstance(s.get("outbox"), dict)), default=0)


def wait_quiet(ctx: Any, timeout: float = 25.0) -> None:
    """The recorder is not recording any more (so a new session can begin)."""
    from .checks import service_state
    ctx.link.wait_for(lambda s: (service_state(s, "recorder") or {}).get("state") != "recording",
                      timeout)
    time.sleep(0.3)
