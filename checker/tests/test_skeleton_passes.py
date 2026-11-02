"""The skeleton, unchanged, passes every automatic check, while the real Stintmate apps may be
running on the same machine: the checker uses pipes, a mutex, a workspace and a descriptor folder
of its own and touches nothing of theirs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import stintmate_ipc as ipc
from conftest import skeleton_descriptor
from test_runtime import leftovers

from connector_check.report import FAIL, PASS


def real_pipes() -> set[str]:
    """Every pipe that is not one a checker run made (those carry the tag `-cc...`)."""
    return {n for n in os.listdir(r"\\.\pipe") if n.startswith("stintmate-") and "-cc" not in n}


def real_files() -> dict[str, float]:
    """The modification times of the real workspace's key and settings, read only."""
    ws = ipc.find_workspace()
    if ws is None:
        return {}
    out = {}
    for rel in (("ipc", "key"), ("hub", "settings.json")):
        path = Path(ws, *rel)
        if path.exists():
            out["/".join(rel)] = path.stat().st_mtime
    return out


def test_the_skeleton_passes_beside_a_real_stintmate(tmp_path):
    pipes_before, files_before = real_pipes(), real_files()
    descriptor = skeleton_descriptor(tmp_path)
    report = tmp_path / "report.json"
    done = subprocess.run(
        [sys.executable, "-m", "connector_check.cli", "--descriptor", str(descriptor),
         "--fake-game-file", r"{workspace}\connectors\{id}\fake_game", "--cpu-seconds", "5",
         "--link-seconds", "10", "--start-timeout", "30", "--report", str(report)],
        capture_output=True, text=True, timeout=900)
    doc = json.loads(report.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in doc["checks"]}
    automatic = {i: c for i, c in by_id.items() if c["status"] != "MANUAL"}
    assert {i: c["status"] for i, c in automatic.items() if c["status"] != PASS} == {}, done.stdout
    assert done.returncode == 0 and doc["exit_code"] == 0, done.stdout
    assert FAIL not in {c["status"] for c in by_id.values()}
    assert len(automatic) == 32
    # Nothing of the real apps was touched, and nothing was left running.
    assert real_pipes() == pipes_before
    assert real_files() == files_before
    assert leftovers() == []
