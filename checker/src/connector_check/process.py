"""Processes the checker starts. Every one ends with the checker, whatever happens: each is in a
Windows job object that kills its tree when the checker goes, and ``kill_all`` (called in a
``finally`` and at exit) kills the trees it knows of."""

from __future__ import annotations

import atexit
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
_LIVE: list[Proc] = []
_JOB: int | None = None


def _job() -> int | None:
    """One job object for the run, with ``KILL_ON_JOB_CLOSE``: if the checker dies in any way,
    Windows ends every process in it. None when the system refuses (the kill in ``finally``
    still works)."""
    global _JOB
    if _JOB is not None:
        return _JOB or None
    _JOB = 0
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE

        class Basic(ctypes.Structure):
            _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("a", "b", "c", "d", "e", "f")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", Io), ("process_mem", ctypes.c_size_t),
                        ("job_mem", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = Extended()
        info.basic.flags = 0x2000                        # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                wintypes.DWORD]
        if job and k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            _JOB = int(job)
    except Exception:  # noqa: BLE001
        _JOB = 0
    return _JOB or None


def _assign(popen: subprocess.Popen) -> None:
    job = _job()
    if not job:
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject(job, int(popen._handle))        # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


class Proc:
    """A started process and everything it starts."""

    def __init__(self, argv: list[str], env: dict[str, str], log_path: Path, *,
                 cwd: str | None = None, stdin: bool = False) -> None:
        self.argv = argv
        self.log_path = log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(log_path, "wb")                                 # noqa: SIM115
        self.popen = subprocess.Popen(
            argv, env=env, cwd=cwd, stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW)
        _assign(self.popen)
        self.started = time.monotonic()
        self.seen: dict[int, float] = {self.pid: psutil.Process(self.pid).create_time()}
        _LIVE.append(self)

    @property
    def pid(self) -> int:
        return self.popen.pid

    def alive(self) -> bool:
        return self.popen.poll() is None

    @property
    def returncode(self) -> int | None:
        return self.popen.poll()

    def tree(self) -> list[psutil.Process]:
        """The live processes of the tree: this one and every descendant. Also remembers them,
        so a child whose parent already exited is still killed."""
        found: list[psutil.Process] = []
        try:
            root = psutil.Process(self.pid)
            found = [root, *root.children(recursive=True)] if self.alive() else root.children(
                recursive=True)
        except psutil.Error:
            pass
        for p in found:
            try:
                self.seen[p.pid] = p.create_time()
            except psutil.Error:
                pass
        return [p for p in found if _running(p)]

    def log_tail(self, lines: int = 6) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return " | ".join(text.strip().splitlines()[-lines:])

    def write(self, text: str) -> None:
        if self.popen.stdin is not None and self.alive():
            try:
                self.popen.stdin.write(text.encode())
                self.popen.stdin.flush()
            except OSError:
                pass

    def wait(self, timeout: float) -> bool:
        """Whether the process ended within `timeout` seconds."""
        try:
            self.popen.wait(timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def kill_tree(self) -> None:
        self.tree()
        victims = []
        for pid, created in self.seen.items():
            try:
                p = psutil.Process(pid)
                if abs(p.create_time() - created) < 1.0:
                    victims.append(p)
            except psutil.Error:
                pass
        for p in victims:
            try:
                p.kill()
            except psutil.Error:
                pass
        psutil.wait_procs(victims, timeout=5)
        try:
            self.popen.wait(2)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.popen.stdin, self._log):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def _running(p: psutil.Process) -> bool:
    try:
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def kill_all() -> None:
    for proc in list(_LIVE):
        try:
            proc.kill_tree()
        except Exception:  # noqa: BLE001
            pass
        _LIVE.remove(proc)


atexit.register(kill_all)


def run(argv: list[str], env: dict[str, str], log_path: Path, timeout: float) -> tuple[int | None, str]:
    """Run to the end (killed after `timeout`): the exit code, or None, and its output."""
    proc = Proc(argv, env, log_path)
    try:
        done = proc.wait(timeout)
        code = proc.returncode if done else None
    finally:
        proc.kill_tree()
    try:
        return code, log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return code, ""


def mutex_exists(name: str) -> bool:
    """Whether a named mutex exists (it is held by a live process)."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = wintypes.HANDLE
    k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.OpenMutexW(0x00100000, False, name)                    # SYNCHRONIZE
    if not handle:
        return False
    k32.CloseHandle(handle)
    return True


HELPER_SCRIPT = r"""
import json, sys
from stintmate_ipc import PeerInfo, connect
pipe, keyhex, out = sys.argv[1:4]
key = bytes.fromhex(keyhex)
for line in sys.stdin:
    if line.strip() == "quit":
        try:
            peer = connect(pipe, PeerInfo("connector-check", name="managing process",
                                          version="0"), key, timeout=5)
            doc = {"ok": True, "result": peer.call("control.quit", {}, timeout=10)}
        except Exception as exc:
            doc = {"ok": False, "error": repr(exc), "code": getattr(exc, "code", None)}
        open(out, "w").write(json.dumps(doc))
"""


class Helper:
    """The managing process: the connector is started with ``--parent-pid`` of this one. It
    sleeps until told to call ``control.quit`` (a child of the parent may ask; the checker, which
    is neither, may not)."""

    def __init__(self, pipe: str, key: bytes, tmp: Path, env: dict[str, str]) -> None:
        self.out = tmp / "helper-quit.json"
        self.proc = Proc([sys.executable, "-I", "-c", HELPER_SCRIPT, pipe, key.hex(), str(self.out)],
                         env, tmp / "helper.log", stdin=True)

    @property
    def pid(self) -> int:
        return self.proc.pid

    def quit(self, timeout: float = 15.0) -> dict[str, Any]:
        """Make the helper call ``control.quit`` and return what it saw."""
        self.out.unlink(missing_ok=True)
        self.proc.write("quit\n")
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.out.exists():
                import json
                try:
                    return json.loads(self.out.read_text())
                except ValueError:
                    pass
            time.sleep(0.1)
        return {"ok": False, "error": "the managing process did not answer"}
