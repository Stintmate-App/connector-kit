import os
import uuid

import pytest

from support import FakePitwall


@pytest.fixture(autouse=True)
def dev_instance(monkeypatch):
    """Every test runs under a tag of its own, so no pipe or mutex is a real one."""
    monkeypatch.setenv("STINTMATE_DEV_INSTANCE", "t" + uuid.uuid4().hex[:10])


@pytest.fixture
def workspace(tmp_path):
    """A temporary workspace with the key the hub's onboarding leaves."""
    ws = tmp_path / "Stintmate"
    (ws / "ipc").mkdir(parents=True)
    key = os.urandom(32)
    (ws / "ipc" / "key").write_text(key.hex() + "\n", encoding="ascii")
    return ws, key


@pytest.fixture
def pitwall(workspace):
    fake = FakePitwall(workspace[1])
    try:
        yield fake
    finally:
        fake.stop()
