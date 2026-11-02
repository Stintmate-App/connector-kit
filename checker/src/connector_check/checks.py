"""The checks: the registry (docs/levels.md rows M1 to M19), the context a run shares, and one
function per group of checks. A group runs when one of its checks is wanted; it records its
outcomes in the report and may leave a check unset (it then reads SKIP "not run")."""

from __future__ import annotations

import json
import os
import logging
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import descriptor as desc_mod
from . import process
from .control import ControlLink
from .process import Proc
from stintmate_ipc import RemoteError
from .report import FAIL, MANUAL, PASS, SKIP, Check, Report

log = logging.getLogger("connector_check")

CHECKS: list[Check] = [
    Check("M1.schema", "Descriptor against its schema", "static"),
    Check("M1.identity", "Descriptor identity: id, endpoint, mutex, launcher", "static"),
    Check("M17.uninstall", "Descriptor uninstall command", "static"),
    Check("M7.key", "Waits for a missing key, never creates it", "key"),
    Check("M2.options", "Starts with the managed options and an unknown one", "start"),
    Check("M2.runtime", "Own runtime in its folder", "manual", manual=True),
    Check("M19.instance", "Honours STINTMATE_DEV_INSTANCE (pipe and mutex)", "start"),
    Check("M3.single", "Single instance by mutex", "single"),
    Check("M4.transport", "Control endpoint passes stintmate-ipc-check", "transport"),
    Check("M4.state", "control.state against its schema", "transport"),
    Check("M5.services", "control.service.stop and start for each service", "services"),
    Check("M5.managed", "control.managed adopts a parent", "services"),
    Check("M8.hello", "Hello meta and connector.status", "link"),
    Check("M9.link", "Linked with the game closed, and stays linked", "link"),
    Check("M7.absent", "Retries while Pitwall is absent", "link"),
    Check("M9.state", "control.state.pitwall follows the connection", "link"),
    Check("M18.texts", "Names and labels come from the language pack", "texts"),
    Check("M6.recorder", "Service recorder: waiting, then recording", "session"),
    Check("M8.game", "game_running before the first write, false after", "session"),
    Check("M10.order", "Catalog, session start and end, no stint, length_m", "session"),
    Check("M10.laps", "Timed flying lap and the lap fields", "session"),
    Check("M11.channels", "Minimum channels on every lap with channels", "session"),
    Check("M12.sanity", "Units and signs of the channels", "session"),
    Check("M12.findings", "Findings kept, signs checked in the car", "manual", manual=True),
    Check("M13.keys", "Every write has a stable idempotency key", "session"),
    Check("M16.logs", "Logs named in the descriptor exist and are not empty", "session"),
    Check("M16.cap", "Logs capped in size", "manual", manual=True),
    Check("M13.retry", "Retryable errors keep the write and its key", "retry"),
    Check("M13.refused", "A refused write is set aside and the next goes on", "retry"),
    Check("M14.outbox", "Outbox: survives Pitwall going away and a restart", "outbox"),
    Check("M15.cpu", "At most 0.5 % of a core with the game closed", "cpu"),
    Check("M15.priority", "Every process below normal priority", "cpu"),
    Check("M15.game", "Nothing reads the game's data with the game closed", "manual", manual=True),
    Check("M17.hold", "Hold and release when setup replaces a running connector", "manual",
          manual=True),
    Check("F.names", "Declared features are known names", "features", features=True),
    Check("F.live_frame", "Live frame valid, in menus and on track", "features", features=True),
    Check("F.capture_target", "connector.capture_target", "features", features=True),
    Check("F.brake_cues", "connector.telemetry", "features", features=True),
    Check("F.actions", "connector.action, test.ping answers", "features", features=True),
    Check("F.guidance", "pitwall.guidance.declare", "features", features=True),
    Check("F.announcements", "pitwall.catalog.declare", "features", features=True),
    Check("M5.stranger", "A stranger cannot make a managed app quit", "quit"),
    Check("M5.quit", "The managing process makes it quit within 20 s", "quit"),
]

# Manual lines say what to do; the report prints it as their evidence.
MANUAL_TEXT = {
    "M2.runtime": "the launcher ships its own runtime inside its folder",
    "M12.findings": "keep the findings document; check the signs in the car",
    "M15.game": "nothing reads the game's data with the game closed; sampling is off the write path",
    "M16.cap": "the logs are capped in size",
    "M17.hold": "setup holds and releases through the hub when it replaces a running connector",
}

