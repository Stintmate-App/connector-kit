"""Every automatic check can fail: each fault below is planted in a copy of the skeleton (or in
its descriptor) and the checker must exit 1 with the named check as FAIL. A fault whose patch no
longer applies fails its own test. The faults run in parallel, one checker process each."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import SKELETON, run_cli, skeleton_descriptor

from connector_check import checks

SRC = SKELETON / "src"
GAME = r"{workspace}\connectors\{id}\fake_game"
FAST = [("control.py", "lap_period_s: float = 2.0", "lap_period_s: float = 0.5"),
        ("control.py", "game_check_s: float = 2.0", "game_check_s: float = 0.3")]
SLOW = [("control.py", "lap_period_s: float = 2.0", "lap_period_s: float = 1.5"),
        ("control.py", "game_check_s: float = 2.0", "game_check_s: float = 0.3")]
# Sets normal priority on purpose: a child inherits its parent's class, and a CI runner may be
# below normal, so only leaving out the call proves nothing there.
NO_PRIORITY = ("main.py", "BELOW_NORMAL_PRIORITY_CLASS = 0x4000", "BELOW_NORMAL_PRIORITY_CLASS = 0x0020")
QUIT_RULE = ("control.py", "if self.managed and self.parent_pid and not", "if False and not")
LAP_TIME = ("recorder.py", "            if time_s is not None:\n                params[\"time_s\"] = time_s",
            "            if True:\n                params[\"time_s\"] = time_s or 30.0")


def frame_without_player() -> str:
    """The skeleton's live frame example, with nobody marked as the player."""
    spec = importlib.util.spec_from_file_location("live_frame_example",
                                                  SKELETON / "examples" / "live_frame.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = module.frame_now()
    for car in frame["participants"]:
        car["is_player"] = False
    return json.dumps(frame)


ACTION = "        self.client.method(\"connector.action\", self._action)   # a pool thread, not inline"
WITH_FRAME = (ACTION + "\n        self.client.method(\"connector.live_frame\", lambda ctx, p: __import__(\"json\")"
              ".loads(" + repr(frame_without_player()) + "), inline=True)")


def p(file: str, old: str, new: str) -> list[tuple[str, str, str]]:
    return [(file, old, new)]


# name: (patches, descriptor changes, expected failing check, checks to run, extra options)
FAULTS: dict[str, tuple[list, dict, str, str, list[str]]] = {
    "no_endpoint": ([], {"endpoint": None}, "M1.schema", "M1", []),
    "wrong_id": ([], {"id": "other"}, "M1.identity", "M1", []),
    "bad_uninstall": ([], {"uninstall": ["C:\\nowhere\\gone.exe"]}, "M17.uninstall",
                      "M17.uninstall", []),
    "bad_icon": ([], {"icon": "\\\\server\\share\\icon.png"}, "F.icon", "F.icon",
                 ["--level", "features"]),
    "unknown_feature": ([], {"features": ["bogus"]}, "F.names", "F.names", ["--level", "features"]),
    "strict_args": ([("main.py", "args, _unknown = p.parse_known_args(argv)", "args = p.parse_args(argv)")],
                    {}, "M2.options", "M2", []),
    "ignores_instance": ([("control.py", "self.pipe = pipe or pipe_for(APP_ID)",
                           "self.pipe = pipe or __import__('stintmate_ipc').pipe_path(APP_ID)"),
                          ("main.py", 'f"{MUTEX}.{tag}" if tag else MUTEX', "MUTEX")],
                         {}, "M19.instance", "M19", []),
    "no_mutex": (p("main.py", 'f"{MUTEX}.{tag}" if tag else MUTEX', "None"), {}, "M3.single", "M3", []),
    "state_no_outbox": (p("control.py", '"outbox": {k: v', '"outbox_x": {k: v'), {}, "M4.state",
                        "M4.state", []),
    "no_service_stop": (p("control.py", '("control.service.stop", self._service_stop)',
                          '("control.service.stopx", self._service_stop)'), {}, "M5.services",
                        "M5.services", []),
    "no_managed": (p("control.py", '("control.managed", self._managed)',
                     '("control.managedx", self._managed)'), {}, "M5.managed", "M5.managed", []),
    "ignores_parent_death": (p("control.py", "if not pid_alive(pid):", "if False:"), {}, "M5.parent",
                             "M5.parent", []),
    "exits_with_parent": (p("control.py", 'log.info("The managing process %s ended; running standalone", pid)',
                            'self.exit.set()'), {}, "M5.parent", "M5.parent", []),
    "starts_alone_managed": (p("control.py", "            self._watch_parent()\n        else:\n            self._start_recorder()",
                               "            self._watch_parent()\n        if True:\n            self._start_recorder()"),
                             {}, "M5.services", "M5.services", []),
    "never_sets_phase": (FAST + [("recorder.py", 'self._set(phase="on_track")', "self._set(phase=None)"),
                                 ("recorder.py", 'phase="on_track" if lap_type != "in" else "garage"',
                                  'phase="garage"')], {}, "M8.game", "M8.game", []),
    "quit_anyone": ([QUIT_RULE], {}, "M5.stranger", "M5.stranger", []),
    "slow_quit": (p("control.py", "threading.Timer(0.1, self.exit.set)",
                    "threading.Timer(30.0, self.exit.set)"), {}, "M5.quit", "M5.quit", []),
    "service_renamed": (FAST + p("__init__.py", 'SERVICE_ID = "recorder"', 'SERVICE_ID = "rec"'),
                        {}, "M6.recorder", "M6", []),
    "always_running": (FAST + p("control.py", '"recording" if rec.recording else "waiting"',
                                '"running"'), {}, "M6.recorder", "M6", []),
    "creates_key": (p("control.py", "create=False)", "create=True)"), {}, "M7.key", "M7.key", []),
    "dies_without_key": (p("control.py", "        self.pipe = pipe or pipe_for(APP_ID)",
                           "        if not default_key_path(workspace).exists():\n"
                           "            raise SystemExit(3)\n        self.pipe = pipe or pipe_for(APP_ID)"),
                         {}, "M7.key", "M7.key", []),
    "no_retry": (p("recorder.py", "Client(pipe, peer_info(), key)",
                   "Client(pipe, peer_info(), key, reconnect=False)"), {}, "M7.absent", "M7.absent",
                 []),
    "no_status": (p("recorder.py", 'state_channel("connector.status"',
                    'state_channel("connector.statusx"'), {}, "M8.hello", "M8.hello", []),
    "game_true_late": (FAST + [("recorder.py", "self._set(game_running=up)               # before the first write, always",
                                "(self._set(game_running=up) if not up else None)"),
                               ("recorder.py", 'self._set(phase="garage", session_key=key, stint=1, live=LIVE)',
                                '__import__("time").sleep(1.0); self._set(game_running=True, phase="garage", session_key=key, stint=1, live=LIVE)')],
                       {}, "M8.game", "M8.game", []),
    "game_always_false": (FAST + p("recorder.py", "self._set(game_running=up)  ",
                                   "self._set(game_running=False)  "), {}, "M8.game", "M8.game", []),
    "link_with_game": (p("recorder.py", "        self.client.start()  ",
                         "        pass  ") + p("recorder.py", "        self._session_stop.clear()",
                                                "        self._session_stop.clear(); self.client.start()"),
                       {}, "M9.link", "M9.link", []),
    "never_refused": (p("recorder.py", 'REFUSED = ("unauthorized", "bad_request", "busy", "connector_busy", "connector_id_taken")',
                        "REFUSED = ()"), {}, "M9.state", "M9.state", []),
    "no_product_in_hello": (p("recorder.py", '"product": PRODUCT})', '})'), {}, "M20.product",
                            "M20.product", []),
    "product_differs": ([], {"product": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed"}, "M20.product",
                        "M20.product", []),
    "id_taken_not_refused": (p("recorder.py", 'REFUSED = ("unauthorized", "bad_request", "busy", "connector_busy", "connector_id_taken")',
                               'REFUSED = ("unauthorized", "bad_request", "busy", "connector_busy")'),
                             {}, "M20.taken", "M20.taken", []),
    "id_taken_retried_at_once": (p("recorder.py", "ID_TAKEN_WAIT_S = 75.0", "ID_TAKEN_WAIT_S = 0.0"),
                                 {}, "M20.taken", "M20.taken", []),
    "registration_overwrites": ([], {}, "M20.registration", "M20.registration",
                                ["--register-command", json.dumps([
                                    sys.executable, "-c",
                                    "import sys, pathlib; pathlib.Path(sys.argv[1], 'skeleton.json')"
                                    ".write_text('x')", "{connectors_dir}"])]),
    "registration_says_nothing": ([], {}, "M20.registration", "M20.registration",
                                  ["--register-command", json.dumps([sys.executable, "-c", "pass"])]),
    "no_length": (FAST + p("session.py", '"length_m": TRACK_LENGTH_M}]}', '"length_m": None}]}'),
                  {}, "M10.order", "M10.order", []),
    "no_end": (FAST + p("recorder.py", 'self._write("session_end", ', '(lambda *a: None)("session_end", '),
               {}, "M10.order", "M10.order", []),
    "sends_stint": (FAST + p("recorder.py", '"seq": seq, "lap_type": lap_type,',
                             '"seq": seq, "stint": 1, "lap_type": lap_type,'), {}, "M10.order",
                    "M10.order", []),
    "flying_after_exit": (FAST + p("session.py", '("lap", "out", None, True),          # out lap: not timed',
                                   '("lap", "flying", 61.5, True),'), {}, "M10.laps", "M10.laps", []),
    "flying_before_exit": (FAST + p("session.py", '("lap", "in", None, True),           # ended in the pits: not timed',
                                    '("lap", "flying", 61.5, True),'), {}, "M10.laps", "M10.laps", []),
    "no_g_lat": (FAST + p("session.py", '"g_lat": _f32(', '"ext_g_lat": _f32('), {}, "M11.channels",
                 "M11", []),
    "g_lat_flipped": (FAST + p("session.py", '"g_lat": _f32([2.0 * s', '"g_lat": _f32([-2.0 * s'),
                      {}, "M12.sanity", "M12.sanity", []),
    "speed_ms": (FAST + p("session.py", "mean_kmh = 3.6 * TRACK_LENGTH_M / lap_s",
                          "mean_kmh = TRACK_LENGTH_M / lap_s"), {}, "M12.sanity", "M12.sanity", []),
    "pedals_percent": (FAST + p("session.py", '"throttle": _f32([min(', '"throttle": _f32([100 * min('),
                       {}, "M12.sanity", "M12.sanity", []),
    "dist_reversed": (FAST + p("session.py", '"dist": _f32([x * TRACK_LENGTH_M',
                               '"dist": _f32([(1 - x) * TRACK_LENGTH_M'), {}, "M12.sanity", "M12.sanity", []),
    "t_not_increasing": (FAST + p("session.py", '"t": _f32([x * lap_s for x in u])',
                                  '"t": _f32([0.0 for x in u])'), {}, "M12.sanity", "M12.sanity", []),
    "no_prefix": (FAST + p("recorder.py", 'f"{CONNECTOR_ID}/{key}/lap/{seq}"', 'f"lap-{key}-{seq}"'),
                  {}, "M13.keys", "M13.keys", []),
    "drops_on_busy": (FAST + p("outbox.py", 'NON_RETRYABLE = ("bad_request",',
                               'NON_RETRYABLE = ("busy", "writer_busy", "timeout", "shutting_down", '
                               '"unavailable", "connector_busy", "bad_request",'),
                      {}, "M13.retry", "M13.retry", []),
    "new_key_on_retry": (FAST + p("outbox.py", "idempotency_key=ik)", "idempotency_key=ik + os.urandom(2).hex())"),
                         {}, "M13.retry", "M13.retry", []),
    "retries_too_large": (FAST + p("outbox.py", '"unknown_session", "too_large",', '"unknown_session",'),
                          {}, "M13.refused", "M13.refused", []),
    "drops_unknown_code": (FAST + p("outbox.py", "if exc.code in NON_RETRYABLE:",
                                    'if exc.code not in ("busy", "writer_busy", "timeout", '
                                    '"shutting_down", "unavailable", "connector_busy"):'),
                           {}, "M13.retry", "M13.retry", []),
    "ignores_retry_ms": (FAST + p("outbox.py", "wait = RETRY_DEFAULT_S if exc.retry_ms is None else exc.retry_ms / 1000",
                                  "wait = 0.05"), {}, "M13.retry", "M13.retry", []),
    "stops_on_refusal": (FAST + p("outbox.py", "if exc.code in NON_RETRYABLE:", "if False:"), {},
                         "M13.refused", "M13.refused", []),
    "memory_outbox": (SLOW + p("outbox.py", "self._items.extend(sorted(found))", "self._items.extend([])"),
                      {}, "M14.outbox", "M14", []),
    "constant_outbox": (SLOW + p("control.py", "{k: v for k, v in rec.outbox.summary().items() if k != \"rejected\"}",
                                 '{"pending": 0, "oldest_at": None}'), {}, "M14.outbox", "M14", []),
    "busy_loop": (p("recorder.py", "if self._stop.wait(self.game_check_s):", "if self._stop.is_set():"),
                  {}, "M15.cpu", "M15.cpu", []),
    "spawns_per_check": (p("control.py", "game_check_s: float = 2.0", "game_check_s: float = 0.3")
                         + p("game.py", "        return self.flag.exists()",
                             "        __import__('subprocess').run([__import__('sys').executable, '-c', 'pass'],"
                             " capture_output=True)\n        return self.flag.exists()"),
                         {}, "M15.cpu", "M15.cpu", []),
    "normal_priority": ([NO_PRIORITY], {}, "M15.priority", "M15.priority", []),
    "no_log_folder": (FAST + p("main.py", 'log_dir / "control.log"', 'log_dir / "other.log"'), {},
                      "M16.logs", "M16.logs", []),
    "no_pack_loader": (p("texts.py", 'return PACK.get(key) or WINDOW["messages"][key]["text"]',
                         'return WINDOW["messages"][key]["text"]'), {}, "M18.texts", "M18", []),
    "no_ping": (p("recorder.py", 'if params.get("code") == "test.ping":', "if False:"),
                {"features": ["actions"]}, "F.actions", "F.actions", ["--level", "features"]),
    "no_guidance": (p("recorder.py", '("pitwall.guidance.declare", {"items": GUIDANCE},',
                      '("pitwall.guidance.declarex", {"items": GUIDANCE},'),
                    {"features": ["guidance"]}, "F.guidance", "F.guidance", ["--level", "features"]),
    "no_announcements": (p("recorder.py", '("pitwall.catalog.declare", {"catalog": ANNOUNCEMENTS},',
                           '("pitwall.catalog.declarex", {"catalog": ANNOUNCEMENTS},'),
                         {"features": ["announcements"]}, "F.announcements", "F.announcements",
                         ["--level", "features"]),
    "frame_no_player": (p("recorder.py", ACTION, WITH_FRAME), {"features": ["live_frame"]},
                        "F.live_frame", "F.live_frame", ["--level", "features"]),
    "no_capture_target": ([], {"features": ["capture_target"]}, "F.capture_target",
                          "F.capture_target", ["--level", "features"]),
    "no_brake_cues": ([], {"features": ["brake_cues"]}, "F.brake_cues", "F.brake_cues",
                      ["--level", "features"]),
}


def faulty_skeleton(tmp: Path, patches: list[tuple[str, str, str]]) -> Path:
    """A copy of the skeleton's source with each (file, old, new) applied; `old` must be there."""
    dest = tmp / "src"
    shutil.copytree(SRC, dest, ignore=shutil.ignore_patterns("__pycache__"))
    for file, old, new in patches:
        path = dest / "skeleton_connector" / file
        text = path.read_text(encoding="utf-8")
        assert old in text, f"the patch no longer applies to {file}: {old!r}"
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return dest


def run_fault(name: str, root: Path, spec: tuple | None = None) -> tuple[int, dict]:
    """Run the checker on the skeleton with `spec` (default: the fault `name`) applied."""
    patches, changes, _expected, only, extra = spec or FAULTS[name]
    tmp = root / name
    tmp.mkdir()
    src = faulty_skeleton(tmp, patches)
    path = skeleton_descriptor(tmp, launcher=sys.executable, args=["-m", "skeleton_connector.main"])
    desc = json.loads(path.read_text())
    for key, value in changes.items():
        if value is None:
            desc.pop(key, None)
        else:
            desc[key] = value
    if desc["id"] != "skeleton":
        path.rename(tmp / f"{desc['id']}-was-skeleton.json")
        path = tmp / f"{desc['id']}-was-skeleton.json"
    path.write_text(json.dumps(desc), encoding="utf-8")
    report = tmp / "report.json"
    done = subprocess.run(
        [sys.executable, "-m", "connector_check.cli", "--descriptor", str(path), "--only", only,
         "--fake-game-file", GAME, "--cpu-seconds", "5", "--link-seconds", "5",
         "--start-timeout", "25", "--session-timeout", "90", "--report", str(report), *extra],
        env={**os.environ, "PYTHONPATH": str(src)}, capture_output=True, text=True, timeout=600)
    doc = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return done.returncode, {c["id"]: c for c in doc.get("checks", [])} | {"_out": done.stdout}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    root = tmp_path_factory.mktemp("faults")
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {name: pool.submit(run_fault, name, root) for name in FAULTS}
        return {name: f.result() for name, f in futures.items()}


@pytest.mark.parametrize("name", sorted(FAULTS))
def test_a_planted_fault_fails_its_check(name, outcomes):
    code, got = outcomes[name]
    expected = FAULTS[name][2]
    assert got.get(expected, {}).get("status") == "FAIL", got.get("_out")
    assert code == 1, got.get("_out")


def test_every_automatic_check_has_a_fault():
    automatic = {c.id for c in checks.CHECKS if not c.manual}
    covered = {f[2] for f in FAULTS.values()} | {"M4.transport"}
    assert automatic - covered == set()


def test_a_dead_pipe_fails_the_transport_check(tmp_path, monkeypatch):
    real = checks.process.run

    def aim_at_a_dead_pipe(argv, env, log_path, timeout):
        argv[5] = argv[5] + "-dead"                       # the pipe argument of `server`
        return real(argv, env, log_path, timeout)

    monkeypatch.setattr(checks.process, "run", aim_at_a_dead_pipe)
    code, out = run_cli("--descriptor", str(skeleton_descriptor(tmp_path)), "--only", "M4.transport",
                        "--start-timeout", "30")
    assert "FAIL  M4.transport" in out and code == 1
