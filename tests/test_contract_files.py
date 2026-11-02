"""The kit's machine-readable contract: schemas compile, the sample validates, and
``contract.json`` carries the keys the documents name.

Run from the repository root:
``uv run --no-project --with pytest --with jsonschema pytest tests``.
"""

import copy
import json
import re
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SCHEMAS = ["live-frame", "control-state", "descriptor"]


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def validator(name):
    return jsonschema.Draft202012Validator(load(DOCS / f"{name}.schema.json"))


@pytest.mark.parametrize("name", SCHEMAS)
def test_each_schema_compiles(name):
    jsonschema.Draft202012Validator.check_schema(load(DOCS / f"{name}.schema.json"))


def test_the_sample_frame_validates_and_has_every_group():
    sample = load(DOCS / "live-frame.sample.json")
    assert list(validator("live-frame").iter_errors(sample)) == []
    for group in ("session", "track", "car", "lap", "splits", "conditions", "participants"):
        assert group in sample, group
    assert any(p["is_player"] for p in sample["participants"])


@pytest.fixture
def frame():
    return load(DOCS / "live-frame.sample.json")


@pytest.mark.parametrize("phase", ["garage", "on_track", "paused"])
@pytest.mark.parametrize("missing", ["session", "track", "car", "lap", "participants"])
def test_a_frame_in_a_session_needs_every_group(frame, phase, missing):
    frame["phase"] = phase
    del frame[missing]
    assert list(validator("live-frame").iter_errors(frame))


def test_a_frame_in_a_session_needs_a_player_participant(frame):
    for p in frame["participants"]:
        p["is_player"] = False
    assert list(validator("live-frame").iter_errors(frame))
    frame["participants"] = []
    assert list(validator("live-frame").iter_errors(frame))


def test_a_frame_in_the_menus_needs_only_sim_and_phase():
    assert list(validator("live-frame").iter_errors({"sim": "examplesim", "phase": "menus"})) == []
    assert list(validator("live-frame").iter_errors({"phase": "menus"}))


def test_a_frame_allows_unknown_fields(frame):
    frame["extra"] = {"anything": 1}
    frame["car"]["extra"] = 1
    assert list(validator("live-frame").iter_errors(frame)) == []


def test_a_frame_needs_the_layout_length(frame):
    del frame["track"]["length_m"]
    assert list(validator("live-frame").iter_errors(frame))


def test_the_frame_files_name_no_simulator():
    text = (DOCS / "live-frame.schema.json").read_text(encoding="utf-8") + \
        (DOCS / "live-frame.sample.json").read_text(encoding="utf-8")
    assert not re.search(r"ams2|automobilista|assetto|iracing|rfactor|raceroom", text, re.I)


CONTROL_STATE = {
    "app": "example-connector-ui", "pid": 100, "version": "1.0.0", "managed": False,
    "parent_pid": None, "window": "hidden", "exiting": False, "autostart": None, "level": "ok",
    "services": [{"id": "recorder", "name": "Recorder", "state": "waiting", "label": "Waiting",
                  "level": "ok", "running": True, "ownership": "own", "pid": 200,
                  "forced_stop": False}],
    "connector": {"id": "example", "name": "Example", "version": "1.0.0", "sim": "examplesim",
                  "sim_name": "Example Simulator"},
    "game_running": False, "pitwall": "waiting", "outbox": {"pending": 0, "oldest_at": None},
    "start_with_app": [],
}


def test_a_control_state_validates_and_a_free_service_state_is_legal():
    v = validator("control-state")
    assert list(v.iter_errors(CONTROL_STATE)) == []
    state = copy.deepcopy(CONTROL_STATE)
    state["services"][0]["state"] = "calibrating"
    state["extra"] = 1
    assert list(v.iter_errors(state)) == []


@pytest.mark.parametrize("key", ["services", "connector", "game_running", "pitwall", "outbox",
                                 "start_with_app", "level", "pid"])
def test_a_control_state_needs_its_required_fields(key):
    state = copy.deepcopy(CONTROL_STATE)
    del state[key]
    assert list(validator("control-state").iter_errors(state))