SESSION_IDS = ["M6.recorder", "M8.game", "M10.order", "M10.laps", "M11.channels", "M12.sanity",
               "M13.keys", "M16.logs", "M13.retry", "M13.refused", "M14.outbox"]


@dataclass
class Ctx:
    """Everything a run shares. ``scratch`` holds what groups hand to each other."""
    args: Any
    report: Report
    descriptor: dict[str, Any]
    descriptor_path: Path
    kit: Path
    contract: dict[str, Any]
    tag: str
    workspace: Path
    key: bytes
    core_pipe: str
    control_pipe: str
    mutex: str
    core: Any = None                 # fakecore.FakeCore
    proc: Any = None                 # process.Proc, the connector under check
    link: Any = None                 # control.ControlLink
    helper: Any = None               # process.Helper, the managing process
    scratch: dict[str, Any] = field(default_factory=dict)

    @property
    def cid(self) -> str:
        return self.descriptor["id"]

    @property
    def data_dir(self) -> Path:
        return self.workspace / "connectors" / self.cid

    def schema(self, name: str) -> dict[str, Any]:
        return json.loads((self.kit / "docs" / name).read_text(encoding="utf-8"))

    def wants(self, *ids: str) -> bool:
        return any(self.report.wants(i) for i in ids)


GROUPS: list[tuple[str, Callable[[Ctx], None]]] = []


def group(name: str) -> Callable[[Callable[[Ctx], None]], Callable[[Ctx], None]]:
    def register(fn: Callable[[Ctx], None]) -> Callable[[Ctx], None]:
        GROUPS.append((name, fn))
        return fn
    return register


def ids_of(name: str) -> list[str]:
    return [c.id for c in CHECKS if c.group == name]


def make_selector(only: list[str], level: str) -> Callable[[Check], bool]:
    def selected(c: Check) -> bool:
        if c.features and level != "features":
            return False
        if not only:
            return True
        head = c.id.split(".")[0]
        return c.id in only or head in only
    return selected


def run(ctx: Ctx) -> None:
    """Run every group that has a wanted check, in order. A group that raises records its
    unfinished checks as failed with the error: a bug in the checker must not look like a pass."""
    for name, fn in GROUPS:
        ids = ids_of(name)
        if not any(ctx.report.wants(i) for i in ids):
            continue
        try:
            fn(ctx)
        except Exception as exc:  # noqa: BLE001
            log.debug("group %s: %s", name, traceback.format_exc())
            for i in ids:
                if ctx.report.status(i) == "PENDING":
                    ctx.report.failed(i, f"the check could not finish: {exc!r}")
    for check in CHECKS:
        if check.manual and ctx.report.wants(check.id):
            ctx.report.results[check.id].evidence = MANUAL_TEXT[check.id]


# ---- static -------------------------------------------------------------------------------

@group("static")
def static_checks(ctx: Ctx) -> None:
    rep, desc = ctx.report, ctx.descriptor
    errors = desc_mod.schema_errors(desc, ctx.schema("descriptor.schema.json"))
    if errors:
        rep.failed("M1.schema", "; ".join(errors[:4]))
    else:
        rep.passed("M1.schema", "valid")
    problems = desc_mod.identity_problems(desc, ctx.descriptor_path)
    if problems:
        rep.failed("M1.identity", "; ".join(problems))
    else:
        rep.passed("M1.identity", f"id {desc['id']}, endpoint {desc['endpoint']}")
    problems = desc_mod.uninstall_problems(desc)
    if problems:
        rep.failed("M17.uninstall", "; ".join(problems))
    else:
        rep.passed("M17.uninstall", desc["uninstall"][0])


# ---- running the connector ------------------------------------------------------------------

def child_env(ctx: Ctx) -> dict[str, str]:
    """The environment of every process the checker starts: the instance tag, and a descriptor
    folder and an AppData of its own, so nothing reaches the user's real ones."""
    tmp: Path = ctx.scratch["tmp"]
    env = dict(os.environ)
    env[ctx.args.instance_env] = ctx.tag
    for name, sub in (("STINTMATE_CONNECTORS_DIR", "descriptors"), ("APPDATA", "appdata")):
        (tmp / sub).mkdir(exist_ok=True)
        env[name] = str(tmp / sub)
    return env


