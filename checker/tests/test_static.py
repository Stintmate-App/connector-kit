"""The report, the command line and the descriptor checks (M1, M17)."""

from __future__ import annotations

import json

from conftest import run_cli, skeleton_descriptor

from connector_check.report import FAIL, MANUAL, PASS, SKIP, Check, Report


def lines_by_id(out: str) -> dict[str, str]:
    return {ln.split("  ")[1]: ln.split("  ")[0] for ln in out.splitlines()
            if ln.split("  ")[0] in (PASS, FAIL, SKIP, MANUAL)}


def test_temporary_workspace_has_the_manifest(tmp_path):
    from connector_check.cli import make_workspace
    ws = make_workspace(tmp_path)
    assert ws.is_dir()
    assert json.loads((ws / "stintmate.json").read_text(encoding="utf-8")) == {"schema": 1}


def test_report_exit_codes_and_worst_outcome_wins():
    checks = [Check("A.one", "one", "g"), Check("A.two", "two", "g"),
              Check("A.man", "manual", "g", manual=True)]
    rep = Report.for_checks(checks, lambda c: True)
    rep.passed("A.one", "ok")
    assert rep.exit_code() == 3                       # A.two never ran: skipped
    rep.finish()
    assert rep.results["A.two"].status == SKIP and rep.exit_code() == 3
    rep.passed("A.two")
    assert rep.exit_code() == 0
    rep.failed("A.one", "bad\nthing")
    rep.passed("A.one", "later pass")                 # a FAIL stays
    assert rep.exit_code() == 1 and rep.results["A.one"].evidence == "bad thing"
    assert rep.results["A.man"].status == MANUAL
    assert json.loads(rep.to_json())["counts"]["FAIL"] == 1


def test_only_picks_by_id_or_by_requirement(descriptor):
    code, out = run_cli("--descriptor", str(descriptor), "--only", "M1")
    got = lines_by_id(out)
    assert set(got) == {"M1.schema", "M1.identity"}
    assert got == {"M1.schema": PASS, "M1.identity": PASS}
    assert code == 0
    code, out = run_cli("--descriptor", str(descriptor), "--only", "M17.uninstall")
    assert lines_by_id(out) == {"M17.uninstall": PASS} and code == 0


def test_a_bad_descriptor_fails_each_static_check(tmp_path):
    path = skeleton_descriptor(tmp_path, id="other", endpoint="connector-x", mutex="Global\\X",
                               launcher="relative.exe", uninstall=["relative.exe"])
    del_keys = json.loads(path.read_text())
    del del_keys["services"]
    path.write_text(json.dumps(del_keys))
    code, out = run_cli("--descriptor", str(path), "--only", "M1,M17.uninstall")
    got = lines_by_id(out)
    assert got == {"M1.schema": FAIL, "M1.identity": FAIL, "M17.uninstall": FAIL}
    assert "services" in out and "not the file name" in out and code == 1


def test_a_missing_descriptor_cannot_run(tmp_path):
    code, _ = run_cli("--descriptor", str(tmp_path / "nope.json"))
    assert code == 2


def test_no_uninstall_key_is_left_to_the_person(tmp_path):
    path = skeleton_descriptor(tmp_path)
    doc = json.loads(path.read_text())
    del doc["uninstall"]
    path.write_text(json.dumps(doc))
    code, out = run_cli("--descriptor", str(path), "--only", "M17.uninstall")
    assert lines_by_id(out) == {"M17.uninstall": MANUAL} and "not declared" in out and code == 0


def test_child_env_drops_the_real_workspace(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from connector_check import checks
    monkeypatch.setenv("STINTMATE_WORKSPACE", str(tmp_path / "real"))
    ctx = SimpleNamespace(scratch={"tmp": tmp_path}, args=SimpleNamespace(instance_env="X_TAG"),
                          tag="t1")
    env = checks.child_env(ctx)
    assert "STINTMATE_WORKSPACE" not in env and env["X_TAG"] == "t1"
    assert env["APPDATA"].startswith(str(tmp_path))
