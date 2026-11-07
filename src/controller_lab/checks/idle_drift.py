"""Idle drift: the controller rests untouched for 30 s, after a short settle."""

from __future__ import annotations

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase

DURATION_S = 30
SETTLE_S = 3


def analyse(data: CheckData) -> Outcome:
    # Only the 30 s rest counts. The settle before it lets the controller be put down.
    rows = data.phase_rows(len(data.phase_starts_ns) - 1) if data.phase_starts_ns else slice(0)
    t, s = data.t_ns[rows], data.samples[rows]
    if t.size == 0 or len(data.phase_starts_ns) < len(PHASES):
        return Outcome("voided", message="No samples were recorded, A to redo")
    held = np.diff(np.append(t, data.end_ns)).astype(np.float64)
    metrics: dict[str, object] = {}
    lines: list[str] = []
    for side, (xi, yi) in (("left", (0, 1)), ("right", (2, 3))):
        x, y = s[:, xi], s[:, yi]
        pct, direction = analysis.stick_offset(x, y, weights=held)
        metrics[f"{side}_offset_pct"] = round(pct, 3)
        metrics[f"{side}_direction"] = direction
        metrics[f"{side}_noise_pct"] = round(analysis.noise_std(x, y, weights=held), 3)
        metrics[f"{side}_max_dev_pct"] = round(analysis.max_deviation(x, y), 3)
        noise = metrics[f"{side}_noise_pct"]
        lines.append(f"{side.capitalize()} stick rests {pct:.1f}% {direction} of centre")
        lines.append(
            f"{side.capitalize()} stick holds still at rest"
            if noise == 0
            else f"{side.capitalize()} stick jitters {noise:.2f}% at rest"
        )
    stray = analysis.button_presses(s[:, 6])
    metrics["stray_presses"] = stray
    metrics["samples"] = int(t.size)
    lines.append(
        "No button pressed itself"
        if stray == 0
        else f"{stray} stray press{'es' if stray != 1 else ''} with nobody touching it"
    )
    return Outcome("done", metrics, lines=tuple(lines))


PHASES = (
    Phase(
        "Put the controller down flat, and let go",
        timeout_s=SETTLE_S,
        duration_s=SETTLE_S,
        visual="controller",
        gate="A",
    ),
    Phase(
        "Put the controller down. Don't touch it.",
        timeout_s=DURATION_S,
        duration_s=DURATION_S,
        visual="controller",
    ),
)


CHECK = Check(
    id="idle_drift",
    title="Idle drift",
    phases=PHASES,
    suites=frozenset({"full", "connection"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        "Measures drift and noise with nobody touching the controller. Press A, put it"
        " down flat, and leave it for 30 s. About 35 s."
    ),
)
