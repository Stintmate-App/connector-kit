"""Helpers for the tests: a fake Pitwall built from the names in the documents (nothing is
imported from Pitwall or the hub), unique pipe names and a polling wait."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque

from stintmate_ipc import Endpoint, PeerInfo, RemoteError

WRITES = ("catalog", "session_start", "lap", "pit_exit", "session_end")


def pipe(what: str) -> str:
    return rf"\\.\pipe\stintmate-test-skeleton-{what}-{uuid.uuid4().hex[:12]}"


def wait(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakePitwall:
    """The ``pitwall`` endpoint as the documents describe it: it answers the calls a connector
    makes, refuses a write without an idempotency key, answers a repeated key with the first
    answer, and records everything. ``refuse_next`` scripts an error for the next writes;
    ``stop`` and ``start`` take it away and bring it back on the same pipe."""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.pipe = pipe("pitwall")
        self.calls: list[tuple[str, dict]] = []          # accepted writes and declarations
        self.attempts: list[tuple[str, str, str | None]] = []   # every write: method, ik, error
        self.hellos: list[PeerInfo] = []
        self.ended = threading.Event()
        self.peer = None
        self.close_with: str | None = None    # close every new connection with this bye code
        self._replies: dict[str, dict] = {}
        self._exits = 0
        self._refuse: deque[tuple[str, int | None]] = deque()
        self.ep: Endpoint | None = None
        self.start()

    def start(self) -> None:
        ep = Endpoint(PeerInfo("pitwall", name="Pitwall (fake)", version="0.0.0"), self.key,
                      pipe=self.pipe)
        ep.on_connect = self._connected
        for kind in WRITES:
            ep.method(f"pitwall.write.{kind}", self._write(kind), ordered=True, idempotent=True,
                      idempotency_cache=False)
        for name in ("pitwall.catalog.declare", "pitwall.guidance.declare",
                     "pitwall.actions.declare", "pitwall.announce"):
            ep.method(name, self._record(name), idempotent=name != "pitwall.announce")
        self.ep = ep.start()

    def stop(self) -> None:
        if self.ep is not None:
            self.ep.stop()
            self.ep = None

    def refuse_next(self, code: str, retry_ms: int | None = None, count: int = 1) -> None:
        self._refuse.extend([(code, retry_ms)] * count)

    def _connected(self, peer) -> None:
        self.hellos.append(peer.info)
        self.peer = peer
        if self.close_with:
            peer.close(self.close_with, "scripted")

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
            ik = ctx.idempotency_key
            if not ik:
                raise RemoteError("bad_request", "a write needs an idempotency key")
            if self._refuse:
                code, retry_ms = self._refuse.popleft()
                self.attempts.append((f"pitwall.write.{kind}", ik, code))
                raise RemoteError(code, "scripted", retry_ms=retry_ms)
            self.attempts.append((f"pitwall.write.{kind}", ik, None))
            if ik in self._replies:
                return self._replies[ik]                 # a repeat stores nothing twice
            self.calls.append((f"pitwall.write.{kind}", {**params, "_ik": ik}))
            reply: dict = {"ok": True}
            if kind == "pit_exit":
                self._exits += 1
                reply = {"ok": True, "stint": self._exits, "new_stint": self._exits > 1}
            elif kind == "lap":
                reply = {"ok": True, "lap": params["seq"], "lap_id": params["seq"], "stint": 1}
            elif kind == "session_end":
                self.ended.set()
            self._replies[ik] = reply
            return reply
        return run

    def named(self, name: str) -> list[dict]:
        return [p for n, p in self.calls if n == name]

    def kinds(self) -> list[str]:
        """The accepted writes, in order."""
        return [n.removeprefix("pitwall.write.") for n, _ in self.calls
                if n.startswith("pitwall.write.")]
