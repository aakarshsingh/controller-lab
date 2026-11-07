"""Bare-bones drawing helpers."""

from __future__ import annotations

import pygame

WHITE = (230, 230, 230)
GREY = (120, 120, 120)
GREEN = (90, 200, 110)
RED = (220, 90, 90)
BG = (20, 22, 26)
PANEL = (34, 37, 43)

_fonts: dict[int, pygame.font.Font] = {}


def _font(size: int) -> pygame.font.Font:
    if size not in _fonts:
        _fonts[size] = pygame.font.Font(None, size)
    return _fonts[size]


def text(
    surface: pygame.Surface,
    s: str,
    pos: tuple[int, int],
    size: int = 24,
    colour: tuple[int, int, int] = WHITE,
) -> pygame.Rect:
    image = _font(size).render(s, True, colour)
    return surface.blit(image, pos)


def panel(
    surface: pygame.Surface,
    rect: pygame.Rect | tuple[int, int, int, int],
    colour: tuple[int, int, int] = PANEL,
) -> pygame.Rect:
    return pygame.draw.rect(surface, colour, rect, border_radius=6)
