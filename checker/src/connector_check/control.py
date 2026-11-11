"""The checker's side of the connector's control endpoint, as a managing app uses it."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from stintmate_ipc import PeerInfo, connect


class ControlLink:
    """Connect to ``connector-<id>``, keep every ``control.state`` value with its time, and call
    methods. ``connect`` retries until the endpoint is served."""

    INFO = PeerInfo("connector-check", name="connector-check", version="0.7.0")

    def __init__(self, pipe: str, key: bytes) -> None:
        self.pipe, self.key = pipe, key
        self.peer: Any = None
        self.latest: dict[str, Any] | None = None
        self.history: list[tuple[float, dict[str, Any]]] = []
        self._cv = threading.Condition()
        self.last_error = ""

    def connect(self, timeout: float, alive: Callable[[], bool] = lambda: True) -> bool:
        """True when the endpoint is served and its state arrived; `alive` False stops early."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and alive():
            try:
                self.peer = connect(self.pipe, self.INFO, self.key, timeout=2.0)
                self.peer.subscribe("control.state", self._on_state)
                break
            except Exception as exc:  # noqa: BLE001 - not served yet
                self.last_error = repr(exc)
                self.peer = None
                time.sleep(0.25)
        if self.peer is None:
            return False
        return self.wait_for(lambda s: True, max(0.1, end - time.monotonic())) is not None

    def _on_state(self, event: Any) -> None:
        with self._cv:
            if isinstance(event.data, dict):
                self.latest = event.data
                self.history.append((time.monotonic(), event.data))
            self._cv.notify_all()

    def wait_for(self, pred: Callable[[dict[str, Any]], bool], timeout: float
                 ) -> dict[str, Any] | None:
        """The first state (the latest one first) that satisfies `pred`, within `timeout` s."""
        end = time.monotonic() + timeout
        with self._cv:
            while True:
                if self.latest is not None and pred(self.latest):
                    return self.latest
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(min(left, 0.25))

    def seen(self, pred: Callable[[dict[str, Any]], bool], since: float = 0.0) -> bool:
        """Whether any state recorded after `since` (a monotonic time) satisfied `pred`."""
        with self._cv:
            return any(t >= since and pred(s) for t, s in self.history)

    def call(self, method: str, params: Any = None, timeout: float = 10.0) -> Any:
        if self.peer is None:
            raise RuntimeError("not connected")
        return self.peer.call(method, params if params is not None else {}, timeout=timeout)

    def close(self) -> None:
        peer, self.peer = self.peer, None
        if peer is not None:
            try:
                peer.close()
            except Exception:  # noqa: BLE001
                pass
