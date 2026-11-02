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

from . import cpu
from . import descriptor as desc_mod
from . import process
from . import sanity
from . import session as sess
from .control import ControlLink
from .fakecore import FakeCore, decode_channel, digest
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
    Check("F.actions", "Declared actions, and test.ping answers", "features", features=True),
    Check("F.guidance", "Declared guidance reached Pitwall", "features", features=True),
    Check("F.announcements", "Declared announcement catalog reached Pitwall", "features", features=True),
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
ORDER = ["static", "key", "start", "single", "transport", "services", "link", "texts", "session",
         "retry", "outbox", "cpu", "features", "quit"]


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
    runtime = [c for c in CHECKS if ctx.report.wants(c.id) and c.group not in ("static", "manual")]
    if runtime and ctx.core is None:
        start_core(ctx)                      # every runtime group may use it
    for name, fn in sorted(GROUPS, key=lambda g: ORDER.index(g[0])):
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
    """The fake Pitwall, on the tagged pipe and with the key of the temporary workspace."""
    ctx.core = FakeCore(ctx.core_pipe, ctx.key, ctx.schema("write-api.schema.json")).start()


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
    if ctx.core is None:
        start_core(ctx)
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
    if rep.status("M7.key") == "PENDING":
        # Managed, the connector starts its recorder when the hub says so: attach does that.
        if not boot(ctx):
            rep.failed("M7.key", "did not start after the key appeared")
        elif ctx.core.wait(lambda: ctx.core.linked, 15.0):
            rep.passed("M7.key", "alive without the key, did not create it, linked within 15 s "
                                 "of the key appearing")
        else:
            rep.failed("M7.key", "not linked within 15 s of the key appearing")


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


# ---- the link to Pitwall -------------------------------------------------------------------

def pitwall_state(ctx: Ctx, want: str, timeout: float) -> bool:
    return ctx.link.wait_for(lambda s: s.get("pitwall") == want, timeout) is not None


def restart_core(ctx: Ctx, pause: float = 0.0) -> None:
    ctx.core.stop()
    time.sleep(pause)
    ctx.core.start()


@group("link")
def link_checks(ctx: Ctx) -> None:
    rep, core = ctx.report, ctx.core
    ids = ids_of("link")
    if not need_boot(ctx, ids):
        return
    # M8.hello and M9.link: linked with the game closed, and it stays so.
    if not core.wait(lambda: core.linked, 15.0):
        why = "; ".join(core.bad_hellos[-1:]) or "no connection within 15 s with the game closed"
        rep.failed("M9.link", f"not linked: {why}")
        rep.failed("M8.hello", f"not linked: {why}")
    else:
        info = core.hellos[-1][1]
        problem = FakeCore.hello_problem(info)
        core.wait(lambda: core.status or core.status_errors, 5.0)
        if problem or core.bad_hellos:
            rep.failed("M8.hello", problem or core.bad_hellos[-1])
        elif core.status_errors or not core.status:
            rep.failed("M8.hello", "connector.status is not declared: "
                       + (core.status_errors[-1] if core.status_errors else "no value"))
        else:
            rep.passed("M8.hello", f"{info.app}, service {info.process}, meta valid, "
                                   "connector.status declared")
        drops = len(core.disconnects)
        hold = ctx.args.link_seconds
        time.sleep(hold)
        game = (core.last_status() or {}).get("game_running")
        if not core.linked or len(core.disconnects) != drops:
            rep.failed("M9.link", f"the link dropped within {hold:.0f} s with the game closed")
        elif game is True:
            rep.failed("M9.link", "game_running is true with the game closed")
        else:
            rep.passed("M9.link", f"linked and still linked {hold:.0f} s later, game closed")
    # M7.absent and the first part of M9.state: Pitwall goes away, then comes back.
    state_problems = []
    restart_at = time.monotonic()
    core.stop()
    if not pitwall_state(ctx, "waiting", 20.0):
        state_problems.append("not 'waiting' within 20 s of Pitwall going away")
    time.sleep(max(0.0, 3.0 - (time.monotonic() - restart_at)))
    core.start()
    t0 = time.monotonic()
    if core.wait(lambda: core.linked, 15.0):
        rep.passed("M7.absent", f"linked {time.monotonic() - t0:.1f} s after Pitwall came back")
    else:
        rep.failed("M7.absent", "not linked within 15 s of Pitwall coming back")
    if not pitwall_state(ctx, "connected", 15.0):
        state_problems.append("not 'connected' within 15 s of Pitwall coming back")
    # A refusal: the state says refused, and the connector tries again.
    for code in ("unauthorized", "connector_busy"):
        hellos = len(core.hellos)
        core.bye_while(code)
        restart_core(ctx)
        refused = pitwall_state(ctx, "refused", 30.0)
        retried = core.wait(lambda: len(core.hellos) >= hellos + 2, 30.0)
        core.bye_while(None)
        back = pitwall_state(ctx, "connected", 40.0)
        if code == "unauthorized" and not refused:
            state_problems.append("bye 'unauthorized' did not show 'refused'")
        if code == "connector_busy" and ctx.link.latest.get("pitwall") == "connected" \
                and not refused and not retried:
            state_problems.append("bye 'connector_busy' still showed 'connected'")
        if not retried:
            state_problems.append(f"no new attempt after bye {code!r}")
        if not back:
            state_problems.append(f"not 'connected' again after bye {code!r}")
    if state_problems:
        rep.failed("M9.state", "; ".join(state_problems))
    else:
        rep.passed("M9.state", "waiting, connected, refused (unauthorized, connector_busy) and "
                               "retried")


