"""A fake Pitwall: the ``pitwall`` endpoint as the kit's documents describe it, on a pipe the
caller names. It applies the hello rules, subscribes to ``connector.status``, validates every write
against the write API's schema and the order rules, answers as the real core does (with numbers
of its own), and records everything. It can be told to refuse the next writes, to close the next
connections with a code, to stop and to start again on the same pipe."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from array import array
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
from stintmate_ipc import Endpoint, PeerInfo, RemoteError

WRITES = ("catalog", "sim_data_kinds", "session_start", "lap", "pit_exit", "setup",
          "sim_data", "participant_laps", "session_end")
# contract.json limits.sim_data_*; tests/test_contract_files.py keeps them equal.
SIM_DATA_LIMITS = {"bytes": 16384, "depth": 6, "keys": 512, "items": 256, "text": 500,
                   "per_session": 5000}
DATA_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
KIND_PATH = re.compile(r"^[A-Za-z0-9_*-]{1,64}(\.[A-Za-z0-9_*-]{1,64}){0,5}$")
DECLARES = ("pitwall.catalog.declare", "pitwall.guidance.declare", "pitwall.actions.declare")
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SIM = re.compile(r"^[a-z0-9_-]{1,32}$")
# contract.json hello_meta.product.pattern; a run replaces it with the kit's own copy.
PRODUCT_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
SESSION_KEY = re.compile(r"^[0-9a-f]{16}$")


@dataclass
class Entry:
    """One call a connector made: every attempt, accepted or not."""
    n: int
    t: float
    method: str
    ik: str | None
    params: Any
    error: str | None = None        # the code the fake core answered, None when it accepted
    replayed: bool = False          # an ik seen before: the first answer was repeated
    connector: str = ""
    message: str = ""

    @property
    def kind(self) -> str:
        return self.method.removeprefix("pitwall.write.")


def jsonable(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        return {"bytes": len(value), "sha": hashlib.sha256(bytes(value)).hexdigest()[:12]}
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def digest(params: Any) -> str:
    """A fingerprint of a write's content, to tell a repeat from a different write."""
    return hashlib.sha256(json.dumps(jsonable(params), sort_keys=True).encode()).hexdigest()[:16]


def decode_channel(value: Any) -> list[float]:
    """A channel as the write API takes it: a list of numbers, or ``{encoding: f32|f64, data}``."""
    if isinstance(value, list):
        return [float(x) for x in value]
    if isinstance(value, dict) and value.get("encoding") in ("f32", "f64") \
            and isinstance(value.get("data"), (bytes, bytearray)):
        arr = array("f" if value["encoding"] == "f32" else "d")
        data = bytes(value["data"])
        if len(data) % arr.itemsize:
            raise ValueError("channel bytes are not a whole number of samples")
        arr.frombytes(data)
        return list(arr)
    raise ValueError("a channel is a list of numbers or {encoding: f32, data: bytes}")


def check_sim_data(data: Any) -> None:
    """The limits of ``sim_data.data`` (docs/write-api.md), with Pitwall's error texts."""
    lim = SIM_DATA_LIMITS
    if not isinstance(data, dict):
        raise _Refused("bad_request", "sim_data.data: must be an object")
    keys = 0

    def walk(value: Any, path: str, depth: int) -> None:
        nonlocal keys
        if isinstance(value, dict):
            if depth > lim["depth"]:
                raise _Refused("bad_request", f"{path}: nested deeper than {lim['depth']}")
            for key, item in value.items():
                if not DATA_KEY.match(key):
                    raise _Refused("bad_request", f"{path}.{key}: bad key")
                keys += 1
                if keys > lim["keys"]:
                    raise _Refused("bad_request", f"sim_data.data: more than {lim['keys']} keys")
                walk(item, f"{path}.{key}", depth + 1)
        elif isinstance(value, list):
            if depth > lim["depth"]:
                raise _Refused("bad_request", f"{path}: nested deeper than {lim['depth']}")
            if len(value) > lim["items"]:
                raise _Refused("bad_request", f"{path}: more than {lim['items']} items")
            for i, item in enumerate(value):
                walk(item, f"{path}[{i}]", depth + 1)
        elif isinstance(value, str):
            if len(value) > lim["text"]:
                raise _Refused("bad_request", f"{path}: a string of more than {lim['text']} "
                                              "characters")
        elif value is None or isinstance(value, bool):
            return
        elif isinstance(value, int):
            if abs(value) > 2 ** 53:
                raise _Refused("bad_request", f"{path}: not a finite number")
        elif isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise _Refused("bad_request", f"{path}: not a finite number")
        else:
            raise _Refused("bad_request", f"{path}: not a JSON value")

    walk(data, "sim_data.data", 1)
    size = len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if size > lim["bytes"]:
        raise _Refused("bad_request", f"sim_data.data: larger than {lim['bytes']} bytes")


