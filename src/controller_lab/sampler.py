"""Records timestamped pad state changes and loop-pass durations. No pygame imports."""

from __future__ import annotations

import numpy as np

TOOL_GAP_NS = 2_000_000  # a loop pass longer than this means the tool stopped polling


class Recorder:
    def __init__(self, timing: bool = False) -> None:
        self.timing = timing
        self._t: list[int] = []
        self._samples: list[tuple[int, ...]] = []
        self._passes: list[int] = []
        self._gaps: list[tuple[int, int]] = []
        self._last: tuple[int, ...] | None = None

    def add(self, t_ns: int, snapshot: tuple[int, ...]) -> None:
        """Store the snapshot only when it differs from the last one."""
        if snapshot != self._last:
            self._t.append(t_ns)
            self._samples.append(snapshot)
            self._last = snapshot

    def mark_pass(self, duration_ns: int, end_ns: int) -> None:
        """Record one loop pass. With timing on, a slow pass becomes a tool gap."""
        self._passes.append(duration_ns)
        if self.timing and duration_ns > TOOL_GAP_NS:
            self._gaps.append((end_ns - duration_ns, end_ns))

    @property
    def t_ns(self) -> np.ndarray:
        return np.array(self._t, dtype=np.int64)

    @property
    def samples(self) -> np.ndarray:
        return np.array(self._samples, dtype=np.int64).reshape(len(self._samples), -1)

    @property
    def pass_ns(self) -> np.ndarray:
        return np.array(self._passes, dtype=np.int64)

    @property
    def tool_gaps(self) -> np.ndarray:
        return np.array(self._gaps, dtype=np.int64).reshape(-1, 2)
