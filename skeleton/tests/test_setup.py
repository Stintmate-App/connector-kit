"""The setup side: the language pack, the hold bracket and install, update and uninstall.

Nothing here touches a real Stintmate: the hub is a fake endpoint on a pipe of its own (the
tests run under a random ``STINTMATE_DEV_INSTANCE``), the descriptors go to a temporary folder
(``STINTMATE_CONNECTORS_DIR``) and the workspace is a temporary one.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import stintmate_ipc
from stintmate_ipc import Endpoint, PeerInfo, RemoteError

import skeleton_connector as S
from skeleton_connector import setup_hold, texts
from skeleton_connector.control import ControlApp
from skeleton_connector.main import hold_mutex

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def own_workspace(monkeypatch, workspace):
    """The workspace the setup and the pack loader find: never the real one."""
    monkeypatch.setenv("STINTMATE_WORKSPACE", str(workspace[0]))
    yield
    texts.PACK.clear()


# ------------------------------------------------------------------ the language pack

def write_pack(ws: Path, tag: str, messages: dict, **top) -> Path:
    folder = ws / "locales" / tag
    folder.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "stintmate-catalog/1", "catalog": "skeleton-connector", "language": tag,
           "messages": {k: {"text": v} for k, v in messages.items()}, **top}
    path = folder / "skeleton-connector.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def set_language(ws: Path, tag) -> None:
    (ws / "hub").mkdir(exist_ok=True)
    (ws / "hub" / "settings.json").write_text(json.dumps({"language": tag}), encoding="utf-8")


def test_labels_come_from_the_pack(workspace, caplog):
    ws, _ = workspace
    # no setting, no pack: English
    texts.load_pack(ws)
    assert texts.t("service.state.recording") == "Recording"
    # es-CL falls back to es per key, then to the built-in English
    set_language(ws, "es-cl")
    write_pack(ws, "es-CL", {"service.state.recording": "Grabando (CL)"})
    write_pack(ws, "es", {"service.state.recording": "Grabando", "service.state.waiting": "Esperando",
                              "service.state.stopped": "Detenido"})
    texts.load_pack(ws)
    assert texts.t("service.state.recording") == "Grabando (CL)"
    assert texts.t("service.state.waiting") == "Esperando"
    # the window shows them
    app = ControlApp(ws, pipe=r"\\.\pipe\stintmate-test-skeleton-never", game_check_s=3600)
    assert app.service()["label"] == "Detenido"
    # a bad file, a wrong catalog and a bad text are skipped and logged, never raised
    write_pack(ws, "es-CL", {"service.state.recording": "{lap}", "nope.key": "x"})
    write_pack(ws, "es", {"service.state.waiting": "Esperando"}, catalog="voice")
    (ws / "locales" / "es").joinpath("skeleton-connector.json").write_text("{", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="skeleton_connector"):
        texts.load_pack(ws)
    assert texts.t("service.state.recording") == "Recording"
    assert texts.t("service.state.waiting") == "Waiting for the game"
    assert "skipped" in caplog.text and "ignored" in caplog.text
    # an invalid tag, or a settings file that is not an object, means English
    set_language(ws, "not a tag")
    assert texts.language(ws) == "en"
    (ws / "hub" / "settings.json").write_text("[]", encoding="utf-8")
    assert texts.language(ws) == "en"
    # the catalog the control app serves stays the built-in English
    assert texts.window_document()["catalog"]["language"] == "en"
    # a pack for the language `en` rewords English
    set_language(ws, "en")
    write_pack(ws, "en", {"service.state.stopped": "Off"})
    texts.load_pack(ws)
    assert texts.t("service.state.stopped") == "Off"


# ------------------------------------------------------------------ the hold bracket

class FakeHub:
    """The ``hub`` endpoint as docs/connector-protocol.md section 1.2 describes it."""

    def __init__(self, key: bytes) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hold_errors: list[RemoteError] = []
        self.hold_states = ["held"]
        self.on_release = None
        ep = Endpoint(PeerInfo("hub", name="Hub (fake)", version="0.0.0"), key, pipe=S.pipe("hub"))
        ep.method("hub.connector.hold", self._hold)
        ep.method("hub.connector.release", self._release)
        self.ep = ep.start()

    def _hold(self, ctx, params):
        self.calls.append(("hold", params))
        if self.hold_errors:
            raise self.hold_errors[0]
        state = self.hold_states.pop(0) if len(self.hold_states) > 1 else self.hold_states[0]
        return {"id": params["id"], "state": state, "restart": True, "expires_in_s": 300}

    def _release(self, ctx, params):
        if self.on_release:
            self.on_release()
        self.calls.append(("release", params))
        return {"id": params["id"], "released": True, "starting": params["start"]}

    def stop(self) -> None:
        self.ep.stop()


@pytest.fixture
def hub(workspace):
    fake = FakeHub(workspace[1])
    try:
        yield fake
    finally:
        fake.stop()


def test_setup_hold_holds_and_releases(hub):
    hub.hold_states = ["stopping", "held"]
    assert setup_hold.main(["hold", "--purpose", "update", "--ttl", "120"]) == 0
    assert hub.calls == [("hold", {"id": "skeleton", "purpose": "update", "ttl_s": 120})] * 2
    hub.calls.clear()
    assert setup_hold.main(["release", "--no-start"]) == 0
    assert hub.calls == [("release", {"id": "skeleton", "start": False})]
    assert setup_hold.main(["release", "--start"]) == 0
    assert hub.calls[-1] == ("release", {"id": "skeleton", "start": True})


@pytest.mark.parametrize("error, code", [
    (RemoteError("busy", "recording", {"recording": True}), 4),
    (RemoteError("busy", "starting", {"state": "starting"}), 3),
    (RemoteError("not_found", "unknown connector"), 3),
    (RemoteError("unknown_method", "older hub"), 3),
    (RemoteError("bad_request", "no"), 3),
])
def test_setup_hold_exit_codes_for_a_refusal(hub, error, code):
    hub.hold_errors = [error]
    assert setup_hold.main(["hold", "--purpose", "uninstall"]) == code


def test_setup_hold_without_a_hub_or_a_workspace(workspace, monkeypatch, tmp_path):
    assert setup_hold.main(["hold", "--purpose", "update"]) == 3        # no hub on the pipe
    assert setup_hold.main(["release", "--start"]) == 3
    monkeypatch.setenv("STINTMATE_WORKSPACE", str(tmp_path / "empty"))  # no key there
    assert setup_hold.main(["hold", "--purpose", "update"]) == 3


def test_setup_hold_waits_for_the_mutex(hub, monkeypatch):
    handle = hold_mutex()
    assert handle and setup_hold.mutex_held()
    monkeypatch.setattr(setup_hold, "HOLD_WAIT_S", 0.5)
    try:
        assert setup_hold.main(["hold", "--purpose", "update"]) == 3    # held, but still running
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))
    assert not setup_hold.mutex_held()
    assert setup_hold.main(["hold", "--purpose", "update"]) == 0


def test_setup_hold_stop_quits_the_connector(workspace, monkeypatch):
    ws, _ = workspace
    assert setup_hold.main(["stop"]) == 0                  # not running: nothing to do
    app = ControlApp(ws, pitwall_pipe=r"\\.\pipe\stintmate-test-skeleton-none",
                     game_check_s=3600).start()
    try:
        assert setup_hold.main(["stop"]) == 0
        assert app.exit.wait(5), "control.quit was not called"
    finally:
        app.stop()
    handle = hold_mutex()                                  # a process that does not quit
    monkeypatch.setattr(setup_hold, "STOP_WAIT_S", 0.5)
    try:
        assert setup_hold.main(["stop"]) == 3
    finally:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))


# ------------------------------------------------------------------ install, update, uninstall

SHELLS = [pytest.param(name, marks=pytest.mark.skipif(shutil.which(name) is None,
                                                      reason=f"needs {name}"))
          for name in ("powershell", "pwsh")]


def fake_environment(dest: Path) -> dict[str, str]:
    """A ``.venv`` whose python runs this test environment's code, standing in for the
    environment the installer makes (a real install needs the network)."""
    scripts = dest / ".venv" / "Scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(sys.executable), scripts / "python.exe")
    shutil.copy2(Path(sys.prefix) / "pyvenv.cfg", dest / ".venv" / "pyvenv.cfg")
    return {"PYTHONPATH": os.pathsep.join([str(ROOT / "src"),
                                           str(Path(stintmate_ipc.__file__).parent.parent)])}


@pytest.mark.parametrize("shell", SHELLS)
def test_install_uninstall_and_update_in_a_temp_folder(shell, tmp_path, workspace, hub):
    machine = tmp_path / "descriptors"
    dest = tmp_path / "Programs" / "Skeleton Connector"        # a path with spaces
    target = machine / "skeleton.json"
    env = {**os.environ, "STINTMATE_CONNECTORS_DIR": str(machine)}

    def powershell(*extra, env=env):
        return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(ROOT / "install.ps1"), "-Destination", str(dest), *extra],
                              capture_output=True, text=True, timeout=180, env=env, cwd=tmp_path)

    # install: the descriptor, the copy of the script, the uninstall command
    done = powershell("-SkipInstall")
    assert done.returncode == 0, done.stderr + done.stdout
    d = json.loads(target.read_text(encoding="utf-8"))
    assert d["window"] is False and d["features"] == []
    assert d["icon"] == str(dest / "icon.png")                  # absolute, in the install folder
    assert (dest / "icon.png").read_bytes() == (ROOT / "icon.png").read_bytes()
    assert (dest / "icon.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    argv = d["uninstall"]
    assert Path(argv[0]).is_absolute() and argv[0].lower().endswith("windowspowershell\\v1.0\\powershell.exe")
    assert argv[argv.index("-File") + 1] == str(dest / "install.ps1")
    assert argv[argv.index("-Destination") + 1] == str(dest) and "-Uninstall" in argv
    assert "-NonInteractive" in argv and "Bypass" in argv
    assert (dest / "install.ps1").read_bytes() == (ROOT / "install.ps1").read_bytes()
    assert hub.calls == []                                      # no environment: nothing to hold

    # update with an environment: hold, replace, release with a start
    env_py = fake_environment(dest)
    run_env = {**env, **env_py}
    done = powershell("-SkipInstall", "-Update", env=run_env)
    assert done.returncode == 0, done.stderr + done.stdout
    assert [c[0] for c in hub.calls] == ["hold", "release"]
    assert hub.calls[0][1]["purpose"] == "update" and hub.calls[1][1]["start"] is True

    # a session being recorded: nothing changes, exit 1
    hub.calls.clear()
    before = target.read_bytes()
    hub.hold_errors = [RemoteError("busy", "recording", {"recording": True})]
    done = powershell("-SkipInstall", "-Update", env=run_env)
    assert done.returncode == 1 and target.read_bytes() == before
    assert [c[0] for c in hub.calls] == ["hold"]
    hub.hold_errors = []

    # the uninstall command from the descriptor, run as the hub's Settings would run it: the
    # descriptor goes first, the release (no start) comes before the environment goes
    hub.calls.clear()
    seen = {}
    hub.on_release = lambda: seen.update(descriptor=target.exists(),
                                         environment=(dest / ".venv").exists())
    done = subprocess.run(argv, capture_output=True, text=True, timeout=180, env=run_env,
                          cwd=tmp_path)
    assert done.returncode == 0, done.stderr + done.stdout
    assert [c[0] for c in hub.calls] == ["hold", "release"]
    assert hub.calls[0][1]["purpose"] == "uninstall" and hub.calls[1][1]["start"] is False
    assert seen == {"descriptor": False, "environment": True}
    assert not target.exists() and not dest.exists()

    # a descriptor that names another install stays; with no hub the connector is stopped
    hub.stop()
    done = powershell("-SkipInstall")
    assert done.returncode == 0, done.stderr + done.stdout
    foreign = json.loads(target.read_text(encoding="utf-8"))
    foreign["launcher"] = str(tmp_path / "elsewhere" / "skeleton-connector.exe")
    target.write_text(json.dumps(foreign), encoding="utf-8")
    fake_environment(dest)
    done = powershell("-Uninstall", env=run_env)
    assert done.returncode == 0, done.stderr + done.stdout
    assert target.exists() and not dest.exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_install_never_overwrites_another_connectors_descriptor(shell, tmp_path):
    machine = tmp_path / "descriptors"
    dest = tmp_path / "Programs" / "Skeleton Connector"
    target = machine / "skeleton.json"
    env = {**os.environ, "STINTMATE_CONNECTORS_DIR": str(machine)}

    def run(*extra):
        return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(ROOT / "install.ps1"), "-Destination", str(dest), *extra],
                              capture_output=True, text=True, timeout=180, env=env, cwd=tmp_path)

    done = run("-SkipInstall")
    assert done.returncode == 0, done.stderr + done.stdout
    ours = json.loads(target.read_text(encoding="utf-8"))
    assert ours["product"] == S.PRODUCT
    assert run("-SkipInstall").returncode == 0                  # the same product: replaced

    # another connector took the id: the install fails, names it and changes nothing
    other = {**ours, "name": "Another connector", "product": "0b7e2d3c-9f41-4a6e-8c15-d2a4f6e81b90"}
    target.write_text(json.dumps(other), encoding="utf-8")
    before = target.read_bytes()
    done = run("-SkipInstall")
    assert done.returncode == 1
    assert "Another connector" in done.stderr and "taken" in done.stderr
    assert target.read_bytes() == before
    done = run("-SkipInstall", "-Update")
    assert done.returncode == 1 and target.read_bytes() == before


@pytest.mark.parametrize("shell", SHELLS)
def test_uninstall_removes_the_descriptor_only_when_launcher_and_product_are_its_own(shell, tmp_path):
    machine = tmp_path / "descriptors"
    dest = tmp_path / "Programs" / "Skeleton Connector"
    target = machine / "skeleton.json"
    env = {**os.environ, "STINTMATE_CONNECTORS_DIR": str(machine)}

    def run(*extra):
        return subprocess.run([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                               str(ROOT / "install.ps1"), "-Destination", str(dest), *extra],
                              capture_output=True, text=True, timeout=180, env=env, cwd=tmp_path)

    assert run("-SkipInstall").returncode == 0
    # our launcher, another product: the file stays
    d = json.loads(target.read_text(encoding="utf-8"))
    d["product"] = "0b7e2d3c-9f41-4a6e-8c15-d2a4f6e81b90"
    target.write_text(json.dumps(d), encoding="utf-8")
    assert run("-Uninstall").returncode == 0
    assert target.exists() and not dest.exists()
    # our product and our launcher: the file goes
    assert run("-SkipInstall").returncode == 1                  # still the other product's
    target.unlink()
    assert run("-SkipInstall").returncode == 0
    assert run("-Uninstall").returncode == 0
    assert not target.exists() and not dest.exists()
