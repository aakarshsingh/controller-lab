"""Holds and combos: hold game-style combinations, then each face button, and catch dropouts."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from controller_lab import analysis
from controller_lab.checks.base import (
    BUTTON_BITS,
    MASK,
    NS_PER_S,
    Check,
    CheckData,
    EndCondition,
    Outcome,
    Phase,
    Snapshot,
)

TIMEOUT_S = 12
FULL = 0.9 * analysis.AXIS_MAX  # what counts as held for the end condition
HALF = 0.5 * analysis.AXIS_MAX  # what counts as still held when looking for dropouts
PUSHED = analysis.PUSH_RADIUS * analysis.AXIS_MAX
A, B, X, Y, LB, RB = (BUTTON_BITS[n] for n in ("A", "B", "X", "Y", "LB", "RB"))


@dataclass(frozen=True)
class Hold:
    key: str
    label: str
    buttons: int
    seconds: float
    lt: bool = False
    rt: bool = False
    stick: str | None = None  # "up" or "any", for the left stick

    def held(self, snap: Snapshot) -> bool:
        """Every part is fully down, as the end condition needs it."""
        lx, ly, _, _, lt, rt, mask = snap
        return (
            mask & self.buttons == self.buttons
            and (not self.lt or lt >= FULL)
            and (not self.rt or rt >= FULL)
            and (self.stick != "up" or ly <= -PUSHED)
            and (self.stick != "any" or np.hypot(lx, ly) >= PUSHED)
        )

    def parts(self, s: np.ndarray) -> list[np.ndarray]:
        """Each part's held state per sample, with a lenient threshold, for dropouts."""
        parts = [s[:, MASK] & bit != 0 for bit in BUTTON_BITS.values() if self.buttons & bit]
        if self.lt:
            parts.append(s[:, 4] >= HALF)
        if self.rt:
            parts.append(s[:, 5] >= HALF)
        if self.stick == "up":
            parts.append(s[:, 1] <= -HALF)
        elif self.stick == "any":
            parts.append(np.hypot(s[:, 0], s[:, 1]) >= HALF)
        return parts


HOLDS = (
    Hold("lb_rb_lt_rt", "LB + RB + LT + RT", LB | RB, 5, lt=True, rt=True),
    Hold("a_rt_up", "A + RT + left stick up", A, 5, rt=True, stick="up"),
    Hold("rb_x", "RB + X", RB | X, 5),
    Hold("lb_y", "LB + Y", LB | Y, 5),
    Hold("lt_stick", "LT + left stick, any direction", 0, 5, lt=True, stick="any"),
    Hold("a", "A", A, 3),
    Hold("b", "B", B, 3),
    Hold("x", "X", X, 3),
    Hold("y", "Y", Y, 3),
)


def hold_for(hold: Hold):
    """End once every part has been held without a break for the hold's seconds."""

    def until(start_ns: int) -> EndCondition:
        state: dict[str, int | None] = {"since": None}

        def end(t_ns: int, snap: Snapshot) -> bool:
            if not hold.held(snap):
                state["since"] = None
                return False
            if state["since"] is None:
                state["since"] = t_ns
            return t_ns - state["since"] >= hold.seconds * NS_PER_S

        return end

    return until


PHASES = tuple(
    Phase(
        f"Hold {hold.label} for {hold.seconds:.0f} s",
        timeout_s=TIMEOUT_S,
        until=hold_for(hold),
        visual="controller",
    )
    for hold in HOLDS
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    lines: list[str] = []
    held_any = False
    total = 0
    for i, hold in enumerate(HOLDS):
        rows = data.phase_rows(i)
        t, s = data.t_ns[rows], data.samples[rows]
        full = np.array([hold.held(tuple(row)) for row in s], dtype=bool)
        if not full.any():
            metrics[f"{hold.key}_held"] = False
            lines.append(f"{hold.label}: not held")
            continue
        held_any = True
        first = int(np.argmax(full))
        count = sum(analysis.dropouts(t[first:], part[first:]) for part in hold.parts(s))
        total += count
        metrics[f"{hold.key}_held"] = True
        metrics[f"{hold.key}_dropouts"] = count
        lines.append(f"{hold.label}: {count} dropout{'s' if count != 1 else ''}")
    metrics["dropouts_total"] = total
    if not held_any:
        return Outcome("skipped", metrics, "Nothing was held")
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="holds",
    title="Holds and combos",
    phases=PHASES,
    suites=frozenset({"full", "connection"}),
    is_timing=False,
    analyse=analyse,
)
