"""Centring and snapback: push each stick fully in 8 directions, 3 times, and let go."""

from __future__ import annotations

from math import hypot

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import (
    NS_PER_S,
    Check,
    CheckData,
    EndCondition,
    Outcome,
    Phase,
    Snapshot,
)

STICKS = {"left": (0, 1), "right": (2, 3)}
FOCUS = {"left": "LS", "right": "RS"}
# Clockwise from up, so the arrow walks round the stick.
ORDER = ("up", "up-right", "right", "down-right", "down", "down-left", "left", "up-left")
ROUNDS = 3
PUSH_TIMEOUT_S = 5
# After release, wait long enough to read the settle offset inside this push.
SETTLE_WAIT_S = 0.35
PUSH = analysis.PUSH_RADIUS * analysis.AXIS_MAX
RELEASE = analysis.RELEASE_RADIUS * analysis.AXIS_MAX


def push_and_release(xi: int, yi: int):
    """End once the stick passed 80%, came back inside 30%, and stayed there 0.35 s."""

    def until(start_ns: int) -> EndCondition:
        state = {"pushed": False, "inside_since": None}

        def end(t_ns: int, snap: Snapshot) -> bool:
            r = hypot(snap[xi], snap[yi])
            if r >= PUSH:
                state["pushed"], state["inside_since"] = True, None
            elif state["pushed"] and r < RELEASE:
                if state["inside_since"] is None:
                    state["inside_since"] = t_ns
                return t_ns - state["inside_since"] >= SETTLE_WAIT_S * NS_PER_S
            else:
                state["inside_since"] = None
            return False

        return end

    return until


def _phases() -> tuple[Phase, ...]:
    phases = []
    for side, (xi, yi) in STICKS.items():
        for n in range(ROUNDS * len(ORDER)):
            direction = ORDER[n % len(ORDER)]
            phases.append(
                Phase(
                    f"{side.capitalize()} stick: push fully {direction}, then let go",
                    timeout_s=PUSH_TIMEOUT_S,
                    until=push_and_release(xi, yi),
                    visual="controller",
                    arrow=direction,
                    focus=FOCUS[side],
                    intro=f"Use the {side.upper()} stick" if n == 0 else "",
                )
            )
    return tuple(phases)


PHASES = _phases()
PER_STICK = len(ORDER) * ROUNDS


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    found = 0
    for s_index, (side, (xi, yi)) in enumerate(STICKS.items()):
        settles, spikes = [], []
        for i in range(s_index * PER_STICK, (s_index + 1) * PER_STICK):
            rows = data.phase_rows(i)
            result = analysis.snapback(
                data.t_ns[rows], data.samples[rows, xi], data.samples[rows, yi]
            )
            if result is not None:
                settles.append(result[0])
                spikes.append(result[1])
        found += len(settles)
        metrics[f"{side}_pushes"] = len(settles)
        if settles:
            metrics[f"{side}_settle_mean_pct"] = round(float(np.mean(settles)), 3)
            metrics[f"{side}_settle_max_pct"] = round(float(np.max(settles)), 3)
            metrics[f"{side}_spike_max_pct"] = round(float(np.max(spikes)), 3)
    if found == 0:
        return Outcome("skipped", metrics, "No pushes were detected")
    lines = []
    for side in STICKS:
        if f"{side}_settle_mean_pct" in metrics:
            name = side.capitalize()
            lines.append(
                f"{name} stick settles {metrics[f'{side}_settle_mean_pct']:.1f}% off centre"
                f" on average, {metrics[f'{side}_settle_max_pct']:.1f}% at worst"
            )
            lines.append(
                f"{name} stick overshoots centre by up to {metrics[f'{side}_spike_max_pct']:.1f}%"
            )
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="centring",
    title="Centring",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        "Push each stick all the way in the direction the arrow shows, then let it spring"
        " back. 8 directions, 3 rounds, left stick then right. About 2 min."
    ),
)
