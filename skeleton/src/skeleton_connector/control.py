"""The control app: serves the endpoint ``connector-skeleton`` (docs/contract.md sections 3
and 5.6) for the Stintmate hub and supervises the service ``recorder``.

Required of a connector: the state channel ``control.state`` and the methods
``control.quit``, ``control.managed``, ``control.service.start`` and ``control.service.stop``.
Optional and served here: ``control.catalog.get``. Not served (no window, no start entry):
``control.show``, ``control.hide``, ``control.autostart.set``, notices and activity; a hub
treats a method it gets ``unknown_method`` for as "not there".
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from pathlib import Path
from typing import Any

from stintmate_ipc import Endpoint, KeyFile, PeerInfo, RemoteError, default_key_path, pipe_path

from . import (APP_ID, CONNECTOR_ID, CONNECTOR_NAME, CONNECTOR_VERSION, CONTROL_APP, SERVICE_ID,
               SIM, SIM_NAME)
from .recorder import Recorder
from .texts import t, window_document

log = logging.getLogger("skeleton_connector.control")


def pid_alive(pid: int) -> bool:
    """Whether a process still runs (the managed mode's parent watch)."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000, False, pid)         # SYNCHRONIZE
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102   # timeout
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class ControlApp:
    def __init__(self, workspace: str | os.PathLike[str], *, pipe: str | None = None,
                 pitwall_pipe: str | None = None, managed: bool = False,
                 parent_pid: int | None = None, lap_period_s: float = 2.0) -> None:
        self.workspace = Path(workspace)
        # The key is only read, never created: the hub's onboarding makes it (guide, section 4).
        self.key = KeyFile(default_key_path(self.workspace), create=False)
        self.pipe = pipe or pipe_path(APP_ID)
        self.pitwall_pipe = pitwall_pipe or pipe_path("pitwall")
        self.lap_period_s = lap_period_s
        self.managed, self.parent_pid = managed, parent_pid
        self.exiting = False
        self.exit = threading.Event()               # set when the process should end
        self.recorder: Recorder | None = None
        self._lock = threading.Lock()
        self._last: dict[str, Any] | None = None
        self.endpoint = Endpoint(PeerInfo(APP_ID, name=CONNECTOR_NAME, version=CONNECTOR_VERSION),
                                 self.key, pipe=self.pipe)
        for name, fn in (("control.quit", self._quit), ("control.managed", self._managed),
                         ("control.service.start", self._service_start),
                         ("control.service.stop", self._service_stop)):
            self.endpoint.method(name, fn, ordered=True)
        self.endpoint.method("control.catalog.get", lambda ctx, p: window_document(),
                             idempotent=True, idempotency_cache=False)
        self.state = self.endpoint.state_channel("control.state", initial=None)

    # ---- lifecycle ---------------------------------------------------------------------

    def start(self) -> ControlApp:
        self.endpoint.start()                       # raises EndpointInUse if already served
        self.publish()
        if self.managed and self.parent_pid:
            self._watch_parent()
        else:
            self._start_recorder()                  # alone: the app's own startup setting
        # Managed: the hub sends control.service.start/stop for each delegable service
        # within a second (protocol section 1.1); do not start it here.
        return self

    def stop(self) -> None:
        self.exiting = True
        self._stop_recorder()
        self.endpoint.stop()

    # ---- the state ---------------------------------------------------------------------

    def service(self) -> dict[str, Any]:
        running = self.recorder is not None
        return {"id": SERVICE_ID, "name": t("service.recorder.name"),
                "state": "running" if running else "stopped",
                "label": t("service.state.running" if running else "service.state.stopped"),
                "level": "ok" if running else "off", "running": running, "ownership": "app",
                "forced_stop": False,
                # Section 1.1: let the hub keep this service on or off across restarts.
                "delegable": True, "default": "on", "refusal": None}

    def build_state(self) -> dict[str, Any]:
        rec = self.recorder
        return {"app": CONTROL_APP, "pid": os.getpid(), "version": CONNECTOR_VERSION,
                "managed": self.managed, "parent_pid": self.parent_pid, "window": "hidden",
                "exiting": self.exiting, "autostart": None,
                "level": "ok" if rec is not None and rec.state == "connected" else "off",
                "services": [self.service()],
                "connector": {"id": CONNECTOR_ID, "name": CONNECTOR_NAME,
                              "version": CONNECTOR_VERSION, "sim": SIM, "sim_name": SIM_NAME},
                "game_running": bool(rec is not None and (rec.status.value or {})
                                     .get("game_running")),
                "pitwall": rec.state if rec is not None else "waiting",
                "outbox": {"pending": 0, "oldest_at": None},
                "start_with_app": []}

    def publish(self) -> None:
        """Set ``control.state`` when it changed (the channel is never resent periodically)."""
        with self._lock:
            value = self.build_state()
            if value != self._last:
                self._last = value
                self.state.set(value)

    # ---- the service -------------------------------------------------------------------

    def _start_recorder(self) -> None:
        with self._lock:
            if self.recorder is not None:
                return
            self.recorder = Recorder(self.pitwall_pipe, self.key,
                                     lap_period_s=self.lap_period_s, on_change=self.publish)
        self.recorder.start()
        self.publish()

    def _stop_recorder(self) -> None:
        with self._lock:
            rec, self.recorder = self.recorder, None
        if rec is not None:
            rec.stop()
        if not self.exiting:
            self.publish()

    @staticmethod
    def _service_param(method: str, params: Any) -> None:
        if not isinstance(params, dict) or params.get("service") != SERVICE_ID:
            raise RemoteError("bad_request", f"{method}: service must be {SERVICE_ID}",
                              {"services": [SERVICE_ID]})

    def _service_start(self, ctx: Any, params: Any) -> dict[str, Any]:
        self._service_param("control.service.start", params)
        self._start_recorder()
        return {"service": SERVICE_ID, "state": self.service()["state"]}

    def _service_stop(self, ctx: Any, params: Any) -> dict[str, Any]:
        self._service_param("control.service.stop", params)
        self._stop_recorder()
        return {"service": SERVICE_ID, "state": self.service()["state"]}

    # ---- control -----------------------------------------------------------------------

    def _quit(self, ctx: Any, params: Any) -> dict[str, Any]:
        """Return at once; the process ends (services stopped) within 20 s."""
        self.exiting = True
        self.publish()
        threading.Timer(0.1, self.exit.set).start()
        return {"exiting": True}

    def _managed(self, ctx: Any, params: Any) -> dict[str, Any]:
        pid = params.get("parent_pid") if isinstance(params, dict) else None
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise RemoteError("bad_request", "control.managed: parent_pid must be a positive "
                                             "integer")
        self.managed, self.parent_pid = True, pid
        self.publish()
        self._watch_parent()
        return {"managed": True, "parent_pid": pid}

    def _watch_parent(self) -> None:
        """Managed mode: when the managing process goes, carry on standalone."""
        pid = self.parent_pid

        def watch() -> None:
            while not self.exit.wait(2.0):
                if self.parent_pid != pid:
                    return
                if not pid_alive(pid):
                    log.info("The managing process %s ended; running standalone", pid)
                    self.managed, self.parent_pid = False, None
                    self.publish()
                    return
        threading.Thread(target=watch, daemon=True, name="parent-watch").start()
