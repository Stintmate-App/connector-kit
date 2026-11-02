"""The launcher: ``skeleton-connector.exe [--workspace W] [--managed --parent-pid N]``.

The hub starts the descriptor's ``launcher`` with ``--workspace <w> --managed --parent-pid
<pid>`` (and ``--allow-synced`` when the folder is synced) appended: all of them are accepted.
This is a gui-script (no console window), so everything goes to ``control.log`` in
``<workspace>/connectors/<id>/``, which the descriptor lists under ``logs``. The folder is
made on the first run and the log is capped (about 1 MB, two older copies kept).
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from stintmate_ipc import EndpointInUse, find_workspace

from . import CONNECTOR_ID, MUTEX, dev_tag
from .control import ControlApp

ERROR_ALREADY_EXISTS = 183
BELOW_NORMAL_PRIORITY_CLASS = 0x4000
log = logging.getLogger("skeleton_connector")


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="skeleton-connector")
    p.add_argument("--workspace")
    p.add_argument("--managed", action="store_true")
    p.add_argument("--parent-pid", type=int)
    p.add_argument("--allow-synced", action="store_true")
    args, _unknown = p.parse_known_args(argv)        # tolerate options added later
    return args


def hold_mutex() -> object | None:
    """The single-instance mutex the descriptor names (the hub's liveness check). None: a
    control app already runs."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    tag = dev_tag()                                   # a test run has a mutex of its own
    handle = kernel32.CreateMutexW(None, False, f"{MUTEX}.{tag}" if tag else MUTEX)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle                                     # held until the process ends


def lower_priority() -> None:
    """Below normal, for this process and the threads it starts: a connector must not take
    CPU from the game, which matters most in VR."""
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)


def setup_logging(log_dir: Path) -> logging.Handler:
    """``control.log`` in `log_dir` (made if missing), capped at about 1 MB plus two copies."""
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_dir / "control.log", maxBytes=1_000_000,
                                                   backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    return handler


def main(argv: list[str] | None = None) -> int:
    lower_priority()
    args = parse(argv)
    workspace = Path(args.workspace) if args.workspace else find_workspace()
    try:
        setup_logging((workspace / "connectors" / CONNECTOR_ID) if workspace else Path.cwd())
    except OSError:
        pass
    mutex = hold_mutex()
    if mutex is None:
        log.info("A control app already runs")
        return 0
    app = None
    try:
        # No workspace yet (first run before the onboarding): log and retry, do not fail.
        while workspace is None:
            log.warning("No Stintmate workspace found; retrying in 5 s")
            time.sleep(5.0)
            workspace = find_workspace()
        app = ControlApp(workspace, managed=args.managed, parent_pid=args.parent_pid)
        app.start()
        log.info("Serving %s (managed=%s)", app.pipe, app.managed)
        app.exit.wait()
        return 0
    except EndpointInUse:
        log.error("The endpoint is already served by another process")
        return 1
    finally:
        if app is not None:
            app.stop()
        logging.shutdown()


if __name__ == "__main__":
    sys.exit(main())
