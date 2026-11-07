"""Reads one controller through the SDL GameController API."""

from __future__ import annotations

import pygame
from pygame._sdl2.controller import Controller

AXES = (
    pygame.CONTROLLER_AXIS_LEFTX,
    pygame.CONTROLLER_AXIS_LEFTY,
    pygame.CONTROLLER_AXIS_RIGHTX,
    pygame.CONTROLLER_AXIS_RIGHTY,
    pygame.CONTROLLER_AXIS_TRIGGERLEFT,
    pygame.CONTROLLER_AXIS_TRIGGERRIGHT,
)
AXIS_NAMES = ("LX", "LY", "RX", "RY", "LT", "RT")

# Bit i of the button mask is BUTTONS[i]. Guide is left out: it opens Game Bar.
BUTTONS = (
    pygame.CONTROLLER_BUTTON_A,
    pygame.CONTROLLER_BUTTON_B,
    pygame.CONTROLLER_BUTTON_X,
    pygame.CONTROLLER_BUTTON_Y,
    pygame.CONTROLLER_BUTTON_BACK,
    pygame.CONTROLLER_BUTTON_START,
    pygame.CONTROLLER_BUTTON_LEFTSTICK,
    pygame.CONTROLLER_BUTTON_RIGHTSTICK,
    pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
    pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
    pygame.CONTROLLER_BUTTON_DPAD_UP,
    pygame.CONTROLLER_BUTTON_DPAD_DOWN,
    pygame.CONTROLLER_BUTTON_DPAD_LEFT,
    pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
)
BUTTON_NAMES = (
    "A",
    "B",
    "X",
    "Y",
    "Back",
    "Start",
    "LS",
    "RS",
    "LB",
    "RB",
    "Up",
    "Down",
    "Left",
    "Right",
)

# (lx, ly, rx, ry, lt, rt, buttons)
Snapshot = tuple[int, int, int, int, int, int, int]


class Gamepad:
    """One open controller. Named Gamepad so it doesn't clash with pygame's Controller."""

    def __init__(self, device_index: int) -> None:
        self._ctrl = Controller(device_index)
        self._joy = self._ctrl.as_joystick()
        self.name: str = self._ctrl.name
        self.guid: str = self._joy.get_guid()
        self.instance_id: int = self._joy.get_instance_id()

    @property
    def power_level(self) -> str:
        return self._joy.get_power_level()

    def snapshot(self) -> Snapshot:
        get_axis, get_button = self._ctrl.get_axis, self._ctrl.get_button
        mask = 0
        for bit, button in enumerate(BUTTONS):
            if get_button(button):
                mask |= 1 << bit
        lx, ly, rx, ry, lt, rt = (get_axis(a) for a in AXES)
        return (lx, ly, rx, ry, lt, rt, mask)

    def rumble(self, low: float, high: float, ms: int) -> bool:
        return self._ctrl.rumble(low, high, ms)

    def close(self) -> None:
        self._ctrl.quit()
