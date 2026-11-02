"""The idle CPU budget: the CPU time of the connector's whole process tree, game closed and
connection open, divided by wall time (docs/levels.md section 4)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import psutil

from .process import Proc

BELOW_NORMAL = {psutil.BELOW_NORMAL_PRIORITY_CLASS, psutil.IDLE_PRIORITY_CLASS}


@dataclass
class Measurement:
    percent: float
    seconds: float
    processes: int
    changed: bool = False                # a process appeared or went during the window
    evidence: str = ""
    above_normal: list[str] = field(default_factory=list)


def snapshot(root: Proc) -> dict[tuple[int, float], tuple[float, str, int | None]]:
    """(pid, start time) -> (cpu seconds so far, name, priority class) for the live tree."""
    out = {}
    for p in root.tree():
        try:
            times = p.cpu_times()
            out[(p.pid, p.create_time())] = (times.user + times.system, p.name(), p.nice())
        except psutil.Error:
            continue
    return out


def measure(root: Proc, seconds: float, settle: float = 10.0) -> Measurement:
    """Wait `settle` s, then watch `seconds`. The CPU time counted is that of the processes
    present at both ends, in percent of one core. If the set of processes changed, the window is
    repeated once; if it changes again that is the result (`changed`)."""
    time.sleep(settle)
    for attempt in range(2):
        before = snapshot(root)
        t0 = time.perf_counter()
        time.sleep(seconds)
        after = snapshot(root)
        wall = time.perf_counter() - t0
        common = before.keys() & after.keys()
        used = sum(after[k][0] - before[k][0] for k in common)
        changed = set(before) != set(after)
        names = ", ".join(f"{after[k][1]}:{k[0]}" for k in sorted(after))
        low = [f"{after[k][1]}:{k[0]}" for k in after
               if after[k][2] not in BELOW_NORMAL and after[k][0] > 0.1]
        if not changed or attempt == 1:
            note = (f"{used * 1000:.0f} ms of CPU in {wall:.1f} s, {len(common)} processes ({names})"
                    + (", and the set of processes changed during the window" if changed else ""))
            return Measurement(used / wall * 100.0, wall, len(common), changed, note, low)
    raise AssertionError("unreachable")
