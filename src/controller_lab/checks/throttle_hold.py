"""Throttle hold: hold RT steady at 25%, 50% and 75% on a gauge, 3 s each."""

from __future__ import annotations

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase, Snapshot

LEVELS = (0.25, 0.5, 0.75)
HOLD_S = 3
APPROACH_TIMEOUT_S = 15
BAND = 0.05
SEGMENT_NS = 5 * 10**9
RT = 5


def at_level(level: float):
    def end(snap: Snapshot) -> bool:
        return abs(snap[RT] / analysis.AXIS_MAX - level) <= BAND

    return end


def _phases() -> tuple[Phase, ...]:
    phases = []
    for level in LEVELS:
        common = {"visual": "gauge", "gauge": level, "focus": "RT"}
        phases.append(
            Phase(
                f"Bring RT to {level:.0%} on the gauge",
                timeout_s=APPROACH_TIMEOUT_S,
                end=at_level(level),
                **common,
            )
        )
        phases.append(
            Phase(
                f"Hold RT steady at {level:.0%}",
                timeout_s=HOLD_S,
                duration_s=HOLD_S,
                **common,
            )
        )
    return tuple(phases)


PHASES = _phases()


def _phase_stop(data: CheckData, i: int) -> int:
    starts = data.phase_starts_ns
    return starts[i + 1] if i + 1 < len(starts) else data.end_ns


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    lines: list[str] = []
    held_t: list[np.ndarray] = []
    held_v: list[np.ndarray] = []
    offset = 0
    for n, level in enumerate(LEVELS):
        i = 2 * n + 1  # the hold phase for this level
        start, stop = data.phase_starts_ns[i], _phase_stop(data, i)
        rows = data.phase_rows(i)
        # The value held at the start counts from the start, and the last one until
        # the end, so the series covers exactly the hold.
        t = np.append(np.maximum(data.t_ns[rows], start), stop)
        v = data.samples[rows, RT]
        v = np.append(v, v[-1:])
        key = f"level_{level * 100:.0f}"
        jit = analysis.jitter(t, v)
        metrics[f"{key}_jitter_pct"] = round(jit, 4)
        metrics[f"{key}_distinct"] = int(np.unique(v).size)
        metrics[f"{key}_mean_pct"] = round(float(v.mean()) / analysis.AXIS_MAX * 100, 3)
        lines.append(f"{level:.0%}: average {metrics[f'{key}_mean_pct']:.0f}%, jitter {jit:.2f}%")
        # Join the holds end to end, so the 5 s segments cover holding time only.
        held_t.append(t - start + offset)
        held_v.append(v)
        offset += stop - start
    t_all, v_all = np.concatenate(held_t), np.concatenate(held_v)
    metrics["steadiness_segments_pct"] = [
        round(analysis.jitter(t_all[seg], v_all[seg]), 4)
        for seg in analysis.segments(t_all, SEGMENT_NS)
    ]
    if float(data.samples[:, RT].max(initial=0)) < BAND * analysis.AXIS_MAX:
        return Outcome("skipped", metrics, "RT wasn't used")
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="throttle_hold",
    title="Throttle hold",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
)
