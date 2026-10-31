"""The service ``recorder``: connects to Pitwall's core and writes one synthetic session.

What a real recorder does, in the smallest form (docs/connector-protocol.md):

- one connection to ``pitwall``, hello as ``connector-<id>`` / process ``recorder`` with the
  required ``meta`` (section 1); the package's ``Client`` reconnects by itself;
- at every connection: the announcement catalog, the guidance and the actions (and it serves
  `connector.action`, section 5.2);
- the car and track catalog, then the facts of a session in order, each write with its
  idempotency key (section 3.1): a repeated call after a lost answer stores nothing twice, so
  a failed write is simply sent again;
- ``connector.status`` set on every change (section 2.1).

A real recorder reads the simulator on a fast loop and writes through an outbox on another
thread (section 3.3); this one has no loop to protect, so it writes inline.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from array import array
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from stintmate_ipc import Client, ConnectionLost, NotConnected, PeerInfo, RemoteError

from . import APP_ID, CONNECTOR_ID, CONNECTOR_NAME, CONNECTOR_VERSION, SERVICE_ID, SIM, SIM_NAME
from .texts import ACTIONS, ANNOUNCEMENTS, GUIDANCE, sha12

log = logging.getLogger("skeleton_connector.recorder")

TRACK_LENGTH_M = 1000.0
CATALOG = {"classes": [{"class_id": "GT", "name": "Grand Touring"}],
           "cars": [{"car_id": "car_a", "name": "Skeleton GT", "class_id": "GT"}],
           "tracks": [{"track_id": "ring", "name": "Skeleton Ring"}],
           "layouts": [{"track_id": "ring", "layout_id": "full", "name": "Skeleton Ring Full",
                        "length_m": TRACK_LENGTH_M}]}
LIVE = {"session_type": "practice", "car_id": "car_a", "track_id": "ring", "layout_id": "full",
        "track_length_m": TRACK_LENGTH_M}

# The synthetic session: (kind, lap_type, time_s, valid). `pit_exit` is a fact, not a lap.
# Laps that ended before an exit are written before it (section 11.2).
SCRIPT: list[tuple[str, str | None, float | None, bool]] = [
    ("pit_exit", None, None, True),      # leaving the garage: stint 1 starts with the session
    ("lap", "out", None, True),          # out lap: not timed
    ("lap", "flying", 61.234, True),
    ("lap", "flying", 60.987, False),    # invalidated by the simulator: still sent
    ("lap", "in", None, True),           # ended in the pits: not timed
    ("pit_exit", None, None, True),      # after a stop on track: stint 2
    ("lap", "out", None, True),
    ("lap", "flying", 60.512, True),
]


def peer_info(service: str = SERVICE_ID) -> PeerInfo:
    """Who this process is (docs/connector-protocol.md section 1)."""
    return PeerInfo(APP_ID, name=CONNECTOR_NAME, version=CONNECTOR_VERSION, process=service,
                    meta={"connector_id": CONNECTOR_ID, "connector_name": CONNECTOR_NAME,
                          "sim": SIM, "sim_name": SIM_NAME, "service": service})


def _f32(values: list[float]) -> dict[str, Any]:
    return {"encoding": "f32", "data": array("f", values).tobytes()}


def channels(n: int = 20, lap_s: float = 60.0) -> dict[str, Any]:
    """Synthetic lap samples: time, distance and speed (`t`, `dist`, `speed` are required)."""
    return {"t": _f32([i * lap_s / n for i in range(n)]),
            "dist": _f32([i * TRACK_LENGTH_M / n for i in range(n)]),
            "speed": _f32([30.0 + i for i in range(n)])}


class Recorder:
    """``start()`` plays the session once in a thread; ``stop()`` ends it for good.
    ``state`` is ``connected`` or ``waiting`` (the control app's ``control.state.pitwall``)."""

    def __init__(self, pipe: str, key: Any, *, lap_period_s: float = 2.0,
                 on_change: Callable[[], None] | None = None) -> None:
        self.client = Client(pipe, peer_info(), key)
        self.client.on_connect = self._on_connect      # runs after every (re)connection
        self.client.on_connect_error = self._connect_error
        self.client.method("connector.action", self._action)   # a pool thread, not inline
        self.lap_period_s = lap_period_s
        self.on_change = on_change
        self.status = self.client.state_channel("connector.status", {
            "game_running": False, "phase": None, "session_key": None, "stint": None,
            "lap": None, "live": None, "outbox": None, "error_code": None, "message": None})
        self.message: str | None = None    # why not connected (key missing, Pitwall absent)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> str:
        return "connected" if self.client.connected else "waiting"

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.client.start()
        self._thread = threading.Thread(target=self._run, daemon=True, name="recorder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(10)
        self._thread = None
        self.client.close()
        self._changed()

    # ---- per connection ----------------------------------------------------------------

    def _connect_error(self, exc: BaseException) -> None:
        """The key file or Pitwall is missing: say it once, the client retries by itself."""
        text = str(exc)
        if text != self.message:
            self.message = text
            log.info("Not connected to Pitwall: %s", text)
            self._changed()

    def _on_connect(self, peer: Any) -> None:
        self.message = None
        self._changed()
        # Optional declarations: a core that predates one answers unknown_method (log, go on).
        for method, params, ik in (
                ("pitwall.catalog.declare", {"catalog": ANNOUNCEMENTS},
                 f"{CONNECTOR_ID}/textcatalog/{sha12(ANNOUNCEMENTS)}"),
                ("pitwall.guidance.declare", {"items": GUIDANCE},
                 f"{CONNECTOR_ID}/guidance/{sha12(GUIDANCE)}"),
                ("pitwall.actions.declare", {"actions": ACTIONS},
                 f"{CONNECTOR_ID}/actions/{sha12(ACTIONS)}")):
            try:
                peer.call(method, params, timeout=5, idempotency_key=ik)
            except RemoteError as exc:
                log.warning("%s: %s", method, exc)

    def _action(self, ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        """``connector.action``: answer in the declared ``result`` format, or raise a
        ``RemoteError`` (``unknown_action`` for a code this connector does not serve)."""
        if params.get("code") == "test.ping":
            return {"pong": "pong"}
        raise RemoteError("unknown_action", f"no action {params.get('code')!r}")

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                log.exception("on_change failed")

    # ---- writing -----------------------------------------------------------------------

    def _write(self, kind: str, params: dict[str, Any], ik: str) -> Any:
        """One write, sent again with the same key until Pitwall answers or we stop."""
        while not self._stop.is_set():
            try:
                self.client.wait_connected(1.0)
                return self.client.call(f"pitwall.write.{kind}", params, timeout=30,
                                        idempotency_key=ik)
            except (NotConnected, ConnectionLost):
                continue
            except RemoteError as exc:
                if exc.code in ("busy", "timeout", "writer_busy", "shutting_down"):
                    time.sleep(0.5)          # the key was not consumed: same call again
                    continue
                raise                          # bad_request & co: not retryable (section 3.1)
        return None

    def _announce(self, code: str, params: dict[str, Any], session_key: str) -> None:
        """Not idempotent and never queued: an announcement is worth something only now."""
        try:
            self.client.call("pitwall.announce", {"code": code, "params": params,
                                                  "session_key": session_key}, timeout=5)
        except (NotConnected, ConnectionLost, RemoteError) as exc:
            log.info("announce %s skipped: %s", code, exc)

    def _set(self, **fields: Any) -> None:
        self.status.set({**(self.status.value or {}), **fields})
        self._changed()

    # ---- the session -------------------------------------------------------------------

    def _run(self) -> None:
        try:
            if self._write("catalog", CATALOG, f"{CONNECTOR_ID}/catalog/{sha12(CATALOG)}") is None:
                return
            self._play()
        except Exception:  # noqa: BLE001 - a bug must reach the log, not die silently
            log.exception("The recorder stopped")
            self._set(error_code="recorder_failed", message="The recorder stopped; see the log")

    def _play(self) -> None:
        key = secrets.token_hex(8)                      # 16 hex digits, new for each session
        clock = datetime.now().astimezone()
        start = {"session_key": key, "started_at": clock.isoformat(timespec="seconds"),
                 "session_type": "practice", "car_id": "car_a", "track_id": "ring",
                 "layout_id": "full"}
        if self._write("session_start", start, f"{CONNECTOR_ID}/{key}/start") is None:
            return
        self._set(game_running=True, phase="garage", session_key=key, stint=1, live=LIVE)
        seqs = {"lap": 0, "pit_exit": 0}
        sim_lap = 0                                     # the lap number the simulator shows
        for kind, lap_type, time_s, valid in SCRIPT:
            if self._stop.wait(self.lap_period_s):
                break
            clock += timedelta(seconds=time_s or 30.0)
            seqs[kind] += 1
            seq = seqs[kind]
            if kind == "pit_exit":
                reply = self._write("pit_exit", {"session_key": key, "seq": seq,
                                                 "at": clock.isoformat(timespec="seconds"),
                                                 "fuel_l": 50.0},
                                    f"{CONNECTOR_ID}/{key}/pit_exit/{seq}")
                if reply is not None:
                    self._set(phase="on_track", stint=reply["stint"])
                continue
            sim_lap += 1
            params: dict[str, Any] = {"session_key": key, "seq": seq, "lap_type": lap_type,
                                      "valid": valid, "sim_lap": sim_lap, "on_track": True,
                                      "channels": channels(lap_s=time_s or 60.0)}
            if time_s is not None:
                params["time_s"] = time_s               # only when the simulator timed it
            reply = self._write("lap", params, f"{CONNECTOR_ID}/{key}/lap/{seq}")
            if reply is None:
                break
            self._set(lap=reply["lap"], phase="on_track" if lap_type != "in" else "garage")
            if time_s is not None and valid:
                self._announce("lap_done", {"lap": sim_lap, "time": time_s}, key)
        self._write("session_end", {"session_key": key,
                                    "ended_at": clock.isoformat(timespec="seconds")},
                    f"{CONNECTOR_ID}/{key}/end")
        self._set(game_running=False, phase=None, session_key=None, stint=None, lap=None,
                  live=None)
