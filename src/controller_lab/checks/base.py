"""What a check is made of. No pygame imports."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

# (lx, ly, rx, ry, lt, rt, buttons), as `source.Gamepad.snapshot()` returns it.
Snapshot = tuple[int, ...]
Status = Literal["done", "skipped", "voided", "not_tested", "interrupted"]
# Called on every loop pass with the time in ns and the latest snapshot.
EndCondition = Callable[[int, Snapshot], bool]

# Bit i of the button mask, in the same order as `source.BUTTON_NAMES`.
BUTTON_BITS = {
    name: 1 << i
    for i, name in enumerate(
        ("A", "B", "X", "Y", "Back", "Start", "LS", "RS", "LB", "RB")
        + ("Up", "Down", "Left", "Right")
    )
}
NS_PER_S = 1_000_000_000
MASK = 6  # column of the button mask in a snapshot
AXIS_MAX = 32767
GATE_PUSH = 0.5 * AXIS_MAX  # a stick or trigger past half way passes a gate
REST_STICK = 0.2 * AXIS_MAX
REST_TRIGGER = 0.05 * AXIS_MAX
DPAD_BITS = sum(BUTTON_BITS[n] for n in ("Up", "Down", "Left", "Right"))
# Plain names for gate inputs, and how the start prompt asks for them.
GATE_NAMES = {
    "LS": "the left stick",
    "RS": "the right stick",
    "LT": "LT, the left trigger",
    "RT": "RT, the right trigger",
    "LB": "LB, the left bumper",
    "RB": "RB, the right bumper",
    "Back": "View",
    "Start": "Menu",
    "D-pad": "the D-pad",
    "Up": "D-pad up",
    "Down": "D-pad down",
    "Left": "D-pad left",
    "Right": "D-pad right",
}
# The shorter names a "Press ... to start" prompt uses. Buttons keep their own letters.
SHORT_NAMES = {
    "LS": "the left stick",
    "RS": "the right stick",
    "D-pad": "the D-pad",
    "Back": "View",
    "Start": "Menu",
}


@dataclass(frozen=True)
class Phase:
    """One prompted step of a check.

    The phase ends when `end(snapshot)` turns true, when the end condition made by
    `until(start_ns)` turns true, when `duration_s` has passed, or when `timeout_s`
    has passed. `until` is called afresh each time the phase starts, so its end
    condition can count presses or rotations. A timeout with `timeout_ends_check` ends
    the whole check, and the check's `analyse` sees `timed_out`. `arrow` names a
    direction to point at on screen, such as "up-left". `focus` names the control in
    use, such as "RS" or "LT", so the live view can ring it. `intro` marks the phase
    where a check moves to another stick or trigger.

    `gate` names the input that starts the phase: a button such as "A", "LT" or "RT"
    past half, "LS" or "RS" past half way, or "D-pad". Inputs joined by "+" must all
    be down. The phase counts only once the gate input is pressed and every input is
    back at rest. When `gate` is None, `phase_gate` picks one: the focus or "A" for a
    check's first phase, and the focus for a phase with an `intro`.

    `rumble` is (low, high, ms) for the body motors, started once the phase counts.
    With `answer`, the phase waits for a yes or no: A or Enter means yes, B or
    Backspace means no, and `S` still skips the check. `gauge` is a target level for
    RT as a fraction of full, drawn on a gauge. `target(seconds)` gives a moving
    target as (x, y) fractions of full, with y growing downwards like SDL, and
    `cursor` names the stick, "left" or "right", that chases it.
    """

    prompt: str
    timeout_s: float
    duration_s: float | None = None
    end: Callable[[Snapshot], bool] | None = None
    until: Callable[[int], EndCondition] | None = None
    visual: str | None = None
    arrow: str | None = None
    focus: str | None = None
    intro: str = ""
    gate: str | None = None
    rumble: tuple[float, float, int] | None = None
    answer: bool = False
    gauge: float | None = None
    target: Callable[[float], tuple[float, float]] | None = None
    cursor: str | None = None
    timeout_ends_check: bool = False


@dataclass(frozen=True)
class CheckData:
    """What a check recorded. Samples are rows of (lx, ly, rx, ry, lt, rt, buttons)."""

    t_ns: np.ndarray
    samples: np.ndarray
    tool_gaps: np.ndarray
    phase_starts_ns: tuple[int, ...]
    end_ns: int
    timed_out: bool
    # Per phase: when it began counting, after any intro, what rumble() returned,
    # and the yes or no answer. None where it doesn't apply or didn't happen.
    counting_starts_ns: tuple[int, ...] = ()
    rumble_ok: tuple[bool | None, ...] = ()
    answers: tuple[bool | None, ...] = ()

    def phase_rows(self, index: int, extra_ns: int = 0) -> slice:
        """Rows that belong to phase `index`: the state held when it began counting, then
        every change until the next phase started, plus `extra_ns` beyond that. Time spent
        waiting at a gate belongs to no phase."""
        starts = self.phase_starts_ns
        counting = self.counting_starts_ns
        start = counting[index] if index < len(counting) else starts[index]
        stop = starts[index + 1] if index + 1 < len(starts) else self.end_ns
        first = max(0, int(np.searchsorted(self.t_ns, start, side="right")) - 1)
        last = int(np.searchsorted(self.t_ns, stop + extra_ns, side="right"))
        return slice(first, last)


@dataclass(frozen=True)
class Outcome:
    """A check's result. `lines` are short plain-language results for its end card."""

    status: Status
    metrics: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    phases: tuple[Phase, ...]
    suites: frozenset[str]
    is_timing: bool
    analyse: Callable[[CheckData], Outcome]
    # One or two plain lines for the start screen: what to do, and about how long.
    brief: str = ""


