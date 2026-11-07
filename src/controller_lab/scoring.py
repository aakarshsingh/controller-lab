"""Scores a run from its metrics and a profile. Pure: no pygame imports.

Each metric maps linearly from good (100) to bad (0) and is clamped. A category's
score is the mean of its metrics, and a category with no data is "not tested" and
left out of the overall. One bad category or one auto-fail makes the run Faulty,
whatever the average says.
"""

from __future__ import annotations

import math
import statistics
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

PROFILE_DIR = Path(__file__).parent / "profiles"
CATEGORIES = {
    "left_stick": "Left stick",
    "right_stick": "Right stick",
    "triggers": "Triggers",
    "buttons": "Buttons",
    "dpad": "D-pad",
    "connection": "Connection",
}
FAULTY_BELOW = 60
MINOR_BELOW = 80
# A Bluetooth value within 10% of the PC's reference is not flagged as a cause.
BLUETOOTH_MARGIN = 1.1
TAPS = 20

BLUETOOTH_NOT_TESTED = "not tested: needs another controller tested over Bluetooth on this PC"
NOT_CALIBRATED = "This controller type's reference isn't calibrated, so treat scores as rough."
HONEST_LIMITS = (
    "Timing is when Windows receives the input, not true end-to-end latency.",
    "A constant delay added by the connection can't be detected by software.",
    "Checks that depend on the person, like tracking, are left out of this score.",
)


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class MetricDef:
    name: str
    source: str  # "<check id>.<metrics key>", or "derived.<key>"
    category: str
    good: float
    bad: float
    connection: str | None = None


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    version: int
    calibrated: bool
    weights: dict[str, float]
    loop_median_ms: float
    loop_p99_ms: float
    auto_fail: dict[str, Any]
    usb_reference: dict[str, float]
    bluetooth_context: dict[str, float]
    bluetooth_bad_ratio: float
    deadzones: dict[str, int]
    metrics: tuple[MetricDef, ...]

    def metric(self, name: str) -> MetricDef:
        for m in self.metrics:
            if m.name == name:
                return m
        raise KeyError(name)


def _need(table: dict[str, Any], key: str, where: str) -> Any:
    if key not in table:
        raise ProfileError(f"{where}: missing '{key}'")
    return table[key]


def load_profile(key: str) -> Profile:
    """Load `profiles/<key>.toml`, checking every key the scoring needs."""
    path = PROFILE_DIR / f"{key}.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProfileError(f"{path.name}: {exc}") from exc
    where = path.name
    weights = _need(data, "weights", where)
    for cat in CATEGORIES:
        _need(weights, cat, f"{where} [weights]")
    loop = _need(data, "loop", where)
    reference = _need(data, "reference", where)
    usb = _need(reference, "usb", f"{where} [reference]")
    for k in ("mean_ms", "max_ms", "rate_hz"):
        _need(usb, k, f"{where} [reference.usb]")
    auto = _need(data, "auto_fail", where)
    for k in ("idle_offset_pct", "settle_offset_pct", "chatter", "hold_dropouts"):
        _need(auto, k, f"{where} [auto_fail]")
    _need(auto, "usb_max_gap_ms", f"{where} [auto_fail]")
    metrics = []
    for i, m in enumerate(_need(data, "metric", where)):
        mw = f"{where} metric {i + 1}"
        md = MetricDef(
            name=_need(m, "name", mw),
            source=_need(m, "source", mw),
            category=_need(m, "category", mw),
            good=float(_need(m, "good", mw)),
            bad=float(_need(m, "bad", mw)),
            connection=m.get("connection"),
        )
        if md.category not in CATEGORIES:
            raise ProfileError(f"{mw}: unknown category '{md.category}'")
        if md.good == md.bad:
            raise ProfileError(f"{mw}: good and bad are the same")
        metrics.append(md)
    return Profile(
        key=key,
        name=str(_need(data, "name", where)),
        version=int(_need(data, "version", where)),
        calibrated=bool(_need(data, "calibrated", where)),
        weights={k: float(v) for k, v in weights.items()},
        loop_median_ms=float(_need(loop, "median_ms", f"{where} [loop]")),
        loop_p99_ms=float(_need(loop, "p99_ms", f"{where} [loop]")),
        auto_fail=dict(auto),
        usb_reference={k: float(v) for k, v in usb.items()},
        bluetooth_context={k: float(v) for k, v in reference.get("bluetooth", {}).items()},
        bluetooth_bad_ratio=float(_need(data, "bluetooth_bad_ratio", where)),
        deadzones=dict(_need(data, "deadzones", where)),
        metrics=tuple(metrics),
    )


