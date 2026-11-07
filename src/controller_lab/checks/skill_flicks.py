"""Skill flicks: 16 quick right stick flicks to prompted directions."""

from __future__ import annotations

import random
from math import hypot

from controller_lab import analysis
from controller_lab.checks.base import (
    NS_PER_S,
    Check,
    CheckData,
    EndCondition,
    Outcome,
    Phase,
    Snapshot,
)

SEED = 16
FLICK_TIMEOUT_S = 5
# After release, keep recording long enough to see a wrong-way snapback.
AFTER_S = 0.25
PUSH = analysis.PUSH_RADIUS * analysis.AXIS_MAX
RELEASE = analysis.RELEASE_RADIUS * analysis.AXIS_MAX
# Every direction twice, in a fixed shuffled order so runs stay comparable.
ORDER = tuple(random.Random(SEED).sample(analysis.DIRECTIONS * 2, len(analysis.DIRECTIONS) * 2))


def flicked(start_ns: int) -> EndCondition:
    """End 0.25 s after the right stick passed 80% and first came back inside 30%."""
    state: dict[str, object] = {"out": False, "released": None}

    def end(t_ns: int, snap: Snapshot) -> bool:
        r = hypot(snap[2], snap[3])
        if r >= PUSH:
            state["out"] = True
        elif state["out"] and r < RELEASE and state["released"] is None:
            state["released"] = t_ns
        released = state["released"]
        return released is not None and t_ns - released >= AFTER_S * NS_PER_S

    return end


PHASES = tuple(
    Phase(
        f"Right stick: flick {direction}, quick and back",
        timeout_s=FLICK_TIMEOUT_S,
        until=flicked,
        visual="controller",
        arrow=direction,
        focus="RS",
        intro="Use the RIGHT stick" if n == 0 else "",
    )
    for n, direction in enumerate(ORDER)
)


def analyse(data: CheckData) -> Outcome:
    flicks = []
    correct = wrong = missed = snapbacks = 0
    for i, prompted in enumerate(ORDER):
        rows = data.phase_rows(i)
        registered, snapback = analysis.flick_result(
            data.t_ns[rows], data.samples[rows, 2], data.samples[rows, 3]
        )
        flicks.append({"prompted": prompted, "registered": registered, "snapback": snapback})
        if registered is None:
            missed += 1
        elif registered == prompted:
            correct += 1
        else:
            wrong += 1
        snapbacks += int(snapback)
    metrics = {
        "flicks": flicks,
        "correct": correct,
        "wrong_direction": wrong,
        "missed": missed,
        "snapbacks": snapbacks,
    }
    if missed == len(ORDER):
        return Outcome("skipped", metrics, "No flicks were detected")
    lines = (
        f"Right direction: {correct} of {len(ORDER)}",
        f"Wrong direction: {wrong}",
        f"Missed: {missed}",
        f"Snapped back the wrong way: {snapbacks}",
    )
    return Outcome("done", metrics, lines=lines)


CHECK = Check(
    id="skill_flicks",
    title="Skill flicks",
    phases=PHASES,
    suites=frozenset({"full"}),
    is_timing=False,
    analyse=analyse,
)
