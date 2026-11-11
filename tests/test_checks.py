import numpy as np

from controller_lab.analysis import trigger_stats
from controller_lab.checks import button_taps, holds, idle_drift, triggers
from controller_lab.checks.base import BUTTON_BITS, CheckData, count_presses, phase_gate, uses_b

MS = 1_000_000
S = 1_000_000_000


def data(t_ms, rows, phase_starts_ms, end_ms, counting_starts_ms=()):
    return CheckData(
        t_ns=np.array(t_ms, dtype=np.int64) * MS,
        samples=np.array(rows, dtype=np.int64).reshape(len(rows), 7),
        tool_gaps=np.empty((0, 2), dtype=np.int64),
        phase_starts_ns=tuple(v * MS for v in phase_starts_ms),
        end_ns=end_ms * MS,
        timed_out=False,
        counting_starts_ns=tuple(v * MS for v in counting_starts_ms),
    )


def row(lt=0, mask=0, lx=0):
    return (lx, 0, 0, 0, lt, 0, mask)


def test_phase_rows_start_with_the_state_held_and_stop_at_the_next_phase():
    d = data([0, 100, 250, 400], [row()] * 4, phase_starts_ms=[50, 300], end_ms=500)
    assert d.phase_rows(0) == slice(0, 3)  # held at 50, then 100 and 250
    assert d.phase_rows(1) == slice(2, 4)  # held at 300, then 400
    assert d.phase_rows(0, extra_ns=150 * MS) == slice(0, 4)


def test_phase_rows_leave_out_time_spent_waiting_at_a_gate():
    d = data(
        [0, 100, 250, 400],
        [row()] * 4,
        phase_starts_ms=[50, 300],
        end_ms=500,
        counting_starts_ms=[200, 300],
    )
    assert d.phase_rows(0) == slice(1, 3)  # the gate wait from 50 to 200 belongs to no phase


def test_b_is_input_only_in_the_phases_that_test_b():
    tap = {p.focus: p for p in button_taps.CHECK.phases}
    assert uses_b(tap["B"])
    assert not uses_b(tap["A"])
    assert [uses_b(p) for p in holds.PHASES].count(True) == 1
    assert uses_b(holds.PHASES[[h.key for h in holds.HOLDS].index("b")])
    # A tap phase after the first has no gate, so B there never doubles as a start.
    assert phase_gate(button_taps.CHECK, 1) is None


def test_a_tap_phase_waits_for_the_first_press_before_its_idle_clock_runs():
    end = count_presses(BUTTON_BITS["B"], target=20, idle_s=3)(0)
    up, down = row(), row(mask=BUTTON_BITS["B"])
    assert not end(0, up)
    assert not end(30 * S, up)  # reading the prompt for 30 s doesn't end it
    assert not end(31 * S, down)
    assert not end(31 * S + 50 * MS, up)
    assert end(35 * S, up)  # 3 s after the last press


def test_trigger_rest_ignores_a_reading_caught_on_the_way_back():
    rest, reach, _, _ = trigger_stats(np.array([771, 0, 0, 9000, 32767, 0]))
    assert rest == 0
    assert reach == 100
    assert trigger_stats(np.array([771, 771, 9000, 32767, 771]))[0] == 771  # a real rest value
    assert trigger_stats(np.array([20000, 32767, 0]))[0] == 20000  # never came to rest


def test_triggers_check_reports_the_rest_from_before_the_first_squeeze():
    lt = [row(lt=771), row(), row(lt=20000), row(lt=32767), row()]
    d = data([0, 8, 500, 600, 900], lt, phase_starts_ms=[0, 1000], end_ms=1100)
    assert triggers.analyse(d).metrics["lt_rest"] == 0


def test_idle_drift_weights_the_first_reading_from_when_the_rest_began():
    # The stick sat at 3000 from before the rest phase, then moved to 0 half way through.
    rows = [row(lx=3000), row(lx=0)]
    d = data([0, 18_000], rows, phase_starts_ms=[0, 3000], end_ms=33_000)
    left = idle_drift.analyse(d).metrics["left_offset_pct"]
    assert left == round(1500 / 32767 * 100, 3)
