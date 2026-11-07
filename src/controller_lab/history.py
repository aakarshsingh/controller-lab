"""Results folder, run directories and metrics.json. No pygame imports.

History stores data only. It never adjusts a reading.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from controller_lab import __version__

CONNECTIONS = ("usb", "bluetooth")
MAX_LABEL = 32
RUN_TIME_FORMAT = "%Y%m%d-%H%M%S"
METRICS_FILE = "metrics.json"
DEFAULT_PREFIX = "controller-"
BAD_CHARS = frozenset('<>:"/\\|?*')
RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


@dataclass(frozen=True)
class Run:
    label: str
    run_dir: Path
    metrics: dict[str, Any]


def results_root() -> Path:
    return Path.home() / "ControllerLab" / "results"


def validate_label(label: str) -> str | None:
    """Say why `label` can't be used as a folder name, or return None when it can."""
    if not label.strip():
        return "Type a label first."
    if len(label) > MAX_LABEL:
        return f"Labels are at most {MAX_LABEL} characters."
    if any(c in BAD_CHARS or ord(c) < 32 for c in label):
        return "Labels can't contain < > : \" / \\ | ? *"
    if label != label.strip() or label.endswith("."):
        return "Labels can't start or end with a space, or end with a dot."
    if label.split(".")[0].strip().upper() in RESERVED:
        return "Windows reserves that name. Pick another."
    return None


def run_time(run_dir_name: str) -> datetime | None:
    """The local start time in a `<YYYYMMDD-HHMMSS>_<connection>` folder name."""
    stamp = run_dir_name.partition("_")[0]
    try:
        return datetime.strptime(stamp, RUN_TIME_FORMAT)
    except ValueError:
        return None


def _subdirs(path: Path) -> list[Path]:
    try:
        return [p for p in path.iterdir() if p.is_dir()]
    except OSError:
        return []


def list_labels() -> list[tuple[str, datetime]]:
    """Every label with at least one run, and its last test time, newest first."""
    labels = []
    for label_dir in _subdirs(results_root()):
        times = [t for p in _subdirs(label_dir) if (t := run_time(p.name)) is not None]
        if times:
            labels.append((label_dir.name, max(times)))
    labels.sort(key=lambda item: item[1], reverse=True)
    return labels


def next_default_label() -> str:
    taken = {p.name.lower() for p in _subdirs(results_root())}
    n = 1
    while f"{DEFAULT_PREFIX}{n}" in taken:
        n += 1
    return f"{DEFAULT_PREFIX}{n}"


def new_run_dir(label: str, connection: str, now: datetime | None = None) -> Path:
    """Create `results/<label>/<YYYYMMDD-HHMMSS>_<connection>/` and return it."""
    error = validate_label(label)
    if error is not None:
        raise ValueError(error)
    if connection not in CONNECTIONS:
        raise ValueError(f"Unknown connection: {connection}")
    stamp = (now or datetime.now()).strftime(RUN_TIME_FORMAT)
    run_dir = results_root() / label / f"{stamp}_{connection}"
    run_dir.mkdir(parents=True)
    return run_dir


def save_metrics(run_dir: Path, metrics: dict[str, Any]) -> Path:
    """Write `metrics.json` with the tool version first. The write is atomic."""
    data = {"tool_version": __version__, **metrics}
    path = run_dir / METRICS_FILE
    tmp = path.with_name(METRICS_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _read_metrics(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8
        return None
    return data if isinstance(data, dict) else None


def load_runs() -> list[Run]:
    """Every readable run under the results root, newest first.

    A missing or malformed `metrics.json` skips that run.
    """
    runs = []
    for label_dir in _subdirs(results_root()):
        for run_dir in _subdirs(label_dir):
            metrics = _read_metrics(run_dir / METRICS_FILE)
            if metrics is not None:
                runs.append(Run(label_dir.name, run_dir, metrics))
    runs.sort(key=lambda run: run.run_dir.name, reverse=True)
    return runs


def label_for_device(device_key: str | None) -> str | None:
    """The label of the newest run with this device key. A None key never matches."""
    if device_key is None:
        return None
    for run in load_runs():
        if run.metrics.get("device_key") == device_key:
            return run.label
    return None
