"""Haptics: run the low motor, then the high motor, and ask if each was felt. Not scored."""

from __future__ import annotations

from controller_lab.checks.base import Check, CheckData, Outcome, Phase

BUZZ_MS = 1000
ANSWER_TIMEOUT_S = 15
MOTORS = {"low": (1.0, 0.0), "high": (0.0, 1.0)}


def _phases() -> tuple[Phase, ...]:
    phases = []
    for n, (name, (low, high)) in enumerate(MOTORS.items()):
        phases.append(
            Phase(
                f"The {name} motor buzzes for 1 s",
                timeout_s=BUZZ_MS / 1000,
                duration_s=BUZZ_MS / 1000,
                rumble=(low, high, BUZZ_MS),
                visual="controller",
                intro="Pick up the controller and hold it" if n == 0 else "",
            )
        )
        phases.append(
            Phase(f"Did you feel the {name} motor buzz?", timeout_s=ANSWER_TIMEOUT_S, answer=True)
        )
    return tuple(phases)


PHASES = _phases()


def _said(answer: bool | None) -> str:
    return "no answer" if answer is None else "felt" if answer else "not felt"


def analyse(data: CheckData) -> Outcome:
    metrics: dict[str, object] = {"scored": False}
    lines: list[str] = []
    answered = False
    for n, name in enumerate(MOTORS):
        ok = data.rumble_ok[2 * n] if data.rumble_ok else None
        felt = data.answers[2 * n + 1] if data.answers else None
        answered |= felt is not None
        metrics[f"{name}_rumble_ok"] = ok
        metrics[f"{name}_felt"] = felt
        sent = "sent" if ok else "not accepted by the controller"
        lines.append(f"{name.capitalize()} motor: {_said(felt)}, buzz {sent}")
    if not answered:
        return Outcome("skipped", metrics, "No answer was given", lines=tuple(lines))
    return Outcome("done", metrics, lines=tuple(lines))


CHECK = Check(
    id="haptics",
    title="Haptics",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
    brief=(
        "Hold the controller in both hands. Each motor buzzes for 1 s, then press A if"
        " you felt it, or B if you didn't. Not scored. About 20 s."
    ),
)
