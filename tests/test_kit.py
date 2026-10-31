"""Checks of the kit itself: public text, links, no binary, the fetch script's integrity check.

Run from the repository root: ``uv run --no-project --with pytest pytest tests``. The
skeleton's own tests are in ``skeleton/tests`` and run from ``skeleton/``.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")
IGNORED = {".git", ".venv", "vendor", "__pycache__", ".pytest_cache"}


def tracked_files():
    """Every file of the kit (the working tree, without the ignored folders)."""
    out = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if path.is_file() and not IGNORED & set(rel.parts):
            out.append(path)
    return out


def text_files():
    return [p for p in tracked_files() if p.suffix in {".md", ".py", ".toml", ".ps1", ".json"}
            or p.name in {"LICENSE", "NOTICE", ".gitignore", ".gitattributes"}]


def test_text_is_public_safe():
    banned = re.compile(r"leona|Leonardo|api_token|C:[\\]Users|Documents[\\]Stintmate", re.I)
    # the conformance test names the private data it must not find
    for path in text_files():
        if path == Path(__file__).resolve():
            continue
        data = path.read_bytes()
        assert not banned.search(data.decode("utf-8")), path
        assert b"\x00" not in data, path


def test_links_stay_inside_the_kit_or_are_public():
    for path in tracked_files():
        if path.suffix != ".md":
            continue
        for m in re.finditer(r"\]\(([^)\s]+)\)", path.read_text(encoding="utf-8")):
            target = m.group(1)
            if target.startswith(("#", "https://")):
                continue
            resolved = (path.parent / target.split("#")[0]).resolve()
            assert ROOT in resolved.parents or resolved == ROOT, f"{path}: {target}"


def test_the_kit_carries_no_binary_and_no_header():
    files = tracked_files()
    assert not [p for p in files if p.suffix in {".whl", ".h", ".dll", ".exe"}]
    assert not (ROOT / "wheels").exists()
    assert (ROOT / "skeleton" / "fetch-ipc.ps1").is_file()


def test_the_skeleton_points_at_the_wheel_the_fetch_script_downloads():
    toml = (ROOT / "skeleton" / "pyproject.toml").read_text(encoding="utf-8")
    fetch = (ROOT / "skeleton" / "fetch-ipc.ps1").read_text(encoding="utf-8")
    version = re.search(r'^\$IpcVersion = "([^"]+)"', fetch, re.M).group(1)
    assert f'path = "vendor/stintmate_ipc-{version}-py3-none-win_amd64.whl"' in toml
    assert 'license = "Apache-2.0"' in toml


def test_native_readme_names_the_release_and_the_override():
    fetch = (ROOT / "skeleton" / "fetch-ipc.ps1").read_text(encoding="utf-8")
    version = re.search(r'^\$IpcVersion = "([^"]+)"', fetch, re.M).group(1)
    readme = (ROOT / "native" / "README.md").read_text(encoding="utf-8")
    assert f"releases/tag/v{version}" in readme
    assert f"stintmate_ipc-{version}-py3-none-win_amd64.whl" in readme
    assert "STINTMATE_IPC_RELEASE" in readme and "_bin/" in readme and "ABI 1.0" in readme


def test_the_licences_are_apache():
    for p in (ROOT / "LICENSE", ROOT / "skeleton" / "LICENSE"):
        assert "Apache License" in p.read_text(encoding="utf-8")[:200], p
    assert "Apache License" in (ROOT / "NOTICE").read_text(encoding="utf-8")


@pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell")
def test_fetch_ipc_rejects_a_corrupted_wheel_and_leaves_nothing(tmp_path):
    skel = tmp_path / "skel"
    shutil.copytree(ROOT / "skeleton", skel, ignore=shutil.ignore_patterns(
        ".venv", "__pycache__", "vendor", ".pytest_cache"))
    release = tmp_path / "release"
    release.mkdir()
    fetch = (skel / "fetch-ipc.ps1").read_text(encoding="utf-8")
    version = re.search(r'^\$IpcVersion = "([^"]+)"', fetch, re.M).group(1)
    wheel = f"stintmate_ipc-{version}-py3-none-win_amd64.whl"
    (release / wheel).write_bytes(b"PK corrupted")
    sums = f"{'0' * 64}  {wheel}\n"
    (release / "SHA256SUMS").write_text(sums, encoding="utf-8")
    env = {**os.environ, "STINTMATE_IPC_RELEASE": str(release)}
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(skel / "fetch-ipc.ps1")], env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "mismatch" in r.stderr
    assert list((skel / "vendor").iterdir()) == []
