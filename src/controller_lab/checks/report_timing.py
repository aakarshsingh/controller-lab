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
    gaps = stats.gaps_over_30ms
    lines = (
        f"{stats.rate_hz:.0f} reports a second, {stats.mean_ms:.1f} ms apart on average",
        f"The slowest 1% took {stats.p99_ms:.1f} ms. Longest gap {stats.max_gap_ms:.0f} ms",
        "No gaps over 30 ms" if gaps == 0 else f"{gaps} gap{'s' if gaps != 1 else ''} over 30 ms",
    )
    return Outcome("done", metrics, lines=lines)


CHECK = Check(
    id="report_timing",
    title="Report timing",
    phases=(
        Phase(
            "Start spinning the left stick",
            timeout_s=START_TIMEOUT_S,
            end=moved,
            timeout_ends_check=True,
            gate="LS",
        ),
        Phase("Spin the left stick continuously", timeout_s=WINDOW_S, duration_s=WINDOW_S),
    ),
    suites=frozenset({"full", "connection"}),
    is_timing=True,
    analyse=analyse,
    brief=(
        f"Spin the left stick in circles, quickly and without stopping, for {WINDOW_S} s."
        " Timing starts when it moves. This screen doesn't update while it measures."
        " About 20 s."
    ),
)