# ---- texts (M18) ---------------------------------------------------------------------------

@group("texts")
def text_checks(ctx: Ctx) -> None:
    rep = ctx.report
    if not need_boot(ctx, ["M18.texts"]):
        return
    try:
        doc = ctx.link.call("control.catalog.get")
    except Exception as exc:  # noqa: BLE001
        rep.failed("M18.texts", f"no control.catalog.get ({exc!r}): texts cannot come from a pack")
        return
    catalog = doc.get("catalog") if isinstance(doc, dict) and "catalog" in doc else doc
    messages = catalog.get("messages") if isinstance(catalog, dict) else None
    if not isinstance(messages, dict) or not messages:
        rep.failed("M18.texts", "control.catalog.get returned no messages")
        return
    tag, prefix = "zz", "ZZ> "
    pack = {"schema": "stintmate-catalog/1", "catalog": catalog.get("catalog"), "language": tag,
            "language_name": "Check", "messages": {
                k: {"text": prefix + str((v or {}).get("text", k)).replace("{", "(").replace("}", ")")}
                for k, v in messages.items()}}
    folder = ctx.workspace / "locales" / tag
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{catalog.get('catalog')}.json").write_text(json.dumps(pack), encoding="utf-8")
    (ctx.workspace / "hub").mkdir(exist_ok=True)
    (ctx.workspace / "hub" / "settings.json").write_text(json.dumps({"language": tag}),
                                                         encoding="utf-8")
    if not restart(ctx):
        rep.failed("M18.texts", "the connector did not start with a language pack")
        return
    state = ctx.link.latest or {}
    bad = [f"{s.get('id')}.{field}={s.get(field)!r}" for s in state.get("services", [])
           for field in ("name", "label") if not str(s.get(field, "")).startswith(prefix)]
    if bad:
        rep.failed("M18.texts", "not from the pack: " + ", ".join(bad[:4]))
    else:
        rep.passed("M18.texts", "service names and labels come from the language pack")


# ---- a session -----------------------------------------------------------------------------

def need_game(ctx: Ctx, ids: list[str]) -> bool:
    """Whether the checker can play a session (a fake game file, or a person for the first)."""
    if ctx.args.skip_session:
        ctx.report.skip_group(ids, "--skip-session")
        return False
    if not need_boot(ctx, ids):
        return False
    return True


def recorder_of(state: dict[str, Any] | None) -> dict[str, Any] | None:
    return service_state(state, "recorder")


