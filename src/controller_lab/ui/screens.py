"""One render function per screen. Each draws the whole window, and the caller flips.

Every screen shares one frame: the step marker at the top, one large primary action at
the bottom right that names what A does, and the secondary keys small at the bottom left.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pygame

from controller_lab.checks.base import Check, Outcome, Phase
from controller_lab.detect import ControllerInfo
from controller_lab.rig import RigResult
from controller_lab.source import Snapshot
from controller_lab.ui.controller_view import draw_controller
from controller_lab.ui.draw import BG, GREEN, GREY, PANEL, RED, WHITE, panel, text

WIDTH = 960
MARGIN = 40
STEPS = ("Connect", "Label", "Suite", "Setup", "Checks", "Results")
PRIMARY_SIZE = 32
PRIMARY_TOP = 500
PRIMARY_PAD = (22, 12)
HINT_POS = (MARGIN, 568)
VIEW_ORIGIN = (MARGIN, 268)
PROGRESS_RECT = pygame.Rect(MARGIN, 222, WIDTH - 2 * MARGIN, 26)
LABEL_ROWS = 7
WAITING_TEXT = "Connect a controller via USB or Bluetooth."
ONE_ONLY_TEXT = "Connect only one controller."
QUIT_TEXT = "Press Esc again to quit."
SUITES = {
    "full": ("Full", "Every check, about 14 min. Best over USB."),
    "connection": ("Connection", "About 3 min. Run it on the second connection."),
}
SETUP_ITEMS = (
    "Plug the controller in directly, not through a hub.",
    "On Bluetooth, charge the controller or fit fresh batteries.",
    "Close Steam, DS4Windows and other remappers.",
    "Keep this window focused.",
    "Rest the controller flat.",
)
LATENCY_LIMIT = "Measured from state changes Windows delivers, not true latency."
STATUS_TEXT = {
    "done": ("Done", GREEN),
    "skipped": ("Skipped", GREY),
    "voided": ("Voided", RED),
    "not_tested": ("Not tested", GREY),
    "interrupted": ("Interrupted", RED),
}
CHECK_HINTS = "B / S  skip    R  redo    Esc  quit"
END_METRICS = 8

_fonts: dict[int, pygame.font.Font] = {}


def _width(s: str, size: int) -> int:
    if size not in _fonts:
        _fonts[size] = pygame.font.Font(None, size)
    return _fonts[size].size(s)[0]


def _wrap_path(path: str, size: int, width: int) -> list[str]:
    """Break a long path into lines at its separators so it stays inside the window."""
    sep = "\\"
    lines, line = [], ""
    for part in path.split(sep):
        piece = line + sep + part if line else part
        if line and _width(piece, size) > width:
            lines.append(line + sep)
            line = part
        else:
            line = piece
    return [*lines, line]


def _frame(s: pygame.Surface, step: int, primary: str, hints: str) -> None:
    """Clear the window and draw the step marker, the primary action and the hints."""
    s.fill(BG)
    text(s, f"Step {step} of {len(STEPS)} · {STEPS[step - 1]}", (MARGIN, 22), 30)
    seg_w, gap = 34, 6
    x0 = WIDTH - MARGIN - len(STEPS) * (seg_w + gap) + gap
    for i in range(len(STEPS)):
        colour = GREEN if i < step else PANEL
        panel(s, (x0 + i * (seg_w + gap), 30, seg_w, 8), colour)
    if primary:
        pad_x, pad_y = PRIMARY_PAD
        w = _width(primary, PRIMARY_SIZE) + 2 * pad_x
        box = pygame.Rect(WIDTH - MARGIN - w, PRIMARY_TOP, w, PRIMARY_SIZE + 2 * pad_y - 8)
        panel(s, box, GREEN)
        text(s, primary, (box.x + pad_x, box.y + pad_y), PRIMARY_SIZE, BG)
    text(s, hints, HINT_POS, 22, GREY)


def waiting(s: pygame.Surface, error: str) -> None:
    _frame(s, 1, "", "Esc  quit")
    text(s, WAITING_TEXT, (MARGIN, 240), 36)
    if error:
        text(s, error, (MARGIN, 300), 24, RED)


def one_only(s: pygame.Surface) -> None:
    _frame(s, 1, "", "Esc  quit")
    text(s, ONE_ONLY_TEXT, (MARGIN, 240), 36)
    text(s, "Disconnect the others to continue.", (MARGIN, 290), 26, GREY)


def label(
    s: pygame.Surface,
    info: ControllerInfo,
    devtree_ms: float,
    labels: list[tuple[str, datetime]],
    cursor: int,
    preselected: str | None,
    note: str,
) -> None:
    on_new = cursor >= len(labels)
    primary = "A  New label" if on_new else f"A  Use {labels[cursor][0]}"
    _frame(s, 2, primary, "Up/Down  choose    Esc  quit")

    fields = (
        ("Name", info.name),
        ("Type", info.type),
        ("Connection", info.connection),
        ("Battery", info.battery),
    )
    for i, (name, value) in enumerate(fields):
        text(s, name, (MARGIN, 80 + i * 32), 26, GREY)
        text(s, value, (175, 80 + i * 32), 26)
    detail = f"{info.vendor_product}   {info.identity_source} {devtree_ms:.2f} ms   {info.backend}"
    text(s, detail, (MARGIN, 215), 20, GREY)

    text(s, "Which controller is this?", (500, 76), 26, GREY)
    rows = [(name, when.strftime("%Y-%m-%d")) for name, when in labels] + [("New label", "")]
    start = max(0, cursor - LABEL_ROWS + 1)
    for i, (name, when) in enumerate(rows[start : start + LABEL_ROWS]):
        index = start + i
        y = 112 + i * 30
        if index == cursor:
            panel(s, (490, y - 4, 430, 28), PANEL)
        is_pre = name == preselected and index < len(labels)
        text(s, name, (505, y), 24, GREEN if is_pre else WHITE)
        if when:
            text(s, when, (830, y + 2), 20, GREY)
    if preselected is not None:
        text(s, f"{preselected}: last used with this controller", (500, 330), 22, GREEN)
    if note:
        text(s, note, (MARGIN, 400), 26, RED)


def new_label(s: pygame.Surface, typed: str, suggestion: str, error: str) -> None:
    name = typed.strip()
    primary = f"A / Enter  Save {name}" if name else f"A  Use {suggestion}"
    _frame(s, 2, primary, "Backspace  delete    B / Esc  back")
    text(s, "Name this controller", (MARGIN, 76), 30)
    panel(s, (MARGIN, 120, 600, 50))
    text(s, typed + "_", (MARGIN + 15, 132), 30, WHITE if typed else GREY)
    if not name:
        text(s, "Or type a name on the keyboard, then press Enter.", (MARGIN, 186), 26)
    if error:
        text(s, error, (MARGIN, 222), 26, RED)
    text(s, 'At most 32 characters, and no < > : " / \\ | ? *', (MARGIN, 262), 22, GREY)
    text(s, "If typing does nothing, click this window first.", (MARGIN, 290), 22, GREY)


def suite(s: pygame.Surface, label_name: str, chosen: str, connection: str) -> None:
    title = SUITES[chosen][0]
    _frame(s, 3, f"A  Choose {title}", "Up/Down  choose    B / Backspace  back    Esc  quit")
    text(s, f"Which checks for {label_name}?", (MARGIN, 76), 30)
    for i, (key, (name, about)) in enumerate(SUITES.items()):
        y = 130 + i * 84
        if key == chosen:
            panel(s, (MARGIN, y - 12, 880, 70), PANEL)
        text(s, name, (MARGIN + 20, y), 32, WHITE if key == chosen else GREY)
        text(s, about, (MARGIN + 20, y + 32), 22, GREY)
    if chosen == "full" and connection != "usb":
        warning = "This controller is on Bluetooth. Full works best over USB."
        text(s, warning, (MARGIN, 310), 24, RED)


def setup(s: pygame.Surface, label_name: str, snap: Snapshot | None) -> None:
    _frame(s, 4, "A  Start", "B / Backspace  back    Esc  quit")
    text(s, f"Before testing {label_name}:", (MARGIN, 72), 30)
    for i, item in enumerate(SETUP_ITEMS):
        text(s, f"-  {item}", (MARGIN, 108 + i * 30), 24)
    if snap is not None:
        draw_controller(s, VIEW_ORIGIN, snap)


def rig(s: pygame.Surface) -> None:
    _frame(s, 5, "", "")
    text(s, "Checking this PC's polling loop for 5 s.", (MARGIN, 220), 32)
    text(s, "Leave the controller alone.", (MARGIN, 265), 36)


def results(
    s: pygame.Surface,
    label_name: str,
    run_dir: Path | None,
    error: str,
    rig_result: RigResult | None,
    checks: dict[str, dict[str, Any]],
) -> None:
    _frame(s, 6, "A  Test another controller", "N  test another controller    Esc  quit")
    if run_dir is None:
        text(s, error or "The run wasn't saved.", (MARGIN, 80), 26, RED)
    else:
        text(s, f"Run saved for {label_name}.", (MARGIN, 76), 30)
        for i, line in enumerate(_wrap_path(str(run_dir), 22, WIDTH - 2 * MARGIN)[:3]):
            text(s, line, (MARGIN, 114 + i * 22), 22, GREY)
        if error:
            text(s, error, (MARGIN, 166), 22, RED)
    if rig_result is not None:
        r = rig_result
        verdict = "ok" if r.ok else "too slow, timing checks are not tested"
        text(
            s,
            f"Rig  median {r.median_ms:.2f} ms   p99 {r.p99_ms:.2f} ms   "
            f"slow passes {r.slow_passes}   {verdict}",
            (MARGIN, 190),
            22,
            GREEN if r.ok else RED,
        )
    for i, entry in enumerate(checks.values()):
        y = 230 + i * 30
        status, colour = STATUS_TEXT.get(entry["status"], (entry["status"], GREY))
        text(s, entry["title"], (MARGIN, y), 26)
        text(s, status, (300, y), 26, colour)
        if entry["message"] and entry["status"] != "skipped":
            text(s, entry["message"], (460, y + 3), 22, GREY)
    y = 230 + len(checks) * 30 + 14
    text(s, "Scores and the verdict aren't built yet.", (MARGIN, y), 24, GREY)


def _check_title(s: pygame.Surface, check: Check, index: int, total: int) -> None:
    text(s, f"Check {index + 1} of {total} · {check.title}", (MARGIN, 72), 30, GREY)


def check(
    s: pygame.Surface,
    check: Check,
    phase: Phase,
    index: int,
    total: int,
    seconds_left: float,
    fraction: float,
    snap: Snapshot | None,
) -> None:
    """A running check. For a timing check this is drawn once, and only the bar refreshes."""
    _frame(s, 5, "", CHECK_HINTS)
    _check_title(s, check, index, total)
    text(s, phase.prompt, (MARGIN, 112), 36)
    if check.is_timing:
        if phase.duration_s is None:
            note = f"Starts when the stick moves. Skipped after {phase.timeout_s:.0f} s."
        else:
            note = f"Measuring for {phase.duration_s:.0f} s. Only this bar refreshes."
        text(s, note, (MARGIN, 165), 24, GREY)
        text(s, LATENCY_LIMIT, (MARGIN, 262), 22, GREY)
        panel(s, PROGRESS_RECT)
        return
    text(s, f"{seconds_left:.0f} s left", (MARGIN, 165), 26, GREY)
    panel(s, PROGRESS_RECT)
    done = PROGRESS_RECT.copy()
    done.width = int(done.width * fraction)
    if done.width:
        panel(s, done, GREEN)
    if snap is not None and phase.visual == "controller":
        draw_controller(s, VIEW_ORIGIN, snap)


def _metric_text(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return "n/a" if value is None else str(value)


def check_end(
    s: pygame.Surface,
    check: Check,
    outcome: Outcome,
    index: int,
    total: int,
    seconds_left: float,
    last: bool,
) -> None:
    voided = outcome.status == "voided"
    if voided:
        _frame(s, 5, "A  Redo", "B / S  skip    Esc  quit")
    else:
        _frame(s, 5, "A  Continue", "R  redo    Esc  quit")
    _check_title(s, check, index, total)
    status, colour = STATUS_TEXT.get(outcome.status, (outcome.status, GREY))
    text(s, status, (MARGIN, 112), 40, colour)
    if outcome.message:
        text(s, outcome.message, (MARGIN, 160), 28, RED if voided else GREY)
    items = [(k, v) for k, v in outcome.metrics.items() if not isinstance(v, dict | list)]
    for i, (name, value) in enumerate(items[:END_METRICS]):
        x, y = MARGIN + (i % 2) * 440, 210 + (i // 2) * 30
        text(s, name.replace("_", " "), (x, y), 22, GREY)
        text(s, _metric_text(value), (x + 230, y), 22)
    if not voided:
        after = "Results" if last else "Next check"
        text(s, f"{after} in {seconds_left:.0f} s", (MARGIN, 450), 24, GREY)


def focus_lost(s: pygame.Surface, check: Check, index: int, total: int) -> None:
    _frame(s, 5, "A  Redo", "B / S  skip    Esc  quit")
    _check_title(s, check, index, total)
    text(s, "Focus was lost, A to redo", (MARGIN, 112), 36, RED)
    text(s, "Another window came to the front, so this check was voided.", (MARGIN, 160), 24)
    text(s, "Click this window or press A on the controller.", (MARGIN, 192), 24, GREY)


def paused(s: pygame.Surface, reason: str, label_name: str, run_connection: str) -> None:
    if reason == "confirm":
        _frame(s, 5, "A  Yes, continue", "B  pick another label    Esc  quit")
        text(s, f"Is this still {label_name}?", (MARGIN, 200), 40)
        text(s, "The interrupted check starts again.", (MARGIN, 250), 24, GREY)
        return
    _frame(s, 5, "", "Esc  quit")
    text(s, "Paused", (MARGIN, 72), 30, GREY)
    if reason == "one_only":
        lines = (ONE_ONLY_TEXT, "Disconnect the others to continue.")
    elif reason == "other_connection" and run_connection == "usb":
        lines = ("Reconnected over Bluetooth.", "Plug the USB cable back in to continue.")
    elif reason == "other_connection":
        lines = ("Reconnected over USB.", "Unplug the USB cable to continue over Bluetooth.")
    else:
        lines = ("The controller disconnected.", "Reconnect it to continue.")
    text(s, lines[0], (MARGIN, 200), 36)
    text(s, lines[1], (MARGIN, 250), 28, GREY)
    text(s, "A run never mixes connections.", (MARGIN, 300), 22, GREY)


def quit_overlay(s: pygame.Surface) -> None:
    box = pygame.Rect(0, 0, 520, 120)
    box.center = (WIDTH // 2, 260)
    panel(s, box.inflate(4, 4), GREY)
    panel(s, box, PANEL)
    text(s, QUIT_TEXT, (box.x + 30, box.y + 28), 36)
    text(s, "Any other key keeps going.", (box.x + 30, box.y + 72), 22, GREY)
