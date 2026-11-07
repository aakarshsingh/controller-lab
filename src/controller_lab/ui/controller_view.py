"""Live controller view, laid out like an Xbox controller.

Left stick top left, D-pad below it, right stick bottom right, and the face buttons top
right as coloured circles: Y on top, X left, B right, A at the bottom. The bumpers sit
above, the triggers fill as bars at the outer edges, and View and Menu sit in the middle.
"""

from __future__ import annotations

import pygame

from controller_lab.source import BUTTON_NAMES, Snapshot
from controller_lab.ui.draw import BG, GREEN, GREY, WHITE, panel

SIZE = (880, 220)
STICK_R = 54
STICK_SCALE = 32768
TRIGGER_MAX = 32767
FACE_R = 19
FACE_GAP = 34
BIT = {name: i for i, name in enumerate(BUTTON_NAMES)}

# Xbox face button colours.
FACE_COLOURS = {
    "A": (96, 185, 70),
    "B": (226, 64, 54),
    "X": (39, 125, 225),
    "Y": (246, 197, 32),
}
DARK = (28, 30, 35)
FOCUS = (246, 197, 32)  # ring round the control in use

_fonts: dict[int, pygame.font.Font] = {}


def _centred(surface: pygame.Surface, s: str, centre: tuple[int, int], size: int, colour) -> None:
    if size not in _fonts:
        _fonts[size] = pygame.font.Font(None, size)
    image = _fonts[size].render(s, True, colour)
    surface.blit(image, image.get_rect(center=centre))


def _pressed(mask: int, name: str) -> bool:
    return bool(mask >> BIT[name] & 1)


def _focus_shapes(ox: int, oy: int) -> dict[str, tuple[str, tuple]]:
    """Where each control sits, for the ring drawn round the one in use."""
    width = SIZE[0]
    fx, fy = ox + 745, oy + 95
    face = {"Y": (0, -1), "X": (-1, 0), "B": (1, 0), "A": (0, 1)}
    shapes: dict[str, tuple[str, tuple]] = {
        "LS": ("circle", ((ox + 145, oy + 105), STICK_R + 7)),
        "RS": ("circle", ((ox + 585, oy + 145), STICK_R + 7)),
        "LT": ("rect", (ox + 12, oy + 34, 34, 172)),
        "RT": ("rect", (ox + width - 46, oy + 34, 34, 172)),
        "LB": ("rect", (ox + 64, oy + 4, 162, 36)),
        "RB": ("rect", (ox + width - 226, oy + 4, 162, 36)),
        "Back": ("circle", ((ox + 395, oy + 80), 19)),
        "Start": ("circle", ((ox + 485, oy + 80), 19)),
        "D-pad": ("rect", (ox + 257, oy + 107, 86, 86)),
    }
    for name, (dx, dy) in face.items():
        shapes[name] = ("circle", ((fx + dx * FACE_GAP, fy + dy * FACE_GAP), FACE_R + 6))
    for name in ("Up", "Down", "Left", "Right"):
        shapes[name] = shapes["D-pad"]
    return shapes


def draw_controller(
    surface: pygame.Surface,
    origin: tuple[int, int],
    snap: Snapshot,
    focus: str | None = None,
) -> None:
    """Draw the controller. `focus` names a control, such as "RS" or "A", to ring."""
    ox, oy = origin
    width, height = SIZE
    panel(surface, (ox, oy, width, height))
    lx, ly, rx, ry, lt, rt, mask = snap

    _trigger(surface, pygame.Rect(ox + 18, oy + 40, 22, 160), lt, "LT")
    _trigger(surface, pygame.Rect(ox + width - 40, oy + 40, 22, 160), rt, "RT")
    _bumper(surface, pygame.Rect(ox + 70, oy + 10, 150, 24), "LB", _pressed(mask, "LB"))
    _bumper(surface, pygame.Rect(ox + width - 220, oy + 10, 150, 24), "RB", _pressed(mask, "RB"))

    _stick(surface, (ox + 145, oy + 105), lx, ly, "LS", _pressed(mask, "LS"))
    _stick(surface, (ox + 585, oy + 145), rx, ry, "RS", _pressed(mask, "RS"))
    _dpad(surface, (ox + 300, oy + 150), mask)
    _small(surface, (ox + 395, oy + 80), "View", _pressed(mask, "Back"))
    _small(surface, (ox + 485, oy + 80), "Menu", _pressed(mask, "Start"))
    _face(surface, (ox + 745, oy + 95), mask)

    shape = _focus_shapes(ox, oy).get(focus) if focus else None
    if shape is not None:
        kind, args = shape
        if kind == "circle":
            pygame.draw.circle(surface, FOCUS, args[0], args[1], 3)
        else:
            pygame.draw.rect(surface, FOCUS, args, 3, border_radius=10)


