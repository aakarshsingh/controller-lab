"""Pure metric functions. Arrays in, numbers out. No pygame imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import pairwise

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


# ---- idle drift ------------------------------------------------------------

DIRECTIONS = ("right", "up-right", "up", "up-left", "left", "down-left", "down", "down-right")


def _weighted(values: np.ndarray, weights: np.ndarray | None) -> np.ndarray:
    v = np.asarray(values, dtype=np.float64)
    if weights is None:
        return np.ones_like(v)
    return np.asarray(weights, dtype=np.float64)


def stick_offset(
    xs: np.ndarray, ys: np.ndarray, weights: np.ndarray | None = None
) -> tuple[float, str]:
    """Mean resting offset as % of the full radius, and its direction.

    `weights` is how long each sample was held, so a value held for seconds counts
    for more than a brief flicker. SDL's y axis grows downwards.
    """
    x, y = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
    w = _weighted(x, weights)
    mx, my = float(np.average(x, weights=w)), float(np.average(y, weights=w))
    pct = float(np.hypot(mx, my)) / AXIS_MAX * 100
    sector = int(np.round(np.degrees(np.arctan2(-my, mx)) / 45)) % 8
    return pct, DIRECTIONS[sector]


def noise_std(xs: np.ndarray, ys: np.ndarray, weights: np.ndarray | None = None) -> float:
    """Spread of the stick around its mean position, as % of the full radius."""
    x, y = np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)
    w = _weighted(x, weights)
    var_x = np.average((x - np.average(x, weights=w)) ** 2, weights=w)
    var_y = np.average((y - np.average(y, weights=w)) ** 2, weights=w)
    return float(np.sqrt(var_x + var_y)) / AXIS_MAX * 100


def max_deviation(xs: np.ndarray, ys: np.ndarray) -> float:
    """The furthest the stick got from the centre, as % of the full radius."""
    r = np.hypot(np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64))
    return float(r.max()) / AXIS_MAX * 100 if r.size else 0.0


def button_presses(masks: np.ndarray) -> int:
    """Presses in a run of button bitmasks: bits set at the start, plus rising edges."""
    m = np.asarray(masks, dtype=np.int64)
    if m.size == 0:
        return 0
    rising = np.concatenate(([m[0]], m[1:] & ~m[:-1]))
    return sum(int(v).bit_count() for v in rising)


# ---- sticks ----------------------------------------------------------------

PUSH_RADIUS = 0.8  # a centring push counts once the stick passes this
RELEASE_RADIUS = 0.3  # and is released once it comes back inside this
SETTLE_NS = 300 * NS_PER_MS
RIM_RADIUS = 0.5  # circle samples inside this are ignored
CIRCLE_BIN_DEG = 5
AXIS_WINDOW_DEG = 10


def _angles(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Angle in degrees from 0 to 360, counter-clockwise from right, with up as 90."""
    return np.degrees(np.arctan2(-np.asarray(y, float), np.asarray(x, float))) % 360


def snapback(
    t_ns: np.ndarray, x: np.ndarray, y: np.ndarray, settle_ns: int = SETTLE_NS
) -> tuple[float, float] | None:
    """Settle offset and opposite-direction spike for one push, both as % of full.

    The push direction is where the stick was furthest out. Release is the first
    sample after that back inside 30%. The spike is the furthest the stick swings
    past centre the other way within `settle_ns`, and the settle offset is where it
    rests `settle_ns` after release. None when the push or release didn't happen.
    """
    t = np.asarray(t_ns, dtype=np.int64)
    xf, yf = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    r = np.hypot(xf, yf)
    if r.size == 0:
        return None
    peak = int(np.argmax(r))
    if r[peak] < PUSH_RADIUS * AXIS_MAX:
        return None
    after = np.flatnonzero(r[peak:] < RELEASE_RADIUS * AXIS_MAX)
    if after.size == 0:
        return None
    rel = peak + int(after[0])
    ux, uy = xf[peak] / r[peak], yf[peak] / r[peak]
    window = (t >= t[rel]) & (t <= t[rel] + settle_ns)
    spike = max(0.0, float(np.max(-(xf[window] * ux + yf[window] * uy))))
    settle = int(np.searchsorted(t, t[rel] + settle_ns, side="right")) - 1
    return float(r[settle]) / AXIS_MAX * 100, spike / AXIS_MAX * 100


@dataclass(frozen=True)
class CircleStats:
    error_pct: float
    weakest: str
    lr_diff_pct: float
    ud_diff_pct: float
    bins_covered: int