def profile_for(controller_type: str) -> str:
    """The profile key for a controller type. Unknown types use the generic profile."""
    return "xbox_series" if controller_type == "Xbox Series" else "generic"


def linear(value: float, good: float, bad: float) -> float:
    """100 at `good`, 0 at `bad`, straight between, clamped. Works either way round."""
    return max(0.0, min(100.0, 100.0 * (bad - value) / (bad - good)))


# ---- Bluetooth reference ----------------------------------------------------


class RunLike(Protocol):
    label: str
    metrics: dict[str, Any]


@dataclass(frozen=True)
class BluetoothReference:
    mean_ms: float
    p99_ms: float
    rate_hz: float
    labels: tuple[str, ...]


def _timing(metrics: dict[str, Any]) -> dict[str, Any] | None:
    rt = metrics.get("checks", {}).get("report_timing")
    if not isinstance(rt, dict) or rt.get("status") != "done":
        return None
    return rt.get("metrics") or None


def bluetooth_reference(runs: Iterable[RunLike], exclude_label: str) -> BluetoothReference | None:
    """The median Bluetooth timing of the other controllers tested on this PC.

    Uses each other label's newest Bluetooth run with a finished report timing check.
    `runs` must be newest first, as `history.load_runs()` gives them.
    """
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        label = run.label
        if label.lower() == exclude_label.lower() or label in latest:
            continue
        if run.metrics.get("connection") != "bluetooth":
            continue
        timing = _timing(run.metrics)
        if timing and all(_finite(timing.get(k)) for k in ("mean_ms", "p99_ms", "rate_hz")):
            latest[label] = timing
    if not latest:
        return None
    values = latest.values()
    return BluetoothReference(
        mean_ms=statistics.median(t["mean_ms"] for t in values),
        p99_ms=statistics.median(t["p99_ms"] for t in values),
        rate_hz=statistics.median(t["rate_hz"] for t in values),
        labels=tuple(sorted(latest)),
    )


# ---- scoring ------------------------------------------------------------------


@dataclass
class CategoryScore:
    title: str
    score: float | None = None
    note: str = ""
    parts: dict[str, float] = field(default_factory=dict)


@dataclass
class RunScore:
    categories: dict[str, CategoryScore]
    overall: float | None
    verdict: str
    auto_fails: list[str]
    issues: list[str]
    notes: list[str]
    summary: str
    profile: str
    profile_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def _values(checks: dict[str, Any]) -> dict[str, Any]:
    """Every metric of every finished check as "<check>.<key>", plus derived ones."""
    values: dict[str, Any] = {}
    for cid, entry in checks.items():
        if isinstance(entry, dict) and entry.get("status") == "done":
            for key, value in (entry.get("metrics") or {}).items():
                values[f"{cid}.{key}"] = value
    derived: dict[str, float] = {}
    for side in ("left", "right"):
        diffs = [values.get(f"circularity.{side}_{k}_diff_pct") for k in ("lr", "ud")]
        diffs = [abs(d) for d in diffs if _finite(d)]
        if diffs:
            derived[f"{side}_reach_diff_pct"] = max(diffs)
    reaches = [v for k, v in values.items() if k.startswith("sprint_turn.reach_") and _finite(v)]
    if reaches:
        derived["sprint_min_reach_pct"] = min(reaches)
    counts = [v for k, v in values.items() if k.startswith("button_taps.") and k.endswith("_count")]
    tapped = [abs(c - TAPS) for c in counts if _finite(c) and c > 0]
    if tapped:
        derived["button_count_error"] = max(tapped)
    expected = values.get("dpad.diagonals_expected")
    if _finite(expected) and expected:
        derived["dpad_diagonal_ratio"] = min(1.0, values["dpad.diagonals_registered"] / expected)
    jitters = [
        v
        for k, v in values.items()
        if k.startswith("throttle_hold.level_") and k.endswith("_jitter_pct") and _finite(v)
    ]
    if jitters:
        derived["throttle_jitter_pct"] = statistics.mean(jitters)
    values.update({f"derived.{k}": v for k, v in derived.items()})
    return values


def _degree(score: float) -> str:
    return "slightly" if score >= FAULTY_BELOW else "noticeably" if score >= 30 else "badly"


