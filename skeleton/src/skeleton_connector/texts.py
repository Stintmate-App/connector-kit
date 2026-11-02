"""Everything the connector says in words, in its own language (English). Nothing is
translated here: the Stintmate hub translates every catalog a connector declares and writes the
result to the user's language pack; :func:`load_pack` reads the window's texts back from it."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("skeleton_connector")

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


PACK: dict[str, str] = {}      # key -> text of the user's language, filled once by load_pack()

TAG = re.compile(r"^[a-z]{2,3}(-[A-Z][a-z]{3})?(-([A-Z]{2}|[0-9]{3}))?$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _normal_tag(raw: Any) -> str:
    """A BCP 47 tag with its case normalised (language lower, script title, region upper), or
    ``en`` when it is not one (docs/catalogs.md section 6)."""
    if not isinstance(raw, str):
        return "en"
    parts = raw.strip().replace("_", "-").split("-")
    fixed = [parts[0].lower()]
    for part in parts[1:]:
        fixed.append(part.title() if len(part) == 4 and part.isalpha() else part.upper())
    tag = "-".join(fixed)
    return tag if TAG.match(tag) else "en"


def language(workspace: Path) -> str:
    """``language`` in ``<workspace>\\hub\\settings.json``; ``en`` when the file or the field is
    missing, unreadable or not a tag (docs/descriptor.md, "Languages")."""
    try:
        data = json.loads((workspace / "hub" / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "en"
    return _normal_tag(data.get("language")) if isinstance(data, dict) else "en"


def chain(tag: str) -> list[str]:
    """``es-CL`` -> ``es-CL``, ``es``, ``en`` (the built-in English comes last, in code)."""
    out, parts = [], tag.split("-")
    while parts:
        out.append("-".join(parts))
        parts.pop()
    return out + ([] if "en" in out else ["en"])


def _pack_file(workspace: Path, tag: str, name: str) -> Path | None:
    """``<workspace>\\locales\\<tag>\\<name>.json``; folder names compare without case."""
    try:
        for folder in (workspace / "locales").iterdir():
            if folder.is_dir() and folder.name.lower() == tag.lower():
                path = folder / f"{name}.json"
                return path if path.is_file() else None
    except OSError:
        pass
    return None


def _read_catalog(path: Path, tag: str) -> dict[str, str] | None:
    """The valid texts of one pack file, or None when the file as a whole cannot be used
    (not JSON, wrong ``schema``, ``catalog`` or ``language``): docs/catalogs.md sections 2, 6."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Language pack %s skipped: %s", path, exc)
        return None
    if (not isinstance(data, dict) or data.get("schema") != "stintmate-catalog/1"
            or data.get("catalog") != WINDOW["catalog"]
            or not isinstance(data.get("language"), str)
            or data["language"].lower() != tag.lower()
            or not isinstance(data.get("messages"), dict)):
        log.warning("Language pack %s skipped: not a %s catalog in %s",
                    path, WINDOW["catalog"], tag)
        return None
    texts: dict[str, str] = {}
    for key, message in data["messages"].items():
        text = message.get("text") if isinstance(message, dict) else None
        # The window's texts take no placeholders: braces and control characters are errors.
        if (key in WINDOW["messages"] and isinstance(text, str) and text.strip()
                and not CONTROL.search(text) and "{" not in text and "}" not in text):
            texts[key] = text
        else:
            log.warning("Language pack %s: text %r ignored", path, key)
    return texts


def load_pack(workspace: str | Path | None) -> dict[str, str]:
    """Read the user's language pack once, at start. Per key the first valid text along the
    chain of the language (``es-CL``, ``es``, ``en``) wins; the built-in English is the last
    fallback. A file that is missing or bad is skipped (and logged); nothing here raises."""
    PACK.clear()
    if workspace is None:
        return PACK
    root = Path(workspace)
    for tag in chain(language(root)):
        path = _pack_file(root, tag, WINDOW["catalog"])
        found = _read_catalog(path, tag) if path else None
        for key, text in (found or {}).items():
            PACK.setdefault(key, text)
    return PACK


def t(key: str) -> str:
    """A window text. Literal keys only, so a test can check every key exists."""
    return PACK.get(key) or WINDOW["messages"][key]["text"]


def sha12(value: Any) -> str:
    """The first 12 hex digits of the SHA-256 of the canonical JSON (the protocol's ``sha12``
    and ``rev``): sorted keys, no spaces, UTF-8."""
    canon = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def window_document() -> dict[str, Any]:
    return {"catalog": WINDOW, "rev": sha12(WINDOW)}