def _stick(
    surface: pygame.Surface,
    centre: tuple[int, int],
    x: int,
    y: int,
    name: str,
    clicked: bool,
) -> None:
    cx, cy = centre
    pygame.draw.circle(surface, GREEN if clicked else BG, centre, STICK_R)
    pygame.draw.circle(surface, GREY, centre, STICK_R, 2)
    pygame.draw.line(surface, GREY, (cx - STICK_R, cy), (cx + STICK_R, cy))
    pygame.draw.line(surface, GREY, (cx, cy - STICK_R), (cx, cy + STICK_R))
    dot = (cx + round(x / STICK_SCALE * STICK_R), cy + round(y / STICK_SCALE * STICK_R))
    pygame.draw.circle(surface, WHITE if clicked else GREEN, dot, 7)
    _centred(surface, name, (cx, cy + STICK_R + 12), 20, GREY)


def _trigger(surface: pygame.Surface, rect: pygame.Rect, value: int, name: str) -> None:
    pygame.draw.rect(surface, BG, rect, border_radius=4)
    filled = round(rect.height * max(0, min(value, TRIGGER_MAX)) / TRIGGER_MAX)
    if filled:
        pygame.draw.rect(
            surface, GREEN, (rect.x, rect.bottom - filled, rect.width, filled), border_radius=4
        )
    _centred(surface, name, (rect.centerx, rect.y - 12), 20, GREY)


def _bumper(surface: pygame.Surface, rect: pygame.Rect, name: str, pressed: bool) -> None:
    pygame.draw.rect(surface, GREEN if pressed else BG, rect, border_radius=12)
    pygame.draw.rect(surface, GREY, rect, 1, border_radius=12)
    _centred(surface, name, rect.center, 20, BG if pressed else WHITE)


def _small(surface: pygame.Surface, centre: tuple[int, int], name: str, pressed: bool) -> None:
    pygame.draw.circle(surface, GREEN if pressed else BG, centre, 13)
    pygame.draw.circle(surface, GREY, centre, 13, 1)
    _centred(surface, name, (centre[0], centre[1] + 26), 18, GREY)


def _face(surface: pygame.Surface, centre: tuple[int, int], mask: int) -> None:
    cx, cy = centre
    spots = {"Y": (0, -1), "X": (-1, 0), "B": (1, 0), "A": (0, 1)}
    for name, (dx, dy) in spots.items():
        pos = (cx + dx * FACE_GAP, cy + dy * FACE_GAP)
        colour = FACE_COLOURS[name]
        if _pressed(mask, name):
            pygame.draw.circle(surface, colour, pos, FACE_R)
            _centred(surface, name, pos, 26, DARK)
        else:
            pygame.draw.circle(surface, DARK, pos, FACE_R)
            pygame.draw.circle(surface, colour, pos, FACE_R, 2)
            _centred(surface, name, pos, 26, colour)


def _dpad(surface: pygame.Surface, centre: tuple[int, int], mask: int) -> None:
    cx, cy = centre
    arm, half = 24, 11
    arms = {
        "Up": (cx - half, cy - half - arm, 2 * half, arm),
        "Down": (cx - half, cy + half, 2 * half, arm),
        "Left": (cx - half - arm, cy - half, arm, 2 * half),
        "Right": (cx + half, cy - half, arm, 2 * half),
    }
    pygame.draw.rect(surface, BG, (cx - half, cy - half, 2 * half, 2 * half))
    for name, rect in arms.items():
        pygame.draw.rect(surface, GREEN if _pressed(mask, name) else BG, rect, border_radius=3)
        pygame.draw.rect(surface, GREY, rect, 1, border_radius=3)
