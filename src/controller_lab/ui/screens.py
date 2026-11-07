"""One render function per screen. Each draws the whole window, and the caller flips.

Every screen shares one frame: the step marker at the top, one large primary action at
the bottom right that names what A does, and the secondary keys small at the bottom left.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pygame

from controller_lab.analysis import DIRECTIONS
from controller_lab.checks.base import Check, Outcome, Phase
from controller_lab.detect import ControllerInfo
from controller_lab.rig import RigResult
from controller_lab.scoring import HONEST_LIMITS, RunScore
from controller_lab.source import Snapshot
from controller_lab.ui.controller_view import FACE_COLOURS, draw_controller
from controller_lab.ui.draw import BG, GREEN, GREY, PANEL, RED, WHITE, panel, text

WIDTH = 960
MARGIN = 40
STEPS = ("Connect", "Label", "Suite", "Setup", "Checks", "Results")
PRIMARY_SIZE = 32
PRIMARY_TOP = 500
PRIMARY_PAD = (22, 12)
HINT_POS = (MARGIN, 568)
HINT_GAP = "    "
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
END_LINES = 10
ISSUES_BOTTOM = 432
NOTES_BOTTOM = 425
SKIPPED_NAMED = 4
VERDICT_COLOURS = {"Healthy": GREEN, "Minor issues": (246, 197, 32), "Faulty": RED}
INTRO_COLOUR = (246, 197, 32)
AXIS_FULL = 32768
GAUGE_BAND = 0.05
TRACK_CENTRE = (WIDTH // 2, 378)
TRACK_R = 105
ARROW_CENTRE = (WIDTH - MARGIN - 50, 175)
ARROW_R = 34

_fonts: dict[int, pygame.font.Font] = {}


def _font(size: int) -> pygame.font.Font:
    if size not in _fonts:
        _fonts[size] = pygame.font.Font(None, size)
    return _fonts[size]


def _width(s: str, size: int) -> int:
    return _font(size).size(s)[0]


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
        w = _keys_width(primary, PRIMARY_SIZE) + 2 * pad_x
        box = pygame.Rect(WIDTH - MARGIN - w, PRIMARY_TOP, w, PRIMARY_SIZE + 2 * pad_y - 8)
        panel(s, box, PANEL)
        pygame.draw.rect(s, GREEN, box, 2, border_radius=6)
        _keys_text(s, primary, (box.x + pad_x, box.y + pad_y), PRIMARY_SIZE, WHITE)
    _keys_text(s, hints, HINT_POS, 22, GREY)


def _glyph_r(size: int) -> int:
    return round(size * 0.42)


def _split_keys(line: str) -> list[tuple[str | None, str]]:
    """Split a line of key hints into segments, each with its leading face button."""
    parts = []
    for seg in line.split(HINT_GAP):
        head = seg.split(" ", 1)[0]
        if head in FACE_COLOURS:
            parts.append((head, seg[len(head) :]))
        else:
            parts.append((None, seg))
    return parts


def _keys_width(line: str, size: int) -> int:
    width = 0
    for i, (key, rest) in enumerate(_split_keys(line)):
        width += _width(HINT_GAP, size) if i else 0
        width += 2 * _glyph_r(size) + 2 if key else 0
        width += _width(rest, size) if rest else 0
    return width


def _keys_text(
    s: pygame.Surface, line: str, pos: tuple[int, int], size: int, colour: tuple[int, int, int]
) -> None:
    """Draw key hints, with A, B, X and Y as the controller's coloured buttons."""
    x, y = pos
    r = _glyph_r(size)
    for i, (key, rest) in enumerate(_split_keys(line)):
        x += _width(HINT_GAP, size) if i else 0
        if key:
            centre = (x + r, y + round(size * 0.34))
            pygame.draw.circle(s, FACE_COLOURS[key], centre, r)
            glyph = _font(round(size * 0.8)).render(key, True, BG)
            s.blit(glyph, glyph.get_rect(center=centre))
            x += 2 * r + 2
        if rest:
            text(s, rest, (x, y), size, colour)
            x += _width(rest, size)


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
    score: RunScore | None,
) -> None:
    """The verdict, the category table, the issues, the notes and the honest limits."""
    _frame(s, 6, "A  Test another controller", "N  test another controller    Esc  quit")
    if score is None:
        text(s, error or "The run wasn't saved, so it has no score.", (MARGIN, 80), 26, RED)
        if run_dir is not None:
            text(s, str(run_dir), (MARGIN, 120), 20, GREY)
        return
    colour = VERDICT_COLOURS.get(score.verdict, GREY)
    text(s, f"{label_name}: {score.verdict}", (MARGIN, 70), 36, colour)
    if score.overall is not None:
        overall = f"Overall {score.overall:.0f}"
        text(s, overall, (WIDTH - MARGIN - _width(overall, 36), 70), 36, colour)
    text(s, score.summary, (MARGIN, 108), 22, GREY)
    if run_dir is not None:
        for i, line in enumerate(_wrap_path(str(run_dir), 18, WIDTH - 2 * MARGIN)[:2]):
            text(s, line, (MARGIN, 136 + i * 17), 18, GREY)
    if error:
        text(s, error, (MARGIN, 170), 20, RED)

    # Category table, left.
    notes = []
    for i, cat in enumerate(score.categories.values()):
        y = 196 + i * 27
        text(s, cat.title, (MARGIN, y), 24)
        if cat.score is None:
            text(s, "not tested", (MARGIN + 150, y), 22, GREY)
            if cat.note and cat.note != "not tested":
                notes.append(f"{cat.title}: {cat.note}")
            continue
        text(s, f"{cat.score:.0f}", (MARGIN + 150, y), 24, _score_colour(cat.score))
        bar = pygame.Rect(MARGIN + 200, y + 4, 220, 12)
        panel(s, bar)
        done = bar.copy()
        done.width = int(bar.width * cat.score / 100)
        if done.width:
            panel(s, done, _score_colour(cat.score))

    # Issues, right. Auto-fails come first.
    x, y, width = 490, 196, WIDTH - MARGIN - 490
    text(s, "Issues", (x, y), 22, GREY)
    y += 26
    lines = [(t, RED) for t in score.auto_fails] + [(t, WHITE) for t in score.issues]
    if not lines:
        lines = [("No issues found.", GREEN)]
    for n, (item, item_colour) in enumerate(lines):
        parts = _wrap(item, 20, width)
        if y + 20 * len(parts) > ISSUES_BOTTOM:
            text(s, f"and {len(lines) - n} more in metrics.json", (x, y), 20, GREY)
            break
        for part in parts:
            text(s, part, (x, y), 20, item_colour)
            y += 20
        y += 4

    # Notes, skipped checks, then the honest limits, above the keys.
    notes += score.notes
    skipped = [e["title"] for e in checks.values() if e.get("status") != "done"]
    if len(skipped) > SKIPPED_NAMED:
        notes.append(f"Not done: {len(skipped)} of {len(checks)} checks.")
    elif skipped:
        notes.append(f"Not done: {', '.join(skipped)}.")
    y = 368
    for note in notes:
        for part in _wrap(note, 18, 430):
            if y > NOTES_BOTTOM:
                break
            text(s, part, (MARGIN, y), 18, GREY)
            y += 17
    for i, limit in enumerate(HONEST_LIMITS):
        text(s, limit, (MARGIN, 446 + i * 17), 17, GREY)
    if rig_result is not None and not rig_result.ok:
        text(s, "This PC's polling loop was too slow for timing checks.", (MARGIN, 500), 18, RED)


