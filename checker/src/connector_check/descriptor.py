"""The descriptor checks (M1, M17) and the command line a managing app runs."""

from __future__ import annotations

import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any

import jsonschema

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def load(path: Path) -> tuple[dict[str, Any] | None, str]:
    """The descriptor as a dict, or None and why not."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return None, f"cannot read {path}: {exc}"
    if not isinstance(doc, dict):
        return None, f"{path} is not a JSON object"
    return doc, ""


def is_absolute(text: Any) -> bool:
    return isinstance(text, str) and PureWindowsPath(text).is_absolute()


def schema_errors(desc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}"
            for e in sorted(validator.iter_errors(desc), key=lambda e: list(e.absolute_path))]


def identity_problems(desc: dict[str, Any], path: Path) -> list[str]:
    out = []
    cid = desc.get("id")
    if cid != path.stem:
        out.append(f"id {cid!r} is not the file name {path.stem!r}")
    if not isinstance(cid, str) or not ID_PATTERN.match(cid):
        out.append(f"id {cid!r} does not match {ID_PATTERN.pattern}")
    if desc.get("endpoint") != f"connector-{cid}":
        out.append(f"endpoint {desc.get('endpoint')!r} is not 'connector-{cid}'")
    mutex = desc.get("mutex")
    if not isinstance(mutex, str) or not mutex.startswith("Local\\"):
        out.append(f"mutex {mutex!r} does not start with 'Local\\'")
    launcher = desc.get("launcher")
    if not is_absolute(launcher):
        out.append(f"launcher {launcher!r} is not an absolute path")
    elif not Path(launcher).exists():
        out.append(f"launcher {launcher} does not exist")
    return out


def uninstall_problems(desc: dict[str, Any]) -> list[str]:
    cmd = desc.get("uninstall")
    if cmd is None:
        return ["no 'uninstall' key"]
    if not isinstance(cmd, list) or not cmd or not all(isinstance(x, str) for x in cmd):
        return ["'uninstall' is not an array of strings"]
    if not is_absolute(cmd[0]):
        return [f"uninstall[0] {cmd[0]!r} is not an absolute path"]
    if not Path(cmd[0]).exists():
        return [f"uninstall[0] {cmd[0]} does not exist"]
    return []


def unknown_features(desc: dict[str, Any], known: list[str]) -> list[str]:
    declared = desc.get("features") or []
    if not isinstance(declared, list):
        return ["(not an array)"]
    return [f for f in declared if f not in known]


def launch_argv(desc: dict[str, Any], workspace: Path, parent_pid: int,
                extra: list[str] | None = None) -> list[str]:
    """What the managing app runs: launcher + args + the trailing options."""
    return [desc["launcher"], *desc.get("args", []), "--workspace", str(workspace), "--managed",
            "--parent-pid", str(parent_pid), *(extra or [])]
