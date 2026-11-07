"""Circularity: roll each stick slowly around the rim 5 times."""

from __future__ import annotations

from math import atan2, hypot, pi

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, EndCondition, Outcome, Phase, Snapshot

STICKS = {"left": (0, 1), "right": (2, 3)}
FOCUS = {"left": "LS", "right": "RS"}
ROTATIONS = 5
TIMEOUT_S = 30
RIM = analysis.RIM_RADIUS * analysis.AXIS_MAX


def rotations(xi: int, yi: int, turns: int):
    """End once the stick has gone `turns` times round while out at the rim."""

    def until(start_ns: int) -> EndCondition:
        state = {"last": None, "total": 0.0}

        def end(t_ns: int, snap: Snapshot) -> bool:
            x, y = snap[xi], snap[yi]
            if hypot(x, y) < RIM:
                state["last"] = None
                return False
            angle = atan2(-y, x)
            if state["last"] is not None:
                step = (angle - state["last"] + pi) % (2 * pi) - pi
                state["total"] += step
            state["last"] = angle
            return abs(state["total"]) >= turns * 2 * pi

        return end

    return until


PHASES = tuple(
    Phase(
        f"{side.capitalize()} stick: roll it slowly round the rim, {ROTATIONS} times",
        timeout_s=TIMEOUT_S,
        until=rotations(xi, yi, ROTATIONS),
        visual="controller",
        focus=FOCUS[side],
        intro=f"Use the {side.upper()} stick",
    )
    for side, (xi, yi) in STICKS.items()
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    covered = 0
    for i, (side, (xi, yi)) in enumerate(STICKS.items()):
        rows = data.phase_rows(i)
        st = analysis.circle_stats(data.samples[rows, xi], data.samples[rows, yi])
        covered += st.bins_covered
        metrics[f"{side}_bins_covered"] = st.bins_covered
        if st.bins_covered:
            metrics[f"{side}_circle_error_pct"] = round(st.error_pct, 3)
            metrics[f"{side}_weakest"] = st.weakest
            metrics[f"{side}_lr_diff_pct"] = round(st.lr_diff_pct, 3)
            metrics[f"{side}_ud_diff_pct"] = round(st.ud_diff_pct, 3)
    if covered == 0:
        return Outcome("skipped", metrics, "The sticks didn't reach the rim")
    lines = [
        f"{side.capitalize()} stick: {metrics[f'{side}_circle_error_pct']:.1f}% circle error,"
        f" weakest {metrics[f'{side}_weakest']}"
        for side in STICKS
        if f"{side}_circle_error_pct" in metrics
    ]
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="circularity",
    title="Circularity",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        "Roll each stick slowly round its rim, pressed against the edge the whole way,"
        f" {ROTATIONS} times. Left stick, then right. About 1 min."
    ),
)
