"""The result of a run: one line per check, a summary, a JSON form and the exit code."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

PASS, FAIL, SKIP, MANUAL = "PASS", "FAIL", "SKIP", "MANUAL"
PENDING = "PENDING"


@dataclass(frozen=True)
class Check:
    """One line of the report. ``group`` names the function in ``checks.GROUPS`` that decides it."""
    id: str
    title: str
    group: str
    manual: bool = False
    features: bool = False          # only at ``--level features``


@dataclass
class Result:
    id: str
    title: str
    status: str
    evidence: str = ""
    manual: bool = False


def one_line(text: object) -> str:
    return " ".join(str(text).split())


@dataclass
class Report:
    results: dict[str, Result] = field(default_factory=dict)
    contract_version: int | None = None
    features: list[str] = field(default_factory=list)

    @classmethod
    def for_checks(cls, checks: list[Check], selected: Callable[[Check], bool]) -> Report:
        rep = cls()
        for c in checks:
            if selected(c):
                rep.results[c.id] = Result(c.id, c.title, MANUAL if c.manual else PENDING,
                                           "", c.manual)
        return rep

    def wants(self, check_id: str) -> bool:
        return check_id in self.results

    def status(self, check_id: str) -> str | None:
        res = self.results.get(check_id)
        return res.status if res else None

    def set(self, check_id: str, status: str, evidence: object = "") -> None:
        """Record a result. A check that was not selected is dropped; a FAIL is never
        overwritten by a later PASS (a check set from two places keeps its worst outcome)."""
        res = self.results.get(check_id)
        if res is None or res.manual:
            return
        if res.status == FAIL and status != FAIL:
            return
        res.status, res.evidence = status, one_line(evidence)

    def passed(self, check_id: str, evidence: object = "") -> None:
        self.set(check_id, PASS, evidence)

    def failed(self, check_id: str, evidence: object) -> None:
        self.set(check_id, FAIL, evidence)

    def skip_group(self, ids: list[str], why: str) -> None:
        for i in ids:
            res = self.results.get(i)
            if res is not None and res.status == PENDING:
                res.status, res.evidence = SKIP, why

    def finish(self, why: str = "not run") -> None:
        self.skip_group(list(self.results), why)

    # ---- output ------------------------------------------------------------------------

    def lines(self) -> list[str]:
        return [f"{r.status}  {r.id}  {r.title}  {r.evidence}".rstrip()
                for r in self.results.values()]

    def counts(self) -> dict[str, int]:
        out = {PASS: 0, FAIL: 0, SKIP: 0, MANUAL: 0}
        for r in self.results.values():
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def summary(self) -> list[str]:
        c = self.counts()
        manual = [r.id for r in self.results.values() if r.status == MANUAL]
        out = [f"Summary: {c[PASS]} passed, {c[FAIL]} failed, {c[SKIP]} skipped, "
               f"{c[MANUAL]} manual.",
               f"contract_version: {self.contract_version}",
               "declared features: " + (", ".join(self.features) or "none")]
        if manual:
            out.append("Stays manual: " + ", ".join(manual))
        return out

    def exit_code(self) -> int:
        statuses = [r.status for r in self.results.values() if not r.manual]
        if FAIL in statuses:
            return 1
        if SKIP in statuses or PENDING in statuses:
            return 3
        return 0

    def to_json(self) -> str:
        return json.dumps({
            "contract_version": self.contract_version, "features": self.features,
            "exit_code": self.exit_code(), "counts": self.counts(),
            "checks": [{"id": r.id, "title": r.title, "status": r.status,
                        "evidence": r.evidence} for r in self.results.values()]}, indent=2)