@group("session")
def session_checks(ctx: Ctx) -> None:
    rep, core = ctx.report, ctx.core
    ids = ["M6.recorder", "M8.game", "M10.order", "M10.laps", "M11.channels", "M12.sanity",
           "M13.keys", "M16.logs"]
    if not need_game(ctx, ids):
        return
    pre = (recorder_of(ctx.link.latest) or {}).get("state")
    base, ended = core.mark(), core.ended_count
    sess.settle(ctx)
    t_on = time.monotonic()
    sess.game_on(ctx)
    done = sess.wait_end(ctx, ended)
    sess.game_off(ctx)
    t_off = time.monotonic()
    core.wait(lambda: (core.last_status() or {}).get("game_running") is False, 25.0)
    entries = core.since(base)
    ctx.scratch["session_entries"] = entries

    # M6: the service is called recorder; waiting without the game, recording in a session.
    svc = recorder_of(ctx.link.latest)
    if svc is None:
        rep.failed("M6.recorder", "no service with the id 'recorder' in control.state")
    else:
        recorded = ctx.link.seen(lambda s: (recorder_of(s) or {}).get("state") == "recording",
                                 since=t_on)
        after = ctx.link.wait_for(lambda s: (recorder_of(s) or {}).get("state") == "waiting", 20.0)
        if pre != "waiting":
            rep.failed("M6.recorder", f"state was {pre!r} with the game closed, not 'waiting'")
        elif not recorded:
            rep.failed("M6.recorder", "state never became 'recording' during the session")
        elif after is None:
            rep.failed("M6.recorder", "state did not go back to 'waiting' after the session")
        else:
            rep.passed("M6.recorder", "waiting, recording, waiting")

    # M8.game: false before the game, true before the first write, false after.
    with core.lock:
        statuses = list(core.status)
    trues = [t for t, v in statuses if v.get("game_running") is True]
    first_true = next((t for t in trues if t >= t_on), None)
    writes = [e for e in entries if e.method.startswith("pitwall.write.")]
    if any(t < t_on for t in trues):
        rep.failed("M8.game", "game_running was true before the game was started")
    elif first_true is None:
        rep.failed("M8.game", "game_running never became true")
    elif writes and writes[0].t < first_true:
        rep.failed("M8.game", f"the first write came {first_true - writes[0].t:.1f} s before "
                              "game_running became true")
    elif (core.last_status() or {}).get("game_running") is not False:
        rep.failed("M8.game", "game_running is not false after the game closed")
    else:
        rep.passed("M8.game", "false, then true before the first write, then false")

    starts = sess.accepted(entries, "session_start")
    if not done or not starts:
        why = "no session was written within the time allowed" if not starts else \
              "no session_end was written"
        for i in ("M10.order", "M10.laps", "M11.channels", "M12.sanity"):
            rep.failed(i, why)
    else:
        judge_session(ctx, entries, starts[0])
    judge_keys(ctx)
    names = ctx.descriptor.get("logs")
    if not isinstance(names, list) or not names:
        rep.failed("M16.logs", "the descriptor names no logs")
    else:
        miss = [n for n in names if not (ctx.data_dir / n).is_file()
                or (ctx.data_dir / n).stat().st_size == 0]
        if miss:
            rep.failed("M16.logs", f"missing or empty in {ctx.data_dir.name}: " + ", ".join(miss))
        else:
            rep.passed("M16.logs", ", ".join(names))


