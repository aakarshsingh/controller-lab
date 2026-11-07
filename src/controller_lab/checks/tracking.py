"""Tracking: follow a moving target with each stick for 40 s. Compare only, never scored.

The path is a Lissajous curve from a fixed seed, so every run sees the same one, with
a stretch in the middle that stays inside ±25% to test fine control near centre.
"""

from __future__ import annotations

import math
from functools import partial

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase

SEED = 2026
DURATION_S = 40
GRID_NS = 10 * 10**6
SEGMENT_NS = 5 * 10**9
AMPLITUDE = 0.7
CENTRE_AMPLITUDE = 0.2
CENTRE_FROM_S, CENTRE_TO_S, RAMP_S = 16.0, 24.0, 1.0
MOVED = 0.1 * analysis.AXIS_MAX
STICKS = {"left": (0, 1), "right": (2, 3)}


def _params() -> dict[str, tuple[float, float, float, float]]:
    rng = np.random.default_rng(SEED)
    return {
        side: (
            float(rng.uniform(0.6, 0.9)),
            float(rng.uniform(0.9, 1.3)),
            float(rng.uniform(0, 2 * math.pi)),
            float(rng.uniform(0, 2 * math.pi)),
        )
        for side in STICKS
    }


PARAMS = _params()


def amplitude(t: float) -> float:
    """Full size, easing down to the near-centre size for the middle stretch."""
    into = min(max((t - (CENTRE_FROM_S - RAMP_S)) / RAMP_S, 0.0), 1.0)
    out = min(max((t - CENTRE_TO_S) / RAMP_S, 0.0), 1.0)
    weight = into - out
    return AMPLITUDE + (CENTRE_AMPLITUDE - AMPLITUDE) * weight


def target(side: str, t: float) -> tuple[float, float]:
    """Target position at `t` seconds, as fractions of full, with y growing downwards."""
    a, b, px, py = PARAMS[side]
    amp = amplitude(t)
    return amp * math.sin(a * t + px), amp * math.sin(b * t + py)


PHASES = tuple(
    Phase(
        f"{side.capitalize()} stick: keep the green dot on the yellow one",
        timeout_s=DURATION_S,
        duration_s=DURATION_S,
        visual="tracking",
        target=partial(target, side),
        cursor=side,
        focus="LS" if side == "left" else "RS",
        intro=f"Use the {side.upper()} stick",
    )
    for side in STICKS
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    lines: list[str] = []
    moved_any = False
    for i, (side, (xi, yi)) in enumerate(STICKS.items()):
        rows = data.phase_rows(i)
        t, s = data.t_ns[rows], data.samples[rows]
        start = data.counting_starts_ns[i] if data.counting_starts_ns else data.phase_starts_ns[i]
        stop = data.phase_starts_ns[i + 1] if i + 1 < len(PHASES) else data.end_ns
        # The phase ends on the first loop pass after 40 s. Keep exactly 40 s.
        stop = min(stop, start + DURATION_S * 10**9)
        if t.size == 0 or stop <= start:
            continue
        grid = np.arange(start, stop, GRID_NS)
        at = np.clip(np.searchsorted(t, grid, side="right") - 1, 0, t.size - 1)
        x, y = s[at, xi], s[at, yi]
        secs = (grid - start) / 1e9
        tx, ty = np.array([target(side, v) for v in secs]).T
        err = analysis.tracking_error(grid, x, y, tx, ty)
        moved = float(np.hypot(x, y).max()) >= MOVED
        moved_any |= moved
        centre = (secs >= CENTRE_FROM_S) & (secs < CENTRE_TO_S)
        parts = analysis.segments(grid, SEGMENT_NS)
        metrics[f"{side}_moved"] = moved
        metrics[f"{side}_rms_pct"] = round(float(np.sqrt((err**2).mean())), 3)
        metrics[f"{side}_mean_pct"] = round(float(err.mean()), 3)
        if centre.any():
            metrics[f"{side}_centre_mean_pct"] = round(float(err[centre].mean()), 3)
        metrics[f"{side}_segments_rms_pct"] = [
            round(float(np.sqrt((err[p] ** 2).mean())), 3) for p in parts
        ]
        metrics[f"{side}_segments_mean_pct"] = [round(float(err[p].mean()), 3) for p in parts]
        lines.append(f"{side.capitalize()} stick: average miss {metrics[f'{side}_mean_pct']:.1f}%")
    if not moved_any:
        return Outcome("skipped", metrics, "The sticks didn't move")
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="tracking",
    title="Tracking",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
)
