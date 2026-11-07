"""Button taps: tap each button 20 times. The counter stays hidden."""

from __future__ import annotations

from controller_lab import analysis
from controller_lab.checks.base import (
    BUTTON_BITS,
    MASK,
    Check,
    CheckData,
    Outcome,
    Phase,
    count_presses,
)

TAPS = 20
IDLE_S = 3
TIMEOUT_S = 60
# Name in the prompt, and the bit it reads. View and Menu are SDL's Back and Start.
FULL_BUTTONS = {
    "A": "A",
    "B": "B",
    "X": "X",
    "Y": "Y",
    "LB": "LB",
    "RB": "RB",
    "View": "Back",
    "Menu": "Start",
    "the left stick click": "LS",
    "the right stick click": "RS",
}
CONNECTION_BUTTONS = {"A": "A", "B": "B"}
SHORT_NAMES = {"the left stick click": "LS click", "the right stick click": "RS click"}


def _phases(buttons: dict[str, str]) -> tuple[Phase, ...]:
    return tuple(
        Phase(
            f"Tap {label} {TAPS} times, at your own pace",
            timeout_s=TIMEOUT_S,
            until=count_presses(BUTTON_BITS[bit], TAPS, IDLE_S),
            visual="controller",
            focus=bit,
        )
        for label, bit in buttons.items()
    )


def _analyse(buttons: dict[str, str]):
    def analyse(data: CheckData) -> Outcome:
        metrics: dict[str, object] = {}
        lines: list[str] = []
        total = chatter_total = 0
        for i, (label, bit) in enumerate(buttons.items()):
            rows = data.phase_rows(i)
            pressed = data.samples[rows, MASK] & BUTTON_BITS[bit] != 0
            count, chatter = analysis.press_train(data.t_ns[rows], pressed)
            metrics[f"{bit}_count"] = count
            metrics[f"{bit}_chatter"] = chatter
            name = SHORT_NAMES.get(label, label)
            lines.append(f"{name}: {count} of {TAPS}" + (f", chatter {chatter}" if chatter else ""))
            total += count
            chatter_total += chatter
        metrics["expected_per_button"] = TAPS
        metrics["chatter_total"] = chatter_total
        if total == 0:
            return Outcome("skipped", metrics, "No presses were detected")
        return Outcome("done", metrics, lines=tuple(lines))

    return analyse


CHECK = Check(
    id="button_taps",
    title="Button taps",
    phases=_phases(FULL_BUTTONS),
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=_analyse(FULL_BUTTONS),
)
# The Connection suite taps A and B only. It shares the id, as only one runs per suite.
CONNECTION_CHECK = Check(
    id="button_taps",
    title="Button taps",
    phases=_phases(CONNECTION_BUTTONS),
    suites=frozenset({"connection"}),
    is_timing=False,
    analyse=_analyse(CONNECTION_BUTTONS),
)
