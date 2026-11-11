"""Writes report.html for one run and compare.html for every run. No pygame imports.

Both pages are single files: plots are inline base64 PNGs and the styles are inline,
so a page can be copied anywhere on its own. Neither page shows a device key or GUID.
"""

from __future__ import annotations

import base64
import html
import io
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from controller_lab import analysis, scoring
from controller_lab.checks.report_timing import WINDOW_S

REPORT_FILE = "report.html"
COMPARE_FILE = "compare.html"
METRICS_FILE = "metrics.json"
HIDDEN_FIELDS = ("device_key", "guid")
MARGIN_POINTS = 5.0  # overall scores this close count as the same
LIST_PREVIEW = 8
NOT_COUNTED = "incomplete, not counted"
COLOURS = ("#2b7de1", "#e24036", "#60b946", "#f6c520", "#9b59b6", "#e67e22", "#16a085")
ROTATE_TIP = (
    "Tip: test the controllers in a rotating order, such as Black, White, Red and then "
    "White, Red, Black, so warming up or getting tired doesn't favour one of them."
)
CSS = """
body { font-family: Segoe UI, Arial, sans-serif; margin: 2em auto; max-width: 960px;
       color: #1c1e22; }
h1 { margin-bottom: 0.2em; } h2 { margin-top: 1.6em; border-bottom: 1px solid #ddd; }
table { border-collapse: collapse; margin: 0.6em 0; }
td, th { border: 1px solid #ddd; padding: 3px 8px; text-align: left; vertical-align: top; }
th { background: #f3f4f6; }
.healthy { color: #2e8b3a; } .minor { color: #b8860b; } .faulty { color: #c0392b; }
.muted { color: #777; } .limits li { color: #555; }
img { max-width: 100%; }
"""
VERDICT_CLASS = {"Healthy": "healthy", "Minor issues": "minor", "Faulty": "faulty"}


# ---- helpers ----------------------------------------------------------------


def _e(value: Any) -> str:
    return html.escape(str(value))


def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n<html lang='en'><head><meta charset='utf-8'>"
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body>{body}</body></html>\n"
    )


def _png(fig: Any, alt: str) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    data = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"<img alt='{_e(alt)}' src='data:image/png;base64,{data}'>"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _samples(run_dir: Path, check_id: str) -> tuple[np.ndarray, np.ndarray] | None:
    """A check's samples CSV as (t_ns, samples), or None when it's missing or empty."""
    path = run_dir / "samples" / f"{check_id}.csv"
    try:
        table = np.loadtxt(path, delimiter=",", skiprows=1, dtype=np.int64, ndmin=2)
    except (OSError, ValueError):
        return None
    if table.size == 0 or table.shape[1] < 8:
        return None
    return table[:, 0], table[:, 1:]


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}" if abs(value) < 1000 else f"{value:.0f}"
    if isinstance(value, list):
        shown = ", ".join(_fmt(v) for v in value[:LIST_PREVIEW])
        more = f" … {len(value) - LIST_PREVIEW} more" if len(value) > LIST_PREVIEW else ""
        return f"[{shown}{more}]"
    if isinstance(value, dict):
        return json.dumps(value, separators=(", ", ": "))
    return "n/a" if value is None else str(value)


def _score_text(score: float | None) -> str:
    return "not tested" if score is None else f"{score:.0f}"


def _limits() -> str:
    items = "".join(f"<li>{_e(line)}</li>" for line in scoring.HONEST_LIMITS)
    return f"<h2>What this can't measure</h2><ul class='limits'>{items}</ul>"


def _table(head: list[str], rows: list[list[str]]) -> str:
    """A table from already-escaped cells."""
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><tr>{th}</tr>{body}</table>"


# ---- report.html --------------------------------------------------------------


