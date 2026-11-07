import numpy as np

from controller_lab.analysis import intervals, motion_start, spin_timing, timing_stats

MS = 1_000_000


def test_synthetic_125_hz_timeline_reads_about_125_hz():
    t_ns = np.arange(0, 2_000) * 8 * MS
    stats = timing_stats(intervals(t_ns, np.empty((0, 2), dtype=np.int64)))
    assert abs(stats.rate_hz - 125.0) < 0.5
    assert stats.gaps_over_30ms == 0


def test_injected_50_ms_gap_is_counted_and_is_the_max_gap():
    t_ns = np.arange(0, 500) * 8 * MS
    t_ns[250:] += 42 * MS  # one interval becomes 50 ms
    stats = timing_stats(intervals(t_ns, np.empty((0, 2), dtype=np.int64)))
    assert stats.gaps_over_30ms == 1
    assert abs(stats.max_gap_ms - 50.0) < 1e-6


def test_interval_overlapping_a_tool_gap_is_excluded():
    t_ns = np.array([0, 8, 16, 66, 74]) * MS
    tool_gaps = np.array([[20 * MS, 25 * MS]])  # inside the 16 to 66 ms interval
    result = intervals(t_ns, tool_gaps)
    assert list(result) == [8 * MS, 8 * MS, 8 * MS]


# ---- spin timing: motion start and still stretches ----------------------

RIM = 0.9 * 32767
STEP_MS = 8
TURN_RAD_S = 2 * np.pi  # one rotation per second
NO_GAPS = np.empty((0, 2), dtype=np.int64)
WINDOW_NS = 15_000 * MS


def spin(*segments: tuple[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a 125 Hz state-change timeline from ("spin", s), ("pause", s),
    ("centre", s) and ("drop", ms) segments. A pause or centre stretch makes no
    samples, as the stick isn't changing. A drop loses reports while moving."""
    t, x, y = [0.0], [RIM], [0.0]
    angle = 0.0
    for kind, amount in segments:
        if kind == "spin":
            for _ in range(int(amount * 1000 / STEP_MS)):
                t.append(t[-1] + STEP_MS)
                angle += TURN_RAD_S * STEP_MS / 1000
                x.append(RIM * np.cos(angle))
                y.append(RIM * np.sin(angle))
        elif kind == "drop":
            t.append(t[-1] + amount)
            angle += TURN_RAD_S * amount / 1000
            x.append(RIM * np.cos(angle))
            y.append(RIM * np.sin(angle))
        elif kind in ("pause", "centre"):
            if kind == "centre":
                t.append(t[-1] + STEP_MS)
                x.append(0.0)
                y.append(0.0)
            t.append(t[-1] + amount * 1000)
            angle += TURN_RAD_S * STEP_MS / 1000
            x.append(RIM * np.cos(angle))
            y.append(RIM * np.sin(angle))
    return (
        (np.array(t) * MS).astype(np.int64),
        np.array(x).astype(np.int64),
        np.array(y).astype(np.int64),
    )


def test_motion_start_finds_first_sample_past_half_way():
    x = np.array([0, 3000, 12000, 17000, 30000])
    y = np.array([0, 0, 0, 0, 0])
    assert motion_start(x, y) == 3


def test_motion_start_is_none_when_stick_stays_inside():
    x = np.array([0, 3000, 12000, 16000])
    assert motion_start(x, np.zeros_like(x)) is None


def test_pause_at_rim_is_left_out_and_counted():
    t, x, y = spin(("spin", 6), ("pause", 2), ("spin", 10))
    stats = spin_timing(t, x, y, NO_GAPS, WINDOW_NS)
    assert abs(stats.rate_hz - 125.0) < 0.5
    assert stats.still_count == 1
    assert stats.gaps_over_30ms == 0
    assert abs(stats.moving_s - 13.0) < 0.1


def test_dropout_while_moving_counts_as_a_gap():
    t, x, y = spin(("spin", 5), ("drop", 50), ("spin", 11))
    stats = spin_timing(t, x, y, NO_GAPS, WINDOW_NS)
    assert stats.gaps_over_30ms == 1
    assert stats.still_count == 0


def test_return_to_centre_is_left_out():
    t, x, y = spin(("spin", 5), ("centre", 1), ("spin", 11))
    stats = spin_timing(t, x, y, NO_GAPS, WINDOW_NS)
    assert stats.gaps_over_30ms == 0
    assert stats.still_count == 1
