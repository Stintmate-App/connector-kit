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
            "sim_name": "Demo", "service": "recorder",
            "product": "6f1c2a84-3b7e-4d59-9a0c-5e8d7b2f1a43"} if meta is None else meta
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


def test_a_two_service_connector_is_seen_as_the_union_of_its_connections():
    from types import SimpleNamespace

    def peer(methods, channels, answer):
        return SimpleNamespace(decl=SimpleNamespace(methods=methods, channels=channels),
                               call=lambda name, params, timeout=0: answer)

    core = FakeCore("unused", b"k" * 32, json.loads(
        (KIT / "docs" / "write-api.schema.json").read_text(encoding="utf-8")))
    assert core.offered() is None
    core.peers.extend([peer(["connector.live_frame"], [], "frame"),
                       peer(["connector.action"], ["connector.telemetry"], "action")])
    assert core.offered() == ({"connector.live_frame", "connector.action"},
                              {"connector.telemetry"})
    assert core.call("connector.live_frame") == "frame"
    assert core.call("connector.action") == "action"


def test_layouts_come_from_every_catalog_the_run_saw(tmp_path):
    from connector_check import session
    from connector_check.fakecore import Entry
    entries = [Entry(0, 0.0, "pitwall.write.catalog", "demo/c", CATALOG, replayed=True),
               Entry(1, 0.0, "pitwall.write.catalog", "demo/d", CATALOG, error="busy")]
    assert session.layouts(entries) == {("ring", "full"): 1000.0}
    assert session.layouts(entries[1:]) == {}


def test_hello_rules_need_a_valid_product():
    good = info().meta
    for bad in (None, "", "6F1C2A84-3B7E-4D59-9A0C-5E8D7B2F1A43",           # missing, empty, upper case
                "6f1c2a84-3b7e-3d59-9a0c-5e8d7b2f1a43",                     # version 3
                "6f1c2a84-3b7e-4d59-0a0c-5e8d7b2f1a43", "not-a-uuid"):      # variant 0, no uuid
        meta = {**good, "product": bad}
        if bad is None:
            del meta["product"]
        assert "product" in FakeCore.hello_problem(info(meta=meta)), bad


def test_simulator_data_kinds_records_and_limits(core):
    peer = dial(core)
    call = lambda m, p, ik: peer.call(f"pitwall.write.{m}", p, idempotency_key=ik)  # noqa: E731
    call("catalog", CATALOG, "demo/c")
    call("session_start", {"session_key": KEY16, "started_at": "2026-01-01T00:00:00+00:00",
                           "session_type": "practice"}, "demo/s")
    kinds = {"kinds": [{"kind": "stall_tyres", "description": "d", "topic": "tyres",
                        "fields": {"wheels.*.tread_mid": {"meaning": "m", "unit": "fraction"}}}]}
    assert call("sim_data_kinds", kinds, "demo/k1") == {
        "ok": True, "type": "sim_data_kinds", "kinds": 1, "changed": ["stall_tyres"]}
    assert call("sim_data_kinds", kinds, "demo/k2")["changed"] == []
    record = {"session_key": KEY16, "seq": 1, "taken_at": "2026-01-01T00:00:01+00:00",
              "kind": "stall_tyres", "data": {"wheels": {"fl": {"tread_mid": 0.98}}}}
    assert call("sim_data", record, "demo/d1") == {
        "ok": True, "type": "sim_data", "session_id": 1, "record": 1, "stint": 1, "stored": True}
    for seq, data, text in ((2, {"a": "x" * 501}, "a string of more than 500"),
                            (3, {"a.b": 1}, "does not match"),
                            (4, {"k": ["x" * 400] * 41}, "larger than 16384 bytes"),
                            (5, {"a": {"b": {"c": {"d": {"e": {"f": {}}}}}}}, "nested deeper")):
        with pytest.raises(RemoteError) as e:
            call("sim_data", {**record, "seq": seq, "data": data}, f"demo/d{seq}")
        assert e.value.code == "bad_request" and text in str(e.value), (seq, str(e.value))
    with pytest.raises(RemoteError):
        call("sim_data_kinds", {"kinds": []}, "demo/k3")


def test_the_simulator_data_limits_are_the_contracts():
    from connector_check.fakecore import SIM_DATA_LIMITS

    limits = json.loads((KIT / "contract.json").read_text())["limits"]
    assert {f"sim_data_max_{k}": v for k, v in SIM_DATA_LIMITS.items()} == {
        k: v for k, v in limits.items() if k.startswith("sim_data_max_")}


def test_lap_positions_are_accepted_for_a_closed_session_and_checked(core):
    import struct

    def chan(n):
        return {"encoding": "f32", "data": struct.pack(f"<{n}f", *range(n))}

    peer = dial(core)
    call = lambda m, p, ik: peer.call(f"pitwall.write.{m}", p, idempotency_key=ik)  # noqa: E731
    call("catalog", CATALOG, "demo/c")
    call("session_start", {"session_key": KEY16, "started_at": "2026-01-01T00:00:00+00:00",
                           "session_type": "practice"}, "demo/s")
    call("lap", {**lap(1), "channels": {"t": chan(4), "dist": chan(4), "speed": chan(4)}},
         "demo/l1")
    call("session_end", {"session_key": KEY16, "ended_at": "2026-01-01T01:00:00+00:00"}, "demo/e")
    msg = {"session_key": KEY16, "lap_seq": 1, "channels": {"x": chan(4), "z": chan(4)}}
    assert call("lap_positions", msg, "demo/p1") == {
        "ok": True, "type": "lap_positions", "session_id": 1, "lap": 1, "lap_id": 1,
        "channels": ["x", "z"]}
    for bad, text in (({**msg, "lap_seq": 7}, "no stored lap with seq 7"),
                      ({**msg, "channels": {"x": chan(5), "z": chan(5)}}, "5 samples, lap 1 has 4"),
                      ({**msg, "channels": {"x": chan(4)}}, "'z' is a required property")):
        with pytest.raises(RemoteError) as e:
            call("lap_positions", bad, f"demo/{text[:6]}")
        assert e.value.code == "bad_request" and text in str(e.value), text