def judge_session(ctx: Ctx, entries: list[Any], start: Any) -> None:
    rep = ctx.report
    key = start.params["session_key"]
    laps = sess.laps_of(entries, key)
    problems = []
    refused = [e for e in entries if e.method.startswith("pitwall.write.") and e.error]
    if refused:
        problems.append(f"Pitwall refused {refused[0].kind}: {refused[0].error} "
                        f"{refused[0].message}")
    lay = sess.layouts(entries)
    no_len = [f"{k[1]}" for k, v in lay.items() if not isinstance(v, (int, float)) or v <= 0]
    if not lay or no_len:
        problems.append("a layout has no length_m: " + ", ".join(no_len or ["(no catalog)"]))
    if any("stint" in e.params for e in entries if e.method.startswith("pitwall.write.")):
        problems.append("a write carries 'stint': Pitwall numbers stints")
    ends = [e for e in sess.accepted(entries, "session_end") if e.params.get("session_key") == key]
    if len(ends) != 1:
        problems.append(f"{len(ends)} session_end for the session")
    if problems:
        rep.failed("M10.order", "; ".join(problems))
    else:
        rep.passed("M10.order", f"catalog, session_start, {len(laps)} laps, session_end; "
                                "length_m present; no stint")
    # M10.laps
    lap_problems = []
    timed = [e for e in laps if e.params.get("lap_type") == "flying" and e.params.get("time_s")]
    if not timed:
        lap_problems.append("no flying lap with a time_s")
    for e in laps:
        p = e.params
        miss = [f for f in ("valid", "sim_lap", "on_track", "lap_type") if f not in p]
        if miss:
            lap_problems.append(f"lap {p.get('seq')} lacks {', '.join(miss)}")
        if p.get("lap_type") != "flying" and p.get("time_s") is not None:
            lap_problems.append(f"lap {p.get('seq')} ({p.get('lap_type')}) carries time_s "
                                f"{p.get('time_s')}: untimed laps leave it out")
    if lap_problems:
        rep.failed("M10.laps", "; ".join(lap_problems[:3]))
    else:
        rep.passed("M10.laps", f"{len(laps)} laps, {len(timed)} timed flying")
    # M11.channels
    need = list(ctx.contract["vocabularies"]["minimum_channels"])
    if not ctx.args.no_position:
        need += list(ctx.contract["vocabularies"]["position_channels"])
    with_ch = [e for e in laps if e.params.get("channels")]
    miss = [f"lap {e.params.get('seq')}: " + ",".join(c for c in need if c not in e.params["channels"])
            for e in with_ch if any(c not in e.params["channels"] for c in need)]
    if not with_ch:
        rep.failed("M11.channels", "no lap carried channels")
    elif miss:
        rep.failed("M11.channels", "missing " + "; ".join(miss[:3]))
    else:
        rep.passed("M11.channels", ", ".join(need) + (" (position not required)"
                                                      if ctx.args.no_position else ""))
    # M12.sanity
    track, layout = start.params.get("track_id"), start.params.get("layout_id")
    length = lay.get((track, layout)) or next(iter(lay.values()), None)
    eligible = sess.eligible(laps, float(length)) if length else []
    if not eligible:
        rep.failed("M12.sanity", "no flying, valid, timed lap with channels to check")
        return
    verdicts = sanity.evaluate(eligible)
    bad = [v for v in verdicts if v.status == sanity.BAD]
    notes = [f"{v.rule}: {v.evidence}" for v in verdicts if v.status == sanity.MANUAL]
    if bad:
        rep.failed("M12.sanity", "; ".join(f"{v.rule}: {v.evidence}" for v in bad[:3]))
    else:
        rep.passed("M12.sanity", f"{len(eligible)} laps, all rules hold"
                   + (" (check by hand: " + "; ".join(notes) + ")" if notes else ""))


def judge_keys(ctx: Ctx) -> None:
    rep, cid = ctx.report, ctx.cid
    calls = [e for e in ctx.core.log if e.method.startswith("pitwall.write.")
             or e.method in ("pitwall.catalog.declare", "pitwall.guidance.declare",
                             "pitwall.actions.declare")]
    problems = []
    if any(not e.ik for e in calls):
        problems.append("a write without an idempotency key")
    wrong = [e.ik for e in calls if e.ik and not e.ik.startswith(f"{cid}/")]
    if wrong:
        problems.append(f"key {wrong[0]!r} does not start with '{cid}/'")
    seen: dict[str, set[tuple[str, str]]] = {}
    for e in calls:
        if e.ik:
            seen.setdefault(e.ik, set()).add((e.method, digest(e.params)))
    clash = [k for k, v in seen.items() if len(v) > 1]
    if clash:
        problems.append(f"key {clash[0]!r} is used for different content")
    if problems:
        rep.failed("M13.keys", "; ".join(problems))
    else:
        rep.passed("M13.keys", f"{len(seen)} distinct keys, all start '{cid}/', stable on repeat")


# ---- retryable and refused writes (M13) ----------------------------------------------------

RETRYABLE = ("busy", "writer_busy", "timeout", "shutting_down", "unavailable", "connector_busy")