def _stick_plot(run_dir: Path) -> str:
    data = _samples(run_dir, "circularity")
    if data is None:
        return "<p class='muted'>No circularity samples in this run.</p>"
    _, s = data
    fig, axes = plt.subplots(1, 2, figsize=(8, 4))
    ring = np.linspace(0, 2 * np.pi, 361)
    for ax, (name, xi, yi) in zip(axes, (("Left", 0, 1), ("Right", 2, 3)), strict=True):
        ax.plot(np.cos(ring), np.sin(ring), color="#bbb", lw=1)
        ax.scatter(s[:, xi] / analysis.AXIS_MAX, -s[:, yi] / analysis.AXIS_MAX, s=2)
        ax.set_title(f"{name} stick, circularity")
        ax.set_aspect("equal")
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-1.15, 1.15)
    return _png(fig, "Stick positions during circularity")


def _trigger_plot(run_dir: Path) -> str:
    data = _samples(run_dir, "triggers")
    if data is None:
        return "<p class='muted'>No trigger samples in this run.</p>"
    t, s = data
    secs = (t - t[0]) / 1e9
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.step(secs, s[:, 4] / analysis.AXIS_MAX * 100, where="post", label="LT")
    ax.step(secs, s[:, 5] / analysis.AXIS_MAX * 100, where="post", label="RT")
    ax.set_xlabel("seconds")
    ax.set_ylabel("% of full travel")
    ax.set_title("Triggers")
    ax.legend()
    return _png(fig, "Trigger travel over time")


def _interval_plot(run_dir: Path) -> str:
    data = _samples(run_dir, "report_timing")
    if data is None:
        return "<p class='muted'>No report timing samples in this run.</p>"
    t, s = data
    start = analysis.motion_start(s[:, 0], s[:, 1])
    if start is None:
        return "<p class='muted'>The stick didn't move during report timing.</p>"
    end = int(np.searchsorted(t, t[start] + WINDOW_S * 10**9, side="right"))
    tt, x, y = t[start:end], s[start:end, 0], s[start:end, 1]
    still = analysis.still_mask(tt, x, y)
    ms = analysis.intervals(tt, np.empty((0, 2), dtype=np.int64), exclude=still) / 1e6
    if ms.size == 0:
        return "<p class='muted'>No intervals to plot.</p>"
    fig, ax = plt.subplots(figsize=(8, 3))
    ax.hist(ms, bins=np.arange(0, max(40.0, float(ms.max()) + 1), 0.5))
    ax.set_xlabel("interval between reports (ms)")
    ax.set_ylabel("count")
    ax.set_title("Report intervals while the stick moved")
    note = (
        "<p class='muted'>Loop slowdowns on the PC aren't in the samples file, so this "
        "plot can include a few intervals that the timing metrics leave out.</p>"
    )
    return _png(fig, "Histogram of report intervals") + note


def _score_section(score: dict[str, Any]) -> str:
    rows = [
        [_e(c["title"]), _e(_score_text(c["score"])), _e(c["note"] if c["score"] is None else "")]
        for c in score.get("categories", {}).values()
    ]
    parts = [_table(["Category", "Score", "Note"], rows)]
    fails = score.get("auto_fails", [])
    issues = score.get("issues", [])
    if fails or issues:
        items = "".join(f"<li class='faulty'>{_e(f)}</li>" for f in fails)
        items += "".join(f"<li>{_e(i)}</li>" for i in issues)
        parts.append(f"<h2>Issues</h2><ul>{items}</ul>")
    else:
        parts.append("<h2>Issues</h2><p class='healthy'>No issues found.</p>")
    meaning = score.get("inferences") or {}
    titles = {k: (c or {}).get("title", k) for k, c in (score.get("categories") or {}).items()}
    if meaning:
        rows = "".join(
            f"<li><b>{_e(titles.get(k, k))}</b>: {' '.join(_e(line) for line in lines)}</li>"
            for k, lines in meaning.items()
        )
        parts.append(f"<h2>What this means</h2><ul>{rows}</ul>")
    notes = score.get("notes", [])
    if notes:
        parts.append("<ul class='muted'>" + "".join(f"<li>{_e(n)}</li>" for n in notes) + "</ul>")
    return "".join(parts)


