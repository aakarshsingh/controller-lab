"""Pure metric functions. Arrays in, numbers out. No pygame imports."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

NS_PER_MS = 1_000_000
GAP_LIMIT_MS = 30.0
AXIS_MAX = 32767
MOTION_RADIUS = 0.5  # the stick counts as moving once it passes half way
STILL_MIN_NS = 20 * NS_PER_MS  # only intervals longer than this can be still
STILL_SPEED_FRACTION = 0.25


@dataclass(frozen=True)
class TimingStats:
    n: int
    rate_hz: float
    mean_ms: float
    jitter_ms: float
    p99_ms: float
    gaps_over_30ms: int
    max_gap_ms: float
    moving_s: float = float("nan")
    still_count: int = 0


@dataclass(frozen=True)
class LoopStats:
    median_ms: float
    p99_ms: float


def intervals(
    t_ns: np.ndarray, tool_gaps: np.ndarray, exclude: np.ndarray | None = None
) -> np.ndarray:
    """Intervals between state changes, in ns.

    An interval that overlaps a tool gap is left out, because the tool was not
    polling for part of it and the interval says nothing about the controller.
    `tool_gaps` is an (N, 2) array of start and end times in ns. `exclude` is an
    optional boolean mask, one entry per interval, of intervals to leave out too.
    """
    t = np.asarray(t_ns, dtype=np.int64)
    if t.size < 2:
        return np.empty(0, dtype=np.int64)
    starts, ends = t[:-1], t[1:]
    keep = np.ones(starts.size, dtype=bool)
    if exclude is not None:
        keep &= ~np.asarray(exclude, dtype=bool)
    for gap_start, gap_end in np.asarray(tool_gaps, dtype=np.int64).reshape(-1, 2):
        keep &= ~((starts < gap_end) & (ends > gap_start))
    return (ends - starts)[keep]


def motion_start(x: np.ndarray, y: np.ndarray) -> int | None:
    """Index of the first sample where the stick is past half way, or None."""
    radius = np.hypot(np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64))
    past = np.flatnonzero(radius > MOTION_RADIUS * AXIS_MAX)
    return int(past[0]) if past.size else None


def still_mask(t_ns: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """One boolean per interval: True where the stick was still, not dropping out.

    State changes can't tell a still stick from a dropout, so the stick's path
    decides. An interval longer than 20 ms is still when either end is inside half
    way, or when it covers under a quarter of the angle the median spin speed
    predicts. A dropout while spinning covers the full angle, so it stays a gap.
    """
    t = np.asarray(t_ns, dtype=np.int64)
    if t.size < 2:
        return np.zeros(0, dtype=bool)
    xf, yf = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    outside = np.hypot(xf, yf) >= MOTION_RADIUS * AXIS_MAX
    dt = np.diff(t)
    step = np.abs(np.angle(np.exp(1j * np.diff(np.arctan2(yf, xf)))))  # wrapped to ±π
    both_out = outside[:-1] & outside[1:]
    long = dt > STILL_MIN_NS
    still = long & ~both_out
    normal = ~long & both_out & (dt > 0)
    if normal.any():
        speed = float(np.median(step[normal] / dt[normal]))
        still |= long & both_out & (step < STILL_SPEED_FRACTION * speed * dt)
    return still


def spin_timing(
    t_ns: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    tool_gaps: np.ndarray,
    window_ns: int,
) -> TimingStats:
    """Report timing for a stick spin, from the first movement for `window_ns`.

    Tool gaps and still stretches are left out. Moving time is the total length of
    the intervals that aren't still.
    """
    t = np.asarray(t_ns, dtype=np.int64)
    start = motion_start(x, y)
    if start is None:
        return timing_stats(np.empty(0, dtype=np.int64))
    end = int(np.searchsorted(t, t[start] + window_ns, side="right"))
    t, xs, ys = t[start:end], np.asarray(x)[start:end], np.asarray(y)[start:end]
    still = still_mask(t, xs, ys)
    stats = timing_stats(intervals(t, tool_gaps, exclude=still))
    moving_ns = int(np.diff(t)[~still].sum()) if t.size > 1 else 0
    return replace(stats, moving_s=moving_ns / 1e9, still_count=int(still.sum()))


def timing_stats(intervals_ns: np.ndarray) -> TimingStats:
    """Report rate, jitter, p99 interval, gaps over 30 ms and the max gap."""
    ms = np.asarray(intervals_ns, dtype=np.float64) / NS_PER_MS
    if ms.size == 0:
        nan = float("nan")
        return TimingStats(0, nan, nan, nan, nan, 0, nan)
    mean = float(ms.mean())
    return TimingStats(
        n=int(ms.size),
        rate_hz=1000.0 / mean if mean > 0 else float("nan"),
        mean_ms=mean,
        jitter_ms=float(ms.std()),
        p99_ms=float(np.percentile(ms, 99)),
        gaps_over_30ms=int((ms > GAP_LIMIT_MS).sum()),
        max_gap_ms=float(ms.max()),
    )


def loop_stats(durations_ns: np.ndarray) -> LoopStats:
    """Median and p99 of loop-pass durations, in ms."""
    ms = np.asarray(durations_ns, dtype=np.float64) / NS_PER_MS
    if ms.size == 0:
        return LoopStats(float("nan"), float("nan"))
    return LoopStats(float(np.median(ms)), float(np.percentile(ms, 99)))