class FakeCore:
    def __init__(self, pipe: str, key: bytes, write_schema: dict[str, Any]) -> None:
        self.pipe, self.key = pipe, key
        defs = write_schema["$defs"]
        self._validators = {
            kind: jsonschema.Draft202012Validator({"$ref": f"#/$defs/{kind}", "$defs": defs})
            for kind in WRITES}
        self.lock = threading.RLock()
        self.log: list[Entry] = []
        self.hellos: list[tuple[float, PeerInfo]] = []
        self.bad_hellos: list[str] = []
        self.disconnects: list[tuple[float, str | None]] = []
        self.status: list[tuple[float, dict[str, Any]]] = []
        self.status_errors: list[str] = []
        self.declared: dict[str, Any] = {}
        self.sim_data_kinds: dict[str, dict[str, Any]] = {}
        self.announces: list[dict[str, Any]] = []
        self.peers: list[Any] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.ended = threading.Event()
        self.ended_count = 0
        self._known: dict[str, set] = {"tracks": set(), "layouts": set(), "cars": set(),
                                       "classes": set()}
        self._catalogs: list[dict[str, Any]] = []
        self._replies: dict[str, dict[str, Any]] = {}
        self._refuse: deque[tuple[str, int | None, str | None]] = deque()
        self._bye: deque[str] = deque()
        self._bye_always: str | None = None
        self.product_pattern = PRODUCT_PATTERN
        self._next_session = 0
        self._next_message = 0
        self.ep: Endpoint | None = None

    # ---- life cycle ---------------------------------------------------------------------

    def start(self) -> FakeCore:
        ep = Endpoint(PeerInfo("pitwall", name="Pitwall (check)", version="0.0.0"), self.key,
                      pipe=self.pipe)
        ep.on_connect = self._connected
        ep.on_disconnect = self._disconnected
        for kind in WRITES:
            ep.method(f"pitwall.write.{kind}", self._write(kind), ordered=True, idempotent=True,
                      idempotency_cache=False)
        for name in DECLARES:
            ep.method(name, self._declare(name), idempotent=True, idempotency_cache=False)
        ep.method("pitwall.announce", self._announce, idempotency_cache=False)
        self.ep = ep.start()
        return self

    def stop(self) -> None:
        ep, self.ep = self.ep, None
        if ep is not None:
            ep.stop()
        with self.lock:
            self.peers.clear()

    @property
    def running(self) -> bool:
        return self.ep is not None

    # ---- scripted behaviour -------------------------------------------------------------

    def refuse_next(self, code: str, retry_ms: int | None = None, method: str | None = None,
                    count: int = 1) -> None:
        """The next `count` write attempts (of `method`, when given) answer this error."""
        with self.lock:
            self._refuse.extend([(code, retry_ms, method)] * count)

    def bye_next(self, code: str, count: int = 1) -> None:
        """The next `count` connections are closed with this ``bye`` code, after the hello."""
        with self.lock:
            self._bye.extend([code] * count)

    def bye_while(self, code: str | None) -> None:
        """Close every connection with `code` until called again with None."""
        with self.lock:
            self._bye_always = code

    # ---- what the connector did ---------------------------------------------------------

    @property
    def linked(self) -> bool:
        with self.lock:
            return bool(self.peers)

    def writes(self, accepted: bool | None = None) -> list[Entry]:
        with self.lock:
            return [e for e in self.log if e.method.startswith("pitwall.write.")
                    and (accepted is None or (e.error is None) == accepted)]

    def mark(self) -> int:
        with self.lock:
            return len(self.log)

    def since(self, mark: int) -> list[Entry]:
        with self.lock:
            return list(self.log[mark:])

    def last_status(self) -> dict[str, Any] | None:
        with self.lock:
            return self.status[-1][1] if self.status else None

    def wait(self, pred, timeout: float, step: float = 0.1) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(step)
        return bool(pred())

    def call(self, name: str, params: Any = None, timeout: float = 10.0) -> Any:
        """Ask a connector's service something (the core relays `connector.live_frame` so): the
        newest connection that declares the method, since a connector may have several."""
        with self.lock:
            peers = list(self.peers)
        if not peers:
            raise RuntimeError("no connector is connected")
        declaring = [p for p in peers if name in set(p.decl.methods)]
        peer = declaring[-1] if declaring else peers[-1]
        return peer.call(name, params if params is not None else {}, timeout=timeout)

    def offered(self) -> tuple[set[str], set[str]] | None:
        """The methods and channels the connector's connections declare together, or None when
        none is connected."""
        with self.lock:
            peers = list(self.peers)
        if not peers:
            return None
        methods: set[str] = set()
        channels: set[str] = set()
        for peer in peers:
            methods |= set(peer.decl.methods)
            channels |= set(peer.decl.channels)
        return methods, channels

    def dump(self, path: str | Path) -> None:
        """Every call, to read back."""
        with self.lock:
            doc = [{"n": e.n, "t": round(e.t, 3), "method": e.method, "ik": e.ik,
                    "error": e.error, "replayed": e.replayed, "params": jsonable(e.params)}
                   for e in self.log]
        Path(path).write_text(json.dumps(doc, indent=1), encoding="utf-8")

    # ---- hello ----------------------------------------------------------------------------

    @staticmethod
    def hello_problem(info: PeerInfo, product_pattern: str = PRODUCT_PATTERN) -> str | None:
        """The rules of the core's hello for a connector, restated from the documents."""
        meta = info.meta or {}
        missing = [k for k in ("connector_id", "connector_name", "sim", "sim_name", "service")
                   if not isinstance(meta.get(k), str) or not meta.get(k)]
        if missing:
            return "peer.meta is missing " + ", ".join(missing)
        if f"connector-{meta['connector_id']}" != info.app or not SLUG.match(meta["connector_id"]):
            return (f"peer.meta.connector_id {meta['connector_id']!r} must be the slug of "
                    f"peer.app ({info.app})")
        if not SIM.match(meta["sim"]):
            return "peer.meta.sim must match ^[a-z0-9_-]{1,32}$"
        if not isinstance(meta.get("product"), str) or not re.match(product_pattern, meta["product"]):
            return f"peer.meta.product {meta.get('product')!r} must be a lowercase UUID version 4"
        if info.process is not None and info.process != meta["service"]:
            return f"peer.process {info.process!r} differs from peer.meta.service"
        return None

    def _connected(self, peer: Any) -> None:
        info = peer.info
        with self.lock:
            self.hellos.append((time.monotonic(), info))
            code = self._bye.popleft() if self._bye else self._bye_always
        if code is not None:
            peer.close(code, "scripted")
            return
        problem = self.hello_problem(info, self.product_pattern)
        if problem is not None:
            with self.lock:
                self.bad_hellos.append(problem)
            peer.close("bad_request", problem)
            return
        with self.lock:
            self.peers.append(peer)
        try:
            peer.subscribe("connector.status", lambda ev, p=peer: self._on_status(ev.data))
        except Exception as exc:  # noqa: BLE001 - a connector without the channel
            with self.lock:
                self.status_errors.append(repr(exc))

    def _disconnected(self, peer: Any) -> None:
        with self.lock:
            if peer in self.peers:
                self.peers.remove(peer)
            try:
                code = peer.close_code
            except Exception:  # noqa: BLE001
                code = None
            self.disconnects.append((time.monotonic(), code))

    def _on_status(self, data: Any) -> None:
        if isinstance(data, dict):
            with self.lock:
                self.status.append((time.monotonic(), data))

    # ---- the methods ----------------------------------------------------------------------

    def _record(self, ctx: Any, method: str, params: Any, error: str | None = None,
                replayed: bool = False) -> Entry:
        info = ctx.peer.info
        entry = Entry(len(self.log), time.monotonic(), method, ctx.idempotency_key, params,
                      error, replayed, (info.meta or {}).get("connector_id", ""))
        self.log.append(entry)
        return entry

    def _refusal(self, method: str) -> tuple[str, int | None] | None:
        if self._refuse:
            code, retry_ms, only = self._refuse[0]
            if only is None or only == method:
                self._refuse.popleft()
                return code, retry_ms
        return None

    def _fail(self, entry: Entry, code: str, message: str, retry_ms: int | None = None) -> None:
        entry.error, entry.message = code, message
        raise RemoteError(code, message, retry_ms=retry_ms)

    def _declare(self, name: str):
        def run(ctx: Any, params: Any) -> dict[str, Any]:
            with self.lock:
                entry = self._record(ctx, name, params)
                scripted = self._refusal(name)
                if scripted:
                    self._fail(entry, scripted[0], "scripted", scripted[1])
                field = {"pitwall.catalog.declare": "catalog", "pitwall.guidance.declare": "items",
                         "pitwall.actions.declare": "actions"}[name]
                want = dict if field == "catalog" else list
                if not entry.ik:
                    self._fail(entry, "bad_request", "a declaration needs an idempotency key")
                if not isinstance(params, dict) or not isinstance(params.get(field), want):
                    self._fail(entry, "bad_request", f"{name}: '{field}' must be a {want.__name__}")
                self.declared[name] = params[field]
            return {"ok": True}
        return run

    def _announce(self, ctx: Any, params: Any) -> dict[str, Any]:
        with self.lock:
            entry = self._record(ctx, "pitwall.announce", params)
            catalog = self.declared.get("pitwall.catalog.declare") or {}
            code = params.get("code") if isinstance(params, dict) else None
            if code not in (catalog.get("messages") or {}):
                self._fail(entry, "bad_request", f"unknown announcement code {code!r}")
            self.announces.append(params)
            self._next_message += 1
            return {"message_id": self._next_message, "at": datetime.now().astimezone().isoformat()}

    def _write(self, kind: str):
        method = f"pitwall.write.{kind}"

        def run(ctx: Any, params: Any) -> dict[str, Any]:
            with self.lock:
                entry = self._record(ctx, method, params)
                scripted = self._refusal(method)
                if scripted:
                    self._fail(entry, scripted[0], "scripted", scripted[1])
                ik = ctx.idempotency_key
                if not ik:
                    self._fail(entry, "bad_request", "a write needs an idempotency key")
                if ik in self._replies:
                    entry.replayed = True
                    return self._replies[ik]
                try:
                    reply = self._apply(kind, params, entry.connector)
                except _Refused as exc:
                    self._fail(entry, exc.code, str(exc))
                self._replies[ik] = reply
                return reply
        return run

    # ---- the write API: schema and the order rules -----------------------------------------

    def _apply(self, kind: str, params: Any, connector: str) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise _Refused("bad_request", "params must be an object")
        msg = {k: v for k, v in params.items() if k not in ("session_key", "seq")}
        msg["type"] = kind
        if kind not in ("catalog", "session_start", "sim_data_kinds"):
            msg["session_id"] = 1
        channels = None
        if isinstance(msg.get("channels"), dict):
            try:
                channels = {name: decode_channel(v) for name, v in msg["channels"].items()}
            except ValueError as exc:
                raise _Refused("bad_request", f"channels: {exc}") from None
            msg["channels"] = channels
        error = next(iter(sorted(self._validators[kind].iter_errors(msg),
                                 key=lambda e: list(e.absolute_path))), None)
        if error is not None:
            where = "/".join(str(p) for p in error.absolute_path) or kind
            raise _Refused("bad_request", f"{where}: {error.message}")
        if kind == "sim_data_kinds":
            return self._sim_data_kinds(msg)
        if kind == "catalog":
            return self._catalog(msg)
        if kind == "session_start":
            return self._session_start(params, msg, connector)
        key = params.get("session_key")
        session = self.sessions.get(key) if isinstance(key, str) else None
        if session is None:
            raise _Refused("unknown_session", f"session_key {key!r} was never started")
        if session["closed"]:
            raise _Refused("session_closed", f"session {key} has ended")
        if kind != "session_end":
            seq = params.get("seq")
            last = session["seq"].get(kind, 0)
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1 or seq <= last:
                raise _Refused("bad_request", f"{kind} seq {seq!r} must be an integer above {last}")
            session["seq"][kind] = seq
        if kind == "sim_data":
            check_sim_data(msg.get("data"))
            if session.get("records", 0) >= SIM_DATA_LIMITS["per_session"]:
                raise _Refused("bad_request", "sim_data: the session already holds "
                                              f"{SIM_DATA_LIMITS['per_session']} records")
            session["records"] = session.get("records", 0) + 1
            return {"ok": True, "type": "sim_data", "session_id": session["id"],
                    "record": session["records"], "stint": session["stint"], "stored": True}
        if kind == "lap":
            return self._lap(session, msg, channels)
        if kind == "pit_exit":
            new = session["laps_since_exit"] > 0
            session["stint"] += 1 if new else 0
            session["laps_since_exit"] = 0
            return {"ok": True, "type": "pit_exit", "session_id": session["id"],
                    "stint": session["stint"], "new_stint": new}
        if kind == "session_end":
            session["closed"] = True
            self.ended_count += 1
            self.ended.set()
            return {"ok": True, "type": "session_end", "session_id": session["id"],
                    "stored": True, "laps": session["laps"], "discarded": 0}
        return {"ok": True, "type": kind, "session_id": session["id"]}

    def _sim_data_kinds(self, msg: dict[str, Any]) -> dict[str, Any]:
        kinds = msg["kinds"]
        names = [k["kind"] for k in kinds]
        if len(set(names)) != len(names):
            raise _Refused("bad_request", "sim_data_kinds.kinds: a kind is described twice")
        for i, k in enumerate(kinds):
            for path in k["fields"]:
                if len(path) > 128 or not KIND_PATH.match(path):
                    raise _Refused("bad_request", f"sim_data_kinds.kinds[{i}].fields.{path}: "
                                                  "bad field path")
        changed = [k["kind"] for k in kinds if self.sim_data_kinds.get(k["kind"]) != k]
        for k in kinds:
            self.sim_data_kinds[k["kind"]] = k
        return {"ok": True, "type": "sim_data_kinds", "kinds": len(kinds), "changed": changed}

    def _catalog(self, msg: dict[str, Any]) -> dict[str, Any]:
        for field, bucket, ident in (("classes", "classes", ("class_id",)),
                                     ("cars", "cars", ("car_id",)),
                                     ("tracks", "tracks", ("track_id",)),
                                     ("layouts", "layouts", ("track_id", "layout_id"))):
            for row in msg.get(field) or []:
                self._known[bucket].add(tuple(row[i] for i in ident))
        self._catalogs.append(msg)
        return {"ok": True, "type": "catalog", **{f: len(msg.get(f) or []) for f in
                                                  ("classes", "cars", "tracks", "layouts")}}

    def _session_start(self, params: dict[str, Any], msg: dict[str, Any], connector: str
                       ) -> dict[str, Any]:
        key = params.get("session_key")
        if not isinstance(key, str) or not SESSION_KEY.match(key):
            raise _Refused("bad_request", "session_key must be 16 hex digits")
        if key in self.sessions:
            raise _Refused("bad_request", f"session_key {key} was used before: a session needs a "
                                          "new one")
        if not self._catalogs:
            raise _Refused("bad_request", "session_start before any catalog write")
        for field, bucket in (("car_id", "cars"), ("track_id", "tracks")):
            if msg.get(field) is not None and (msg[field],) not in self._known[bucket]:
                raise _Refused("bad_request", f"{field} {msg[field]!r} is not in the catalog")
        if msg.get("layout_id") is not None and (msg.get("track_id"), msg["layout_id"]) \
                not in self._known["layouts"]:
            raise _Refused("bad_request", f"layout {msg.get('layout_id')!r} is not in the catalog")
        for other in self.sessions.values():                  # the next start closes an open one
            if other["connector"] == connector and not other["closed"]:
                other["closed"] = True
        self._next_session += 1
        self.sessions[key] = {"id": self._next_session, "closed": False, "seq": {}, "stint": 1,
                              "laps": 0, "laps_since_exit": 0, "connector": connector,
                              "key": key, "start": msg}
        return {"ok": True, "type": "session_start", "session_id": self._next_session}

    def _lap(self, session: dict[str, Any], msg: dict[str, Any],
             channels: dict[str, list[float]] | None) -> dict[str, Any]:
        if channels:
            missing = [c for c in ("t", "dist", "speed") if c not in channels]
            if missing:
                raise _Refused("bad_request", "channels: " + ", ".join(missing) + " required "
                                              "when any channel is sent")
            if len({len(v) for v in channels.values()}) != 1:
                raise _Refused("bad_request", "channels: the channels differ in length")
        session["laps"] += 1
        session["laps_since_exit"] += 1
        return {"ok": True, "type": "lap", "session_id": session["id"], "lap": session["laps"],
                "lap_id": session["laps"], "stint": session["stint"]}


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
