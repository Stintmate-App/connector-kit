"""The examples load, and the example live frame is a valid frame."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from stintmate_ipc import RemoteError

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
NAMES = ("live_frame", "capture_target", "setups", "options")


def load(name: str):
    spec = importlib.util.spec_from_file_location(f"skeleton_example_{name}", EXAMPLES / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_examples_load(tmp_path):
    modules = {name: load(name) for name in NAMES}
    for name in NAMES:
        assert (EXAMPLES / f"{name}.py").read_text(encoding="utf-8").startswith('"""')
    assert (EXAMPLES / "README.md").is_file()
    # capture_target: with no game window the answer is game_not_running
    with pytest.raises(RemoteError) as err:
        modules["capture_target"].capture_target(None)
    assert err.value.code == "game_not_running"
    # setups: the basic fields and extra
    params = modules["setups"].setup_params("0123456789abcdef", 1, "session_start")
    assert params["reason"] == "session_start" and params["seq"] == 1
    assert params["extra"]["diff_preload"]["label"]
    # options: defaults, a file, a wrong type
    options = modules["options"]
    path = tmp_path / "settings.json"
    assert options.load_options(path) == options.DEFAULTS
    path.write_text(json.dumps({"lap_period_s": 5, "announce_laps": "yes", "other": 1}),
                    encoding="utf-8")
    loaded = options.load_options(path)
    assert loaded == {"lap_period_s": 5, "announce_laps": True}
    path.write_text("{", encoding="utf-8")
    assert options.load_options(path) == options.DEFAULTS


def test_the_example_frame_matches_the_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((ROOT.parent / "docs" / "live-frame.schema.json").read_text(encoding="utf-8"))
    frame = load("live_frame").frame_now()
    jsonschema.Draft202012Validator(schema).validate(frame)
    assert sum(1 for p in frame["participants"] if p["is_player"]) == 1
