"""The checks: the registry (docs/levels.md rows M1 to M19), the context a run shares, and one
function per group of checks. A group runs when one of its checks is wanted; it records its
outcomes in the report and may leave a check unset (it then reads SKIP "not run")."""

from __future__ import annotations

import json
import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import descriptor as desc_mod
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


# ==== END OF GROUPS ====