def _metrics_section(checks: dict[str, Any]) -> str:
    out = ["<h2>Raw metrics</h2>"]
    for cid, entry in checks.items():
        status = entry.get("status", "")
        message = entry.get("message", "")
        title = entry.get("title", cid)
        out.append(f"<h3>{_e(title)} <span class='muted'>({_e(status)})</span></h3>")
        if message:
            out.append(f"<p class='muted'>{_e(message)}</p>")
        metrics = entry.get("metrics") or {}
        if metrics:
            rows = [[_e(k), _e(_fmt(v))] for k, v in metrics.items()]
            out.append(_table(["Metric", "Value"], rows))
    return "".join(out)


def build_report(run_dir: Path, metrics: dict[str, Any]) -> str:
    score = metrics.get("score") or {}
    label = metrics.get("label", run_dir.parent.name)
    verdict = score.get("verdict", "Not scored")
    overall = score.get("overall")
    cls = VERDICT_CLASS.get(verdict, "muted")
    head = f"<h1>{_e(label)}: <span class='{cls}'>{_e(verdict)}</span>"
    if overall is not None:
        head += f" <span class='{cls}'>· overall {overall:.0f}</span>"
    checks = metrics.get("checks") or {}
    not_done = sum(1 for e in checks.values() if (e or {}).get("status") != "done")
    if not_done:
        head += (
            f" <span class='muted'>· {not_done} check{'s' if not_done != 1 else ''} not done</span>"
        )
    head += "</h1>"
    meta_fields = (
        ("Started", "started_at"),
        ("Controller", "name"),
        ("Type", "type"),
        ("Connection", "connection"),
        ("Battery", "battery"),
        ("Input backend", "backend"),
        ("Suite", "suite"),
        ("Tool version", "tool_version"),
        ("Profile", "profile"),
        ("Profile version", "profile_version"),
    )
    meta = [[_e(name), _e(metrics.get(key, "n/a"))] for name, key in meta_fields]
    rig = metrics.get("rig") or {}
    if rig:
        meta.append(
            [
                "Rig check",
                _e(
                    f"median {rig.get('median_ms', 0):.2f} ms, p99 {rig.get('p99_ms', 0):.2f} ms, "
                    f"{'ok' if rig.get('ok') else 'too slow for timing checks'}"
                ),
            ]
        )
    timing = (metrics.get("checks", {}).get("report_timing") or {}).get("metrics") or {}
    if "tool_gaps" in timing:
        meta.append(["Tool gaps left out of timing", _e(timing["tool_gaps"])])
    body = [
        head,
        f"<p>{_e(score.get('summary', ''))}</p>",
        _table(["", ""], meta),
        "<h2>Scores</h2>",
        _score_section(score) if score else "<p class='muted'>This run has no score.</p>",
        _limits(),
        "<h2>Sticks</h2>",
        _stick_plot(run_dir),
        "<h2>Triggers</h2>",
        _trigger_plot(run_dir),
        "<h2>Report timing</h2>",
        _interval_plot(run_dir),
        _metrics_section(metrics.get("checks", {})),
    ]
    return _page(f"Controller Lab: {label}", "".join(body))


def write_report(run_dir: Path) -> Path:
    """Write `report.html` into a run folder and return its path."""
    metrics = _read_json(run_dir / METRICS_FILE)
    if metrics is None:
        raise ValueError(f"{run_dir / METRICS_FILE} is missing or unreadable")
    for key in HIDDEN_FIELDS:
        metrics.pop(key, None)
    path = run_dir / REPORT_FILE
    path.write_text(build_report(run_dir, metrics), encoding="utf-8")
    return path


# ---- compare.html ---------------------------------------------------------------


@dataclass(frozen=True)
class Run:
    label: str
    run_dir: Path
    metrics: dict[str, Any]


