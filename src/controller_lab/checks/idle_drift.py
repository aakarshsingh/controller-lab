"""Idle drift: the controller rests untouched for 30 s."""

from __future__ import annotations

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase

DURATION_S = 30


def analyse(data: CheckData) -> Outcome:
    t, s = data.t_ns, data.samples
    if t.size == 0:
        return Outcome("voided", message="No samples were recorded, A to redo")
    held = np.diff(np.append(t, data.end_ns)).astype(np.float64)
    metrics: dict[str, object] = {}
    for side, (xi, yi) in (("left", (0, 1)), ("right", (2, 3))):
        x, y = s[:, xi], s[:, yi]
        pct, direction = analysis.stick_offset(x, y, weights=held)
        metrics[f"{side}_offset_pct"] = round(pct, 3)
        metrics[f"{side}_direction"] = direction
        metrics[f"{side}_noise_pct"] = round(analysis.noise_std(x, y, weights=held), 3)
        metrics[f"{side}_max_dev_pct"] = round(analysis.max_deviation(x, y), 3)
    metrics["stray_presses"] = analysis.button_presses(s[:, 6])
    metrics["samples"] = int(t.size)
    return Outcome("done", metrics)


CHECK = Check(
    id="idle_drift",
    title="Idle drift",
    phases=(
        Phase(
            "Put the controller down. Don't touch it.",
            timeout_s=DURATION_S,
            duration_s=DURATION_S,
            visual="controller",
        ),
    ),
    suites=frozenset({"full", "connection"}),
    is_timing=False,
    analyse=analyse,
)
