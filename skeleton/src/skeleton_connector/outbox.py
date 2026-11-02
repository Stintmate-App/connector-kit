"""The outbox: every write to Pitwall goes through the disk first (docs/contract.md 5.4).

Keep this file as it is; it is the part of a connector that is the same for every game.

``put`` stores a write as ``<20-digit sequence>.msgpack`` holding ``{method, v, ik, p, at}``.
One thread sends the files in order, one at a time, with the stored idempotency key:

- ``ok``: the file is deleted;
- one of the six codes in ``NON_RETRYABLE``: the file moves to ``rejected/`` and the next one
  goes on (a refused lap must not block the laps after it);
- anything else (``unavailable``, ``busy``, ``timeout``, a lost connection ...): the file stays
  and is sent again with the same key after ``retry_ms`` (1 s by default, at most 10 s).

Files left by an earlier run are sent first, so a lap finished while Pitwall was not running
is kept. Call ``put`` from the session thread, never from a sampling loop: it writes a file.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from stintmate_ipc import IpcError, RemoteError
# Not in the package's __all__, but it is the codec Pitwall speaks (MessagePack).
from stintmate_ipc._codec import packb, unpackb

log = logging.getLogger("skeleton_connector.outbox")

NON_RETRYABLE = ("bad_request", "session_closed", "unknown_session", "too_large",
                 "unknown_method", "version_unsupported")
SEND_TIMEOUT_S = 30.0
RETRY_DEFAULT_S, RETRY_MIN_S, RETRY_MAX_S = 1.0, 0.05, 10.0
LOST_CONNECTION_S = 0.1

Item = tuple[int, Path, str, str, str]          # sequence, file, method, ik, stored at


class Outbox:
    def __init__(self, folder: str | Path, client: Any, *,
                 on_result: Callable[[str, Any, Any], None] | None = None,
                 on_rejected: Callable[[str, Any, str], None] | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        """``client`` is the connection to Pitwall (a ``stintmate_ipc.Client``)."""
        self.folder = Path(folder)
        self.rejected_dir = self.folder / "rejected"
        self.client = client
        self.on_result, self.on_rejected, self.on_change = on_result, on_rejected, on_change
        self._items: deque[Item] = deque()
        self._rejected = 0
        self._next = 1
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- start and close ---------------------------------------------------------------

    def start(self) -> Outbox:
        self.folder.mkdir(parents=True, exist_ok=True)
        for tmp in self.folder.glob("*.tmp"):
            tmp.unlink(missing_ok=True)                 # a write cut short by a crash
        found: list[Item] = []
        for path in self.folder.glob("*.msgpack"):
            try:
                doc = unpackb(path.read_bytes())
                found.append((int(path.stem), path, doc["method"], doc["ik"], doc.get("at", "")))
            except Exception as exc:  # noqa: BLE001 - any unreadable file goes aside
                log.error("Outbox file %s is unreadable (%s): moved to rejected", path.name, exc)
                self._move_aside(path)
        self._items.extend(sorted(found))
        seqs = [int(p.stem) for p in self.rejected_dir.glob("*.msgpack") if p.stem.isdigit()]
        self._rejected = len(seqs)
        self._next = max([i[0] for i in self._items] + seqs, default=0) + 1
        if self._items:
            log.info("Outbox: %d write(s) waiting from an earlier run", len(self._items))
        self._thread = threading.Thread(target=self._run, daemon=True, name="outbox")
        self._thread.start()
        return self

    def close(self, drain_s: float = 5.0) -> None:
        """Give what is waiting ``drain_s`` seconds to go while connected, then stop. The rest
        stays on disk for the next run."""
        if self.client.connected:
            self.flush(drain_s)
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(10)                        # a daemon: a hung call does not hold the exit

    def flush(self, timeout: float) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: not self._items, timeout)

    # ---- put and summary ---------------------------------------------------------------

    def put(self, method: str, ik: str, params: Any) -> None:
        at = datetime.now().astimezone().isoformat(timespec="milliseconds")
        data = packb({"method": method, "v": 1, "ik": ik, "p": params, "at": at})
        with self._cond:
            seq = self._next
            self._next += 1
            path = self.folder / f"{seq:020d}.msgpack"
            tmp = path.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)                       # atomic: the whole file or none
            self._items.append((seq, path, method, ik, at))
            self._cond.notify_all()
        self._changed()

    def summary(self) -> dict[str, Any]:
        """``connector.status.outbox`` (the control state takes ``pending`` and ``oldest_at``)."""
        with self._cond:
            return {"pending": len(self._items),
                    "oldest_at": self._items[0][4] if self._items else None,
                    "rejected": self._rejected}

    # ---- the sender --------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cond:
                if not self._items:
                    self._cond.notify_all()             # flush() waits for this
                    self._cond.wait(0.2)
                    continue
                item = self._items[0]
            if not self.client.connected:
                self._stop.wait(0.05)
                continue
            try:
                self._send(item)
            except Exception:  # noqa: BLE001 - the sender must never die: log, wait, go on
                log.exception("Outbox sender failed; the write stays and is sent again")
                self._stop.wait(RETRY_DEFAULT_S)

    def _send(self, item: Item) -> None:
        _seq, path, method, ik, _at = item
        try:
            params = unpackb(path.read_bytes())["p"]
        except Exception as exc:  # noqa: BLE001
            log.error("Outbox file %s is unreadable (%s): moved to rejected", path.name, exc)
            self._finish(item, rejected=True)
            return
        try:
            result = self.client.call(method, params, timeout=SEND_TIMEOUT_S,
                                      idempotency_key=ik)
        except RemoteError as exc:
            if exc.code in NON_RETRYABLE:
                log.error("%s %s refused by Pitwall: %s: %s (moved to rejected)", method, ik,
                          exc.code, exc.message)
                self._finish(item, rejected=True)
                if self.on_rejected is not None:
                    try:
                        self.on_rejected(method, params, exc.code)
                    except Exception:  # noqa: BLE001
                        log.exception("on_rejected failed")
                return
            wait = RETRY_DEFAULT_S if exc.retry_ms is None else exc.retry_ms / 1000
            log.info("%s %s: %s, sent again", method, ik, exc.code)
            self._stop.wait(min(RETRY_MAX_S, max(RETRY_MIN_S, wait)))
            return
        except IpcError as exc:
            # CallTimeout, ConnectionLost, NotConnected, CallCancelled ...: same file, same
            # key, sent again once the call can go through.
            log.info("%s %s: %s, sent again", method, ik, type(exc).__name__)
            self._stop.wait(LOST_CONNECTION_S)
            return
        self._finish(item)
        if self.on_result is not None:
            try:
                self.on_result(method, params, result)
            except Exception:  # noqa: BLE001
                log.exception("on_result failed")

    def _finish(self, item: Item, rejected: bool = False) -> None:
        with self._cond:
            if self._items and self._items[0] is item:
                self._items.popleft()
            if rejected:
                self._rejected += 1
            self._cond.notify_all()
        if rejected:
            self._move_aside(item[1])
        else:
            item[1].unlink(missing_ok=True)
        self._changed()

    def _move_aside(self, path: Path) -> None:
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(path, self.rejected_dir / path.name)
        except OSError as exc:
            log.error("Could not move %s aside: %s", path.name, exc)

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001
                log.exception("on_change failed")