def load_runs(root: Path) -> list[Run]:
    """Every readable run under `root`, newest first."""
    runs = []
    try:
        label_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return []
    for label_dir in label_dirs:
        try:
            run_dirs = [p for p in label_dir.iterdir() if p.is_dir()]
        except OSError:
            continue
        for run_dir in run_dirs:
            metrics = _read_json(run_dir / METRICS_FILE)
            if metrics is not None:
                runs.append(Run(label_dir.name, run_dir, metrics))
    runs.sort(key=lambda r: r.run_dir.name, reverse=True)
    return runs


@dataclass
class Group:
    """Every scored run of one label over one connection."""

    label: str
    connection: str
    scores: list[scoring.RunScore]
    runs: list[Run]

    def median_overall(self) -> float | None:
        values = [s.overall for s in self.scores if s.overall is not None]
        return statistics.median(values) if values else None

    def median_category(self, key: str) -> float | None:
        values = [
            s.categories[key].score for s in self.scores if s.categories[key].score is not None
        ]
        return statistics.median(values) if values else None


def _finished(run: Run) -> bool:
    """A run that reached Results has a saved score. One that was abandoned doesn't."""
    return bool(run.metrics.get("score"))


def _groups(runs: list[Run]) -> list[Group]:
    """Group the finished runs. An abandoned run measured too little to rank."""
    groups: dict[tuple[str, str], Group] = {}
    for run in runs:
        if not _finished(run):
            continue
        ctype = run.metrics.get("type", "")
        profile = scoring.load_profile(scoring.profile_for(ctype))
        # Bluetooth is judged again against every other controller's Bluetooth runs.
        score = scoring.score_run(run.metrics, profile, runs)
        key = (run.label, run.metrics.get("connection", "usb"))
        group = groups.setdefault(key, Group(key[0], key[1], [], []))
        group.scores.append(score)
        group.runs.append(run)
    return list(groups.values())


def _ranking(groups: list[Group]) -> str:
    ranked = sorted(groups, key=lambda g: -(g.median_overall() or -1))
    rows = []
    for i, g in enumerate(ranked, start=1):
        latest = g.scores[0].verdict if g.scores else "n/a"
        overall = g.median_overall()
        cls = VERDICT_CLASS.get(latest, "muted")
        rows.append(
            [
                str(i),
                _e(g.label),
                _e(g.connection),
                _e(_score_text(overall)),
                f"<span class='{cls}'>{_e(latest)}</span>",
                str(len(g.runs)),
                _e(", ".join(sorted({str(r.metrics.get("suite", "?")) for r in g.runs}))),
            ]
        )
    head = ["Rank", "Controller", "Connection", "Median overall", "Latest verdict", "Runs", "Suite"]
    return "<h2>Ranking</h2>" + _table(head, rows)


def _category_tables(groups: list[Group]) -> str:
    out = ["<h2>Categories</h2>"]
    for key, title in scoring.CATEGORIES.items():
        rows = [
            [_e(g.label), _e(g.connection), _e(_score_text(g.median_category(key)))]
            for g in sorted(groups, key=lambda g: (g.label, g.connection))
        ]
        out.append(f"<h3>{_e(title)}</h3>" + _table(["Controller", "Connection", "Median"], rows))
    return "".join(out)


def _usb_vs_bluetooth(groups: list[Group], context: dict[str, float]) -> str:
    by_label: dict[str, dict[str, Group]] = {}
    for g in groups:
        by_label.setdefault(g.label, {})[g.connection] = g
    rows = []
    for label in sorted(by_label):
        pair = by_label[label]
        usb, bt = pair.get("usb"), pair.get("bluetooth")
        backends = {
            conn: {r.metrics.get("backend", "?") for r in g.runs} for conn, g in pair.items()
        }
        note = ""
        if usb and bt and backends["usb"] != backends["bluetooth"]:
            note = "different backends"
        rows.append(
            [
                _e(label),
                _e(_score_text(usb.median_overall() if usb else None)),
                _e(_score_text(usb.median_category("connection") if usb else None)),
                _e(_score_text(bt.median_overall() if bt else None)),
                _e(_score_text(bt.median_category("connection") if bt else None)),
                _e(note),
            ]
        )
    head = ["Controller", "USB overall", "USB timing", "Bluetooth overall", "Bluetooth timing", ""]
    ctx = ""
    if context:
        ctx = (
            f"<p class='muted'>Bluetooth timing is judged against the other controllers "
            f"tested over Bluetooth on this PC. Gamepadla's Bluetooth figure, about "
            f"{context.get('mean_ms', 0):.0f} ms average and {context.get('max_ms', 0):.1f} "
            f"ms max, is context only.</p>"
        )
    return "<h2>USB and Bluetooth</h2>" + _table(head, rows) + ctx


