"""Checks that this PC can hold a ~1 kHz polling loop."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from controller_lab.analysis import NS_PER_MS, loop_stats
from controller_lab.sampler import TOOL_GAP_NS

SLEEP_S = 0.0005
# Fallback if the loop misses its target: sleep, then spin until the pass is 1 ms old.
SPIN_WAIT = False
PERIOD_NS = 1_000_000


@dataclass(frozen=True)
class RigResult:
    median_ms: float
    p99_ms: float
    sleep_ms: float
    slow_passes: int
    ok: bool


def pace(pass_start_ns: int) -> None:
    time.sleep(SLEEP_S)
    if SPIN_WAIT:
        deadline = pass_start_ns + PERIOD_NS
        while time.perf_counter_ns() < deadline:
            pass


def run_rig(
    loop: Callable[[], object],
    median_limit_ms: float,
    p99_limit_ms: float,
    seconds: float = 5,
) -> RigResult:
    """Run `loop` once per pass for `seconds`, paced like the app, and time it.

    The loop limits come from the controller's profile.
    """
    passes: list[int] = []
    sleeps: list[int] = []
    end = time.perf_counter_ns() + int(seconds * 1e9)
    prev = time.perf_counter_ns()
    while prev < end:
        loop()
        s0 = time.perf_counter_ns()
        pace(prev)
        now = time.perf_counter_ns()
        sleeps.append(now - s0)
        passes.append(now - prev)
        prev = now
    stats = loop_stats(np.array(passes))
    slow = sum(1 for p in passes if p > TOOL_GAP_NS)
    return RigResult(
        median_ms=stats.median_ms,
        p99_ms=stats.p99_ms,
        sleep_ms=float(np.median(sleeps)) / NS_PER_MS,
        slow_passes=slow,
        ok=stats.median_ms <= median_limit_ms and stats.p99_ms <= p99_limit_ms,
    )
