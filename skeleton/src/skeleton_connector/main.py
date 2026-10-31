"""The launcher: ``skeleton-connector.exe [--workspace W] [--managed --parent-pid N]``.

The hub starts the descriptor's ``launcher`` with ``--workspace <w> --managed --parent-pid
<pid>`` (and ``--allow-synced`` when the folder is synced) appended: all of them are accepted.
This is a gui-script (no console window), so everything goes to ``control.log`` next to the
descriptor.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import sys
import time
from pathlib import Path

from stintmate_ipc import EndpointInUse, find_workspace

from . import CONNECTOR_ID, MUTEX
from .control import ControlApp

ERROR_ALREADY_EXISTS = 183
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
    handle = kernel32.CreateMutexW(None, False, MUTEX)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return None
    return handle                                     # held until the process ends


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    workspace = Path(args.workspace) if args.workspace else find_workspace()
    log_dir = (workspace / "connectors" / CONNECTOR_ID) if workspace else Path.cwd()
    try:
        logging.basicConfig(filename=log_dir / "control.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