def phase_gate(check: Check, index: int) -> str | None:
    """The input that starts phase `index`, or None when it starts straight away."""
    phase = check.phases[index]
    if phase.gate is not None:
        return phase.gate
    if index == 0:
        return phase.focus or "A"
    return phase.focus if phase.intro and phase.focus else None


def gate_name(gate: str) -> str:
    return " + ".join(GATE_NAMES.get(part, part) for part in gate.split("+"))


def gate_prompt(gate: str) -> str:
    """What the start screen asks for, such as "Press RT to start"."""
    parts = gate.split("+")
    verb = "Push" if all(p in ("LS", "RS") for p in parts) else "Press"
    return f"{verb} {gate_short(gate)} to start"


def gate_short(gate: str) -> str:
    """The gate's inputs by their short names, such as "LB + RB" or "the left stick"."""
    return " + ".join(SHORT_NAMES.get(p, p) for p in gate.split("+"))


def _part_pressed(part: str, snap: Snapshot) -> bool:
    lx, ly, rx, ry, lt, rt, mask = snap
    if part == "LS":
        return math.hypot(lx, ly) > GATE_PUSH
    if part == "RS":
        return math.hypot(rx, ry) > GATE_PUSH
    if part == "LT":
        return lt > GATE_PUSH
    if part == "RT":
        return rt > GATE_PUSH
    if part == "D-pad":
        return mask & DPAD_BITS != 0
    bit = BUTTON_BITS.get(part)
    return bit is not None and mask & bit != 0


def gate_pressed(gate: str, snap: Snapshot) -> bool:
    return all(_part_pressed(part, snap) for part in gate.split("+"))


def at_rest(snap: Snapshot) -> bool:
    """No button down, both triggers under 5% and both sticks under 20%."""
    lx, ly, rx, ry, lt, rt, mask = snap
    return (
        mask == 0
        and lt < REST_TRIGGER
        and rt < REST_TRIGGER
        and math.hypot(lx, ly) < REST_STICK
        and math.hypot(rx, ry) < REST_STICK
    )


def uses_b(phase: Phase, gate: str | None = None) -> bool:
    """True when the phase reads B, so B is input there and not skip."""
    named = (phase.focus or "").split("+") + (gate or "").split("+")
    return phase.answer or "B" in named


def count_presses(bits: int, target: int, idle_s: float) -> Callable[[int], EndCondition]:
    """An end condition: `target` presses of all of `bits` together, or `idle_s` after the
    last press. The idle clock starts at the first press, so reading the prompt never ends
    the phase. A press already down when the phase starts isn't counted."""

    def until(start_ns: int) -> EndCondition:
        state: dict[str, Any] = {"down": None, "count": 0, "last": None}

        def end(t_ns: int, snap: Snapshot) -> bool:
            down = snap[MASK] & bits == bits
            if state["down"] is None:
                state["down"] = down
            elif down != state["down"]:
                state["down"], state["last"] = down, t_ns
                state["count"] += int(down)
            last = state["last"]
            idle = last is not None and t_ns - last >= idle_s * NS_PER_S
            return state["count"] >= target or idle

        return end

    return until
