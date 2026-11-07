"""D-pad: tap each direction 10 times and each diagonal 5 times."""

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

TAPS = 10
DIAGONAL_TAPS = 5
IDLE_S = 3
TIMEOUT_S = 45
UP, DOWN, LEFT, RIGHT = (BUTTON_BITS[n] for n in ("Up", "Down", "Left", "Right"))
# Each direction, and the directions next to it that shouldn't fire.
CARDINALS = {
    "up": (UP, LEFT | RIGHT),
    "down": (DOWN, LEFT | RIGHT),
    "left": (LEFT, UP | DOWN),
    "right": (RIGHT, UP | DOWN),
}
DIAGONALS = {
    "up-left": UP | LEFT,
    "up-right": UP | RIGHT,
    "down-left": DOWN | LEFT,
    "down-right": DOWN | RIGHT,
}

PHASES = tuple(
    Phase(
        f"Tap the D-pad {name} {TAPS} times",
        timeout_s=TIMEOUT_S,
        until=count_presses(bit, TAPS, IDLE_S),
        visual="controller",
        arrow=name,
        focus="D-pad",
    )
    for name, (bit, _) in CARDINALS.items()
) + tuple(
    Phase(
        f"Tap the D-pad {name} diagonal {DIAGONAL_TAPS} times",
        timeout_s=TIMEOUT_S,
        until=count_presses(pair, DIAGONAL_TAPS, IDLE_S),
        visual="controller",
        arrow=name,
        focus="D-pad",
    )
    for name, pair in DIAGONALS.items()
)


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {}
    lines: list[str] = []
    activity = ghosts_total = diagonals_total = 0
    for i, (name, (bit, neighbours)) in enumerate(CARDINALS.items()):
        rows = data.phase_rows(i)
        masks = data.samples[rows, MASK]
        count, _ = analysis.press_train(data.t_ns[rows], masks & bit != 0)
        ghosts = analysis.dpad_ghosts(masks, neighbours)
        metrics[f"{name}_count"] = count
        metrics[f"{name}_ghosts"] = ghosts
        lines.append(f"{name}: {count} of {TAPS}" + (f", {ghosts} ghost" if ghosts else ""))
        activity += count
        ghosts_total += ghosts
    for j, (name, pair) in enumerate(DIAGONALS.items(), start=len(CARDINALS)):
        registered = analysis.diagonal_presses(data.samples[data.phase_rows(j), MASK], pair)
        metrics[f"{name}_registered"] = registered
        lines.append(f"{name}: {registered} of {DIAGONAL_TAPS}")
        diagonals_total += registered
    metrics["ghosts_total"] = ghosts_total
    metrics["diagonals_registered"] = diagonals_total
    metrics["diagonals_expected"] = DIAGONAL_TAPS * len(DIAGONALS)
    if activity + diagonals_total == 0:
        return Outcome("skipped", metrics, "No D-pad presses were detected")
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="dpad",
    title="D-pad",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
)