def test_the_pitwall_field_is_the_declared_enum():
    contract = load(ROOT / "contract.json")
    enum = load(DOCS / "control-state.schema.json")["properties"]["pitwall"]["enum"]
    assert enum == contract["vocabularies"]["control_pitwall"]
    state = copy.deepcopy(CONTROL_STATE)
    state["pitwall"] = "idle"
    assert list(validator("control-state").iter_errors(state))


def descriptor_example():
    text = (DOCS / "descriptor.md").read_text(encoding="utf-8")
    return json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))


def test_the_documented_descriptor_validates():
    example = descriptor_example()
    assert list(validator("descriptor").iter_errors(example)) == []
    example.update({"window": True, "features": ["live_frame", "made_up"], "unknown_key": 1})
    assert list(validator("descriptor").iter_errors(example)) == []


@pytest.mark.parametrize("key", ["schema", "id", "name", "version", "launcher", "args",
                                 "control_protocol", "endpoint", "mutex", "services"])
def test_a_descriptor_needs_its_required_keys(key):
    example = descriptor_example()
    del example[key]
    assert list(validator("descriptor").iter_errors(example))


def test_a_descriptor_refuses_another_control_protocol_schema_and_id():
    for key, value in (("control_protocol", "stintmate-ipc/3"), ("schema", 2), ("id", "Bad Id")):
        example = descriptor_example()
        example[key] = value
        assert list(validator("descriptor").iter_errors(example)), key


def test_the_contract_json_carries_the_new_keys():
    contract = load(ROOT / "contract.json")
    assert contract["contract_version"] == 2
    assert contract["budgets"] == {"idle_cpu_percent_of_core": 0.5, "idle_sample_s": 60,
                                   "game_check_min_interval_s": 2}
    assert contract["write_errors"]["non_retryable"] == [
        "bad_request", "session_closed", "unknown_session", "too_large", "unknown_method",
        "version_unsupported"]
    vocab = contract["vocabularies"]
    assert vocab["control_pitwall"] == ["connected", "waiting", "refused"]
    assert vocab["minimum_channels"] == ["t", "dist", "speed", "throttle", "brake", "g_lat"]
    assert vocab["position_channels"] == ["x", "z"]
    assert {"stopped", "starting", "loading", "connecting", "waiting_models", "waiting",
            "recording", "running"} == set(vocab["service_states"])
    assert len(vocab["features"]) == len(set(vocab["features"])) == 21
    assert not {"budgets", "write_errors", "contract_version"} & set(contract["limits"])


def test_the_channel_vocabulary_holds_the_minimum_and_position_channels():
    vocab = load(ROOT / "contract.json")["vocabularies"]
    channels = set(vocab["channels"])
    assert set(vocab["minimum_channels"]) | set(vocab["position_channels"]) <= channels


def test_the_documents_list_exactly_the_declared_features():
    features = load(ROOT / "contract.json")["vocabularies"]["features"]
    descriptor = (DOCS / "descriptor.md").read_text(encoding="utf-8")
    levels = (DOCS / "levels.md").read_text(encoding="utf-8")
    parity = levels.split("## 6. Parity")[1].split("## 7.")[0]
    for name in features:
        assert f"`{name}`" in descriptor, name
    named = set(re.findall(r"\| [^|]*\(`([a-z_]+)`\) \|", parity))
    assert named == set(features)


def test_the_non_retryable_codes_in_the_outbox_text_match_the_contract():
    codes = load(ROOT / "contract.json")["write_errors"]["non_retryable"]
    text = (DOCS / "contract.md").read_text(encoding="utf-8")
    step = text.split("### 5.4 Laps while Pitwall is down: the outbox")[1].split("\n4. ")[0]
    for code in codes:
        assert f"`{code}`" in step, code


def test_the_budget_and_the_link_rule_read_the_same_everywhere():
    budget = "a connector uses at most 0.5 % of one core,\n> measured over 60 s"
    levels = (DOCS / "levels.md").read_text(encoding="utf-8")
    connectors = (DOCS / "connectors.md").read_text(encoding="utf-8").replace("   > ", "> ")
    assert budget in levels and budget in connectors
    rule = "Never\n> disconnect on purpose while your recorder runs."
    protocol = (DOCS / "connector-protocol.md").read_text(encoding="utf-8").replace("  > ", "> ")
    for text in (levels, connectors, protocol):
        assert rule in text
