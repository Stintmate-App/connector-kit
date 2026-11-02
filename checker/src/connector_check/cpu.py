"""The idle CPU budget: the CPU time of the connector's whole process tree, game closed and
connection open, divided by wall time (docs/levels.md section 4)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import psutil

from .process import Proc, job_usage

BELOW_NORMAL = {psutil.BELOW_NORMAL_PRIORITY_CLASS, psutil.IDLE_PRIORITY_CLASS}
MIN_CPU_S = 0.1                       # a process with less CPU than this is not judged on priority


@dataclass
class Measurement:
    percent: float
    seconds: float
    processes: int
    changed: bool = False                # a process appeared or went during the window
    evidence: str = ""
    above_normal: list[str] = field(default_factory=list)
    unjudged: int = 0                    # processes with too little CPU time to judge their priority


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
    """Wait `settle` s, then watch `seconds`. The CPU time counted is the job object's own
    accounting: every process the checker started, ended ones included, so a connector that starts
    a helper process for each check cannot hide in the gap between two snapshots. Without a job,
    the processes present at both ends are counted, and the window repeats once if the set of
    processes changed."""
    time.sleep(settle)
    for attempt in range(2):
        job_before = job_usage()
        before = snapshot(root)
        t0 = time.perf_counter()
        time.sleep(seconds)
        after = snapshot(root)
        job_after = job_usage()
        wall = time.perf_counter() - t0
        common = before.keys() & after.keys()
        changed = set(before) != set(after)
        names = ", ".join(f"{after[k][1]}:{k[0]}" for k in sorted(after))
        # The busiest process is always judged: on a fast machine all of them may stay under the
        # limit, and a launcher parent that does no work must not count.
        judged = {k for k in after if after[k][0] > MIN_CPU_S}
        if after:
            judged.add(max(after, key=lambda k: after[k][0]))
        unjudged = len(after) - len(judged)
        low = [f"{after[k][1]}:{k[0]}" for k in judged if after[k][2] not in BELOW_NORMAL]
        if job_before is not None and job_after is not None:
            used, started = job_after[0] - job_before[0], job_after[1] - job_before[1]
            note = (f"{used * 1000:.0f} ms of CPU in {wall:.1f} s by every process of the job, "
                    f"{started} started in the window ({names})")
            return Measurement(used / wall * 100.0, wall, len(after), False, note, low, unjudged)
        used = sum(after[k][0] - before[k][0] for k in common)
        if not changed or attempt == 1:
            note = (f"{used * 1000:.0f} ms of CPU in {wall:.1f} s, {len(common)} processes ({names})"
                    + (", and the set of processes changed during the window" if changed else ""))
            return Measurement(used / wall * 100.0, wall, len(common), changed, note, low, unjudged)
    raise AssertionError("unreachable")