@group("retry")
def retry_checks(ctx: Ctx) -> None:
    rep, core = ctx.report, ctx.core
    ids = ["M13.retry", "M13.refused"]
    if not need_game(ctx, ids):
        return
    if sess.game_path(ctx) is None:
        rep.skip_group(ids, "needs --fake-game-file: the checker plays a second session")
        return
    sess.settle(ctx)
    base, ended = core.mark(), core.ended_count
    for code in RETRYABLE:
        core.refuse_next(code, retry_ms=100)
    core.refuse_next("bad_request", method="pitwall.write.lap")
    sess.game_on(ctx)
    done = sess.wait_end(ctx, ended, min(ctx.args.session_timeout, 180.0))
    sess.game_off(ctx)
    sess.settle(ctx)
    entries = core.since(base)
    writes = [e for e in entries if e.method.startswith("pitwall.write.")]
    problems = []
    for e in writes:
        if e.error in RETRYABLE:
            later = [x for x in writes if x.n > e.n and x.ik == e.ik]
            if not later:
                twin = [x for x in writes if x.n > e.n and x.method == e.method
                        and digest(x.params) == digest(e.params)]
                problems.append(f"{e.kind} refused with {e.error!r} was "
                                + ("sent again under a new key" if twin else "dropped"))
            elif later[0].method != e.method or digest(later[0].params) != digest(e.params):
                problems.append(f"the retry of {e.kind} changed its content")
    if not done:
        problems.append("the session did not end: the writes after a refusal never came")
    if problems:
        rep.failed("M13.retry", "; ".join(dict.fromkeys(problems)))
    else:
        rep.passed("M13.retry", "kept and sent again with the same key after " + ", ".join(RETRYABLE))
    bad = [e for e in writes if e.error == "bad_request" and e.method.endswith(".lap")
           and e.message == "scripted"]
    if not bad:
        rep.failed("M13.refused", "the connector never sent the lap that was to be refused")
        return
    ik = bad[0].ik
    again = [e for e in writes if e.ik == ik]
    after = [e for e in writes if e.n > bad[0].n and e.error is None and e.ik != ik]
    if len(again) > 1:
        rep.failed("M13.refused", "a refused (bad_request) write was sent again")
    elif not after or not done:
        rep.failed("M13.refused", "the writes after the refused one never arrived")
    else:
        rep.passed("M13.refused", f"not repeated; {len(after)} later writes accepted")


# ---- the outbox (M14) ----------------------------------------------------------------------

@group("outbox")
def outbox_checks(ctx: Ctx) -> None:
    rep, core = ctx.report, ctx.core
    ids = ["M14.outbox"]
    if not need_game(ctx, ids):
        return
    if sess.game_path(ctx) is None:
        rep.skip_group(ids, "needs --fake-game-file: the checker plays and interrupts a session")
        return
    # Part 1: Pitwall goes away mid-session and comes back.
    sess.settle(ctx)
    base, ended, since = core.mark(), core.ended_count, time.monotonic()
    sess.game_on(ctx)
    core.wait(lambda: len(sess.accepted(core.since(base), "session_start")) > 0
              and len(core.writes(accepted=True)) >= 3, 30.0)
    core.stop()
    peak = sess.wait_pending(ctx, 1, 30.0)
    if peak < 1:
        sess.game_off(ctx)
        core.start()
        rep.failed("M14.outbox", "outbox.pending in control.state never rose while Pitwall was away")
        return
    time.sleep(2.0)
    core.start()
    done = sess.wait_end(ctx, ended, 120.0)
    sess.game_off(ctx)
    sess.settle(ctx)
    emptied = ctx.link.wait_for(lambda s: s.get("outbox", {}).get("pending") == 0, 30.0)
    acc = [e for e in core.since(base) if e.method.startswith("pitwall.write.")]
    problems = []
    if not done:
        problems.append("the session never ended after Pitwall came back")
    if emptied is None:
        problems.append("outbox.pending did not return to 0")
    refused = [e for e in acc if e.error]
    if refused:
        problems.append(f"{refused[0].kind} refused after the replay: {refused[0].error} "
                        f"{refused[0].message}")
    for kind in ("lap", "pit_exit"):
        seqs = [e.params["seq"] for e in acc if e.error is None and not e.replayed
                and e.method.endswith(kind)]
        if seqs != list(range(1, len(seqs) + 1)):
            problems.append(f"{kind} writes arrived as {seqs}: lost or out of order")
    if problems:
        rep.failed("M14.outbox", "; ".join(problems))
        return
    # Part 2: the connector is killed with writes pending, and started again.
    base, ended = core.mark(), core.ended_count
    sess.game_on(ctx)
    core.wait(lambda: len(sess.accepted(core.since(base), "session_start")) > 0
              and len(core.since(base)) >= 3, 30.0)
    core.stop()
    first_ik = {e.ik for e in core.log if e.error is None}
    if sess.wait_pending(ctx, 1, 30.0) < 1:
        sess.game_off(ctx)
        core.start()
        rep.failed("M14.outbox", "outbox.pending never rose before the kill")
        return
    sess.game_off(ctx)
    time.sleep(3.0)
    count = sess.pending(ctx)
    ctx.scratch.pop("boot_failed", None)
    restarted = restart(ctx)
    core.start()
    if not restarted:
        rep.failed("M14.outbox", "the connector did not start again after the kill")
        return
    replayed = core.wait(lambda: len([e for e in core.since(base) if e.error is None
                                      and e.method.startswith("pitwall.write.")
                                      and e.ik not in first_ik]) >= max(1, count), 60.0)
    emptied = ctx.link.wait_for(lambda s: s.get("outbox", {}).get("pending") == 0, 30.0)
    if not replayed or emptied is None:
        rep.failed("M14.outbox", f"{count} writes were pending at the kill; after the restart "
                                 "they did not all reach Pitwall")
    else:
        rep.passed("M14.outbox", f"{peak} pending while Pitwall was away, replayed in order; "
                                 f"{count} pending at a kill, sent after the restart")


