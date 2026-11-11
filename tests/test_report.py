from pathlib import Path

from controller_lab import report, scoring

SERIES = scoring.load_profile("xbox_series")


def a_run(label, stamp, finished):
    idle = {"status": "done", "metrics": {"left_offset_pct": 1.0, "right_offset_pct": 1.0}}
    metrics = {
        "label": label,
        "type": "Xbox Series",
        "connection": "usb",
        "suite": "full",
        "checks": {"idle_drift": idle},
    }
    if finished:
        metrics["score"] = scoring.score_run(metrics, SERIES).to_dict()
    return report.Run(label, Path(f"{label}/{stamp}_usb"), metrics)


def test_compare_counts_only_runs_that_reached_results():
    runs = [
        a_run("White", "20261101-120000", finished=False),
        a_run("Black", "20261101-110000", finished=False),
        a_run("Black", "20261101-100000", finished=True),
    ]
    groups = report._groups(runs)
    assert [(g.label, len(g.runs)) for g in groups] == [("Black", 1)]
    page = report.build_compare(runs)
    assert "2 stopped before Results" in page
    assert page.count(report.NOT_COUNTED) == 2


def test_compare_with_no_finished_run_says_so():
    page = report.build_compare([a_run("Black", "20261101-110000", finished=False)])
    assert "No run has finished yet" in page