def key_path(ctx: Ctx) -> Path:
    return ctx.workspace / "ipc" / "key"


def write_key(ctx: Ctx) -> None:
    """What the hub's first run leaves: the key file. Only the checker ever writes it."""
    key_path(ctx).parent.mkdir(parents=True, exist_ok=True)
    key_path(ctx).write_text(ctx.key.hex() + "\n", encoding="ascii")


def launch(ctx: Ctx) -> Proc | None:
    """Start the connector as the hub does: launcher + args + the trailing options, plus an option
    the connector does not know. Returns None, and says why in scratch, when it cannot start."""
    tmp: Path = ctx.scratch["tmp"]
    env = child_env(ctx)
    if ctx.helper is None:
        ctx.helper = process.Helper(ctx.control_pipe, ctx.key, tmp, env)
    argv = desc_mod.launch_argv(ctx.descriptor, ctx.workspace, ctx.helper.pid,
                                ["--allow-synced", "--future-option", "x"])
    ctx.scratch["launches"] = ctx.scratch.get("launches", 0) + 1
    try:
        ctx.proc = process.Proc(argv, env, tmp / f"connector-{ctx.scratch['launches']}.log",
                                cwd=str(Path(ctx.descriptor["launcher"]).parent))
    except OSError as exc:
        ctx.proc = None
        ctx.scratch["launch_error"] = f"cannot start {ctx.descriptor.get('launcher')}: {exc}"
        return None
    ctx.scratch["launched_at"] = time.monotonic()
    return ctx.proc


def attach(ctx: Ctx, first: bool = False) -> bool:
    """Connect to the control endpoint; as a hub does with a managed connector, start the
    services. On the first attach this decides M2.options and M19.instance."""
    rep = ctx.report
    proc = ctx.proc
    if ctx.link is not None:
        ctx.link.close()
    ctx.link = ControlLink(ctx.control_pipe, ctx.key)
    timeout = ctx.args.start_timeout
    left = max(1.0, timeout - (time.monotonic() - ctx.scratch.get("launched_at", time.monotonic())))
    ok = proc is not None and ctx.link.connect(left, alive=proc.alive)
    if first:
        took = time.monotonic() - ctx.scratch.get("launched_at", time.monotonic())
        tagged = process.mutex_exists(ctx.mutex)
        if ok:
            rep.passed("M2.options", f"control.state after {took:.1f} s with --future-option x")
            rep.passed("M19.instance", f"tagged pipe served and mutex {ctx.mutex} held" if tagged
                       else "tagged pipe served")
            if not tagged:
                rep.failed("M19.instance", f"the tagged pipe is served but mutex {ctx.mutex} is not held")
        else:
            if proc is None:
                why = ctx.scratch.get("launch_error", "cannot start")
            elif not proc.alive():
                why = f"exited with code {proc.returncode} ({proc.log_tail()})"
            else:
                why = (f"no control.state on {ctx.control_pipe} within {timeout:.0f} s "
                       f"({ctx.link.last_error})")
            rep.failed("M2.options", why)
            if proc is not None and proc.alive():
                rep.failed("M19.instance",
                           f"alive, but neither the tagged pipe nor mutex {ctx.mutex} answer: does "
                           f"it read {ctx.args.instance_env} in every process?"
                           if not tagged else f"the pipe {ctx.control_pipe} is not served")
            else:
                rep.skip_group(["M19.instance"], "the connector did not run")
    if not ok:
        return False
    start_services(ctx)
    return True


def start_services(ctx: Ctx) -> None:
    """Managed, the hub tells each delegable service to start (the connector waits for it)."""
    state = ctx.link.latest or {}
    if not state.get("managed"):
        return
    for svc in state.get("services", []):
        if isinstance(svc, dict) and not svc.get("running") and isinstance(svc.get("id"), str):
            try:
                ctx.link.call("control.service.start", {"service": svc["id"]})
            except Exception as exc:  # noqa: BLE001 - M5.services reports it
                log.info("control.service.start %s: %r", svc["id"], exc)


