"""The checks of each suite, in run order. Only implemented checks are listed."""

from __future__ import annotations

from controller_lab.checks import idle_drift, report_timing
from controller_lab.checks.base import Check

ORDER: tuple[Check, ...] = (idle_drift.CHECK, report_timing.CHECK)
SUITES = ("full", "connection")


def suite(name: str) -> list[Check]:
    if name not in SUITES:
        raise ValueError(f"Unknown suite: {name}")
    return [check for check in ORDER if name in check.suites]