def circle_stats(x: np.ndarray, y: np.ndarray) -> CircleStats:
    """Circle error from stick samples pressed against the rim.

    Each 5° bin keeps the furthest reach in it, as a fraction of full. Circle error is
    the mean distance of those reaches from a perfect circle. The weakest direction
    is the 45° sector with the lowest mean reach. The reach differences compare
    right against left and up against down, within 10° of each axis.
    """
    xf, yf = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    r = np.hypot(xf, yf) / AXIS_MAX
    rim = r > RIM_RADIUS
    nan = float("nan")
    if not rim.any():
        return CircleStats(nan, "", nan, nan, 0)
    deg, r = _angles(xf[rim], yf[rim]), r[rim]
    n_bins = 360 // CIRCLE_BIN_DEG
    bins = (deg // CIRCLE_BIN_DEG).astype(int) % n_bins
    reach = np.full(n_bins, np.nan)
    np.fmax.at(reach, bins, r)
    covered = ~np.isnan(reach)
    centres = (np.arange(n_bins) + 0.5) * CIRCLE_BIN_DEG

    def mean_reach(around: float, half_width: float) -> float:
        dist = np.abs((centres - around + 180) % 360 - 180)
        sel = covered & (dist <= half_width)
        return float(reach[sel].mean()) if sel.any() else nan

    sectors = [mean_reach(k * 45, 22.5) for k in range(8)]
    known = [(v, k) for k, v in enumerate(sectors) if not np.isnan(v)]
    weakest = DIRECTIONS[min(known)[1]] if known else ""
    w = AXIS_WINDOW_DEG
    return CircleStats(
        error_pct=float(np.abs(reach[covered] - 1.0).mean()) * 100,
        weakest=weakest,
        lr_diff_pct=(mean_reach(0, w) - mean_reach(180, w)) * 100,
        ud_diff_pct=(mean_reach(90, w) - mean_reach(270, w)) * 100,
        bins_covered=int(covered.sum()),
    )


def sweep_stats(values: np.ndarray) -> tuple[int, int, float]:
    """Distinct values, distinct values within ±10% of centre, and the max jump in %."""
    v = np.asarray(values, dtype=np.int64)
    if v.size == 0:
        return 0, 0, float("nan")
    distinct = np.unique(v)
    near = int((np.abs(distinct) <= 0.1 * AXIS_MAX).sum())
    jump = float(np.abs(np.diff(v)).max()) / AXIS_MAX * 100 if v.size > 1 else 0.0
    return int(distinct.size), near, jump


def trigger_stats(values: np.ndarray) -> tuple[int, float, int, float]:
    """Rest value, max reach in %, distinct values, and the max jump in %."""
    v = np.asarray(values, dtype=np.int64)
    if v.size == 0:
        return 0, float("nan"), 0, float("nan")
    jump = float(np.abs(np.diff(v)).max()) / AXIS_MAX * 100 if v.size > 1 else 0.0
    return int(v[0]), float(v.max()) / AXIS_MAX * 100, int(np.unique(v).size), jump


# ---- buttons and D-pad ----------------------------------------------------

CHATTER_NS = 10 * NS_PER_MS


def press_train(t_ns: np.ndarray, pressed: np.ndarray) -> tuple[int, int]:
    """Presses, and chatter: presses or gaps between presses shorter than 10 ms."""
    t = np.asarray(t_ns, dtype=np.int64)
    p = np.asarray(pressed, dtype=bool)
    if p.size == 0:
        return 0, 0
    change = np.flatnonzero(np.diff(p.astype(np.int8)) != 0) + 1
    edges_t, edges_v = t[change], p[change]
    presses = int(edges_v.sum()) + int(p[0])
    # Every stretch between two edges is a press or a gap. The first and last are open.
    chatter = int((np.diff(edges_t) < CHATTER_NS).sum())
    return presses, chatter


def _rising(masks: np.ndarray) -> np.ndarray:
    m = np.asarray(masks, dtype=np.int64)
    if m.size == 0:
        return m
    return np.concatenate(([m[0]], m[1:] & ~m[:-1]))


def dpad_ghosts(masks: np.ndarray, neighbours: int) -> int:
    """Presses of the neighbouring D-pad directions while one direction is tapped."""
    return sum(int(v & neighbours).bit_count() for v in _rising(masks))


def diagonal_presses(masks: np.ndarray, pair: int) -> int:
    """Times both directions of a diagonal came down together."""
    both = (np.asarray(masks, dtype=np.int64) & pair) == pair
    if both.size == 0:
        return 0
    return int(both[0]) + int((both[1:] & ~both[:-1]).sum())


# ---- holds, flicks, throttle and tracking ---------------------------------

DROPOUT_MAX_NS = 200 * NS_PER_MS  # a longer gap is the person letting go, not a dropout
RIGHT_DEADZONE = 8689  # XInput's right stick deadzone
FLICK_WINDOW_NS = 150 * NS_PER_MS
JITTER_GRID_NS = NS_PER_MS
JITTER_WINDOW = 100  # grid steps, so 100 ms


def dropouts(t_ns: np.ndarray, held: np.ndarray, max_ns: int = DROPOUT_MAX_NS) -> int:
    """Brief releases inside a hold: held, then not for under `max_ns`, then held again."""
    t = np.asarray(t_ns, dtype=np.int64)
    h = np.asarray(held, dtype=bool)
    if h.size < 3:
        return 0
    change = np.flatnonzero(np.diff(h.astype(np.int8)) != 0) + 1
    count = 0
    for a, b in pairwise(change):
        if not h[a] and h[b] and t[b] - t[a] < max_ns:
            count += 1
    return count


def flick_result(
    t_ns: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    deadzone: int = RIGHT_DEADZONE,
    window_ns: int = FLICK_WINDOW_NS,
) -> tuple[str | None, bool]:
    """The direction a flick registered, and whether it snapped back the wrong way.

    The direction is the 45° sector of the furthest reading. A wrong-direction
    snapback is a reading opposite the flick beyond `deadzone`, within `window_ns` of
    release. Release is the first reading back inside 30% after the furthest one.
    Returns (None, False) when the stick never passed 80%.
    """
    t = np.asarray(t_ns, dtype=np.int64)
    xf, yf = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
    r = np.hypot(xf, yf)
    if r.size == 0:
        return None, False
    peak = int(np.argmax(r))
    if r[peak] < PUSH_RADIUS * AXIS_MAX:
        return None, False
    sector = int(np.round(_angles(xf[peak : peak + 1], yf[peak : peak + 1])[0] / 45)) % 8
    after = np.flatnonzero(r[peak:] < RELEASE_RADIUS * AXIS_MAX)
    if after.size == 0:
        return DIRECTIONS[sector], False
    rel = peak + int(after[0])
    ux, uy = xf[peak] / r[peak], yf[peak] / r[peak]
    window = (t >= t[rel]) & (t <= t[rel] + window_ns)
    opposite = -(xf[window] * ux + yf[window] * uy)
    return DIRECTIONS[sector], bool(opposite.size and opposite.max() > deadzone)


def segments(t_ns: np.ndarray, seg_ns: int) -> list[slice]:
    """Split a series into consecutive windows of `seg_ns` from its first sample."""
    t = np.asarray(t_ns, dtype=np.int64)
    if t.size == 0:
        return []
    n = int((t[-1] - t[0]) // seg_ns) + 1
    edges = np.searchsorted(t, t[0] + np.arange(1, n) * seg_ns, side="left")
    bounds = [0, *edges.tolist(), t.size]
    return [slice(a, b) for a, b in pairwise(bounds) if b > a]


def _step_grid(t_ns: np.ndarray, v: np.ndarray, step_ns: int) -> np.ndarray:
    """Hold each value until the next change, sampled every `step_ns`."""
    t = np.asarray(t_ns, dtype=np.int64)
    grid = np.arange(t[0], t[-1], step_ns)
    return np.asarray(v, dtype=np.float64)[np.searchsorted(t, grid, side="right") - 1]


def jitter(t_ns: np.ndarray, values: np.ndarray) -> float:
    """Fast jitter in % of full scale: the std left after a 100 ms moving average."""
    t = np.asarray(t_ns, dtype=np.int64)
    if t.size < 2:
        return float("nan")
    g = _step_grid(t, values, JITTER_GRID_NS)
    w = JITTER_WINDOW
    if g.size <= w:
        return float("nan")
    trend = np.convolve(g, np.ones(w) / w, mode="valid")
    centred = g[w // 2 : w // 2 + trend.size]
    return float((centred - trend).std()) / AXIS_MAX * 100


def tracking_error(
    t_ns: np.ndarray, x: np.ndarray, y: np.ndarray, tx: np.ndarray, ty: np.ndarray
) -> np.ndarray:
    """Distance from stick to target at each sample, in % of the full radius.

    `tx` and `ty` are the target's position at each sample time, as fractions of
    full, with y growing downwards like SDL.
    """
    sx = np.asarray(x, dtype=np.float64) / AXIS_MAX
    sy = np.asarray(y, dtype=np.float64) / AXIS_MAX
    return np.hypot(sx - np.asarray(tx), sy - np.asarray(ty)) * 100
