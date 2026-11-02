"""The outbox: every write goes to disk first, keeps its key, and survives a Pitwall that is
away, busy or refusing (docs/contract.md section 5.4)."""

from __future__ import annotations

import re

import pytest
from stintmate_ipc import KeyFile
from stintmate_ipc._codec import unpackb

import skeleton_connector as S
from skeleton_connector.recorder import Recorder
from skeleton_connector.session import SCRIPT
from support import wait

SESSION_WRITES = 1 + 1 + len(SCRIPT) + 1          # catalog, start, the script, end
RETRY_CODES = ["connector_busy", "unavailable", "busy", "writer_busy", "timeout",
               "shutting_down"]


def _folder(ws):
    return ws / "connectors" / S.CONNECTOR_ID


def _recorder(ws, pitwall):
    return Recorder(pitwall.pipe, KeyFile(ws / "ipc" / "key"), ws, lap_period_s=0,
                    game_check_s=0.05)


def _open_game(ws):
    flag = _folder(ws) / "fake_game"
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()
    return flag


def _files(folder):
    return sorted(folder.glob("*.msgpack"))


@pytest.mark.parametrize("code", RETRY_CODES)
def test_retryable_codes_keep_the_write(workspace, pitwall, code):
    ws, _ = workspace
    pitwall.refuse_next(code, retry_ms=50)
    rec = _recorder(ws, pitwall)
    rec.start()
    try:
        _open_game(ws)
        assert pitwall.ended.wait(15)
    finally:
        rec.stop()
    refused = [a for a in pitwall.attempts if a[2] == code]
    assert len(refused) == 1
    method, ik, _ = refused[0]
    assert method == "pitwall.write.catalog"
    # the same call came again with the same key, and was stored once
    assert [i for m, i, e in pitwall.attempts if i == ik and e is None] == [ik]
    assert pitwall.kinds().count("catalog") == 1
    assert pitwall.kinds() == ["catalog", "session_start"] + [k for k, *_ in SCRIPT] + [
        "session_end"]
    assert not (_folder(ws) / "outbox" / "rejected").exists()


def test_a_refused_write_goes_aside_and_the_next_goes_on(workspace, pitwall):
    ws, _ = workspace
    pitwall.refuse_next("bad_request")
    rec = _recorder(ws, pitwall)
    rec.start()
    try:
        _open_game(ws)
        assert pitwall.ended.wait(15)
        assert wait(lambda: rec.outbox.summary()["pending"] == 0)
    finally:
        rec.stop()
    aside = list((_folder(ws) / "outbox" / "rejected").glob("*.msgpack"))
    assert len(aside) == 1 and unpackb(aside[0].read_bytes())["method"] == "pitwall.write.catalog"
    assert pitwall.kinds()[0] == "session_start"      # the write after it went on
    assert rec.outbox.summary()["rejected"] == 1


def test_writes_survive_pitwall_going_away(workspace, pitwall):
    ws, _ = workspace
    pitwall.stop()
    rec = _recorder(ws, pitwall)
    rec.start()
    try:
        _open_game(ws)
        assert wait(lambda: rec.outbox.summary()["pending"] == SESSION_WRITES)
        assert pitwall.calls == []
        status = rec.status.value["outbox"]
        assert status["pending"] == SESSION_WRITES and status["oldest_at"]
        pitwall.start()
        assert pitwall.ended.wait(20)
    finally:
        rec.stop()
    assert pitwall.kinds() == ["catalog", "session_start"] + [k for k, *_ in SCRIPT] + [
        "session_end"]
    assert _files(_folder(ws) / "outbox") == []


def test_writes_survive_a_restart_of_the_connector(workspace, pitwall):
    ws, _ = workspace
    pitwall.stop()
    first = _recorder(ws, pitwall)
    first.start()
    flag = _open_game(ws)
    assert wait(lambda: first.outbox.summary()["pending"] == SESSION_WRITES)
    first.stop()
    flag.unlink()
    kept = [unpackb(p.read_bytes())["ik"] for p in _files(_folder(ws) / "outbox")]
    assert len(kept) == SESSION_WRITES

    pitwall.start()
    second = _recorder(ws, pitwall)
    second.start()
    try:
        assert pitwall.ended.wait(20)
    finally:
        second.stop()
    stored = [p["_ik"] for n, p in pitwall.calls if n.startswith("pitwall.write.")]
    assert stored == kept                              # the same keys, in the same order


def test_outbox_files_have_the_documented_format(workspace, pitwall):
    ws, _ = workspace
    pitwall.stop()
    rec = _recorder(ws, pitwall)
    rec.start()
    try:
        _open_game(ws)
        assert wait(lambda: rec.outbox.summary()["pending"] == SESSION_WRITES)
    finally:
        rec.stop()
    folder = _folder(ws) / "outbox"
    files = _files(folder)
    assert [int(f.stem) for f in files] == list(range(1, SESSION_WRITES + 1))
    assert all(re.fullmatch(r"\d{20}\.msgpack", f.name) for f in files)
    assert not list(folder.glob("*.tmp"))
    docs = [unpackb(f.read_bytes()) for f in files]
    for doc in docs:
        assert set(doc) == {"method", "v", "ik", "p", "at"} and doc["v"] == 1
        assert doc["method"].startswith("pitwall.write.") and doc["ik"].startswith("skeleton/")
        assert isinstance(doc["p"], dict) and doc["at"]
    assert len({d["ik"] for d in docs}) == SESSION_WRITES
    lap = next(d for d in docs if d["method"].endswith(".lap"))
    assert isinstance(lap["p"]["channels"]["t"]["data"], bytes)


def test_a_call_timeout_keeps_the_write_and_the_sender_goes_on(workspace, tmp_path):
    """CallTimeout is not a RemoteError: the sender must survive it and send again."""
    from stintmate_ipc import CallTimeout

    from skeleton_connector.outbox import Outbox

    class Client:
        connected = True

        def __init__(self):
            self.calls = []

        def call(self, method, params, timeout, idempotency_key):
            self.calls.append(idempotency_key)
            if len(self.calls) == 1:
                raise CallTimeout("no answer")
            return {"ok": True}

    client = Client()
    box = Outbox(tmp_path / "outbox", client).start()
    try:
        box.put("pitwall.write.lap", "k/1", {"x": 1})
        assert wait(lambda: box.summary()["pending"] == 0)
    finally:
        box.close(0)
    assert client.calls == ["k/1", "k/1"]
    assert not (tmp_path / "outbox" / "rejected").exists()
