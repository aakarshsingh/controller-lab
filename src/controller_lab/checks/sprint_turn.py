"""Sprint and turn: hold RT fully and swing the left stick through 8 directions for 15 s."""

from __future__ import annotations

import math

from controller_lab import analysis
from controller_lab.checks.base import Check, CheckData, Outcome, Phase, Snapshot

SWING_S = 15
START_TIMEOUT_S = 15
HELD = 0.95 * analysis.AXIS_MAX
HALF = 0.5 * analysis.AXIS_MAX
ORDER = ("up", "right", "down", "left", "up-right", "down-left", "up-left", "down-right")


def rt_held(snap: Snapshot) -> bool:
    return snap[5] >= HELD


def _unit(direction: str) -> tuple[float, float]:
    angle = math.radians(analysis.DIRECTIONS.index(direction) * 45)
    return math.cos(angle), -math.sin(angle)  # SDL's y grows downwards


PHASES = (
    Phase(
        "Hold RT, the right trigger, all the way down, and keep it there",
        timeout_s=START_TIMEOUT_S,
        end=rt_held,
        visual="controller",
        focus="RT",
        timeout_ends_check=True,
    ),
) + tuple(
    Phase(
        f"Keep RT held. Swing the left stick {direction}",
        timeout_s=SWING_S / len(ORDER),
        duration_s=SWING_S / len(ORDER),
        visual="controller",
        arrow=direction,
        focus="LS",
    )
    for direction in ORDER
)


def analyse(data: CheckData) -> Outcome:
    if data.timed_out:
        return Outcome("skipped", message="RT wasn't held down")
    first = data.phase_rows(1)
    rows = slice(first.start, data.phase_rows(len(PHASES) - 1).stop)
    t, s = data.t_ns[rows], data.samples[rows]
    rt = s[:, 5]
    below = rt < HELD
    metrics: dict[str, object] = {
        "rt_min_pct": round(float(rt.min()) / analysis.AXIS_MAX * 100, 3),
        "rt_sag_count": int(below[0]) + int((below[1:] & ~below[:-1]).sum()),
        "rt_dropouts": analysis.dropouts(t, rt >= HALF),
    }
    sags = metrics["rt_sag_count"]
    lines = [
        f"RT stayed above {metrics['rt_min_pct']:.0f}% while held",
        "RT never sagged below 95%"
        if sags == 0
        else f"RT sagged below 95% {sags} time{'s' if sags != 1 else ''}",
    ]
    for i, direction in enumerate(ORDER, start=1):
        r = data.phase_rows(i)
        ux, uy = _unit(direction)
        proj = data.samples[r, 0] * ux + data.samples[r, 1] * uy
        reach = float(max(0.0, proj.max())) / analysis.AXIS_MAX * 100 if proj.size else 0.0
        metrics[f"reach_{direction}_pct"] = round(reach, 3)
        lines.append(f"Left stick reached {reach:.0f}% {direction}")
    return Outcome("done", metrics, lines=tuple(lines[:10]))


CHECK = Check(
    id="sprint_turn",
    title="Sprint and turn",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        "Hold RT, the right trigger, all the way down, then swing the left stick to"
        " each direction the arrow shows while RT stays down. About 20 s."
    ),
)
