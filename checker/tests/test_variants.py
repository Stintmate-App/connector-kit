"""A correct connector that differs from the skeleton must still pass. Each variant below is the
skeleton changed the way another author could correctly change it; the checks it touches must not
fail. The variants run in parallel, one checker process each."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from test_planted_faults import FAST, LAP_TIME, p, run_fault

CATALOG_LINE = '        self._write("catalog", CATALOG, f"{CONNECTOR_ID}/catalog/{sha12(CATALOG)}")\n'
ON_CONNECT = "        self.message = self.refused = None\n        self._changed()\n        # Optional"

# name: (patches, descriptor changes, checks to run, {check: expected status}, extra options)
VARIANTS = {
    "catalog_once_per_connection": (
        FAST + [("recorder.py", CATALOG_LINE, ""),
                ("recorder.py", ON_CONNECT, ON_CONNECT.replace(
                    "        self._changed()\n",
                    CATALOG_LINE + "        self._changed()\n", 1))],
        {}, "M10.order,M10.laps,M12.sanity",
        {"M10.order": "PASS", "M10.laps": "PASS", "M12.sanity": "PASS"}, []),
    "no_sim_lap": (
        FAST + p("recorder.py", '"valid": valid, "sim_lap": sim_lap, "on_track": True,',
                 '"valid": valid, "on_track": True,'),
        {}, "M10.laps,M12.sanity", {"M10.laps": "PASS", "M12.sanity": "PASS"}, []),
    "timed_out_and_in_laps": (
        FAST + [LAP_TIME], {}, "M10.laps", {"M10.laps": "PASS"}, []),
    "no_catalog_method": (
        p("control.py", 'self.endpoint.method("control.catalog.get",',
          'self.endpoint.method("control.catalog.getx",'),
        {}, "M18.texts", {"M18.texts": "MANUAL"}, []),
}


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory):
    root = tmp_path_factory.mktemp("variants")
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(run_fault, name, root, (v[0], v[1], "", v[2], v[4]))
                   for name, v in VARIANTS.items()}
        return {name: f.result() for name, f in futures.items()}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_a_correct_variant_passes(name, outcomes):
    code, got = outcomes[name]
    for check, status in VARIANTS[name][3].items():
        assert got.get(check, {}).get("status") == status, got.get("_out")
    assert code == 0, got.get("_out")
