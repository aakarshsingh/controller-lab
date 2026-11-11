from dataclasses import dataclass
from typing import Any

import pytest

from controller_lab.scoring import (
    BLUETOOTH_NOT_TESTED,
    NOT_CALIBRATED,
    bluetooth_reference,
    linear,
    load_profile,
    score_run,
)

SERIES = load_profile("xbox_series")


@dataclass
class Run:
    label: str
    metrics: dict[str, Any]


def timing(mean, p99=None, rate=None, max_gap=None):
    return {
        "status": "done",
        "metrics": {
            "mean_ms": mean,
            "p99_ms": p99 if p99 is not None else mean * 1.3,
            "rate_hz": rate if rate is not None else 1000 / mean,
            "gaps_over_30ms": 0,
            "max_gap_ms": max_gap if max_gap is not None else mean * 1.5,
        },
    }


def run(checks, connection="usb", label="Black", dropped=False):
    return {
        "label": label,
        "type": "Xbox Series",
        "connection": connection,
        "connection_dropped": dropped,
        "rig": {"median_ms": 1.0, "p99_ms": 1.7, "ok": True},
        "checks": checks,
    }


def value_for(name, score):
    """The raw value that maps to `score` for one of the profile's metrics."""
    m = SERIES.metric(name)
    return m.good + (m.bad - m.good) * (100 - score) / 100


def idle(left_offset):
    return {
        "status": "done",
        "metrics": {"left_offset_pct": left_offset, "left_direction": "up-left"},
    }


def test_linear_maps_and_clamps():
    assert linear(5, good=0, bad=10) == 50
    assert linear(-1, good=0, bad=10) == 100
    assert linear(20, good=0, bad=10) == 0
    assert linear(90, good=100, bad=60) == 75  # higher is better


def test_drift_score_of_30_with_everything_else_at_100_is_faulty():
    result = score_run(run({"idle_drift": idle(value_for("left_idle_offset", 30))}), SERIES)
    assert result.categories["left_stick"].score == pytest.approx(30)
    assert result.verdict == "Faulty"
    assert any("drifts" in issue and "up-left" in issue for issue in result.issues)


def test_category_at_70_is_minor():
    result = score_run(run({"idle_drift": idle(value_for("left_idle_offset", 70))}), SERIES)
    assert result.categories["left_stick"].score == pytest.approx(70)
    assert result.verdict == "Minor issues"


def test_auto_fail_with_a_high_score_is_faulty():
    checks = {"report_timing": timing(8.3, p99=12.2, rate=120, max_gap=150)}
    result = score_run(run(checks), SERIES)
    assert result.categories["connection"].score == pytest.approx(100)
    assert result.verdict == "Faulty"
    assert any("100 ms" in fail for fail in result.auto_fails)


def test_missing_category_is_not_tested_and_left_out_of_the_overall():
    result = score_run(run({"idle_drift": idle(value_for("left_idle_offset", 50))}), SERIES)
    assert result.categories["triggers"].score is None
    assert result.categories["triggers"].note == "not tested"
    assert result.overall == pytest.approx(50)


def test_bluetooth_reference_uses_each_other_labels_latest_bluetooth_run():
    runs = [  # newest first, as history.load_runs() gives them
        Run("Black", run({"report_timing": timing(40)}, "bluetooth", "Black")),
        Run("White", run({"report_timing": timing(18)}, "bluetooth", "White")),
        Run("White", run({"report_timing": timing(8)}, "usb", "White")),
        Run("Red", run({"report_timing": timing(19)}, "bluetooth", "Red")),
        Run("White", run({"report_timing": timing(25)}, "bluetooth", "White")),
    ]
    ref = bluetooth_reference(runs, exclude_label="Black")
    assert ref is not None
    assert ref.mean_ms == pytest.approx(18.5)
    assert ref.labels == ("Red", "White")
    assert bluetooth_reference(runs[:1], exclude_label="Black") is None


def test_no_other_bluetooth_runs_leaves_timing_not_tested_and_a_drop_still_fails():
    checks = {"report_timing": timing(30)}
    result = score_run(run(checks, "bluetooth", dropped=True), SERIES, runs=[])
    assert result.categories["connection"].score is None
    assert result.categories["connection"].note == BLUETOOTH_NOT_TESTED
    assert result.verdict == "Faulty"
    assert any("disconnected" in fail for fail in result.auto_fails)


def bluetooth_against_18_5(mean):
    runs = [
        Run("White", run({"report_timing": timing(18, 24, 55)}, "bluetooth", "White")),
        Run("Red", run({"report_timing": timing(19, 24, 53)}, "bluetooth", "Red")),
    ]
    checks = {"report_timing": timing(mean, 24, 1000 / mean)}
    return score_run(run(checks, "bluetooth"), SERIES, runs=runs)


def test_bluetooth_within_the_pc_reference_is_not_flagged():
    result = bluetooth_against_18_5(19)
    assert not any("Bluetooth" in issue for issue in result.issues)
    assert result.categories["connection"].score > 80


