"""The checks of each suite, in run order. Only implemented checks are listed."""

from __future__ import annotations

from controller_lab.checks import (
    button_taps,
    centring,
    circularity,
    dpad,
    haptics,
    holds,
    idle_drift,
    report_timing,
    skill_flicks,
    sprint_turn,
    sweep,
    throttle_hold,
    tracking,
    triggers,
)
from controller_lab.checks.base import Check

ORDER: tuple[Check, ...] = (
    idle_drift.CHECK,
    centring.CHECK,
    circularity.CHECK,
    sweep.CHECK,
    triggers.CHECK,
    button_taps.CHECK,
    button_taps.CONNECTION_CHECK,
    dpad.CHECK,
    holds.CHECK,
    sprint_turn.CHECK,
    skill_flicks.CHECK,
    throttle_hold.CHECK,
    report_timing.CHECK,
    haptics.CHECK,
    tracking.CHECK,
)
SUITES = ("full", "connection")


def suite(name: str) -> list[Check]:
    if name not in SUITES:
        raise ValueError(f"Unknown suite: {name}")
    return [check for check in ORDER if name in check.suites]
