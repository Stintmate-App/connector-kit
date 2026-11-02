"""The command line: ``connector-check --descriptor <file> [options]`` (see the README)."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from pathlib import Path

EXIT_OK, EXIT_FAIL, EXIT_CANNOT, EXIT_SKIPPED = 0, 1, 2, 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="connector-check",
        description="Checks a connector against the kit's levels with a fake Pitwall. It uses a "
                    "temporary workspace, a random tag and pipes of its own, so it can run beside "
                    "a real Stintmate.")
    p.add_argument("--descriptor", required=True, type=Path,
                   help="the connector's descriptor file (<id>.json)")
    p.add_argument("--level", choices=("minimum", "features"), default="minimum",
                   help="features also checks each declared feature (default: minimum)")
    p.add_argument("--only", action="append", default=[],
                   help="comma-separated ids to run, e.g. M1,M5 or M5.quit")
    p.add_argument("--skip-session", action="store_true",
                   help="do not play a session (the checks that need one are skipped)")
    p.add_argument("--fake-game-file", metavar="PATH",
                   help="a file whose existence means the game runs; the checker creates and "
                        "removes it. {workspace} and {id} are replaced; a relative path is "
                        "relative to the temporary workspace. Without it the checker asks you "
                        "to play")
    p.add_argument("--session-timeout", type=float, default=1800.0)
    p.add_argument("--cpu-seconds", type=float, default=60.0)
    p.add_argument("--instance-env", default="STINTMATE_DEV_INSTANCE", metavar="NAME")
    p.add_argument("--mutex", metavar="NAME",
                   help="the tagged mutex name, for a connector with its own rule")
    p.add_argument("--no-position", action="store_true",
                   help="the game gives no position: x and z are not required (reported)")
    p.add_argument("--report", type=Path, metavar="FILE.json")
    p.add_argument("--start-timeout", type=float, default=60.0,
                   help="seconds to wait for control.state after the start (default 60)")
    p.add_argument("--link-seconds", type=float, default=30.0,
                   help="seconds the link must hold with the game closed (default 30)")
    return p


def kit_root() -> Path:
    """The kit: ``checker/src/connector_check/cli.py`` is three folders below it."""
    return Path(__file__).resolve().parents[3]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.only = [x.strip() for part in args.only for x in part.split(",") if x.strip()]
    try:
        import stintmate_ipc as ipc
    except (ImportError, OSError) as exc:
        print(f"connector-check: the stintmate-ipc package is not usable ({exc}); "
              "run powershell -ExecutionPolicy Bypass -File skeleton\\fetch-ipc.ps1", file=sys.stderr)
        return EXIT_CANNOT
    from . import checks, descriptor
    from .report import Report

    kit = kit_root()
    try:
        contract = json.loads((kit / "contract.json").read_text(encoding="utf-8"))
        (kit / "docs" / "descriptor.schema.json").stat()
    except (OSError, ValueError) as exc:
        print(f"connector-check: the kit's files are not beside the checker ({exc})",
              file=sys.stderr)
        return EXIT_CANNOT
    desc, why = descriptor.load(args.descriptor)
    if desc is None:
        print(f"connector-check: {why}", file=sys.stderr)
        return EXIT_CANNOT

    selected = checks.make_selector(args.only, args.level)
    report = Report.for_checks(checks.CHECKS, selected)
    report.contract_version = contract.get("contract_version")
    declared = desc.get("features")
    report.features = [str(f) for f in declared] if isinstance(declared, list) else []

    tag = "cc" + secrets.token_hex(5)
    tmp = Path(tempfile.mkdtemp(prefix="connector-check-"))
    workspace = tmp / "workspace"
    workspace.mkdir()
    user = f"{ipc.pipe_user()}-{tag}"
    endpoint = desc.get("endpoint") if isinstance(desc.get("endpoint"), str) else "connector-x"
    mutex = args.mutex or f"{desc.get('mutex', '')}.{tag}"
    ctx = checks.Ctx(args=args, report=report, descriptor=desc, descriptor_path=args.descriptor,
                     kit=kit, contract=contract, tag=tag, workspace=workspace,
                     key=os.urandom(32), core_pipe=ipc.pipe_path("pitwall", user=user),
                     control_pipe=ipc.pipe_path(endpoint, user=user), mutex=mutex)
    ctx.scratch["tmp"] = tmp
    code = EXIT_CANNOT
    try:
        checks.run(ctx)
        report.finish("skipped" if args.skip_session else "not run")
        code = report.exit_code()
    except KeyboardInterrupt:
        report.finish("interrupted")
        print("connector-check: interrupted", file=sys.stderr)
    finally:
        try:
            teardown(ctx)
        except Exception as exc:  # noqa: BLE001 - the report below must still print
            print(f"connector-check: cleaning up failed ({exc!r}); folder {tmp}", file=sys.stderr)
    for line in report.lines() + [""] + report.summary():
        print(line)
    if args.report:
        args.report.write_text(report.to_json(), encoding="utf-8")
    return code


def teardown(ctx) -> None:
    """End everything the run started, whatever happened: the link, the fake core, every
    process of the connector's tree and the helper, then the temporary folder."""
    for name in ("link", "core"):
        obj = getattr(ctx, name, None)
        if obj is not None:
            try:
                (obj.close if name == "link" else obj.stop)()
            except Exception:  # noqa: BLE001
                pass
    try:
        from . import process
        process.kill_all()
    except ImportError:
        pass
    tmp = ctx.scratch.get("tmp")
    if tmp is not None:
        for _ in range(10):
            shutil.rmtree(tmp, ignore_errors=True)
            if not Path(tmp).exists():
                break
            time.sleep(0.5)
        else:
            print(f"connector-check: could not remove {tmp}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