def _issue(m: MetricDef, value: float, score: float, values: dict[str, Any]) -> str:
    """One plain-language line for a metric that scored under 80."""
    side = "Left" if m.name.startswith("left") else "Right"
    if m.name.endswith("_idle_offset"):
        direction = values.get(f"idle_drift.{side.lower()}_direction", "off centre")
        return f"{side} stick drifts {_degree(score)} {direction} ({value:.0f}%)"
    templates = {
        "_idle_noise": f"{side} stick jitters at rest ({value:.2f}% noise)",
        "_settle_mean": f"{side} stick doesn't return to centre cleanly ({value:.1f}% off)",
        "_settle_max": f"{side} stick sometimes stops {value:.1f}% off centre after letting go",
        "_snapback": f"{side} stick snaps back past centre ({value:.0f}%)",
        "_circle_error": f"{side} stick's range isn't round ({value:.1f}% circle error)",
        "_reach_diff": f"{side} stick reaches further one way than the other ({value:.1f}%)",
        "_sweep_centre": f"{side} stick is coarse near centre ({value:.0f} steps)",
        "_sweep_jump": f"{side} stick skips values when moved slowly ({value:.0f}% jump)",
    }
    for suffix, text in templates.items():
        if m.name.endswith(suffix):
            return text
    fixed = {
        "sprint_reach": f"Left stick falls short while RT is held ({value:.0f}% reach)",
        "flick_wrong_direction": f"Right stick flicks registered the wrong way {value:.0f} times",
        "flick_snapback": f"Right stick snapped back the wrong way after {value:.0f} flicks",
        "lt_rest": f"LT doesn't rest at zero ({value:.0f})",
        "rt_rest": f"RT doesn't rest at zero ({value:.0f})",
        "lt_reach": f"LT doesn't reach full travel ({value:.0f}%)",
        "rt_reach": f"RT doesn't reach full travel ({value:.0f}%)",
        "lt_jump": f"LT skips values when squeezed slowly ({value:.0f}% jump)",
        "rt_jump": f"RT skips values when squeezed slowly ({value:.0f}% jump)",
        "rt_sag": f"RT sagged below 95% while held, {value:.0f} times",
        "throttle_jitter": f"RT wobbles when held part way ({value:.2f}% jitter)",
        "stray_presses": f"Buttons registered {value:.0f} stray presses at rest",
        "tap_count_error": f"A button missed or added up to {value:.0f} of {TAPS} taps",
        "tap_chatter": f"Buttons chattered {value:.0f} times, a press registering twice",
        "hold_dropouts": f"Held buttons dropped out {value:.0f} times",
        "dpad_ghosts": f"The D-pad fired a neighbouring direction {value:.0f} times",
        "dpad_diagonals": f"The D-pad missed diagonals ({value:.0%} registered)",
        "usb_mean": f"USB reports arrive slowly ({value:.1f} ms average)",
        "usb_p99": f"USB reports are sometimes late ({value:.1f} ms at the 99th percentile)",
        "usb_rate": f"USB report rate is low ({value:.0f} Hz)",
        "usb_gaps": f"USB reports had {value:.0f} gaps over 30 ms",
    }
    return fixed.get(m.name, f"{m.name.replace('_', ' ')} is outside the healthy range")


def _bluetooth(
    category: CategoryScore,
    timing: dict[str, Any] | None,
    ref: BluetoothReference | None,
    ratio: float,
    issues: list[str],
) -> None:
    """Score Bluetooth timing against the other controllers on this PC."""
    if timing is None:
        return
    if ref is None:
        category.note = BLUETOOTH_NOT_TESTED
        return
    mean, p99, rate = timing["mean_ms"], timing["p99_ms"], timing["rate_hz"]
    category.parts = {
        "bluetooth_mean": linear(mean, ref.mean_ms, ref.mean_ms * ratio),
        "bluetooth_p99": linear(p99, ref.p99_ms, ref.p99_ms * ratio),
        "bluetooth_rate": linear(rate, ref.rate_hz, ref.rate_hz / ratio),
    }
    slower = (
        mean > ref.mean_ms * BLUETOOTH_MARGIN
        or p99 > ref.p99_ms * BLUETOOTH_MARGIN
        or rate < ref.rate_hz / BLUETOOTH_MARGIN
    )
    if slower:
        others = ", ".join(ref.labels)
        issues.append(
            f"Bluetooth timing is slower than the other controllers on this PC ({others}): "
            f"{mean:.1f} ms average against {ref.mean_ms:.1f} ms"
        )


