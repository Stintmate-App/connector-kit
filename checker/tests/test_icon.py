"""The declared icon (F.icon): the skeleton's passes, each fault fails, no key is a SKIP."""

from __future__ import annotations

import json
import shutil
import struct
import zlib
from pathlib import Path

import pytest
from conftest import SKELETON, run_cli, skeleton_descriptor

from connector_check import descriptor as D
from connector_check.report import FAIL, PASS, SKIP
from test_static import lines_by_id


def png(path: Path, width: int, height: int, pad: int = 0) -> Path:
    """A valid grey PNG of the given size (`pad` extra bytes in an ancillary chunk)."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data)))
    rows = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    data = (D.PNG_SIGNATURE + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + (chunk(b"tEXt", b"x\x00" + b"a" * pad) if pad else b"")
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    path.write_bytes(data)
    return path


def check_icon(tmp_path: Path, **override) -> tuple[int, dict[str, str], str]:
    path = skeleton_descriptor(tmp_path, **override)
    code, out = run_cli("--descriptor", str(path), "--only", "F.icon", "--level", "features")
    return code, lines_by_id(out), out


def test_the_skeletons_icon_passes(tmp_path):
    copy = tmp_path / "Skeleton Connector" / "icon.png"       # a path with spaces
    copy.parent.mkdir()
    shutil.copy2(SKELETON / "icon.png", copy)
    assert D.icon_problems({"icon": str(copy)}) == []
    code, got, out = check_icon(tmp_path, icon=str(copy))
    assert got == {"F.icon": PASS} and code == 0, out


def test_the_skeleton_icon_is_a_neutral_256_px_square():
    data = (SKELETON / "icon.png").read_bytes()
    assert data[:8] == D.PNG_SIGNATURE and len(data) < D.ICON_MAX_BYTES
    assert struct.unpack(">II", data[16:24]) == (256, 256)


def test_a_descriptor_without_an_icon_skips_the_check(tmp_path):
    code, got, out = check_icon(tmp_path)
    assert got == {"F.icon": SKIP} and "no icon" in out and code == 3


def test_the_check_runs_only_at_the_features_level(tmp_path):
    path = skeleton_descriptor(tmp_path, icon=r"\server\share\icon.png")
    code, out = run_cli("--descriptor", str(path), "--only", "M1")
    assert "F.icon" not in out


def faults(tmp_path: Path) -> dict[str, dict]:
    good = png(tmp_path / "good.png", 256, 256)
    return {
        "unc": {"icon": r"\server\share\icon.png"},
        "device": {"icon": "\\?\\" + str(good)},
        "relative": {"icon": "icon.png"},
        "not_png_name": {"icon": str(good.with_suffix(".jpg"))},
        "missing": {"icon": str(tmp_path / "gone.png")},
        "not_a_string": {"icon": 7},
        "not_a_png": {"icon": str(tmp_path / "text.png")},
        "too_big": {"icon": str(png(tmp_path / "big.png", 256, 256, pad=600 * 1024))},
        "not_square": {"icon": str(png(tmp_path / "wide.png", 300, 200))},
        "too_small": {"icon": str(png(tmp_path / "small.png", 16, 16))},
        "too_large": {"icon": str(png(tmp_path / "huge.png", 1025, 1025))},
    }


@pytest.mark.parametrize("name", ["unc", "device", "relative", "not_png_name", "missing",
                                  "not_a_string", "not_a_png", "too_big", "not_square",
                                  "too_small", "too_large"])
def test_a_planted_icon_fault_fails(name, tmp_path):
    (tmp_path / "text.png").write_text("not a png", encoding="utf-8")
    (tmp_path / "good.jpg").write_bytes(b"x")
    code, got, out = check_icon(tmp_path, **faults(tmp_path)[name])
    assert got == {"F.icon": FAIL} and code == 1, out
    assert D.icon_problems(json.loads((tmp_path / "skeleton.json").read_text())) != []


def test_limits_are_inclusive(tmp_path):
    for side in (32, 1024):
        assert D.icon_problems({"icon": str(png(tmp_path / f"{side}.png", side, side))}) == []
    upper = png(tmp_path / "upper.png", 64, 64, pad=D.ICON_MAX_BYTES)
    assert upper.stat().st_size > D.ICON_MAX_BYTES
    assert D.icon_problems({"icon": str(upper).replace("\\", "/")}) != []
    assert D.icon_problems({"icon": str(png(tmp_path / "UPPER.PNG", 64, 64))}) == []
