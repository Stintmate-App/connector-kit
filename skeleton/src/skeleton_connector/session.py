"""What a session holds: the catalog, the script of laps and their channels.

REPLACE THIS with what your simulator gives. Everything here is synthetic. The channels are
the part to read twice: units and signs are part of the contract (docs/data-model.md), and
the analysis trusts them without a check.
"""

from __future__ import annotations

import math
from array import array
from typing import Any

TRACK_LENGTH_M = 1000.0
SAMPLES = 200
CATALOG = {"classes": [{"class_id": "GT", "name": "Grand Touring"}],
           "cars": [{"car_id": "car_a", "name": "Skeleton GT", "class_id": "GT"}],
           "tracks": [{"track_id": "ring", "name": "Skeleton Ring"}],
           "layouts": [{"track_id": "ring", "layout_id": "full", "name": "Skeleton Ring Full",
                        "length_m": TRACK_LENGTH_M}]}
LIVE = {"session_type": "practice", "car_id": "car_a", "track_id": "ring", "layout_id": "full",
        "track_length_m": TRACK_LENGTH_M}

# The synthetic session: (kind, lap_type, time_s, valid). `pit_exit` is a fact, not a lap.
# Laps that ended before an exit are written before it (docs/connector-protocol.md 11.2).
SCRIPT: list[tuple[str, str | None, float | None, bool]] = [
    ("pit_exit", None, None, True),      # leaving the garage: stint 1 starts with the session
    ("lap", "out", None, True),          # out lap: not timed
    ("lap", "flying", 61.234, True),
    ("lap", "flying", 60.987, False),    # invalidated by the simulator: still sent
    ("lap", "in", None, True),           # ended in the pits: not timed
    ("pit_exit", None, None, True),      # after a stop on track: stint 2
    ("lap", "out", None, True),
    ("lap", "flying", 60.512, True),
]


def _f32(values: list[float]) -> dict[str, Any]:
    return {"encoding": "f32", "data": array("f", values).tobytes()}


def channels(lap_s: float = 60.0) -> dict[str, Any]:
    """One lap of samples. Units are the contract's: `t` seconds from the lap start, `dist`
    metres from the line, `speed` km/h, `throttle` and `brake` 0..1, `steer` -1..1 (positive
    to the right), `g_lat` in g with the SAME sign as `steer`, `x` and `z` metres. Check each
    one against your game; a wrong unit or sign gives wrong analysis and no error."""
    u = [i / SAMPLES for i in range(SAMPLES)]
    mean_kmh = 3.6 * TRACK_LENGTH_M / lap_s              # what the lap time and length imply
    steer = [math.sin(4 * math.pi * x) for x in u]       # two corners to each side
    radius = TRACK_LENGTH_M / (2 * math.pi)
    return {"t": _f32([x * lap_s for x in u]),
            "dist": _f32([x * TRACK_LENGTH_M for x in u]),
            "speed": _f32([mean_kmh * (1 + 0.3 * math.cos(4 * math.pi * x)) for x in u]),
            "throttle": _f32([min(1.0, max(0.0, 0.5 + math.cos(4 * math.pi * x))) for x in u]),
            "brake": _f32([min(1.0, max(0.0, -0.3 - math.cos(4 * math.pi * x)) * 1.5)
                           for x in u]),
            "steer": _f32(steer),
            "g_lat": _f32([2.0 * s for s in steer]),
            "x": _f32([radius * math.cos(2 * math.pi * x) for x in u]),
            "z": _f32([radius * math.sin(2 * math.pi * x) for x in u])}
