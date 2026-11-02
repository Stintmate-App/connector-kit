"""Lap sanity: pure functions over one lap's channels. They catch the errors that raise no error
anywhere else: a speed in m/s, a lateral acceleration in m/s2 or with the wrong sign, pedals in
percent, a distance that runs backwards. Units are the data model's: ``speed`` km/h, ``g_lat`` in g
(positive to the right, the same sign as ``steer``), pedals 0..1, ``t`` seconds, ``dist`` and
``x``, ``z`` metres."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

OK, BAD, MANUAL, NA = "ok", "bad", "manual", "n/a"
RULES = ("t_increasing", "dist_increasing", "dist_length", "pedals_range", "speed_unit",
         "g_lat_unit", "g_lat_sign", "position_length", "fuel")


@dataclass
class Lap:
    channels: dict[str, list[float]]
    length_m: float
    time_s: float
    fuel_start_l: float | None = None
    fuel_used_l: float | None = None
    name: str = ""


def pearson(a: list[float], b: list[float]) -> float | None:
    n = len(a)
    if n < 3:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[int(round(p * (len(ordered) - 1)))]


def t_increasing(lap: Lap) -> tuple[str, str]:
    t = lap.channels.get("t")
    if not t or len(t) < 2:
        return MANUAL, "no t channel"
    if any(b <= a for a, b in zip(t, t[1:])):
        return BAD, "t does not strictly increase"
    if abs(t[-1] - lap.time_s) > 1.0:
        return BAD, f"last t {t[-1]:.2f} s is not within 1 s of the lap time {lap.time_s:.2f} s"
    return OK, ""


def dist_increasing(lap: Lap) -> tuple[str, str]:
    d = lap.channels.get("dist")
    if not d or len(d) < 2:
        return MANUAL, "no dist channel"
    back = sum(1 for a, b in zip(d, d[1:]) if b < a)
    if back > 0.01 * (len(d) - 1):
        return BAD, f"dist goes backwards in {back} of {len(d) - 1} steps"
    return OK, ""


def dist_length(lap: Lap) -> tuple[str, str]:
    d = lap.channels.get("dist")
    if not d:
        return MANUAL, "no dist channel"
    if d[0] >= 0.02 * lap.length_m:
        return BAD, f"dist starts at {d[0]:.0f} m, over 2 % of the {lap.length_m:.0f} m layout"
    if abs(d[-1] - lap.length_m) > 0.05 * lap.length_m:
        return BAD, f"dist ends at {d[-1]:.0f} m, not within 5 % of the {lap.length_m:.0f} m layout"
    return OK, ""


def pedals_range(lap: Lap) -> tuple[str, str]:
    th, br = lap.channels.get("throttle"), lap.channels.get("brake")
    if not th or not br:
        return MANUAL, "no pedal channel"
    for name, v in (("throttle", th), ("brake", br)):
        if min(v) < -0.01 or max(v) > 1.01:
            return BAD, f"{name} is {min(v):.2f}..{max(v):.2f}, not 0..1 (percent?)"
    if max(th) <= 0.5:
        return BAD, f"throttle never above 0.5 (max {max(th):.2f})"
    if max(br) <= 0.05:             # a flat-out lap (an oval) may have none: a note, not a failure
        return MANUAL, f"brake never above 0.05 (max {max(br):.2f}): fine on a lap with no braking"
    return OK, ""


def speed_unit(lap: Lap) -> tuple[str, str]:
    v = lap.channels.get("speed")
    if not v:
        return MANUAL, "no speed channel"
    if min(v) < 0 or max(v) > 500:
        return BAD, f"speed is {min(v):.1f}..{max(v):.1f}, not 0..500 km/h"
    expected = 3.6 * lap.length_m / lap.time_s
    mean = sum(v) / len(v)
    if abs(mean - expected) > 0.2 * expected:
        return BAD, (f"mean speed {mean:.1f} is not within 20 % of {expected:.1f} km/h, the "
                     "speed the length and lap time imply (m/s?)")
    return OK, ""


def g_lat_unit(lap: Lap) -> tuple[str, str]:
    g = lap.channels.get("g_lat")
    if not g:
        return MANUAL, "no g_lat channel"
    p99 = percentile([abs(x) for x in g], 0.99)
    if not 0.2 <= p99 <= 8:
        return BAD, f"99th percentile of |g_lat| is {p99:.2f}, not in 0.2..8 g (m/s2?)"
    return OK, ""


def g_lat_sign(lap: Lap) -> tuple[str, str]:
    g, steer, speed = (lap.channels.get(n) for n in ("g_lat", "steer", "speed"))
    if not g:
        return MANUAL, "no g_lat channel"
    if not steer or not speed:
        return MANUAL, "no steer channel: check the sign of g_lat in the car"
    pairs = [(s, x) for s, x, v in zip(steer, g, speed) if abs(s) > 0.05 and v > 30]
    r = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    if r is None:
        return MANUAL, "steer and g_lat could not be correlated"
    if r >= 0.3:
        return OK, f"correlation with steer {r:+.2f}"
    if r <= -0.3:
        return BAD, f"g_lat has the opposite sign of steer (correlation {r:+.2f}): flipped"
    return MANUAL, f"correlation with steer {r:+.2f} is inconclusive"


def position_length(lap: Lap) -> tuple[str, str]:
    x, z = lap.channels.get("x"), lap.channels.get("z")
    if not x or not z:
        return NA, "no position"
    path = sum(math.hypot(b - a, d - c) for a, b, c, d in zip(x, x[1:], z, z[1:]))
    if abs(path - lap.length_m) > 0.05 * lap.length_m:
        return BAD, f"the x, z path is {path:.0f} m, not within 5 % of {lap.length_m:.0f} m"
    return OK, ""


def fuel(lap: Lap) -> tuple[str, str]:
    if lap.fuel_start_l is None and lap.fuel_used_l is None:
        return NA, "no fuel"
    s, u = lap.fuel_start_l, lap.fuel_used_l
    if s is None or u is None or not 0 < u < s:
        return BAD, f"fuel_used_l {u} and fuel_start_l {s} do not satisfy 0 < used < start"
    return OK, ""


@dataclass
class Verdict:
    rule: str
    status: str                 # ok | bad | manual | n/a
    evidence: str = ""
    bad_laps: list[str] = field(default_factory=list)


def evaluate(laps: list[Lap]) -> list[Verdict]:
    """Every rule over the eligible laps. A flying lap is not always a clean one, so a rule fails
    only when at least half of the laps break it (and at least one does)."""
    out = []
    for rule in RULES:
        fn = globals()[rule]
        results = [(lap, *fn(lap)) for lap in laps]
        bad = [(lap, why) for lap, st, why in results if st == BAD]
        manual = [why for _, st, why in results if st == MANUAL]
        applicable = [r for r in results if r[1] != NA]
        if not applicable:
            out.append(Verdict(rule, NA, results[0][2] if results else "no laps"))
        elif bad and len(bad) * 2 >= len(applicable):
            names = [lap.name for lap, _ in bad]
            out.append(Verdict(rule, BAD, f"{bad[0][1]} ({len(bad)} of {len(applicable)} laps: "
                                          f"{', '.join(names)})", names))
        elif manual and len(manual) * 2 > len(applicable):
            out.append(Verdict(rule, MANUAL, manual[0]))
        else:
            out.append(Verdict(rule, OK, ""))
    return out