def _score_colour(score: float) -> tuple[int, int, int]:
    return GREEN if score >= 80 else INTRO_COLOUR if score >= 60 else RED


def _wrap(line: str, size: int, width: int) -> list[str]:
    """Break text into lines at spaces so each fits in `width`."""
    out, current = [], ""
    for word in line.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and _width(candidate, size) > width:
            out.append(current)
            current = word
        else:
            current = candidate
    return [*out, current] if current else out


def _check_title(s: pygame.Surface, check: Check, index: int, total: int) -> None:
    text(s, f"Check {index + 1} of {total} · {check.title}", (MARGIN, 72), 30, GREY)


def check(
    s: pygame.Surface,
    check: Check,
    phase: Phase,
    index: int,
    total: int,
    phase_index: int,
    seconds_left: float,
    fraction: float,
    intro_left: float,
    elapsed: float,
    snap: Snapshot | None,
) -> None:
    """A running check. For a timing check this is drawn once, and only the bar refreshes.

    While `intro_left` is above zero, the phase's get-ready banner replaces the prompt.
    `elapsed` is how long the phase has been counting, which moves a tracking target.
    """
    if phase.answer:
        _frame(s, 5, "A  Yes", "B  No    S  skip    Esc  quit")
    else:
        _frame(s, 5, "", CHECK_HINTS)
    _check_title(s, check, index, total)
    parts = len(check.phases)
    if parts > 1 and not check.is_timing:
        text(s, f"{phase_index + 1} of {parts}", (WIDTH - MARGIN - 70, 76), 26, GREY)
    if intro_left > 0:
        text(s, phase.intro, (MARGIN, 112), 44, INTRO_COLOUR)
        text(s, f"Get ready. Starts in {math.ceil(intro_left)} s.", (MARGIN, 165), 26, GREY)
        _visual(s, phase, 0.0, snap)
        return
    text(s, phase.prompt, (MARGIN, 112), 36)
    if phase.arrow is not None:
        _arrow(s, ARROW_CENTRE, phase.arrow)
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
    _visual(s, phase, elapsed, snap)


