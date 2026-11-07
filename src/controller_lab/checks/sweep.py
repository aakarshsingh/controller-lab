"""Slow sweep: move each stick slowly left to right and back, 3 times."""

from __future__ import annotations

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, EndCondition, Outcome, Phase, Snapshot

STICKS = {"left": 0, "right": 2}
FOCUS = {"left": "LS", "right": "RS"}
SWEEPS = 3
TIMEOUT_S = 30
EDGE = analysis.PUSH_RADIUS * analysis.AXIS_MAX


def sweeps(xi: int, count: int):
    """End once the stick has reached the far left and far right `count` times each."""

    def until(start_ns: int) -> EndCondition:
        state = {"side": 0, "edges": 0}

        def end(t_ns: int, snap: Snapshot) -> bool:
            x = snap[xi]
            side = 1 if x >= EDGE else -1 if x <= -EDGE else 0
            if side and side != state["side"]:
                state["side"] = side
                state["edges"] += 1
            return state["edges"] >= 2 * count

        return end

    return until


PHASES = tuple(
    Phase(
        f"{side.capitalize()} stick: sweep slowly left to right and back, {SWEEPS} times",
        timeout_s=TIMEOUT_S,
        until=sweeps(xi, SWEEPS),
        visual="controller",
        focus=FOCUS[side],
        intro=f"Use the {side.upper()} stick",
    )
    for side, xi in STICKS.items()
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    swept = 0
    for i, (side, xi) in enumerate(STICKS.items()):
        x = data.samples[data.phase_rows(i), xi]
        if x.size == 0 or abs(x).max() < EDGE:
            metrics[f"{side}_swept"] = False
            continue
        swept += 1
        distinct, near, jump = analysis.sweep_stats(x)
        metrics[f"{side}_swept"] = True
        metrics[f"{side}_distinct"] = distinct
        metrics[f"{side}_distinct_centre"] = near
        metrics[f"{side}_max_jump_pct"] = round(jump, 3)
    if swept == 0:
        return Outcome("skipped", metrics, "The sticks weren't swept")
    return Outcome("done", metrics)


CHECK = Check(
    id="sweep",
    title="Slow sweep",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
)