def test_bluetooth_well_over_the_pc_reference_is_flagged_naming_the_others():
    result = bluetooth_against_18_5(30)
    flagged = [issue for issue in result.issues if "Bluetooth" in issue]
    assert flagged
    assert "Red" in flagged[0] and "White" in flagged[0]


def test_usb_values_are_never_used_for_a_bluetooth_run():
    # 18.5 ms would score far below 100 against the USB reference of 8.3 ms.
    result = bluetooth_against_18_5(18.5)
    assert result.categories["connection"].score == pytest.approx(100, abs=1)
    no_ref = score_run(run({"report_timing": timing(18.5)}, "bluetooth"), SERIES, runs=[])
    assert no_ref.categories["connection"].score is None


def test_generic_profile_adds_the_not_calibrated_note():
    generic = load_profile("generic")
    result = score_run(run({"idle_drift": idle(1.0)}), generic)
    assert NOT_CALIBRATED in result.notes
    assert NOT_CALIBRATED not in score_run(run({"idle_drift": idle(1.0)}), SERIES).notes


def idle_full(left=1.0, right=1.0, stray=0):
    return {
        "status": "done",
        "metrics": {
            "left_offset_pct": left,
            "left_direction": "up",
            "left_noise_pct": 0.0,
            "right_offset_pct": right,
            "right_direction": "left",
            "right_noise_pct": 0.0,
            "stray_presses": stray,
        },
    }


def test_issue_counts_use_the_singular_for_one():
    one = score_run(run({"idle_drift": idle_full(stray=1)}), SERIES)
    two = score_run(run({"idle_drift": idle_full(stray=2)}), SERIES)
    assert "Buttons registered 1 stray press at rest" in one.issues
    assert "Buttons registered 2 stray presses at rest" in two.issues


def test_a_flick_snapback_gets_the_flick_wording_not_the_stick_one():
    flicks = {"status": "done", "metrics": {"wrong_direction": 0, "snapbacks": 1}}
    result = score_run(run({"skill_flicks": flicks}), SERIES)
    flagged = [i for i in result.issues if "snap" in i]
    assert flagged == ["Right stick snapped back past centre after 1 flick"]


def test_inferences_cover_each_tested_category_and_no_untested_one():
    result = score_run(run({"idle_drift": idle_full()}), SERIES)
    assert set(result.inferences) == {"left_stick", "right_stick", "buttons"}
    assert all(1 <= len(lines) <= 2 for lines in result.inferences.values())


def test_a_rest_offset_inside_the_deadzone_says_games_wont_see_it():
    result = score_run(run({"idle_drift": idle_full(right=4.0)}), SERIES)
    first = result.inferences["right_stick"][0]
    assert "4.0% left" in first
    assert "26.5%" in first
    assert "games won't see it" in first


def taps(**counts):
    full = dict.fromkeys(("A", "B", "X", "Y", "LB", "RB", "Back", "Start", "LS", "RS"), 20)
    metrics = {f"{k}_count": v for k, v in (full | counts).items()} | {"chatter_total": 0}
    return {"status": "done", "metrics": metrics}


def test_a_button_that_never_registers_a_tap_is_faulty_and_named():
    result = score_run(run({"button_taps": taps(X=0, Back=0)}), SERIES)
    assert result.verdict == "Faulty"
    assert "X never registered a press when tapped" in result.auto_fails
    assert "View never registered a press when tapped" in result.auto_fails
    assert "A button registered none of its taps" in result.issues
    assert score_run(run({"button_taps": taps()}), SERIES).verdict == "Healthy"


def test_a_skipped_tap_check_with_no_presses_is_not_scored():
    skipped = taps(
        **dict.fromkeys(("A", "B", "X", "Y", "LB", "RB", "Back", "Start", "LS", "RS"), 0)
    )
    skipped["status"] = "skipped"
    result = score_run(run({"button_taps": skipped}), SERIES)
    assert result.auto_fails == []
    assert result.verdict == "Not tested"


def test_a_hold_that_never_registered_is_an_issue_naming_it():
    holds = {
        "status": "done",
        "metrics": {"dropouts_total": 0, "not_held": 1, "not_held_labels": ["RB + X"]},
    }
    result = score_run(run({"holds": holds}), SERIES)
    assert "Never registered as fully held: RB + X" in result.issues
    assert result.auto_fails == []
    assert result.categories["buttons"].score == pytest.approx(75)


def test_a_rest_over_the_limit_but_inside_the_deadzone_agrees_with_the_verdict():
    result = score_run(run({"idle_drift": idle_full(left=9.5)}), SERIES)
    first = result.inferences["left_stick"][0]
    assert result.verdict == "Faulty"
    assert "over the 8% limit that marks a fault" in first
    assert "retest to confirm" in first
    assert "may grow into drift" not in first


def test_an_input_reported_as_not_responding_is_faulty_whatever_the_check_became():
    for status in ("unresponsive", "skipped", "done"):
        entry = {
            "title": "Skill flicks",
            "status": status,
            "metrics": {"unresponsive": ["the right stick"]},
        }
        result = score_run(run({"skill_flicks": entry}), SERIES)
        assert result.verdict == "Faulty"
        assert result.auto_fails == ["The right stick didn't respond, as reported in Skill flicks"]
