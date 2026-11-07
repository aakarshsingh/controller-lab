"""Report timing: spin the left stick for 15 s, timed from the first movement."""

from __future__ import annotations

from dataclasses import asdict
from math import hypot

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase, Snapshot

WINDOW_S = 15
START_TIMEOUT_S = 30
MIN_MOVING_S = 10.0
MOTION_LIMIT = analysis.MOTION_RADIUS * analysis.AXIS_MAX


def moved(snap: Snapshot) -> bool:
    return hypot(snap[0], snap[1]) > MOTION_LIMIT


def analyse(data: CheckData) -> Outcome:
    if data.timed_out:
        return Outcome("skipped", message=f"No movement within {START_TIMEOUT_S} s")
    s = data.samples
    stats = analysis.spin_timing(data.t_ns, s[:, 0], s[:, 1], data.tool_gaps, WINDOW_S * 10**9)
    metrics = {**asdict(stats), "tool_gaps": len(data.tool_gaps)}
    if not stats.moving_s >= MIN_MOVING_S:
        return Outcome("voided", metrics, "Keep spinning, A to redo")
    return Outcome("done", metrics)


CHECK = Check(
    id="report_timing",
    title="Report timing",
    phases=(
        Phase(
            "Push the left stick past half way to start",
            timeout_s=START_TIMEOUT_S,
            end=moved,
            timeout_ends_check=True,
        ),
        Phase("Spin the left stick continuously", timeout_s=WINDOW_S, duration_s=WINDOW_S),
    ),
    suites=frozenset({"full", "connection"}),
    is_timing=True,
    analyse=analyse,
)
