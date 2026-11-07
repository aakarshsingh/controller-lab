"""Live controller view: two stick circles, two trigger bars and button lights."""

from __future__ import annotations

import pygame

from controller_lab.source import BUTTON_NAMES, Snapshot
from controller_lab.ui.draw import BG, GREEN, GREY, WHITE, panel, text

SIZE = (880, 220)
STICK_R = 80
STICK_SCALE = 32768
TRIGGER_MAX = 32767
BUTTON_ROWS = (
    ("LB", "RB", "Back", "Start"),
    ("X", "Y", "A", "B"),
    ("Up", "Down", "Left", "Right"),
    ("LS", "RS"),
)
BUTTON_SIZE = (82, 34)
BUTTON_STEP = (90, 44)
BIT = {name: i for i, name in enumerate(BUTTON_NAMES)}


def draw_controller(surface: pygame.Surface, origin: tuple[int, int], snap: Snapshot) -> None:
    ox, oy = origin
    width, height = SIZE
    panel(surface, (ox, oy, width, height))
    lx, ly, rx, ry, lt, rt, mask = snap
    _stick(surface, (ox + 110, oy + 110), lx, ly, "LS")
    _stick(surface, (ox + width - 110, oy + 110), rx, ry, "RS")
    _trigger(surface, pygame.Rect(ox + 215, oy + 30, 24, 160), lt, "LT")
    _trigger(surface, pygame.Rect(ox + width - 239, oy + 30, 24, 160), rt, "RT")
    _buttons(surface, (ox + 264, oy + 27), mask)


def _stick(surface: pygame.Surface, centre: tuple[int, int], x: int, y: int, name: str) -> None:
    cx, cy = centre
    pygame.draw.circle(surface, BG, centre, STICK_R)
    pygame.draw.circle(surface, GREY, centre, STICK_R, 2)
    pygame.draw.line(surface, GREY, (cx - STICK_R, cy), (cx + STICK_R, cy))
    pygame.draw.line(surface, GREY, (cx, cy - STICK_R), (cx, cy + STICK_R))
    dot = (cx + round(x / STICK_SCALE * STICK_R), cy + round(y / STICK_SCALE * STICK_R))
    pygame.draw.circle(surface, GREEN, dot, 7)
    text(surface, name, (cx - 10, cy + STICK_R + 6), 20, GREY)


def _trigger(surface: pygame.Surface, rect: pygame.Rect, value: int, name: str) -> None:
    pygame.draw.rect(surface, BG, rect)
    filled = round(rect.height * max(0, min(value, TRIGGER_MAX)) / TRIGGER_MAX)
    if filled:
        pygame.draw.rect(surface, GREEN, (rect.x, rect.bottom - filled, rect.width, filled))
    text(surface, name, (rect.x, rect.bottom + 6), 20, GREY)


def _buttons(surface: pygame.Surface, origin: tuple[int, int], mask: int) -> None:
    x0, y0 = origin
    for row, names in enumerate(BUTTON_ROWS):
        for col, name in enumerate(names):
            x, y = x0 + col * BUTTON_STEP[0], y0 + row * BUTTON_STEP[1]
            pressed = bool(mask >> BIT[name] & 1)
            panel(surface, (x, y, *BUTTON_SIZE), GREEN if pressed else BG)
            text(surface, name, (x + 10, y + 9), 22, BG if pressed else WHITE)
