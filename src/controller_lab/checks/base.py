"""What a check is made of. No pygame imports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

# (lx, ly, rx, ry, lt, rt, buttons), as `source.Gamepad.snapshot()` returns it.
Snapshot = tuple[int, ...]
Status = Literal["done", "skipped", "voided", "not_tested", "interrupted"]


@dataclass(frozen=True)
class Phase:
    """One prompted step of a check.

    The phase ends when `end(snapshot)` turns true, when `duration_s` has passed, or
    when `timeout_s` has passed. A timeout with `timeout_ends_check` ends the whole
    check, and the check's `analyse` sees `timed_out`.
    """

    prompt: str
    timeout_s: float
    duration_s: float | None = None
    end: Callable[[Snapshot], bool] | None = None
    visual: str | None = None
    timeout_ends_check: bool = False


@dataclass(frozen=True)
class CheckData:
    """What a check recorded: samples as (t_ns, lx, ly, rx, ry, lt, rt, buttons)."""

    t_ns: np.ndarray
    samples: np.ndarray
    tool_gaps: np.ndarray
    phase_starts_ns: tuple[int, ...]
    end_ns: int
    timed_out: bool


@dataclass(frozen=True)
class Outcome:
    status: Status
    metrics: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    phases: tuple[Phase, ...]
    suites: frozenset[str]
    is_timing: bool
    analyse: Callable[[CheckData], Outcome]
