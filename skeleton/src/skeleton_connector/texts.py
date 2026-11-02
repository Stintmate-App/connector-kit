"""Everything the connector says in words, in its own language (English). Nothing is
translated here: the Stintmate hub translates every catalog a connector declares."""

from __future__ import annotations

import hashlib
import json
from typing import Any

# The announcement catalog (docs/connector-protocol.md section 5; catalogs.md section 2).
ANNOUNCEMENTS: dict[str, Any] = {
    "schema": "stintmate-catalog/1",
    "catalog": "connector.skeleton",
    "language": "en",
    "language_name": "English",
    "messages": {
        "lap_done": {
            "text": "Lap {lap}, {time}.",
            "params": {"lap": "int", "time": "lap_time"},
            "audience": "driver", "priority": "low", "max_wait_s": 20, "kind": "announcement",
            "group": "laps",
            "note": "Said after a timed lap. lap = the number the simulator shows, "
                    "time = the lap time in seconds.",
        },
    },
}

# Rules for the engineer about this simulator (docs/connector-protocol.md section 5.1), English.
GUIDANCE: list[dict[str, str]] = [
    {"id": "synthetic_data", "topic": "session",
     "when": "you read laps or sessions recorded by the Skeleton connector",
     "text": "The Skeleton Simulator does not exist: its laps are synthetic, generated to show "
             "a connector's messages. Treat the data as a test, never as real driving."},
]

# Actions the engineer can send (docs/connector-protocol.md section 5.2), English: the sample is
# `test.ping`; a real action also declares what it changes (`requires_confirmation`).
ACTIONS: list[dict[str, Any]] = [
    {"code": "test.ping",
     "description": "Round trip to the connector: it answers 'pong'. Use it to prove the "
                    "connector is alive.",
     "timeout_s": 5,
     "params": {"type": "object", "additionalProperties": False, "properties": {}},
     "result": {"type": "object", "additionalProperties": False, "required": ["pong"],
                "properties": {"pong": {"type": "string", "maxLength": 64,
                                        "description": "Always 'pong'."}}},
     "example": {}},
]

# The control app's own window texts (control.catalog.get), catalog `<id>-connector`.
WINDOW: dict[str, Any] = {
    "schema": "stintmate-catalog/1",
    "catalog": "skeleton-connector",
    "language": "en",
    "language_name": "English",
    "messages": {
        "service.recorder.name": {"text": "Recorder"},
        "service.state.waiting": {"text": "Waiting for the game"},
        "service.state.recording": {"text": "Recording"},
        "service.state.stopped": {"text": "Stopped"},
    },
}


def t(key: str) -> str:
    """A window text. Literal keys only, so a test can check every key exists."""
    return WINDOW["messages"][key]["text"]


def sha12(value: Any) -> str:
    """The first 12 hex digits of the SHA-256 of the canonical JSON (the protocol's ``sha12``
    and ``rev``): sorted keys, no spaces, UTF-8."""
    canon = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def window_document() -> dict[str, Any]:
    return {"catalog": WINDOW, "rev": sha12(WINDOW)}
