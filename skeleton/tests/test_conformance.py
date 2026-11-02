"""The skeleton works with only the contract.

A fake Pitwall built from the names in the documents (strings below: nothing is imported from
Pitwall or the hub) in a temporary workspace that holds ``ipc\\key`` (the connector only
reads it), on unique pipe names:

- the recorder's link to ``pitwall``: the hello, the declarations, the writes in the order and
  with the fields docs/connector-protocol.md sections 3 and 11 give, the catalog and the guidance;
- the control endpoint ``connector-skeleton`` (docs/contract.md 3 and 5.6) as a hub uses it;
- ``stintmate-ipc-check server`` (the package's checker) against that endpoint;
- the descriptor ``install.ps1`` writes.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from array import array
from pathlib import Path

import pytest
from stintmate_ipc import (KeyFile, PeerInfo, PipeNotFound, RemoteError, connect, pipe_path,
                           pipe_user)
from stintmate_ipc.check.cli import main as ipc_check

import skeleton_connector as S
from skeleton_connector.control import ControlApp
from skeleton_connector.main import parse, setup_logging
from skeleton_connector.recorder import Recorder
from skeleton_connector.session import SCRIPT, TRACK_LENGTH_M, channels
from skeleton_connector.texts import ACTIONS, ANNOUNCEMENTS, GUIDANCE, WINDOW, sha12, t
from support import pipe, wait

ROOT = Path(__file__).resolve().parents[1]
# Names from the contract documents.
CONTROL_REQUIRED = {"control.quit", "control.managed", "control.service.start",
                    "control.service.stop"}
CONTROL_OPTIONAL = {"control.show", "control.hide", "control.autostart.set",
                    "control.catalog.get", "control.activity.act"}
COMMON_STATE = {"app", "pid", "version", "managed", "parent_pid", "window", "exiting",
                "autostart", "level", "services"}
CONNECTOR_STATE = {"connector", "game_running", "pitwall", "outbox", "start_with_app"}
SERVICE_FIELDS = {"id", "name", "state", "label", "level", "running", "ownership",
                  "forced_stop"}
TOPICS = {"setup", "capture", "telemetry", "session", "car", "track", "tyres", "fuel",
          "strategy", "controls", "other"}


# ------------------------------------------------------------------ the recorder -> Pitwall

def test_the_recorder_writes_a_session_as_the_protocol_says(workspace, pitwall):
    ws, _ = workspace
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), ws, lap_period_s=0,
                   game_check_s=0.05)
    rec.start()
    try:
        game = ws / "connectors" / S.CONNECTOR_ID / "fake_game"
        game.parent.mkdir(parents=True, exist_ok=True)
        game.touch()
        assert pitwall.ended.wait(15), "the session was not written"
    finally:
        rec.stop()

    # hello (docs/connector-protocol.md section 1)
    info = pitwall.hellos[0]
    assert (info.app, info.process, info.name) == ("connector-skeleton", "recorder",
                                                   "Skeleton connector")
    assert info.meta == {"connector_id": "skeleton", "connector_name": "Skeleton connector",
                         "sim": "skeletonsim", "sim_name": "Skeleton Simulator",
                         "service": "recorder"}
    assert re.fullmatch(r"[a-z0-9_-]{1,32}", info.meta["sim"])

    # declarations: catalog, guidance (section 5, 5.1)
    assert pitwall.named("pitwall.catalog.declare") == [{"catalog": ANNOUNCEMENTS}]
    assert pitwall.named("pitwall.guidance.declare") == [{"items": GUIDANCE}]
    assert pitwall.named("pitwall.actions.declare") == [{"actions": ACTIONS}]
    assert ANNOUNCEMENTS["catalog"] == "connector.skeleton" and ANNOUNCEMENTS["language"] == "en"

    # the writes, in order (section 3.1, 11.2)
    kinds = [n.removeprefix("pitwall.write.") for n, _ in pitwall.calls
             if n.startswith("pitwall.write.")]
    assert kinds == ["catalog", "session_start"] + [k for k, *_ in SCRIPT] + ["session_end"]
    key = pitwall.named("pitwall.write.session_start")[0]["session_key"]
    assert re.fullmatch(r"[0-9a-f]{16}", key)
    for name, params in pitwall.calls:
        if name.startswith("pitwall.write.") and name != "pitwall.write.catalog":
            assert params["session_key"] == key
            assert "session_id" not in params
    laps = pitwall.named("pitwall.write.lap")
    assert [p["seq"] for p in laps] == list(range(1, len(laps) + 1))
    assert [p["lap_type"] for p in laps] == ["out", "flying", "flying", "in", "out", "flying"]
    for p in laps:
        assert "stint" not in p                          # Pitwall decides stints (11.1)
        assert p["on_track"] is True and isinstance(p["valid"], bool)
        assert p["sim_lap"] == p["seq"]
        assert {"t", "dist", "speed", "throttle", "brake", "g_lat", "x", "z"} <= set(p["channels"])
        assert p["channels"]["t"]["encoding"] == "f32"
        assert p["_ik"] == f"skeleton/{key}/lap/{p['seq']}"
        # time_s only when the simulator timed the lap, never 0 or -1
        assert ("time_s" in p) == (p["lap_type"] == "flying")
    assert [p["valid"] for p in laps if p["lap_type"] == "flying"] == [True, False, True]
    exits = pitwall.named("pitwall.write.pit_exit")
    assert [p["seq"] for p in exits] == [1, 2] and all("at" in p for p in exits)
    assert pitwall.named("pitwall.write.session_start")[0]["_ik"] == f"skeleton/{key}/start"

    # announcements only for declared codes with their declared params (section 5)
    for p in pitwall.named("pitwall.announce"):
        spec = ANNOUNCEMENTS["messages"][p["code"]]
        assert set(p["params"]) == set(spec["params"])
    assert [p["params"]["lap"] for p in pitwall.named("pitwall.announce")] == [2, 6]


def test_the_declared_texts_follow_the_rules():
    for code, msg in ANNOUNCEMENTS["messages"].items():
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,31}", code)
        assert msg["audience"] in ("driver", "agent") and "ttl_s" not in msg
        assert 0.5 <= msg.get("max_wait_s", 1) <= 600 and msg.get("priority", "normal") in (
            "spotter", "critical", "high", "normal", "low")
        placeholders = set(re.findall(r"\{([a-z][a-z0-9_]*)\}", msg["text"]))
        assert placeholders == set(msg["params"])
    assert len(GUIDANCE) <= 16
    for item in GUIDANCE:
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,31}", item["id"]) and item["topic"] in TOPICS
        assert 1 <= len(item["when"]) <= 120 and "\n" not in item["when"]
        assert 1 <= len(item["text"]) <= 1500
    assert sum(len(i["when"]) + len(i["text"]) for i in GUIDANCE) <= 8000
    assert t("service.recorder.name") == "Recorder"
    assert sha12(WINDOW) == sha12(json.loads(json.dumps(WINDOW)))


def test_no_key_is_reported_and_never_created(workspace, pitwall):
    ws, _ = workspace
    (ws / "ipc" / "key").unlink()
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), ws, lap_period_s=0,
                   game_check_s=0.05)
    rec.start()
    try:
        assert wait(lambda: "key file not found" in (rec.message or ""))
        assert rec.state == "waiting" and not (ws / "ipc" / "key").exists()
    finally:
        rec.stop()


# ------------------------------------------------------------------ the control endpoint

@pytest.fixture
def control(workspace, pitwall):
    ws, _ = workspace
    name = pipe("control")
    app = ControlApp(ws, pipe=name, pitwall_pipe=pitwall.pipe, lap_period_s=0,
                     game_check_s=0.05).start()
    try:
        yield name, app, ws
    finally:
        app.stop()


def _hub(pipe, ws):
    return connect(pipe, PeerInfo("hub", process="test"), KeyFile(ws / "ipc" / "key"))


def test_the_control_profile_as_a_hub_sees_it(control, pitwall):
    pipe, app, ws = control
    hub = _hub(pipe, ws)
    try:
        assert hub.info.app == "connector-skeleton"
        assert CONTROL_REQUIRED <= set(hub.decl.methods)
        assert set(hub.decl.methods) <= CONTROL_REQUIRED | CONTROL_OPTIONAL
        states: list = []
        hub.subscribe("control.state", lambda ev: states.append(ev.data))
        assert wait(lambda: states and states[-1])
        state = states[-1]
        assert (COMMON_STATE | CONNECTOR_STATE) <= set(state)
        assert state["app"] == "skeleton-connector" and state["window"] in ("shown", "hidden")
        assert state["level"] in ("off", "ok", "warn", "error")
        assert state["pitwall"] in ("connected", "waiting", "refused")
        assert state["connector"] == {"id": "skeleton", "name": "Skeleton connector",
                                      "version": S.CONNECTOR_VERSION, "sim": "skeletonsim",
                                      "sim_name": "Skeleton Simulator"}
        (service,) = state["services"]
        assert SERVICE_FIELDS <= set(service) and service["id"] == "recorder"
        assert service["delegable"] is True and service["default"] == "on"
        assert service["refusal"] is None

        # Standalone, the app starts its recorder itself; the hub's stop and start follow.
        assert wait(lambda: states[-1]["services"][0]["running"] is True)
        assert hub.call("control.service.stop", {"service": "recorder"}) == {
            "service": "recorder", "state": "stopped"}
        assert wait(lambda: states[-1]["services"][0]["state"] == "stopped")
        assert hub.call("control.service.start", {"service": "recorder"})["state"] == "waiting"
        with pytest.raises(RemoteError) as bad:
            hub.call("control.service.start", {"service": "nope"})
        assert bad.value.code == "bad_request"

        me = os.getpid()
        assert hub.call("control.managed", {"parent_pid": me}) == {"managed": True,
                                                                   "parent_pid": me}
        assert wait(lambda: states[-1]["managed"] is True and states[-1]["parent_pid"] == me)

        got = hub.call("control.catalog.get", {})
        assert got["rev"] == sha12(got["catalog"]) and got["catalog"]["catalog"] == WINDOW["catalog"]
        assert got["catalog"]["language"] == "en"
    finally:
        hub.close()


def test_a_managed_start_leaves_the_delegable_service_to_the_hub(workspace, pitwall):
    ws, _ = workspace
    app = ControlApp(ws, pipe=pipe("control"), pitwall_pipe=pitwall.pipe, managed=True,
                     parent_pid=os.getpid(), lap_period_s=0).start()
    try:
        assert app.recorder is None and app.build_state()["services"][0]["running"] is False
    finally:
        app.stop()


def test_control_quit_ends_the_process_and_the_pipe(control):
    pipe, app, ws = control
    hub = _hub(pipe, ws)
    try:
        assert hub.call("control.quit", {}) == {"exiting": True}
    finally:
        hub.close()
    assert app.exit.wait(5)
    app.stop()                    # what main() does at the end

    def gone():
        try:
            _hub(pipe, ws).close()
        except (PipeNotFound, Exception):  # noqa: BLE001 - not reachable either way
            return True
        return False
    assert wait(gone)


def test_the_control_endpoint_passes_stintmate_ipc_check(control, capsys):
    pipe, _app, ws = control
    code = ipc_check(["server", pipe, "--key-file", str(ws / "ipc" / "key"), "--fast",
                      "--call", "control.catalog.get={}"])
    assert code == 0, capsys.readouterr().out


# ------------------------------------------------------------------ launcher and descriptor

def test_the_launcher_tolerates_the_hubs_options():
    args = parse(["--workspace", r"C:\W", "--managed", "--parent-pid", "123",
                  "--allow-synced", "--something-new", "x"])
    assert (args.workspace, args.managed, args.parent_pid) == (r"C:\W", True, 123)


@pytest.mark.skipif(shutil.which("powershell") is None, reason="needs Windows PowerShell")
def test_install_writes_the_descriptor_the_hub_reads(tmp_path):
    machine = tmp_path / "machine"
    dest = tmp_path / "Programs" / "Skeleton"
    env = {**os.environ, "STINTMATE_CONNECTORS_DIR": str(machine)}

    def run(*extra):
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(ROOT / "install.ps1"), "-Destination", str(dest), *extra],
                              capture_output=True, text=True, timeout=120, env=env)

    done = run("-SkipInstall")
    assert done.returncode == 0, done.stderr + done.stdout
    target = machine / f"{S.CONNECTOR_ID}.json"
    assert [p.name for p in machine.iterdir()] == [target.name]     # no temporary file left
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    d = json.loads(raw)
    folder = dest
    assert d["schema"] == 1 and d["id"] == target.stem == S.CONNECTOR_ID
    assert (d["name"], d["sim"], d["sim_name"], d["version"]) == (
        S.CONNECTOR_NAME, S.SIM, S.SIM_NAME, S.CONNECTOR_VERSION)
    launcher = Path(d["launcher"])
    assert launcher.is_absolute() and folder in launcher.parents   # its own environment
    assert launcher.name == "skeleton-connector.exe" and d["args"] == []
    assert d["control_protocol"] == "stintmate-ipc/2" and d["endpoint"] == S.APP_ID
    assert d["mutex"] == S.MUTEX and d["mutex"].startswith("Local\\")
    assert d["services"] == [{"id": S.SERVICE_ID, "name": "Recorder"}]
    assert d["logs"] == ["control.log"]
    assert S.CONNECTOR_VERSION in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # the uninstall removes the descriptor only when it names this install, then the folder
    other = machine / "other-keeps.json"
    other.write_text("{}", encoding="utf-8")
    assert run("-Uninstall").returncode == 0
    assert not target.exists() and not dest.exists() and other.exists()
    run("-SkipInstall")
    foreign = json.loads(target.read_text(encoding="utf-8"))
    foreign["launcher"] = str(tmp_path / "elsewhere" / "skeleton-connector.exe")
    target.write_text(json.dumps(foreign), encoding="utf-8")
    assert run("-Uninstall").returncode == 0
    assert target.exists() and not dest.exists()                  # another install's file stays


def test_it_imports_no_stintmate_app():
    banned = re.compile(r"^\s*(?:from|import)\s+(stintmate_hub|pitwall|ams2_connector|"
                        r"stintmate_voice)\b", re.M)
    for path in [p for sub in ("src", "tests", "examples") for p in (ROOT / sub).rglob("*.py")]:
        assert not banned.search(path.read_text(encoding="utf-8")), path
    deps = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # only stintmate-ipc, from a path: the release wheel in vendor/; never a path into a
    # Stintmate app, never git
    m = re.search(r'^stintmate-ipc = \{ path = "([^"]+)"', deps, re.M)
    assert m, "stintmate-ipc must come from a path"
    assert re.fullmatch(r"vendor/stintmate_ipc-[\d.]+-[^/]+\.whl", m.group(1)), m.group(1)
    assert "apps/" not in m.group(1) and "git =" not in deps
    assert "stintmate-ipc" in deps and "pitwall" not in deps.split("[tool.uv.sources]")[0]


# ------------------------------------------------------------------ the actions (section 5.2)

COMMON_KEYS = {"type", "description", "default"}
SCHEMA_KEYS = {"object": COMMON_KEYS | {"properties", "required", "additionalProperties"},
               "string": COMMON_KEYS | {"maxLength", "minLength", "enum"},
               "number": COMMON_KEYS | {"unit", "minimum", "maximum", "exclusiveMinimum",
                                        "exclusiveMaximum"},
               "integer": COMMON_KEYS | {"unit", "minimum", "maximum", "enum"},
               "boolean": COMMON_KEYS,
               "array": COMMON_KEYS | {"items", "maxItems", "minItems"}}


def _check_schema(node: dict, root: bool = False) -> None:
    """The subset of JSON Schema the declaration may use (the rules, section 5.2)."""
    assert isinstance(node.get("type"), str) and node["type"] in SCHEMA_KEYS, node
    assert set(node) <= SCHEMA_KEYS[node["type"]], node
    if not root:
        assert 1 <= len(node["description"]) <= 300
    if node["type"] == "object":
        assert node.get("additionalProperties", False) is False
        for name, child in node["properties"].items():
            assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name)
            _check_schema(child)
    if node["type"] == "string":
        assert "enum" in node or 1 <= node["maxLength"] <= 65536
    if node["type"] == "number":
        assert 1 <= len(node["unit"]) <= 16 and node["minimum"] <= node["maximum"]
    if node["type"] == "integer":
        assert isinstance(node["minimum"], int) and isinstance(node["maximum"], int)
        assert node["minimum"] <= node["maximum"]
    if node["type"] == "array":
        assert 1 <= node["maxItems"] <= 1000
        _check_schema(node["items"])


def test_the_schema_check_accepts_every_type_of_the_subset():
    _check_schema({"type": "object", "properties": {
        "gap": {"type": "number", "description": "Gap.", "unit": "mm", "minimum": 0, "maximum": 5},
        "clicks": {"type": "integer", "description": "Clicks.", "minimum": 0, "maximum": 9},
        "laps": {"type": "array", "description": "Laps.", "maxItems": 4,
                 "items": {"type": "integer", "description": "A lap.", "minimum": 1,
                           "maximum": 99}}}}, root=True)


def test_the_declared_actions_follow_the_rules():
    assert 1 <= len(ACTIONS) <= 32
    codes = [a["code"] for a in ACTIONS]
    assert len(set(codes)) == len(codes) and "test.ping" in codes
    for a in ACTIONS:
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){0,2}", a["code"])
        assert 1 <= len(a["description"]) <= 600
        assert 1 <= a.get("timeout_s", 10) <= 60
        assert isinstance(a.get("requires_confirmation", False), bool)
        for key in ("params", "result"):
            assert a[key]["type"] == "object"
            _check_schema(a[key], root=True)
        assert set(a.get("example", {})) <= set(a["params"]["properties"])


def test_the_recorder_serves_test_ping_through_a_test_core(workspace, pitwall):
    ws, _ = workspace
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), ws, lap_period_s=0,
                   game_check_s=0.05)
    rec.start()
    try:
        assert wait(lambda: pitwall.named("pitwall.actions.declare"))
        spec = ACTIONS[0]
        answer = pitwall.call_connector("connector.action",
                                        {"code": "test.ping", "data": spec["example"],
                                         "call_id": "0123456789abcdef"})
        assert answer == {"pong": "pong"}
        assert set(answer) <= set(spec["result"]["properties"])
        assert set(spec["result"]["required"]) <= set(answer)
        with pytest.raises(RemoteError) as e:
            pitwall.call_connector("connector.action",
                                   {"code": "nope", "data": {}, "call_id": "0123456789abcdef"})
        assert e.value.code == "unknown_action"
    finally:
        rec.stop()


# ------------------------------------------------------------------ the game and the link

def _fake_game(ws) -> Path:
    flag = ws / "connectors" / S.CONNECTOR_ID / "fake_game"
    flag.parent.mkdir(parents=True, exist_ok=True)
    return flag


def _recorder(ws, pitwall, period=0.0):
    return Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), ws, lap_period_s=period,
                    game_check_s=0.05)


def test_the_recorder_links_with_the_game_closed(workspace, pitwall):
    ws, _ = workspace
    rec = _recorder(ws, pitwall)
    rec.start()
    try:
        assert wait(lambda: rec.state == "connected" and pitwall.hellos)
        time.sleep(0.5)                                  # a game check or ten
        assert rec.state == "connected" and rec.status.value["game_running"] is False
        assert pitwall.kinds() == [] and not rec.recording
        assert rec.outbox.summary()["pending"] == 0
    finally:
        rec.stop()


def test_a_session_plays_when_the_game_appears(workspace, pitwall):
    ws, _ = workspace
    rec = _recorder(ws, pitwall)
    seen = []
    put = rec.outbox.put
    rec.outbox.put = lambda *a: (seen.append(rec.status.value["game_running"]), put(*a))
    rec.start()
    try:
        _fake_game(ws).touch()
        assert pitwall.ended.wait(15)
    finally:
        rec.stop()
    assert seen and all(seen)                            # game_running was set before any write
    assert pitwall.kinds()[:2] == ["catalog", "session_start"]
    assert pitwall.kinds()[-1] == "session_end"


def test_a_session_closes_when_the_game_goes(workspace, pitwall):
    ws, _ = workspace
    rec = _recorder(ws, pitwall, period=0.3)
    rec.start()
    try:
        flag = _fake_game(ws)
        flag.touch()
        assert wait(lambda: "session_start" in pitwall.kinds())
        flag.unlink()
        assert pitwall.ended.wait(15)
        assert wait(lambda: rec.status.value["game_running"] is False)
    finally:
        rec.stop()
    assert pitwall.kinds()[-1] == "session_end"
    assert len(pitwall.named("pitwall.write.lap")) < 6   # the script was cut short


def _decode(ch) -> list[float]:
    values = array("f")
    values.frombytes(ch["data"])
    return list(values)


@pytest.mark.parametrize("lap_s", sorted({s[2] for s in SCRIPT if s[2]} | {60.0}))
def test_the_laps_carry_the_minimum_channels(lap_s):
    """The sanity rules a checker applies to a timed lap: units, signs and lengths."""
    ch = {k: _decode(v) for k, v in channels(lap_s).items()}
    assert {"t", "dist", "speed", "throttle", "brake", "g_lat", "x", "z"} <= set(ch)
    n = len(ch["t"])
    assert n == 200 and all(len(v) == n for v in ch.values())
    assert all(a < b for a, b in zip(ch["t"], ch["t"][1:])) and abs(ch["t"][-1] - lap_s) < 1
    assert ch["dist"][0] < 0.02 * TRACK_LENGTH_M
    assert abs(ch["dist"][-1] - TRACK_LENGTH_M) < 0.05 * TRACK_LENGTH_M
    assert all(a <= b for a, b in zip(ch["dist"], ch["dist"][1:]))
    for pedal in ("throttle", "brake"):
        assert -0.01 <= min(ch[pedal]) and max(ch[pedal]) <= 1.01
    assert max(ch["throttle"]) > 0.5 and max(ch["brake"]) > 0.05
    mean = sum(ch["speed"]) / n
    assert 0 < min(ch["speed"]) and max(ch["speed"]) <= 500
    assert abs(mean - 3.6 * TRACK_LENGTH_M / lap_s) < 0.2 * mean            # km/h, not m/s
    g = sorted(abs(v) for v in ch["g_lat"])
    assert 0.2 <= g[int(0.99 * (n - 1))] <= 8                              # g, not m/s2
    pairs = [(s, g_) for s, g_, v in zip(ch["steer"], ch["g_lat"], ch["speed"])
             if abs(s) > 0.05 and v > 30]
    ms, mg = (sum(p[i] for p in pairs) / len(pairs) for i in (0, 1))
    cov = sum((s - ms) * (g_ - mg) for s, g_ in pairs)
    var = math.sqrt(sum((s - ms) ** 2 for s, _ in pairs) * sum((g_ - mg) ** 2 for _, g_ in pairs))
    assert cov / var > 0.3                                                 # same sign as steer
    path = sum(math.dist((ch["x"][i], ch["z"][i]), (ch["x"][i + 1], ch["z"][i + 1]))
               for i in range(n - 1))
    assert abs(path - TRACK_LENGTH_M) < 0.05 * TRACK_LENGTH_M


def test_a_refused_connection_shows_refused(workspace, pitwall):
    ws, _ = workspace
    rec = Recorder(pitwall.pipe, os.urandom(32), ws, lap_period_s=0, game_check_s=0.05)
    rec.start()
    try:
        assert wait(lambda: rec.state == "refused") and rec.refused == "unauthorized"
    finally:
        rec.stop()


def test_a_stranger_cannot_quit_a_managed_app(workspace, pitwall):
    ws, _ = workspace
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        stranger = ControlApp(ws, pipe=pipe("control"), pitwall_pipe=pitwall.pipe,
                              managed=True, parent_pid=helper.pid, lap_period_s=0).start()
        parent = ControlApp(ws, pipe=pipe("control"), pitwall_pipe=pitwall.pipe,
                            managed=True, parent_pid=os.getpid(), lap_period_s=0).start()
        try:
            hub = _hub(stranger.pipe, ws)
            with pytest.raises(RemoteError) as e:
                hub.call("control.quit", {})
            assert e.value.code == "managed" and e.value.data == {"managed_by": helper.pid}
            assert not stranger.exit.is_set()
            hub.close()
            hub = _hub(parent.pipe, ws)
            assert hub.call("control.quit", {}) == {"exiting": True}     # its own parent
            hub.close()
            assert parent.exit.wait(5)
        finally:
            stranger.stop()
            parent.stop()
    finally:
        helper.kill()
        helper.wait(10)


# ------------------------------------------------------------------ logs and the dev instance

def test_first_run_creates_the_log_folder(tmp_path):
    folder = tmp_path / "Stintmate" / "connectors" / S.CONNECTOR_ID
    handler = setup_logging(folder)
    try:
        logging.getLogger("skeleton_connector").info("hello")
        handler.flush()
        assert (folder / "control.log").read_text(encoding="utf-8").count("hello") == 1
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()


def test_the_log_is_capped(tmp_path):
    handler = setup_logging(tmp_path)
    try:
        for _ in range(3000):
            logging.getLogger("skeleton_connector").info("x" * 1000)
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["control.log", "control.log.1",
                                                          "control.log.2"]
    assert all(p.stat().st_size <= 1_001_100 for p in tmp_path.iterdir())


def test_the_dev_instance_separates_the_pipes(monkeypatch):
    monkeypatch.delenv("STINTMATE_DEV_INSTANCE")
    assert S.dev_tag() is None and S.pipe("pitwall") == pipe_path("pitwall")
    monkeypatch.setenv("STINTMATE_DEV_INSTANCE", "My Tag_1!" + "z" * 40)
    assert S.dev_tag() == "mytag1" + "z" * 18
    assert S.pipe("pitwall") == pipe_path("pitwall", user=f"{pipe_user()}-{S.dev_tag()}")
    assert S.pipe("pitwall") != pipe_path("pitwall")