def _visual(s: pygame.Surface, phase: Phase, elapsed: float, snap: Snapshot | None) -> None:
    """The panel under the progress bar: the controller, a gauge, or a tracking target."""
    if snap is None:
        return
    if phase.visual == "controller":
        draw_controller(s, VIEW_ORIGIN, snap, phase.focus)
    elif phase.visual == "gauge" and phase.gauge is not None:
        _gauge(s, snap[5] / AXIS_FULL, phase.gauge)
    elif phase.visual == "tracking" and phase.target is not None:
        side = 0 if phase.cursor == "left" else 2
        _tracking(s, phase.target(elapsed), (snap[side] / AXIS_FULL, snap[side + 1] / AXIS_FULL))


def _gauge(s: pygame.Surface, value: float, level: float) -> None:
    """RT as a bar, with the target level and its ±5% band marked."""
    bar = pygame.Rect(MARGIN, 300, WIDTH - 2 * MARGIN, 64)
    panel(s, bar)
    fill = bar.copy()
    fill.width = int(bar.width * max(0.0, min(1.0, value)))
    if fill.width:
        panel(s, fill, GREEN)
    band = pygame.Rect(0, bar.y - 8, int(bar.width * 2 * GAUGE_BAND), bar.height + 16)
    band.centerx = bar.x + int(bar.width * level)
    pygame.draw.rect(s, INTRO_COLOUR, band, 3, border_radius=6)
    text(s, "0%", (bar.x, bar.bottom + 14), 22, GREY)
    text(s, "100%", (bar.right - 44, bar.bottom + 14), 22, GREY)
    text(s, f"Target {level:.0%}", (band.x, bar.bottom + 14), 26, INTRO_COLOUR)
    text(s, f"RT now {value:.0%}", (MARGIN, 410), 30)


def _tracking(s: pygame.Surface, target: tuple[float, float], stick: tuple[float, float]) -> None:
    """A round field: the yellow target moves, and the green dot is the stick."""
    cx, cy = TRACK_CENTRE
    pygame.draw.circle(s, PANEL, TRACK_CENTRE, TRACK_R)
    pygame.draw.circle(s, GREY, TRACK_CENTRE, TRACK_R, 2)
    pygame.draw.circle(s, GREY, TRACK_CENTRE, round(TRACK_R * 0.25), 1)
    tx, ty = target
    pygame.draw.circle(s, INTRO_COLOUR, (cx + round(tx * TRACK_R), cy + round(ty * TRACK_R)), 11)
    sx, sy = stick
    pygame.draw.circle(s, GREEN, (cx + round(sx * TRACK_R), cy + round(sy * TRACK_R)), 7)
    text(s, "Keep the green dot", (MARGIN, 330), 26, GREY)
    text(s, "on the yellow one.", (MARGIN, 360), 26, GREY)


def _arrow(s: pygame.Surface, centre: tuple[int, int], direction: str) -> None:
    """A large arrow pointing in one of the eight stick directions."""
    k = DIRECTIONS.index(direction)
    angle = math.radians(k * 45)
    ux, uy = math.cos(angle), -math.sin(angle)  # screen y grows downwards
    cx, cy = centre
    pygame.draw.circle(s, PANEL, centre, ARROW_R + 8)
    tip = (cx + ux * ARROW_R, cy + uy * ARROW_R)
    tail = (cx - ux * ARROW_R * 0.6, cy - uy * ARROW_R * 0.6)
    pygame.draw.line(s, GREEN, tail, tip, 6)
    px, py = -uy, ux
    base = (tip[0] - ux * 18, tip[1] - uy * 18)
    head = [tip, (base[0] + px * 12, base[1] + py * 12), (base[0] - px * 12, base[1] - py * 12)]
    pygame.draw.polygon(s, GREEN, head)


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
    if outcome.lines:
        for i, line in enumerate(outcome.lines[:END_LINES]):
            x, y = MARGIN + (i % 2) * 440, 210 + (i // 2) * 34
            text(s, line, (x, y), 28)
    else:
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