# ---- CPU and priority (M15) ----------------------------------------------------------------

@group("cpu")
def cpu_checks(ctx: Ctx) -> None:
    rep, core = ctx.report, ctx.core
    ids = ["M15.cpu", "M15.priority"]
    if not need_boot(ctx, ids):
        return
    sess.game_off(ctx)
    sess.settle(ctx)
    core.wait(lambda: core.linked, 20.0)
    if not core.linked:
        rep.failed("M15.cpu", "not linked to Pitwall: the idle budget is measured linked")
        return
    m = cpu.measure(ctx.proc, ctx.args.cpu_seconds)
    limit = float(ctx.contract["budgets"]["idle_cpu_percent_of_core"])
    if m.changed:
        rep.failed("M15.cpu", f"{m.percent:.2f} %: " + m.evidence)
    elif m.percent > limit:
        rep.failed("M15.cpu", f"{m.percent:.2f} % of a core, over {limit} %: " + m.evidence)
    else:
        rep.passed("M15.cpu", f"{m.percent:.2f} % of a core (limit {limit} %): " + m.evidence)
    if m.above_normal:
        rep.failed("M15.priority", "not below normal priority: " + ", ".join(m.above_normal))
    else:
        rep.passed("M15.priority", f"{m.processes} processes, all below normal or idle")


# ---- declared features (--level features) ---------------------------------------------------

@group("features")
def feature_checks(ctx: Ctx) -> None:
    rep, desc = ctx.report, ctx.descriptor
    unknown = desc_mod.unknown_features(desc, list(ctx.contract["vocabularies"]["features"]))
    if unknown:
        rep.failed("F.names", "not in the features list of contract.json: " + ", ".join(unknown))
    else:
        rep.passed("F.names", f"{len(desc.get('features') or [])} declared names, all known")
    ids = ["F.announcements", "F.guidance", "F.actions"]
    if not need_boot(ctx, ids):
        return
    declared, core = desc.get("features") or [], ctx.core
    core.wait(lambda: len(core.declared) >= 3, 10.0)
    for fid, feature, method in (("F.announcements", "announcements", "pitwall.catalog.declare"),
                                 ("F.guidance", "guidance", "pitwall.guidance.declare"),
                                 ("F.actions", "actions", "pitwall.actions.declare")):
        if feature not in declared:
            rep.passed(fid, "not declared")
        elif method not in core.declared:
            rep.failed(fid, f"declared in the descriptor, but {method} never reached Pitwall")
        elif feature != "actions":
            rep.passed(fid, f"{method} received")
        else:
            try:
                res = core.call("connector.action", {"code": "test.ping", "params": {}})
                ok = isinstance(res, dict) and "pong" in res
                rep.set(fid, PASS if ok else FAIL, f"test.ping answered {res!r}")
            except Exception as exc:  # noqa: BLE001
                rep.failed(fid, f"connector.action test.ping: {exc!r}")


# ==== END OF GROUPS ====