def boot(ctx: Ctx) -> bool:
    """The connector runs and the checker is attached to its control endpoint (idempotent)."""
    if ctx.scratch.get("boot_failed"):
        return False
    if ctx.proc is not None and ctx.proc.alive() and ctx.link is not None \
            and ctx.link.peer is not None:
        return True
    first = "attached_once" not in ctx.scratch
    if ctx.core is None:
        start_core(ctx)
    if ctx.proc is None or not ctx.proc.alive():
        if not key_path(ctx).exists():
            write_key(ctx)
        launch(ctx)
    ctx.scratch["attached_once"] = True
    ok = attach(ctx, first=first)
    if not ok:
        ctx.scratch["boot_failed"] = True
    return ok


def start_core(ctx: Ctx) -> None:
    """The fake Pitwall, on the tagged pipe (added with the fake core)."""


def need_boot(ctx: Ctx, ids: list[str]) -> bool:
    if boot(ctx):
        return True
    ctx.report.skip_group(ids, "the connector did not start (see M2.options)")
    return False


def restart(ctx: Ctx) -> bool:
    """Kill the connector's tree and start it again (the key and workspace stay)."""
    if ctx.link is not None:
        ctx.link.close()
        ctx.link = None
    if ctx.proc is not None:
        ctx.proc.kill_tree()
        ctx.proc = None
    ctx.scratch.pop("boot_failed", None)
    return boot(ctx)


# ---- M7.key, M2, M19, M3, M4, M5 -----------------------------------------------------------

@group("key")
def key_checks(ctx: Ctx) -> None:
    rep = ctx.report
    if key_path(ctx).exists():
        key_path(ctx).unlink()
    proc = launch(ctx)
    if proc is None:
        rep.failed("M7.key", ctx.scratch.get("launch_error", "cannot start"))
        return
    end = time.monotonic() + 5.0
    while time.monotonic() < end and proc.alive() and not key_path(ctx).exists():
        time.sleep(0.1)
    if not proc.alive():
        rep.failed("M7.key", f"exited with code {proc.returncode} while the key was missing "
                             f"({proc.log_tail()})")
    elif key_path(ctx).exists():
        rep.failed("M7.key", "created the key file: a connector only reads it")
    else:
        ctx.scratch["key_waited"] = True
    write_key(ctx)
    ctx.scratch["key_added_at"] = time.monotonic()


@group("start")
def start_checks(ctx: Ctx) -> None:
    if ctx.proc is not None and ctx.scratch.get("key_waited") is None and not ctx.proc.alive():
        ctx.proc = None                       # died in the key phase: a fresh start decides M2
    boot(ctx)
    if ctx.report.status("M2.options") == "PENDING":
        ctx.report.failed("M2.options", "did not start")


@group("single")
def single_instance(ctx: Ctx) -> None:
    rep = ctx.report
    if not need_boot(ctx, ["M3.single"]):
        return
    if not process.mutex_exists(ctx.mutex):
        rep.failed("M3.single", f"mutex {ctx.mutex} is not held while the control app runs")
        return
    second = process.Proc(ctx.proc.argv, child_env(ctx), ctx.scratch["tmp"] / "second.log",
                          cwd=str(Path(ctx.descriptor["launcher"]).parent))
    try:
        exited = second.wait(10.0)
        code = second.returncode
    finally:
        second.kill_tree()
    probe = ControlLink(ctx.control_pipe, ctx.key)
    try:
        answers = probe.connect(5.0)
    finally:
        probe.close()
    if not exited:
        rep.failed("M3.single", "a second start was still running after 10 s")
    elif not answers or not ctx.proc.alive():
        rep.failed("M3.single", "the first instance stopped answering after a second start")
    else:
        rep.passed("M3.single", f"mutex held; the second start exited with code {code}")


IPC_CHECK = ("import sys; from stintmate_ipc.check.cli import main; sys.exit(main(sys.argv[1:]))")


@group("transport")
def transport_checks(ctx: Ctx) -> None:
    rep = ctx.report
    if not need_boot(ctx, ["M4.transport", "M4.state"]):
        return
    code, out = process.run(
        [sys.executable, "-I", "-c", IPC_CHECK, "server", ctx.control_pipe, "--key-file",
         str(key_path(ctx)), "--fast"], child_env(ctx), ctx.scratch["tmp"] / "ipc-check.log", 180)
    tail = " | ".join(out.strip().splitlines()[-3:])
    if code == 0:
        rep.passed("M4.transport", "stintmate-ipc-check server --fast: ok")
    else:
        rep.failed("M4.transport", f"stintmate-ipc-check exit {code}: {tail}")
    state = ctx.link.wait_for(lambda s: True, 5.0)
    errors = desc_mod.schema_errors(state or {}, ctx.schema("control-state.schema.json"))
    if errors:
        rep.failed("M4.state", "; ".join(errors[:4]))
    else:
        rep.passed("M4.state", "control.state matches its schema")


