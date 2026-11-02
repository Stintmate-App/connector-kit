"""Options the user changes: a ``settings.json`` read once at start, defaults in code.

Use it from ``control.py`` or ``recorder.py``::

    options = load_options(workspace / "connectors" / CONNECTOR_ID / "settings.json")

What the user can choose is configuration the user manages (a file, or the control app's
window), and the code applies it as written. A missing or unreadable file means the defaults; an
unknown key is ignored; a value of the wrong type keeps the default for that key.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("skeleton_connector")

DEFAULTS: dict[str, Any] = {
    "lap_period_s": 2.0,        # seconds between the synthetic laps
    "announce_laps": True,      # say the lap time after each timed lap
}


def _fits(value: Any, default: Any) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(default))


def load_options(path: Path) -> dict[str, Any]:
    options = dict(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return options
    except (OSError, ValueError) as exc:
        log.warning("%s cannot be read, using the defaults: %s", path, exc)
        return options
    if not isinstance(data, dict):
        log.warning("%s is not a JSON object, using the defaults", path)
        return options
    for key, default in DEFAULTS.items():
        if key not in data:
            continue
        if _fits(data[key], default):
            options[key] = data[key]
        else:
            log.warning("%s: %r has the wrong type, kept %r", path, key, default)
    return options
