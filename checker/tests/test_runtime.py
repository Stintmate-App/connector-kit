"""The checks that start the connector (M2, M3, M4, M5, M19) against the skeleton, and the rule
that nothing the checker starts outlives it."""

from __future__ import annotations

import os

import psutil
from conftest import run_cli

from connector_check.report import PASS


def leftovers() -> list[str]:
    """Processes whose command line names a checker's temporary folder."""
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        line = " ".join(p.info["cmdline"] or [])
        if "connector-check-" in line and p.info["pid"] != os.getpid() and "pytest" not in line:
            out.append(line[:120])
    return out


def status_of(out: str) -> dict[str, str]:
    return {ln.split("  ")[1]: ln.split("  ")[0] for ln in out.splitlines()
            if ln.split("  ")[0] in ("PASS", "FAIL", "SKIP", "MANUAL")}


def test_the_skeleton_starts_is_single_and_quits(descriptor):
    code, out = run_cli("--descriptor", str(descriptor), "--only", "M2,M19,M3,M4,M5",
                        "--start-timeout", "30")
    got = status_of(out)
    for check in ("M2.options", "M19.instance", "M3.single", "M4.transport", "M4.state",
                  "M5.services", "M5.managed", "M5.stranger", "M5.quit"):
        assert got[check] == PASS, out
    assert code == 0, out
    assert leftovers() == []


def test_a_launcher_that_does_not_exist_fails_to_start_and_leaves_nothing(tmp_path):
    from conftest import skeleton_descriptor
    path = skeleton_descriptor(tmp_path, launcher=str(tmp_path / "missing.exe"))
    code, out = run_cli("--descriptor", str(path), "--only", "M2,M3", "--start-timeout", "5")
    got = status_of(out)
    assert got["M2.options"] == "FAIL" and got["M3.single"] == "SKIP" and code == 1
    assert leftovers() == []
