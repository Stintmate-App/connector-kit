"""Each sanity rule, both ways, over arrays (no connector involved)."""

from __future__ import annotations

import math

from connector_check import sanity as S

LENGTH, TIME, N = 1000.0, 60.0, 200


def good(**over) -> S.Lap:
    u = [i / N for i in range(N)]
    steer = [math.sin(4 * math.pi * x) for x in u]
    mean = 3.6 * LENGTH / TIME
    ch = {"t": [x * TIME for x in u], "dist": [x * LENGTH for x in u],
          "speed": [mean * (1 + 0.3 * math.cos(4 * math.pi * x)) for x in u],
          "throttle": [min(1.0, max(0.0, 0.5 + math.cos(4 * math.pi * x))) for x in u],
          "brake": [min(1.0, max(0.0, -0.3 - math.cos(4 * math.pi * x)) * 1.5) for x in u],
          "steer": steer, "g_lat": [2.0 * s for s in steer],
          "x": [LENGTH / (2 * math.pi) * math.cos(2 * math.pi * x) for x in u],
          "z": [LENGTH / (2 * math.pi) * math.sin(2 * math.pi * x) for x in u]}
    ch.update(over.pop("channels", {}))
    return S.Lap(ch, over.pop("length_m", LENGTH), over.pop("time_s", TIME), name="lap", **over)


def verdict(lap: S.Lap, rule: str) -> str:
    return getattr(S, rule)(lap)[0]


def test_a_clean_lap_passes_every_rule():
    assert {v.rule: v.status for v in S.evaluate([good()])} == {
        **{r: S.OK for r in S.RULES}, "fuel": S.NA}


def test_time_must_increase_and_end_at_the_lap_time():
    assert verdict(good(channels={"t": [0.0] + [1.0] * (N - 1)}), "t_increasing") == S.BAD
    assert verdict(good(time_s=70.0), "t_increasing") == S.BAD      # last t is 59.7 s
    assert verdict(good(), "t_increasing") == S.OK


def test_distance_runs_forward_from_the_line_to_the_length():
    assert verdict(good(channels={"dist": [LENGTH - i * LENGTH / N for i in range(N)]}),
                   "dist_increasing") == S.BAD
    assert verdict(good(channels={"dist": [100 + i * LENGTH / N for i in range(N)]}),
                   "dist_length") == S.BAD
    assert verdict(good(length_m=3000.0), "dist_length") == S.BAD
    assert verdict(good(), "dist_increasing") == verdict(good(), "dist_length") == S.OK


def test_pedals_are_zero_to_one_and_used():
    assert verdict(good(channels={"throttle": [100 * x for x in good().channels["throttle"]]}),
                   "pedals_range") == S.BAD                          # percent
    assert verdict(good(channels={"brake": [0.0] * N}), "pedals_range") == S.MANUAL   # an oval lap
    assert verdict(good(channels={"throttle": [0.2] * N}), "pedals_range") == S.BAD
    assert verdict(good(), "pedals_range") == S.OK


def test_speed_is_kmh():
    ms = [v / 3.6 for v in good().channels["speed"]]
    assert verdict(good(channels={"speed": ms}), "speed_unit") == S.BAD
    assert verdict(good(channels={"speed": [900.0] * N}), "speed_unit") == S.BAD
    assert verdict(good(), "speed_unit") == S.OK


def test_g_lat_is_in_g_not_m_s2():
    ms2 = [x * 9.81 * 3 for x in good().channels["g_lat"]]
    assert verdict(good(channels={"g_lat": ms2}), "g_lat_unit") == S.BAD
    assert verdict(good(channels={"g_lat": [0.01] * N}), "g_lat_unit") == S.BAD
    assert verdict(good(), "g_lat_unit") == S.OK


def test_g_lat_sign_follows_steer_and_is_manual_without_it():
    flipped = [-x for x in good().channels["g_lat"]]
    assert verdict(good(channels={"g_lat": flipped}), "g_lat_sign") == S.BAD
    assert verdict(good(), "g_lat_sign") == S.OK
    no_steer = good()
    del no_steer.channels["steer"]
    assert verdict(no_steer, "g_lat_sign") == S.MANUAL
    assert verdict(good(channels={"g_lat": [0.5] * N}), "g_lat_sign") == S.MANUAL


def test_the_position_path_matches_the_length():
    assert verdict(good(channels={"x": [2 * v for v in good().channels["x"]]}),
                   "position_length") == S.BAD
    assert verdict(good(), "position_length") == S.OK
    bare = good()
    del bare.channels["x"]
    assert verdict(bare, "position_length") == S.NA


def test_fuel_used_is_between_zero_and_the_start():
    assert verdict(good(fuel_start_l=50.0, fuel_used_l=2.0), "fuel") == S.OK
    assert verdict(good(fuel_start_l=50.0, fuel_used_l=60.0), "fuel") == S.BAD
    assert verdict(good(fuel_start_l=50.0, fuel_used_l=0.0), "fuel") == S.BAD
    assert verdict(good(fuel_used_l=2.0), "fuel") == S.BAD


def test_a_rule_fails_only_when_half_the_laps_break_it():
    bad_speed = {"speed": [v / 3.6 for v in good().channels["speed"]]}
    one_of_three = S.evaluate([good(), good(), good(channels=bad_speed)])
    assert {v.rule: v.status for v in one_of_three}["speed_unit"] == S.OK
    two_of_three = S.evaluate([good(), good(channels=bad_speed), good(channels=bad_speed)])
    got = {v.rule: v for v in two_of_three}["speed_unit"]
    assert got.status == S.BAD and "2 of 3 laps" in got.evidence
