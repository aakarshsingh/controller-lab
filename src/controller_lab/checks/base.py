"""What a check is made of. No pygame imports."""

from __future__ import annotations

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
INTRO_S = 2.5


@dataclass(frozen=True)
class Phase:
    """One prompted step of a check.

    The phase ends when `end(snapshot)` turns true, when the end condition made by
    `until(start_ns)` turns true, when `duration_s` has passed, or when `timeout_s`
    has passed. `until` is called afresh each time the phase starts, so its end
    condition can count presses or rotations. A timeout with `timeout_ends_check` ends
    the whole check, and the check's `analyse` sees `timed_out`. `arrow` names a
    direction to point at on screen, such as "up-left". `focus` names the control in
    use, such as "RS" or "LT", so the live view can ring it. `intro` is a get-ready
    banner shown for `INTRO_S` before the phase starts counting, used when a check
    moves to another stick or trigger.

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
        """Rows that belong to phase `index`: the state held when it started, then every
        change until the next phase started, plus `extra_ns` beyond that."""
        starts = self.phase_starts_ns
        start = starts[index]
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


def count_presses(bits: int, target: int, idle_s: float) -> Callable[[int], EndCondition]:
    """An end condition: `target` presses of all of `bits` together, or `idle_s` with no
    change to them. The idle clock starts with the phase."""

    def until(start_ns: int) -> EndCondition:
        state = {"down": False, "count": 0, "last": start_ns}

        def end(t_ns: int, snap: Snapshot) -> bool:
            down = snap[MASK] & bits == bits
            if down != state["down"]:
                state["down"], state["last"] = down, t_ns
                state["count"] += int(down)
            return state["count"] >= target or t_ns - state["last"] >= idle_s * NS_PER_S

        return end

    return until