def service_state(state: dict[str, Any] | None, sid: str) -> dict[str, Any] | None:
    for svc in (state or {}).get("services", []):
        if isinstance(svc, dict) and svc.get("id") == sid:
            return svc
    return None


@group("services")
def service_checks(ctx: Ctx) -> None:
    rep = ctx.report
    if not need_boot(ctx, ["M5.services", "M5.managed"]):
        return
    problems = []
    for svc in ctx.descriptor.get("services", []):
        sid = svc.get("id")
        for verb, want in (("stop", False), ("start", True)):
            try:
                ctx.link.call(f"control.service.{verb}", {"service": sid})
            except Exception as exc:  # noqa: BLE001
                problems.append(f"control.service.{verb} {sid}: {exc!r}")
                continue
            got = ctx.link.wait_for(
                lambda s, sid=sid, want=want: (service_state(s, sid) or {}).get("running") is want,
                10.0)
            if got is None:
                problems.append(f"{sid}: running did not become {want} after {verb}")
    if problems:
        rep.failed("M5.services", "; ".join(problems[:3]))
    else:
        rep.passed("M5.services", "stop and start change running for "
                   + ", ".join(str(s.get("id")) for s in ctx.descriptor.get("services", [])))
    me, helper = os.getpid(), ctx.helper.pid
    try:
        for pid in (me, helper):
            res = ctx.link.call("control.managed", {"parent_pid": pid})
            got = ctx.link.wait_for(lambda s, pid=pid: s.get("managed") is True
                                    and s.get("parent_pid") == pid, 5.0)
            if got is None or res.get("parent_pid") != pid:
                rep.failed("M5.managed", f"after control.managed {pid} the state says "
                                         f"managed={(ctx.link.latest or {}).get('managed')}, "
                                         f"parent_pid={(ctx.link.latest or {}).get('parent_pid')}")
                return
        rep.passed("M5.managed", "managed and parent_pid follow control.managed")
    except Exception as exc:  # noqa: BLE001
        rep.failed("M5.managed", f"control.managed: {exc!r}")


@group("quit")
def quit_checks(ctx: Ctx) -> None:
    rep = ctx.report
    if not need_boot(ctx, ["M5.stranger", "M5.quit"]):
        return
    try:
        ctx.link.call("control.quit")
        rep.failed("M5.stranger", "a caller that is neither the parent nor its child made a "
                                  "managed app quit")
    except RemoteError as exc:
        if exc.code == "managed":
            time.sleep(1.0)
            if ctx.proc.alive():
                rep.passed("M5.stranger", "refused with 'managed' and still running")
            else:
                rep.failed("M5.stranger", "answered 'managed' but exited")
        else:
            rep.failed("M5.stranger", f"answered {exc.code!r}, not 'managed'")
    except Exception as exc:  # noqa: BLE001
        rep.failed("M5.stranger", f"control.quit: {exc!r}")
    if not ctx.proc.alive() and not restart(ctx):
        rep.skip_group(["M5.quit"], "could not restart after the stranger quit it")
        return
    res = ctx.helper.quit()
    if not res.get("ok"):
        rep.failed("M5.quit", f"the managing process could not quit it: {res.get('error')} "
                              f"{res.get('code') or ''}")
        return
    t0 = time.monotonic()
    ended = ctx.proc.wait(20.0)
    while ended and ctx.proc.tree() and time.monotonic() - t0 < 20.0:
        time.sleep(0.2)
    took = time.monotonic() - t0
    probe = ControlLink(ctx.control_pipe, ctx.key)
    try:
        still_served = probe.connect(1.0)
    finally:
        probe.close()
    if not ended or ctx.proc.tree() or took > 20.0:
        rep.failed("M5.quit", f"the process tree was still running {took:.0f} s after control.quit")
    elif still_served or process.mutex_exists(ctx.mutex):
        rep.failed("M5.quit", "the pipe or the mutex outlived the process")
    else:
        rep.passed("M5.quit", f"exited {took:.1f} s after control.quit; pipe and mutex gone")


# ==== END OF GROUPS ====