def score_run(
    run: dict[str, Any], profile: Profile, runs: Iterable[RunLike] | None = None
) -> RunScore:
    """Score one run. `runs` are this PC's saved runs, needed for Bluetooth timing."""
    connection = run.get("connection", "usb")
    checks = run.get("checks", {})
    values = _values(checks)
    categories = {key: CategoryScore(title) for key, title in CATEGORIES.items()}
    issues: list[str] = []
    auto_fails: list[str] = []
    notes: list[str] = []

    for m in profile.metrics:
        if m.connection is not None and m.connection != connection:
            continue
        value = values.get(m.source)
        if not _finite(value):
            continue
        score = linear(value, m.good, m.bad)
        categories[m.category].parts[m.name] = score
        if score < MINOR_BELOW:
            issues.append(_issue(m, value, score, values))

    if connection == "bluetooth":
        ref = bluetooth_reference(runs or [], run.get("label", ""))
        _bluetooth(categories["connection"], _timing(run), ref, profile.bluetooth_bad_ratio, issues)
        context = profile.bluetooth_context
        if context:
            notes.append(
                f"Gamepadla's Bluetooth figure is about {context.get('mean_ms', 0):.0f} ms "
                f"average and {context.get('max_ms', 0):.1f} ms max. It's shown as context only."
            )
    rt = checks.get("report_timing", {})
    if rt.get("status") == "not_tested" and not categories["connection"].parts:
        rig = run.get("rig") or {}
        categories["connection"].note = (
            f"not tested: this PC's loop is too slow (median {rig.get('median_ms', 0):.2f} ms, "
            f"p99 {rig.get('p99_ms', 0):.2f} ms)"
        )

    _auto_fails(run, values, profile, connection, auto_fails)

    for cat in categories.values():
        if cat.parts:
            cat.score = statistics.mean(cat.parts.values())
        elif not cat.note:
            cat.note = "not tested"
    tested = {k: c.score for k, c in categories.items() if c.score is not None}
    overall = None
    if tested:
        weight = sum(profile.weights[k] for k in tested)
        overall = sum(profile.weights[k] * s for k, s in tested.items()) / weight

    if not tested and not auto_fails:
        verdict = "Not tested"
    elif auto_fails or any(s < FAULTY_BELOW for s in tested.values()):
        verdict = "Faulty"
    elif any(s < MINOR_BELOW for s in tested.values()):
        verdict = "Minor issues"
    else:
        verdict = "Healthy"

    missing = [c.title for k, c in categories.items() if k not in tested]
    if tested and missing:
        notes.append(
            f"Not tested: {', '.join(missing)}. The verdict covers only what was measured."
        )
    if not profile.calibrated:
        notes.append(NOT_CALIBRATED)
    summary = {
        "Healthy": "No hardware fault found. If play feels off, the player or the game is "
        "the more likely cause.",
        "Minor issues": "Minor issues found. The controller works, with the weaknesses below.",
        "Faulty": "A hardware fault is likely. See the issues below.",
        "Not tested": "Nothing was measured, so there is no verdict.",
    }[verdict]
    return RunScore(
        categories=categories,
        overall=overall,
        verdict=verdict,
        auto_fails=auto_fails,
        issues=issues,
        notes=notes,
        summary=summary,
        profile=profile.key,
        profile_version=profile.version,
    )


def _auto_fails(
    run: dict[str, Any],
    values: dict[str, Any],
    profile: Profile,
    connection: str,
    out: list[str],
) -> None:
    limits = profile.auto_fail
    for side in ("left", "right"):
        offset = values.get(f"idle_drift.{side}_offset_pct")
        if _finite(offset) and offset > limits["idle_offset_pct"]:
            out.append(
                f"{side.capitalize()} stick rests {offset:.0f}% off centre, "
                f"over the {limits['idle_offset_pct']:.0f}% limit"
            )
        settle = values.get(f"centring.{side}_settle_max_pct")
        if _finite(settle) and settle > limits["settle_offset_pct"]:
            out.append(
                f"{side.capitalize()} stick stopped {settle:.0f}% off centre after a push, "
                f"over the {limits['settle_offset_pct']:.0f}% limit"
            )
    chatter = values.get("button_taps.chatter_total")
    if _finite(chatter) and chatter > limits["chatter"]:
        out.append("A button chattered, registering one press twice")
    drops = [values.get("holds.dropouts_total"), values.get("sprint_turn.rt_dropouts")]
    if sum(d for d in drops if _finite(d)) > limits["hold_dropouts"]:
        out.append("An input dropped out while it was held")
    gap = values.get("report_timing.max_gap_ms")
    if connection == "usb" and _finite(gap) and gap > limits["usb_max_gap_ms"]:
        out.append(
            f"A {gap:.0f} ms gap between USB reports, over the "
            f"{limits['usb_max_gap_ms']:.0f} ms limit"
        )
    if (
        connection == "bluetooth"
        and limits.get("bluetooth_drop", True)
        and run.get("connection_dropped")
    ):
        out.append("The controller disconnected over Bluetooth during a check")
