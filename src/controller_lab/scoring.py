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
    # One or two plain lines per tested category: what it means in games, and what to do.
    inferences: dict[str, list[str]] = field(default_factory=dict)

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


def _n(value: float, one: str, many: str | None = None) -> str:
    """A count with its noun, singular for exactly 1: "1 flick", "2 flicks"."""
    count = round(value)
    return f"{count} {one if count == 1 else many or one + 's'}"


def _issue(m: MetricDef, value: float, score: float, values: dict[str, Any]) -> str:
    """One plain-language line for a metric that scored under 80, keyed by exact name."""
    side = "Left" if m.name.startswith("left") else "Right"
    if m.name.endswith("_idle_offset"):
        direction = values.get(f"idle_drift.{side.lower()}_direction", "off centre")
        return f"{side} stick drifts {_degree(score)} {direction} ({value:.0f}%)"
    per_side = {
        "idle_noise": f"{side} stick jitters at rest ({value:.2f}% noise)",
        "settle_mean": f"{side} stick doesn't return to centre cleanly ({value:.1f}% off)",
        "settle_max": f"{side} stick sometimes stops {value:.1f}% off centre after letting go",
        "snapback": f"{side} stick snaps back past centre ({value:.0f}%)",
        "circle_error": f"{side} stick's range isn't round ({value:.1f}% circle error)",
        "reach_diff": f"{side} stick reaches further one way than the other ({value:.1f}%)",
        "sweep_centre": f"{side} stick is coarse near centre ({_n(value, 'step')})",
        "sweep_jump": f"{side} stick skips values when moved slowly ({value:.0f}% jump)",
    }
    templates = {f"{s_}_{k}": v for s_ in ("left", "right") for k, v in per_side.items()}
    templates |= {
        "sprint_reach": f"Left stick falls short while RT is held ({value:.0f}% reach)",
        "flick_wrong_direction": f"{_n(value, 'right stick flick')} registered the wrong way",
        "flick_snapback": f"Right stick snapped back past centre after {_n(value, 'flick')}",
        "lt_rest": f"LT doesn't rest at zero ({value:.0f})",
        "rt_rest": f"RT doesn't rest at zero ({value:.0f})",
        "lt_reach": f"LT doesn't reach full travel ({value:.0f}%)",
        "rt_reach": f"RT doesn't reach full travel ({value:.0f}%)",
        "lt_jump": f"LT skips values when squeezed slowly ({value:.0f}% jump)",
        "rt_jump": f"RT skips values when squeezed slowly ({value:.0f}% jump)",
        "rt_sag": f"RT sagged below 95% while held, {_n(value, 'time')}",
        "throttle_jitter": f"RT wobbles when held part way ({value:.2f}% jitter)",
        "stray_presses": f"Buttons registered {_n(value, 'stray press', 'stray presses')} at rest",
        "tap_count_error": f"A button missed or added up to {value:.0f} of {TAPS} taps",
        "tap_chatter": f"Buttons chattered {_n(value, 'time')}, a press registering twice",
        "hold_dropouts": f"Held buttons dropped out {_n(value, 'time')}",
        "dpad_ghosts": f"The D-pad fired a neighbouring direction {_n(value, 'time')}",
        "dpad_diagonals": f"The D-pad missed diagonals ({value:.0%} registered)",
        "usb_mean": f"USB reports arrive slowly ({value:.1f} ms average)",
        "usb_p99": f"USB reports are sometimes late ({value:.1f} ms at the 99th percentile)",
        "usb_rate": f"USB report rate is low ({value:.0f} Hz)",
        "usb_gaps": f"USB reports had {_n(value, 'gap')} over 30 ms",
    }
    return templates.get(m.name, f"{m.name.replace('_', ' ')} is outside the healthy range")


