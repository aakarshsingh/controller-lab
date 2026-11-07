import numpy as np

from controller_lab.analysis import (
    button_presses,
    circle_stats,
    dpad_ghosts,
    dropouts,
    flick_result,
    intervals,
    jitter,
    motion_start,
    noise_std,
    press_train,
    segments,
    snapback,
    spin_timing,
    stick_offset,
    timing_stats,
)

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


# ---- idle drift ----------------------------------------------------------


def test_stick_resting_up_left_reads_its_offset_and_direction():
    rng = np.random.default_rng(1)
    c = 0.05 * 32767 / np.sqrt(2)  # 5% of the radius, up and to the left
    x = -c + rng.normal(0, 30, 500)
    y = -c + rng.normal(0, 30, 500)  # SDL's y grows downwards
    pct, direction = stick_offset(x, y)
    assert abs(pct - 5.0) < 0.2
    assert direction == "up-left"


def test_stick_offset_is_weighted_by_time_held():
    x = np.array([0.0, 3276.7])  # 10% right, held 9 times longer
    pct, direction = stick_offset(x, np.zeros(2), weights=np.array([1.0, 9.0]))
    assert abs(pct - 9.0) < 1e-9
    assert direction == "right"


def test_noise_std_of_a_known_array():
    x = np.array([-327.67, 327.67] * 50)  # ±1% around the centre
    assert abs(noise_std(x, np.zeros_like(x)) - 1.0) < 1e-9


def test_button_presses_count_rising_edges():
    masks = np.array([0, 0b1, 0b1, 0, 0b11, 0b10])
    assert button_presses(masks) == 3


# ---- sticks, buttons and D-pad --------------------------------------------

FULL = 32767


def ring(radius_at, n=720):
    """Points around the rim. `radius_at(deg)` gives the radius as a fraction of full."""
    deg = np.arange(n) * 360 / n
    r = np.array([radius_at(d) for d in deg]) * FULL
    rad = np.radians(deg)
    return r * np.cos(rad), -r * np.sin(rad)  # SDL's y grows downwards


def test_perfect_circle_has_about_zero_circle_error():
    x, y = ring(lambda d: 1.0)
    stats = circle_stats(x, y)
    assert stats.error_pct < 0.1
    assert stats.bins_covered == 72


def test_flattened_quadrant_is_named_as_the_weakest_direction():
    x, y = ring(lambda d: 0.8 if 90 <= d < 180 else 1.0)
    stats = circle_stats(x, y)
    assert stats.weakest == "up-left"
    assert stats.error_pct > 4.0


def test_snapback_overshoot_of_30_percent_is_detected():
    t = np.array([0, 100, 200, 208, 216, 224, 260, 400, 700]) * MS
    x = np.array([0, FULL, FULL, 0, -0.3 * FULL, -0.1 * FULL, 500, 500, 500])
    settle_pct, spike_pct = snapback(t, x, np.zeros_like(x))
    assert abs(spike_pct - 30.0) < 0.5
    assert abs(settle_pct - 500 / FULL * 100) < 0.01


def press_times(bounce_at=None):
    """20 taps: 80 ms down, 120 ms up. Optionally split one press with a 6 ms bounce."""
    t, pressed, now = [0], [False], 0
    for i in range(20):
        now += 120
        t.append(now)
        pressed.append(True)
        if i == bounce_at:
            t += [now + 40, now + 46]
            pressed += [False, True]
        now += 80
        t.append(now)
        pressed.append(False)
    return np.array(t) * MS, np.array(pressed)


def test_press_train_with_a_6_ms_bounce_is_flagged_as_chatter():
    count, chatter = press_train(*press_times(bounce_at=7))
    assert chatter == 1
    assert count == 21


def test_clean_train_of_20_is_not_flagged():
    assert press_train(*press_times()) == (20, 0)


def test_dpad_ghost_is_detected():
    up, left = 1 << 10, 1 << 12
    masks = np.array([0, up, 0, up, up | left, up, 0])
    assert dpad_ghosts(masks, neighbours=left | (1 << 13)) == 1
    assert dpad_ghosts(np.array([0, up, 0, up, 0]), neighbours=left) == 0


# ---- holds, flicks, throttle and tracking ---------------------------------


def test_hold_with_one_20_ms_dropout_is_detected():
    t = np.array([0, 100, 2100, 2120, 5000]) * MS
    held = np.array([False, True, False, True, True])
    assert dropouts(t, held) == 1
    assert dropouts(np.array([0, 100, 5000]) * MS, np.array([False, True, True])) == 0


def flick(overshoot):
    """A right flick, released, then swinging `overshoot` past centre the other way."""
    t = np.array([0, 50, 100, 140, 150, 160, 200, 400]) * MS
    x = np.array([0, 20000, FULL, 12000, 0, -overshoot, -2000, 0])
    return t, x, np.zeros_like(x)


def test_flick_with_opposite_overshoot_past_the_deadzone_is_flagged():
    direction, snapback_flag = flick_result(*flick(10000))
    assert direction == "right"
    assert snapback_flag


def test_flick_that_stays_inside_the_deadzone_is_not_flagged():
    direction, snapback_flag = flick_result(*flick(8000))
    assert direction == "right"
    assert not snapback_flag


def test_segments_split_a_40_s_series_into_8_parts():
    t = np.arange(0, 40_000, 10) * MS
    parts = segments(t, 5_000 * MS)
    assert len(parts) == 8
    assert sum(p.stop - p.start for p in parts) == t.size


def test_jitter_of_a_known_noisy_constant_matches_its_std():
    rng = np.random.default_rng(3)
    t = np.arange(0, 3_000, 8) * MS
    noise = rng.normal(0, 200, t.size)
    v = 16000 + noise
    expected = noise.std() / FULL * 100
    assert abs(jitter(t, v) - expected) / expected < 0.15
