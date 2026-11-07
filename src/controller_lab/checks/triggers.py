"""Triggers: squeeze each trigger slowly all the way, then let go, 5 times."""

from __future__ import annotations

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, EndCondition, Outcome, Phase, Snapshot

TRIGGERS = {"lt": 4, "rt": 5}
SQUEEZES = 5
TIMEOUT_S = 30
FULL = 0.9 * analysis.AXIS_MAX
LET_GO = 0.1 * analysis.AXIS_MAX


def squeezes(col: int, count: int):
    """End once the trigger has gone past 90% from below 10%, `count` times."""

    def until(start_ns: int) -> EndCondition:
        state = {"armed": True, "count": 0}

        def end(t_ns: int, snap: Snapshot) -> bool:
            v = snap[col]
            if v <= LET_GO:
                state["armed"] = True
            elif v >= FULL and state["armed"]:
                state["armed"] = False
                state["count"] += 1
            return state["count"] >= count

        return end

    return until


PHASES = tuple(
    Phase(
        f"Squeeze {name.upper()} slowly all the way, then let go, {SQUEEZES} times",
        timeout_s=TIMEOUT_S,
        until=squeezes(col, SQUEEZES),
        visual="controller",
        focus=name.upper(),
        intro=f"Use the {'LEFT' if name == 'lt' else 'RIGHT'} trigger, {name.upper()}",
    )
    for name, col in TRIGGERS.items()
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    used = 0
    for i, (name, col) in enumerate(TRIGGERS.items()):
        rest, reach, distinct, jump = analysis.trigger_stats(data.samples[data.phase_rows(i), col])
        metrics[f"{name}_rest"] = rest
        metrics[f"{name}_reach_pct"] = round(reach, 3)
        metrics[f"{name}_distinct"] = distinct
        metrics[f"{name}_max_jump_pct"] = round(jump, 3)
        used += int(reach >= LET_GO / analysis.AXIS_MAX * 100)
    if used == 0:
        return Outcome("skipped", metrics, "The triggers weren't squeezed")
    lines = [
        f"{name.upper()} rests at {metrics[f'{name}_rest'] / analysis.AXIS_MAX * 100:.0f}%,"
        f" reaches {metrics[f'{name}_reach_pct']:.0f}%,"
        f" biggest jump {metrics[f'{name}_max_jump_pct']:.0f}%"
        for name in TRIGGERS
    ]
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="triggers",
    title="Triggers",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        f"Squeeze each trigger slowly all the way down, then let go, {SQUEEZES} times."
        " LT, the left trigger, first, then RT. About 1 min."
    ),
)