# What a weak metric means in play, keyed by the metric name without its stick side.
MEANING = {
    "idle_offset": "it rests off centre, which can show up as drift",
    "idle_noise": "its reading jitters at rest",
    "settle_mean": "it doesn't spring back to centre cleanly, so aim can creep after a move",
    "settle_max": "it sometimes stops off centre after a push, so aim can creep",
    "snapback": "it overshoots centre on release, which can nudge aim the other way",
    "circle_error": "its range isn't round, so diagonals can feel short of full speed",
    "reach_diff": "it reaches further one way, so turning speed can differ by direction",
    "sweep_centre": "small moves near centre are coarse, so fine aim can feel steppy",
    "sweep_jump": "it skips values when moved slowly, so slow aim can jerk",
    "sprint_reach": "it falls short while RT is held, so sprint-and-turn can feel limited",
    "flick_wrong_direction": "quick flicks sometimes register the wrong way",
    "flick_snapback": "quick flicks sometimes bounce back past centre",
    "lt_rest": "LT doesn't fully release, so a game may see a light press",
    "rt_rest": "RT doesn't fully release, so a game may see a light press",
    "lt_reach": "LT doesn't reach full, so a full press may not register",
    "rt_reach": "RT doesn't reach full, so full throttle may not register",
    "lt_jump": "LT skips values, so part presses can feel steppy",
    "rt_jump": "RT skips values, so part presses can feel steppy",
    "rt_sag": "RT sags while held down, so sprint or full throttle can drop",
    "throttle_jitter": "RT wobbles when held part way, so steady throttle is hard",
    "stray_presses": "a button pressed itself with nobody touching it",
    "tap_count_error": "a button missed or added taps",
    "tap_chatter": "a single press sometimes registered twice",
    "hold_dropouts": "a held input briefly let go",
    "dpad_ghosts": "the D-pad sometimes fires a neighbouring direction",
    "dpad_diagonals": "the D-pad doesn't always register diagonals",
    "usb_mean": "input arrives later than on a healthy controller over USB",
    "usb_p99": "some reports arrive late",
    "usb_rate": "it sends fewer reports than a healthy controller over USB",
    "usb_gaps": "reports sometimes pause for over 30 ms",
    "bluetooth_mean": "input arrives later than on the other controllers over Bluetooth",
    "bluetooth_p99": "some reports arrive later than on the other controllers",
    "bluetooth_rate": "it sends fewer reports than the other controllers over Bluetooth",
}
# Said when nothing in a category is weak. It claims only what was measured.
ALL_GOOD = "Everything measured for it is in the healthy range."


def _stick_rest(side: str, values: dict[str, Any], profile: Profile) -> str | None:
    offset = values.get(f"idle_drift.{side}_offset_pct")
    deadzone = profile.deadzones.get(side)
    if not _finite(offset) or not deadzone:
        return None
    direction = values.get(f"idle_drift.{side}_direction", "off")
    dz = deadzone / 32767 * 100
    limit = profile.auto_fail["idle_offset_pct"]
    rest = f"Rests {offset:.1f}% {direction} of centre"
    if offset > dz:
        return f"{rest}, past the game deadzone of {dz:.1f}%, so games will see drift."
    if offset > limit:
        return (
            f"{rest}, inside the game deadzone of {dz:.1f}% but over the {limit:.0f}% limit,"
            " so it may grow into drift. Retest once to rule out where it came to rest."
        )
    return f"{rest}, inside the game deadzone of {dz:.1f}%, so games won't see it."


def _connection_line(values: dict[str, Any], profile: Profile, connection: str) -> str | None:
    mean, rate = values.get("report_timing.mean_ms"), values.get("report_timing.rate_hz")
    if not (_finite(mean) and _finite(rate)):
        return None
    if connection == "usb":
        ref = profile.usb_reference
        return (
            f"Reports every {mean:.1f} ms at {rate:.0f} Hz, against about"
            f" {ref.get('mean_ms', 0):.1f} ms for a healthy controller over USB."
        )
    return f"Reports every {mean:.1f} ms at {rate:.0f} Hz over Bluetooth."


def inferences(
    score: RunScore, values: dict[str, Any], profile: Profile, connection: str = "usb"
) -> dict[str, list[str]]:
    """One or two plain lines per tested category: what it means in games, and what to do.

    The first line anchors the reading where there is a reference, such as a stick's rest
    against the game deadzone. The second explains the weakest metric, or says all is well.
    """
    out: dict[str, list[str]] = {}
    for key, cat in score.categories.items():
        if cat.score is None:
            continue
        lines: list[str] = []
        if key in ("left_stick", "right_stick"):
            rest = _stick_rest(key.split("_")[0], values, profile)
            if rest:
                lines.append(rest)
        elif key == "connection":
            line = _connection_line(values, profile, connection)
            if line:
                lines.append(line)
        worst_name, worst = min(cat.parts.items(), key=lambda kv: kv[1])
        if worst < MINOR_BELOW:
            bare = worst_name.removeprefix("left_").removeprefix("right_")
            meaning = MEANING.get(bare, "one reading is outside the healthy range")
            if worst < FAULTY_BELOW:
                advice = "That is likely a hardware fault: clean, recalibrate or repair it."
            else:
                advice = "It's minor, so retest before blaming the controller."
            lines.append(f"Weakest: {meaning}. {advice}")
        else:
            lines.append(ALL_GOOD)
        out[key] = lines[:2]
    return out


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
    result = RunScore(
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
    result.inferences = inferences(result, values, profile, connection)
    return result


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
