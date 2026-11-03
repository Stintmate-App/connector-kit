"""Shared helpers for the checker's tests. The kit's root is two folders above ``checker/``."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1]
KIT = CHECKER.parent
SKELETON = KIT / "skeleton"


def skeleton_descriptor(tmp_path: Path, **override) -> Path:
    """A descriptor for the skeleton in this clone, run with the skeleton's own venv launcher."""
    launcher = SKELETON / ".venv" / "Scripts" / "skeleton-connector.exe"
    desc = {"schema": 1, "id": "skeleton", "name": "Skeleton connector",
            "product": "6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43", "sim": "skeletonsim",
            "sim_name": "Skeleton Simulator", "version": "1.0.0", "launcher": str(launcher),
            "args": [], "control_protocol": "stintmate-ipc/2", "endpoint": "connector-skeleton",
            "mutex": "Local\\SkeletonConnector.Control",
            "services": [{"id": "recorder", "name": "Recorder"}], "logs": ["control.log"],
            "uninstall": [os.path.join(os.environ.get("SystemRoot", "C:\\Windows"),
                                       "System32", "cmd.exe")],
            "window": False, "features": [], **override}
    path = tmp_path / "skeleton.json"
    path.write_text(json.dumps(desc), encoding="utf-8")
    return path


def run_cli(*args: str) -> tuple[int, str]:
    """Run ``connector-check`` in this interpreter and return (exit code, stdout)."""
    import contextlib
    import io

    from connector_check.cli import main
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args))
    return code, out.getvalue()


@pytest.fixture
def descriptor(tmp_path):
    return skeleton_descriptor(tmp_path)


__all__ = ["CHECKER", "KIT", "SKELETON", "skeleton_descriptor", "run_cli", "sys"]