def _circles(runs: list[Run]) -> str:
    latest: dict[str, Run] = {}
    for run in runs:
        if run.label not in latest and _samples(run.run_dir, "circularity") is not None:
            latest[run.label] = run
    if not latest:
        return "<h2>Stick circles</h2><p class='muted'>No circularity samples yet.</p>"
    fig, axes = plt.subplots(1, 2, figsize=(9, 4.5))
    ring = np.linspace(0, 2 * np.pi, 361)
    for ax, (name, xi, yi) in zip(axes, (("Left", 0, 1), ("Right", 2, 3)), strict=True):
        ax.plot(np.cos(ring), np.sin(ring), color="#bbb", lw=1)
        for i, (label, run) in enumerate(sorted(latest.items())):
            data = _samples(run.run_dir, "circularity")
            assert data is not None
            _, s = data
            ax.scatter(
                s[:, xi] / analysis.AXIS_MAX,
                -s[:, yi] / analysis.AXIS_MAX,
                s=2,
                color=COLOURS[i % len(COLOURS)],
                label=label,
            )
        ax.set_title(f"{name} stick")
        ax.set_aspect("equal")
        ax.set_xlim(-1.15, 1.15)
        ax.set_ylim(-1.15, 1.15)
    axes[1].legend(markerscale=5)
    return "<h2>Stick circles</h2>" + _png(fig, "Stick circles, one colour per controller")


def _person_checks(runs: list[Run]) -> str:
    """Tracking and throttle hold, which depend on the person, so compare only."""
    sections = []
    specs = (
        ("Tracking", "tracking", ("left_segments_mean_pct", "right_segments_mean_pct"), "miss"),
        ("Throttle hold", "throttle_hold", ("steadiness_segments_pct",), "jitter"),
    )
    for title, cid, keys, word in specs:
        per_label: dict[str, list[list[float]]] = {}
        for run in runs:
            entry = run.metrics.get("checks", {}).get(cid) or {}
            if entry.get("status") != "done":
                continue
            values: list[float] = []
            for k in keys:
                values += [v for v in entry.get("metrics", {}).get(k) or [] if v is not None]
            if values:
                per_label.setdefault(run.label, []).append(values)
        if not per_label:
            sections.append(f"<h3>{_e(title)}</h3><p class='muted'>No runs yet.</p>")
            continue
        medians = {
            label: statistics.median(v for run_values in runs_ for v in run_values)
            for label, runs_ in per_label.items()
        }
        best = min(medians, key=lambda k: medians[k])
        rows = []
        for label in sorted(per_label):
            n = len(per_label[label])
            if label == best:
                mark = "best"
            elif n < 2 or len(per_label[best]) < 2:
                mark = "indicative only, fewer than 2 runs"
            else:
                pooled = [v for r in per_label[label] for v in r]
                pooled_best = [v for r in per_label[best] for v in r]
                _, p = analysis.mann_whitney(np.array(pooled), np.array(pooled_best))
                mark = f"{'significant' if p < 0.05 else 'not significant'} (p = {p:.3f})"
            rows.append([_e(label), str(n), f"{medians[label]:.2f}%", _e(mark)])
        head = ["Controller", "Runs", f"Median {word}", "Against the best"]
        sections.append(f"<h3>{_e(title)}</h3>" + _table(head, rows))
    intro = (
        "<p class='muted'>These depend on the person, so they only count here. A difference "
        "is significant only with at least 2 runs per controller and a Mann-Whitney p "
        "below 0.05.</p>"
    )
    return "<h2>Tracking and throttle hold</h2>" + intro + "".join(sections)


