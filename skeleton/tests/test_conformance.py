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
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest
from stintmate_ipc import (Endpoint, KeyFile, PeerInfo, PipeNotFound, RemoteError, connect)
from stintmate_ipc.check.cli import main as ipc_check

import skeleton_connector as S
from skeleton_connector.control import ControlApp
from skeleton_connector.main import parse
from skeleton_connector.recorder import SCRIPT, Recorder
from skeleton_connector.texts import ACTIONS, ANNOUNCEMENTS, GUIDANCE, WINDOW, sha12, t

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


def _pipe(what: str) -> str:
    return rf"\\.\pipe\stintmate-test-skeleton-{what}-{uuid.uuid4().hex[:12]}"


def _wait(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def workspace(tmp_path):
    """A temporary workspace with the key the hub's onboarding leaves."""
    ws = tmp_path / "Stintmate"
    (ws / "ipc").mkdir(parents=True)
    key = os.urandom(32)
    (ws / "ipc" / "key").write_text(key.hex() + "\n", encoding="ascii")
    return ws, key


class FakePitwall:
    """The ``pitwall`` endpoint as the documents describe it: it answers the calls a connector
    makes, refuses a write without an idempotency key, and records everything."""

    def __init__(self, key: bytes) -> None:
        self.pipe = _pipe("pitwall")
        self.calls: list[tuple[str, dict]] = []
        self.hellos: list[PeerInfo] = []
        self.ended = threading.Event()
        self._exits = 0
        ep = Endpoint(PeerInfo("pitwall", name="Pitwall (fake)", version="0.0.0"), key,
                      pipe=self.pipe)
        ep.on_connect = self._connected
        for kind in ("catalog", "session_start", "lap", "pit_exit", "session_end"):
            ep.method(f"pitwall.write.{kind}", self._write(kind), ordered=True, idempotent=True)
        for name in ("pitwall.catalog.declare", "pitwall.guidance.declare", "pitwall.actions.declare",
                     "pitwall.announce"):
            ep.method(name, self._record(name), idempotent=name != "pitwall.announce")
        self.ep = ep.start()

    def _connected(self, peer) -> None:
        self.hellos.append(peer.info)
        self.peer = peer

    def call_connector(self, method: str, params: dict):
        """Call the connector as the core relays an action."""
        return self.peer.call(method, params, timeout=10)

    def _record(self, name):
        def run(ctx, params):
            self.calls.append((name, params))
            return {"ok": True}
        return run

    def _write(self, kind):
        def run(ctx, params):
            if not ctx.idempotency_key:
                raise RemoteError("bad_request", "a write needs an idempotency key")
            self.calls.append((f"pitwall.write.{kind}", {**params, "_ik": ctx.idempotency_key}))
            if kind == "pit_exit":
                self._exits += 1
                return {"ok": True, "stint": self._exits, "new_stint": self._exits > 1}
            if kind == "lap":
                return {"ok": True, "lap": params["seq"], "lap_id": params["seq"], "stint": 1}
            if kind == "session_end":
                self.ended.set()
            return {"ok": True}
        return run

    def named(self, name: str) -> list[dict]:
        return [p for n, p in self.calls if n == name]

    def stop(self) -> None:
        self.ep.stop()


@pytest.fixture
def pitwall(workspace):
    fake = FakePitwall(workspace[1])
    try:
        yield fake
    finally:
        fake.stop()


# ------------------------------------------------------------------ the recorder -> Pitwall

def test_the_recorder_writes_a_session_as_the_protocol_says(workspace, pitwall):
    ws, _ = workspace
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), lap_period_s=0)
    rec.start()
    try:
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
        assert {"t", "dist", "speed"} <= set(p["channels"])
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
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), lap_period_s=0)
    rec.start()
    try:
        assert _wait(lambda: "key file not found" in (rec.message or ""))
        assert rec.state == "waiting" and not (ws / "ipc" / "key").exists()
    finally:
        rec.stop()


# ------------------------------------------------------------------ the control endpoint

@pytest.fixture
def control(workspace, pitwall):
    ws, _ = workspace
    pipe = _pipe("control")
    app = ControlApp(ws, pipe=pipe, pitwall_pipe=pitwall.pipe, lap_period_s=0).start()
    try:
        yield pipe, app, ws
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
        assert _wait(lambda: states and states[-1])
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
        assert _wait(lambda: states[-1]["services"][0]["running"] is True)
        assert hub.call("control.service.stop", {"service": "recorder"}) == {
            "service": "recorder", "state": "stopped"}
        assert _wait(lambda: states[-1]["services"][0]["state"] == "stopped")
        assert hub.call("control.service.start", {"service": "recorder"})["state"] == "running"
        with pytest.raises(RemoteError) as bad:
            hub.call("control.service.start", {"service": "nope"})
        assert bad.value.code == "bad_request"

        me = os.getpid()
        assert hub.call("control.managed", {"parent_pid": me}) == {"managed": True,
                                                                   "parent_pid": me}
        assert _wait(lambda: states[-1]["managed"] is True and states[-1]["parent_pid"] == me)

        got = hub.call("control.catalog.get", {})
        assert got["rev"] == sha12(got["catalog"]) and got["catalog"]["catalog"] == WINDOW["catalog"]
        assert got["catalog"]["language"] == "en"
    finally:
        hub.close()


def test_a_managed_start_leaves_the_delegable_service_to_the_hub(workspace, pitwall):
    ws, _ = workspace
    app = ControlApp(ws, pipe=_pipe("control"), pitwall_pipe=pitwall.pipe, managed=True,
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
    assert _wait(gone)


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
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")):
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

SCHEMA_KEYS = {"object": {"type", "description", "properties", "required",
                          "additionalProperties"},
               "string": {"type", "description", "maxLength", "minLength", "enum"},
               "boolean": {"type", "description"}}


def _check_schema(node: dict, root: bool = False) -> None:
    """The subset of JSON Schema the declaration may use (the rules, section 5.2)."""
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
    rec = Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), lap_period_s=0)
    rec.start()
    try:
        assert _wait(lambda: pitwall.named("pitwall.actions.declare"))
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
