"""``pitwall.write.setup``: the setup the user chose, at the session start and at each pit exit.

Add to ``recorder.py``, in ``_play``: after the ``session_start`` write, and after every
``pit_exit`` write (the setup then lands in the new stint)::

    self._write("setup", setup_params(key, seq, "session_start"),
                f"{CONNECTOR_ID}/{key}/setup/{seq}")

``seq`` counts the setups of the session from 1; the idempotency key is
``<id>/<session_key>/setup/<seq>`` (docs/connector-protocol.md section 3.1). Send only what the
user set, never readings (docs/write-api.md). The basic parameters are fields; every other
parameter goes in ``extra``, keyed by your own key, each with a ``label`` and a ``value``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def setup_params(session_key: str, seq: int, reason: str,
                 taken_at: datetime | None = None) -> dict[str, Any]:
    """``reason`` is ``session_start``, ``pit_exit`` or ``engineer``."""
    moment = (taken_at or datetime.now().astimezone()).isoformat(timespec="seconds")
    return {
        "session_key": session_key, "seq": seq, "taken_at": moment, "reason": reason,
        "source": "telemetry",
        "brake_bias_front_pct": 56.5,
        "compound_front": "medium", "compound_rear": "medium",
        "pressure_fl_kpa": 180.0, "pressure_fr_kpa": 180.0,
        "pressure_rl_kpa": 175.0, "pressure_rr_kpa": 175.0,
        "fuel_l": 50.0, "wing_front": 4, "wing_rear": 7, "abs": 3, "tc": 2,
        "extra": {
            "diff_preload": {"label": "Differential preload", "value": 80, "unit": "Nm",
                             "group": "drivetrain"},
            "engine_map": {"label": "Engine map", "value": "rich"},
        },
    }
