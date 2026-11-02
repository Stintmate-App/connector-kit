"""What ``install.ps1`` asks of the running Stintmate while it replaces or removes this connector.

Run by the setup, never by the connector itself, as
``<venv>\\Scripts\\python.exe -m skeleton_connector.setup_hold <verb>``
(docs/connector-protocol.md section 1.2):

- ``hold --purpose update|uninstall --ttl 300``: ask the hub to quit the connector and keep it
  stopped, then wait until the connector's mutex is free.
- ``stop``: no hub to ask; ask the connector to quit through its own control endpoint and wait
  until the mutex is free.
- ``release --start|--no-start``: end the hold.

Exit codes: 0 done; 3 there is no usable hub (or, for ``stop``, the connector is still running):
the caller falls back to ``stop`` or to asking the user to close Stintmate; 4 the hub refuses
because a session is being recorded: nothing may be changed.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time

from stintmate_ipc import (KeyFile, PeerInfo, RemoteError, connect, default_key_path,
                           find_workspace)

from . import CONNECTOR_ID, CONNECTOR_VERSION, MUTEX, APP_ID, dev_tag, pipe

OK, NO_HUB, RECORDING = 0, 3, 4
SYNCHRONIZE = 0x00100000
HOLD_WAIT_S = 30.0          # how long to wait for `held`, and then for the mutex
STOP_WAIT_S = 20.0
INFO = PeerInfo(APP_ID, name="Skeleton connector setup", version=CONNECTOR_VERSION,
                process="setup")


def mutex_held() -> bool:
    """True while a process owns the connector's single-instance mutex."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    tag = dev_tag()
    handle = kernel32.OpenMutexW(SYNCHRONIZE, 0, f"{MUTEX}.{tag}" if tag else MUTEX)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def wait_mutex_free(seconds: float) -> bool:
    end = time.monotonic() + seconds
    while mutex_held():
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)
    return True


def _connect(app: str):
    workspace = find_workspace()
    if workspace is None:
        raise RuntimeError("no Stintmate workspace")
    return connect(pipe(app), INFO, KeyFile(default_key_path(workspace), create=False),
                   timeout=3)


def hold(purpose: str, ttl: int) -> int:
    try:
        hub = _connect("hub")
    except Exception as exc:                  # no workspace, no key, no pipe, a refused hello
        print(f"setup_hold: the hub cannot be reached: {exc}", file=sys.stderr)
        return NO_HUB
    try:
        end = time.monotonic() + HOLD_WAIT_S
        while True:
            reply = hub.call("hub.connector.hold",
                             {"id": CONNECTOR_ID, "purpose": purpose, "ttl_s": ttl}, timeout=5)
            if reply.get("state") == "held":
                break
            if time.monotonic() >= end:
                print("setup_hold: the hub did not finish holding the connector",
                      file=sys.stderr)
                return NO_HUB
            time.sleep(0.5)
    except RemoteError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        if exc.code == "busy" and details.get("recording"):
            print("setup_hold: a session is being recorded; finish it and try again",
                  file=sys.stderr)
            return RECORDING
        print(f"setup_hold: the hub refused the hold: {exc}", file=sys.stderr)
        return NO_HUB                          # not_found, unknown_method, busy, bad_request
    except Exception as exc:
        print(f"setup_hold: the hold failed: {exc}", file=sys.stderr)
        return NO_HUB
    finally:
        hub.close()
    if not wait_mutex_free(HOLD_WAIT_S):
        print("setup_hold: the connector is still running", file=sys.stderr)
        return NO_HUB
    return OK


def stop() -> int:
    try:
        own = _connect(APP_ID)
    except Exception:
        own = None                             # not running (or not reachable): check the mutex
    if own is not None:
        try:
            own.call("control.quit", {}, timeout=5)
        except Exception as exc:
            print(f"setup_hold: control.quit failed: {exc}", file=sys.stderr)
        finally:
            own.close()
    return OK if wait_mutex_free(STOP_WAIT_S) else NO_HUB


def release(start: bool) -> int:
    try:
        hub = _connect("hub")
    except Exception as exc:
        print(f"setup_hold: the hub cannot be reached: {exc}", file=sys.stderr)
        return NO_HUB
    try:
        hub.call("hub.connector.release", {"id": CONNECTOR_ID, "start": start}, timeout=5)
    except Exception as exc:                   # an older hub: nothing was held
        print(f"setup_hold: release failed: {exc}", file=sys.stderr)
        return NO_HUB
    finally:
        hub.close()
    return OK


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="setup_hold")
    sub = p.add_subparsers(dest="verb", required=True)
    h = sub.add_parser("hold")
    h.add_argument("--purpose", choices=("update", "uninstall"), default="update")
    h.add_argument("--ttl", type=int, default=300)
    sub.add_parser("stop")
    r = sub.add_parser("release")
    group = r.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", dest="start", action="store_true")
    group.add_argument("--no-start", dest="start", action="store_false")
    args = p.parse_args(argv)
    if args.verb == "hold":
        return hold(args.purpose, args.ttl)
    if args.verb == "stop":
        return stop()
    return release(args.start)


if __name__ == "__main__":
    sys.exit(main())
