"""``connector.live_frame``: what is happening on track now, answered from memory.

Add to ``recorder.py``, in ``__init__`` before ``self.client.start()``::

    self.client.method("connector.live_frame", lambda ctx, p: frame_now(), inline=True)

``inline=True`` answers on the connection's own thread: only do that when the answer is read
from memory, never from the game. Pitwall's deadline is 1 s for the agent's tools and 0.3 s
after each stored lap. With the game closed, raise ``RemoteError("game_not_running", ...)``.
The frame's fields, units and rules: docs/connector-protocol.md section 7;
the schema: docs/live-frame.schema.json.
"""

from __future__ import annotations

from typing import Any


def frame_now() -> dict[str, Any]:
    """A frame on track. Times are seconds and ``None`` when the simulator does not give them
    (never -1 or 0 as "unknown"). At least one participant has ``is_player: true``."""
    return {
        "sim": "skeletonsim",
        "phase": "on_track",
        "session": {"type": "practice", "time_remaining_s": 1200.0, "laps_in_event": None,
                    "duration_s": 1800, "additional_laps": 0},
        "track": {"track_id": "ring", "layout_id": "full", "length_m": 1000.0},
        "car": {"car_id": "car_a", "pit_mode": 0, "speed_kmh": 180.0, "gear": 4,
                "rpm": 6500, "fuel_l": 42.0, "fuel_capacity_l": 60.0},
        "lap": {"current_lap": 3, "laps_completed": 2, "distance_m": 420.0, "time_s": 24.0,
                "valid": True, "sector": 1, "last_lap_s": 60.2, "best_lap_s": 59.8},
        "participants": [
            {"index": 0, "name": "Player", "is_player": True, "is_safety_car": False,
             "active": True, "position": 1, "laps_completed": 2, "current_lap": 3,
             "distance_m": 420.0, "sector": 1, "pit_mode": 0, "car_id": "car_a",
             "class_id": None, "last_lap_s": 60.2, "best_lap_s": 59.8},
        ],
    }