def _summary(groups: list[Group]) -> str:
    overalls = [o for g in groups if (o := g.median_overall()) is not None]
    flagged = []
    for g in sorted(groups, key=lambda g: (g.label, g.connection)):
        latest = g.scores[0]
        for key, title in scoring.CATEGORIES.items():
            median = g.median_category(key)
            if median is not None and median < scoring.MINOR_BELOW:
                flagged.append(f"{g.label} over {g.connection}: {title} scores {median:.0f}")
        flagged += [f"{g.label} over {g.connection}: {fail}" for fail in latest.auto_fails]
    if not flagged and overalls and max(overalls) - min(overalls) <= MARGIN_POINTS:
        return (
            "<p class='healthy'>No hardware fault found. Every category is healthy and the "
            f"controllers score within {MARGIN_POINTS:.0f} points of each other, so a "
            "difference you feel is more likely the player or the game.</p>"
        )
    if not flagged:
        return (
            "<p>No category is below the healthy line, but the overall scores differ by "
            "more than the margin. See the ranking.</p>"
        )
    return "<ul>" + "".join(f"<li class='faulty'>{_e(f)}</li>" for f in flagged) + "</ul>"


def _versions(runs: list[Run], groups: list[Group]) -> str:
    verdicts = {id(r): s.verdict for g in groups for r, s in zip(g.runs, g.scores, strict=True)}
    rows = [
        [
            _e(r.label),
            _e(r.metrics.get("started_at", r.run_dir.name)),
            _e(r.metrics.get("connection", "?")),
            _e(r.metrics.get("suite", "?")),
            _e(r.metrics.get("tool_version", "?")),
            _e(f"{r.metrics.get('profile', '?')} v{r.metrics.get('profile_version', '?')}"),
            _e(r.metrics.get("backend", "?")),
            _e(verdicts.get(id(r), NOT_COUNTED)),
        ]
        for r in runs
    ]
    head = ["Controller", "Started", "Connection", "Suite", "Tool", "Profile", "Backend", "Verdict"]
    return "<h2>Runs</h2>" + _table(head, rows)


def build_compare(runs: list[Run]) -> str:
    if not runs:
        body = (
            "<h1>Compare</h1><p>There is nothing to compare yet. Test a controller "
            "first, and its runs will show up here.</p>"
        )
        return _page("Controller Lab: compare", body)
    groups = _groups(runs)
    if not groups:
        body = (
            "<h1>Compare</h1><p>No run has finished yet, so there is nothing to compare. "
            "A run counts once it reaches Results.</p>" + _versions(runs, groups)
        )
        return _page("Controller Lab: compare", body)
    context = scoring.load_profile("xbox_series").bluetooth_context
    count = f"{len(runs)} runs of {len({r.label for r in runs})} controllers on this PC."
    unfinished = sum(1 for r in runs if not _finished(r))
    if unfinished:
        count += (
            f" {unfinished} stopped before Results, so {'they are' if unfinished != 1 else 'it is'}"
            " listed under Runs but not counted."
        )
    body = [
        "<h1>Compare</h1>",
        f"<p class='muted'>{count}</p>",
        _summary(groups),
        _ranking(groups),
        _category_tables(groups),
        _usb_vs_bluetooth(groups, context),
        _circles(runs),
        _person_checks(runs),
        _versions(runs, groups),
        _limits(),
        f"<p>{_e(ROTATE_TIP)}</p>",
    ]
    return _page("Controller Lab: compare", "".join(body))


def write_compare(root: Path) -> Path:
    """Write `compare.html` at the results root from every run under it."""
    root.mkdir(parents=True, exist_ok=True)
    runs = load_runs(root)
    for run in runs:
        for key in HIDDEN_FIELDS:
            run.metrics.pop(key, None)
    path = root / COMPARE_FILE
    path.write_text(build_compare(runs), encoding="utf-8")
    return path
