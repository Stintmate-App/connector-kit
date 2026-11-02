"""The fake core behaves as the documents say: hello rules, write order, idempotency, scripted
errors, and a stop and start on the same pipe."""

from __future__ import annotations

import json
import os
import uuid
from array import array

import pytest
from conftest import KIT
from stintmate_ipc import PeerInfo, RemoteError, connect, pipe_path, pipe_user

from connector_check.fakecore import FakeCore, decode_channel, digest

CATALOG = {"classes": [{"class_id": "GT", "name": "GT"}],
           "cars": [{"car_id": "car_a", "name": "A", "class_id": "GT"}],
           "tracks": [{"track_id": "ring", "name": "Ring"}],
           "layouts": [{"track_id": "ring", "layout_id": "full", "name": "Full",
                        "length_m": 1000.0}]}
KEY16 = "0123456789abcdef"


def info(meta=None, app="connector-demo", process="recorder"):
    meta = {"connector_id": "demo", "connector_name": "Demo", "sim": "demosim",
            "sim_name": "Demo", "service": "recorder"} if meta is None else meta
    return PeerInfo(app, name="Demo", version="1", process=process, meta=meta)


@pytest.fixture
def core():
    key = os.urandom(32)
    pipe = pipe_path("pitwall", user=f"{pipe_user()}-t{uuid.uuid4().hex[:10]}")
    schema = json.loads((KIT / "docs" / "write-api.schema.json").read_text())
    fake = FakeCore(pipe, key, schema).start()
    try:
        yield fake
    finally:
        fake.stop()


def dial(core, **kw):
    return connect(core.pipe, info(**kw), core.key)


def lap(seq, **extra):
    return {"session_key": KEY16, "seq": seq, "lap_type": "flying", "valid": True,
            "sim_lap": seq, "on_track": True, "time_s": 60.0, **extra}


def test_hello_rules_close_a_bad_meta_with_bad_request(core):
    peer = dial(core, meta={"connector_id": "demo"})
    assert core.wait(lambda: core.bad_hellos, 5)
    assert "missing connector_name" in core.bad_hellos[0]
    assert core.wait(lambda: peer.close_code == "bad_request", 5)
    assert FakeCore.hello_problem(info(app="connector-other")) is not None
    assert FakeCore.hello_problem(info(process="other")) is not None
    assert FakeCore.hello_problem(info()) is None


def test_the_order_rules_and_the_replies(core):
    peer = dial(core)
    call = lambda m, p, ik: peer.call(f"pitwall.write.{m}", p, idempotency_key=ik)  # noqa: E731
    with pytest.raises(RemoteError) as e:
        call("session_start", {"session_key": KEY16, "started_at": "2026-01-01T00:00:00+00:00",
                               "session_type": "practice"}, "demo/s")
    assert e.value.code == "bad_request" and "catalog" in str(e.value)
    assert call("catalog", CATALOG, "demo/c")["layouts"] == 1
    start = {"session_key": KEY16, "started_at": "2026-01-01T00:00:00+00:00",
             "session_type": "practice", "car_id": "car_a", "track_id": "ring",
             "layout_id": "full"}
    assert call("session_start", start, "demo/s")["session_id"] == 1
    assert call("session_start", start, "demo/s")["session_id"] == 1      # the same key: repeat
    with pytest.raises(RemoteError) as e:
        call("session_start", start, "demo/s2")
    assert e.value.code == "bad_request" and "new one" in str(e.value)
    assert call("pit_exit", {"session_key": KEY16, "seq": 1, "at": "2026-01-01T00:00:01+00:00"},
                "demo/p1")["new_stint"] is False
    assert call("lap", lap(1), "demo/l1")["lap"] == 1
    with pytest.raises(RemoteError) as e:                      # a field the write API lacks
        call("lap", lap(2, bogus=1), "demo/l2")
    assert e.value.code == "bad_request" and "bogus" in str(e.value)
    with pytest.raises(RemoteError) as e:                      # seq must increase
        call("lap", lap(1), "demo/l1b")
    assert e.value.code == "bad_request"
    assert call("pit_exit", {"session_key": KEY16, "seq": 2, "at": "2026-01-01T00:00:02+00:00"},
                "demo/p2")["stint"] == 2
    with pytest.raises(RemoteError) as e:
        peer.call("pitwall.write.lap", lap(3), idempotency_key=None)
    assert e.value.code in ("bad_request", "idempotency_required")
    assert call("session_end", {"session_key": KEY16, "ended_at": "2026-01-01T00:01:00+00:00"},
                "demo/e")["laps"] == 1
    assert core.ended.is_set()
    with pytest.raises(RemoteError) as e:
        call("lap", lap(4), "demo/l4")
    assert e.value.code == "session_closed"
    with pytest.raises(RemoteError) as e:
        call("lap", {**lap(1), "session_key": "ffffffffffffffff"}, "demo/lx")
    assert e.value.code == "unknown_session"
    assert [x.ik for x in core.writes(accepted=True)] == [
        "demo/c", "demo/s", "demo/s", "demo/p1", "demo/l1", "demo/p2", "demo/e"]


def test_refuse_next_scripts_errors_and_a_repeat_stores_nothing_twice(core):
    peer = dial(core)
    core.refuse_next("busy", retry_ms=50)
    core.refuse_next("bad_request", method="pitwall.write.lap")
    with pytest.raises(RemoteError) as e:
        peer.call("pitwall.write.catalog", CATALOG, idempotency_key="demo/c")
    assert (e.value.code, e.value.retry_ms) == ("busy", 50)
    peer.call("pitwall.write.catalog", CATALOG, idempotency_key="demo/c")
    peer.call("pitwall.write.catalog", CATALOG, idempotency_key="demo/c")
    entries = core.writes()
    assert [(x.error, x.replayed) for x in entries] == [("busy", False), (None, False),
                                                        (None, True)]
    assert digest(entries[0].params) == digest(entries[2].params)
    assert core.mark() == 3 and len(core.since(1)) == 2


def test_a_scripted_bye_and_a_restart_on_the_same_pipe(core):
    core.bye_next("unauthorized")
    first = dial(core)
    assert core.wait(lambda: first.close_code == "unauthorized", 5)
    second = dial(core)
    assert core.wait(lambda: core.linked, 5)
    core.stop()
    assert core.wait(lambda: second.close_code is not None, 5) and not core.linked
    core.start()
    third = dial(core)
    assert core.wait(lambda: core.linked, 5) and third.info.app == "pitwall"


def test_channels_decode_both_forms():
    raw = {"encoding": "f32", "data": array("f", [1.0, 2.5]).tobytes()}
    assert decode_channel(raw) == [1.0, 2.5] and decode_channel([1, 2]) == [1.0, 2.0]
    with pytest.raises(ValueError):
        decode_channel({"encoding": "i16", "data": b"\x00\x00"})
