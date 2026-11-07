"""Main loop and screen state machine.

The report timing spike stays behind `T` on the Label screen until the check
framework replaces it.
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import pygame
from pygame._sdl2 import controller

from controller_lab import analysis, devtree, history, rig
from controller_lab.analysis import TimingStats
from controller_lab.detect import ControllerInfo, controller_info
from controller_lab.devtree import DeviceInfo
from controller_lab.rig import RigResult
from controller_lab.sampler import Recorder
from controller_lab.source import Gamepad, Snapshot
from controller_lab.ui import screens
from controller_lab.ui.draw import GREEN, panel

WINDOW = (960, 600)
FRAME_NS = 1_000_000_000 // 60
INFO_REFRESH_NS = 1_000_000_000
TIMING_NS = 15 * 1_000_000_000
NO_MOTION_NS = 30 * 1_000_000_000
MIN_MOVING_S = 10.0
PROGRESS_NS = 250_000_000
PROGRESS_RECT = pygame.Rect(40, 300, 880, 30)
MOTION_LIMIT = analysis.MOTION_RADIUS * analysis.AXIS_MAX
QUIT_CONFIRM_NS = 3 * 1_000_000_000
SUITES = ("full", "connection")


class Screen(StrEnum):
    WAITING = "waiting"
    ONE_ONLY = "one_only"
    LABEL = "label"
    NEW_LABEL = "new_label"
    SUITE = "suite"
    SETUP = "setup"
    RIG = "rig"
    RESULTS = "results"


# B and Backspace go back one screen before the checks start.
BACK = {Screen.SUITE: Screen.LABEL, Screen.SETUP: Screen.SUITE}

KEY_ACTIONS = {
    pygame.K_SPACE: "continue",
    pygame.K_RETURN: "continue",
    pygame.K_KP_ENTER: "continue",
    pygame.K_UP: "up",
    pygame.K_DOWN: "down",
    pygame.K_BACKSPACE: "back",
    pygame.K_s: "skip",
    pygame.K_r: "redo",
    pygame.K_n: "next",
    pygame.K_t: "timing",
}
BUTTON_ACTIONS = {
    pygame.CONTROLLER_BUTTON_A: "continue",
    pygame.CONTROLLER_BUTTON_B: "back",
    pygame.CONTROLLER_BUTTON_DPAD_UP: "up",
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
}
# A press made during the rig check must not act on the screen that follows it.
PRESS_EVENTS = (pygame.KEYDOWN, pygame.CONTROLLERBUTTONDOWN, pygame.TEXTINPUT)


class App:
    def __init__(self, surface: pygame.Surface) -> None:
        self.surface = surface
        self.running = True
        self.state = Screen.WAITING
        self.pending: list[pygame.event.Event] = []
        self.gamepad: Gamepad | None = None
        self.info: ControllerInfo | None = None
        self.devtree_ms = 0.0
        self.connected_at = datetime.now(UTC)
        self.snap: Snapshot | None = None
        self.error = ""
        self.quit_asked_at: int | None = None
        # Label, New label and Suite
        self.labels: list[tuple[str, datetime]] = []
        self.preselected: str | None = None
        self.cursor = 0
        self.suite = SUITES[0]
        self.label = ""
        self.typed = ""
        self.suggestion = ""
        self.label_error = ""
        # Rig and the saved run
        self.rig_result: RigResult | None = None
        self.run_dir: Path | None = None
        self.save_error = ""
        # Report timing spike
        self.timing_result: tuple[TimingStats, int] | None = None
        self.timing_note = ""
        self.recorder: Recorder | None = None
        self.timing_start = 0
        self.motion_ns: int | None = None
        self.last_frame = 0
        self.last_progress = 0
        self.last_info = 0
        pygame.key.stop_text_input()

    # ---- main loop -------------------------------------------------------

    def loop(self) -> None:
        prev = time.perf_counter_ns()
        while self.running:
            now = time.perf_counter_ns()
            if self.recorder is not None:
                self.recorder.mark_pass(now - prev, now)
            prev = now

            events, self.pending = self.pending + pygame.event.get(), []
            for event in events:
                self.handle(event)

            if self.state == Screen.RIG:
                self.run_rig()
                prev = time.perf_counter_ns()
                continue

            if self.gamepad is not None:
                t = time.perf_counter_ns()
                self.snap = self.read()
                if self.recorder is not None and self.snap is not None:
                    self.recorder.add(t, self.snap)
                    if self.motion_ns is None and math.hypot(*self.snap[:2]) > MOTION_LIMIT:
                        self.motion_ns = t

            if self.recorder is not None:
                self.tick_timing(now)
            elif now - self.last_frame >= FRAME_NS:
                self.last_frame = now
                self.render(now)

            rig.pace(now)

    def read(self) -> Snapshot | None:
        try:
            return self.gamepad.snapshot() if self.gamepad else None
        except pygame.error:
            return None

    def go(self, state: Screen) -> None:
        if state == Screen.NEW_LABEL:
            pygame.key.start_text_input()
        elif self.state == Screen.NEW_LABEL:
            pygame.key.stop_text_input()
        self.state = state
        self.last_frame = 0

    # ---- events ----------------------------------------------------------

    def handle(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            self.handle_key(event.key)
        elif event.type == pygame.TEXTINPUT:
            if self.state == Screen.NEW_LABEL:
                self.typed = (self.typed + event.text)[: history.MAX_LABEL]
                self.label_error = ""
        elif event.type == pygame.MOUSEBUTTONDOWN:
            self.quit_asked_at = None
        elif event.type == pygame.CONTROLLERBUTTONDOWN:
            ours = self.gamepad is not None and event.instance_id == self.gamepad.instance_id
            if ours and not self.close_quit_confirm() and event.button in BUTTON_ACTIONS:
                self.act(BUTTON_ACTIONS[event.button])
        elif event.type == pygame.CONTROLLERDEVICEADDED:
            if controller.get_count() > 1:
                self.block_extra()
            elif self.gamepad is None:
                self.connect(event.device_index)
        elif event.type == pygame.CONTROLLERDEVICEREMOVED:
            if self.gamepad is not None and event.instance_id == self.gamepad.instance_id:
                self.disconnect()
                self.go(Screen.WAITING)
            elif self.state == Screen.ONE_ONLY and controller.get_count() <= 1:
                self.go(Screen.WAITING)
                if controller.get_count() == 1:
                    self.connect(0)

    def handle_key(self, key: int) -> None:
        if self.recorder is not None:
            if key == pygame.K_ESCAPE:
                self.cancel_timing()
        elif self.state == Screen.NEW_LABEL:
            self.edit_label(key)
        elif key == pygame.K_ESCAPE:
            self.ask_quit()
        elif not self.close_quit_confirm() and key in KEY_ACTIONS:
            self.act(KEY_ACTIONS[key])

    def ask_quit(self) -> None:
        """A first `Esc` asks, and a second one within 3 s quits."""
        now = time.perf_counter_ns()
        if self.quit_asked_at is not None and now - self.quit_asked_at <= QUIT_CONFIRM_NS:
            self.running = False
        else:
            self.quit_asked_at = now
        self.last_frame = 0

    def close_quit_confirm(self) -> bool:
        """Close the quit overlay. Return True when it was open, so the input is used up."""
        was_open = self.quit_asked_at is not None
        self.quit_asked_at = None
        if was_open:
            self.last_frame = 0
        return was_open

    def act(self, action: str) -> None:
        if self.recorder is not None:
            return
        state = self.state
        if action == "back" and state in BACK:
            self.go(BACK[state])
        elif state == Screen.LABEL:
            self.act_label(action)
        elif state == Screen.NEW_LABEL:
            if action == "continue":
                self.accept_label()
            elif action == "back":
                self.go(Screen.LABEL)
        elif state == Screen.SUITE:
            if action in ("up", "down"):
                step = 1 if action == "down" else -1
                self.suite = SUITES[max(0, min(len(SUITES) - 1, SUITES.index(self.suite) + step))]
            elif action == "continue":
                self.go(Screen.SETUP)
        elif state == Screen.SETUP and action == "continue":
            self.go(Screen.RIG)
        elif state == Screen.RESULTS and action in ("continue", "next"):
            self.test_another()

    def act_label(self, action: str) -> None:
        new_row = len(self.labels)
        if action == "up":
            self.cursor = max(0, self.cursor - 1)
        elif action == "down":
            self.cursor = min(new_row, self.cursor + 1)
        elif action == "timing" and self.gamepad is not None:
            self.start_timing()
        elif action == "continue":
            if self.cursor == new_row:
                self.suggestion = history.next_default_label()
                self.typed = self.label_error = ""
                self.go(Screen.NEW_LABEL)
            else:
                self.choose_label(self.labels[self.cursor][0])

    def edit_label(self, key: int) -> None:
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.accept_label()
        elif key == pygame.K_BACKSPACE:
            self.typed = self.typed[:-1]
            self.label_error = ""
        elif key == pygame.K_ESCAPE:
            self.go(Screen.LABEL)

    def accept_label(self) -> None:
        label = self.typed.strip() or self.suggestion
        # Windows folders ignore case, so reuse the saved spelling of a label.
        for saved, _ in self.labels:
            if saved.lower() == label.lower():
                label = saved
        error = history.validate_label(label)
        if error is not None:
            self.label_error = error
            return
        self.choose_label(label)

    def choose_label(self, label: str) -> None:
        self.label = label
        connection = self.info.connection if self.info is not None else "usb"
        self.suite = "full" if connection == "usb" else "connection"
        self.go(Screen.SUITE)

    def block_extra(self) -> None:
        """More than one controller is attached: test nothing until one is left."""
        self.disconnect()
        self.go(Screen.ONE_ONLY)

    def read_devices(self) -> list[DeviceInfo]:
        t0 = time.perf_counter_ns()
        devices = devtree.find_controllers()
        self.devtree_ms = (time.perf_counter_ns() - t0) / 1e6
        return devices

    def refresh_info(self) -> None:
        if self.gamepad is not None:
            self.info = controller_info(self.gamepad, self.read_devices(), self.connected_at)
            self.last_info = time.perf_counter_ns()

    def connect(self, device_index: int) -> None:
        try:
            self.gamepad = Gamepad(device_index)
        except pygame.error as exc:
            self.error = f"Could not open the controller: {exc}"
            return
        self.error = ""
        self.connected_at = datetime.now(UTC)
        self.refresh_info()
        assert self.info is not None
        self.labels = history.list_labels()
        self.preselected = history.label_for_device(self.info.device_key)
        names = [name for name, _ in self.labels]
        self.cursor = names.index(self.preselected) if self.preselected in names else len(names)
        self.suite = SUITES[0]
        self.rig_result = self.timing_result = None
        self.timing_note = ""
        self.go(Screen.LABEL)

    def disconnect(self) -> None:
        if self.gamepad is not None:
            self.gamepad.close()
        self.gamepad = self.info = self.snap = None
        self.recorder = None
        self.rig_result = self.timing_result = None
        self.timing_note = ""

    def test_another(self) -> None:
        self.disconnect()
        self.go(Screen.WAITING)
        count = controller.get_count()
        if count > 1:
            self.go(Screen.ONE_ONLY)
        elif count == 1:
            self.connect(0)

    # ---- rig and run -----------------------------------------------------

    def run_rig(self) -> None:
        screens.rig(self.surface)
        pygame.display.flip()
        self.rig_result = rig.run_rig(self.rig_pass)
        self.pending = [e for e in self.pending if e.type not in PRESS_EVENTS]
        self.save_run()

    def rig_pass(self) -> None:
        self.pending.extend(pygame.event.get())
        self.read()

    def save_run(self) -> None:
        """Save an empty run. Checks fill it in once they exist."""
        self.refresh_info()
        if self.info is None:
            self.go(Screen.WAITING)
            return
        try:
            self.run_dir = history.new_run_dir(self.label, self.info.connection)
            history.save_metrics(self.run_dir, {"label": self.label, **asdict(self.info)})
            self.save_error = ""
        except (OSError, ValueError) as exc:
            self.run_dir = None
            self.save_error = f"Could not save the run: {exc}"
        self.go(Screen.RESULTS)

    # ---- report timing spike ---------------------------------------------

    def start_timing(self) -> None:
        self.recorder = Recorder(timing=True)
        self.timing_start = self.last_progress = time.perf_counter_ns()
        self.motion_ns = None
        self.timing_note = ""
        screens.timing_prompt(self.surface, PROGRESS_RECT)
        pygame.display.flip()

    def cancel_timing(self) -> None:
        """`Esc` during the spike drops the measurement and returns to Label."""
        self.recorder = None
        self.timing_note = ""
        self.last_frame = 0

    def tick_timing(self, now: int) -> None:
        if self.motion_ns is None:
            if now - self.timing_start >= NO_MOTION_NS:
                self.recorder = None
                self.timing_result = None
                self.timing_note = "No movement, T to try again"
                self.last_frame = 0
            return
        elapsed = now - self.motion_ns
        if elapsed >= TIMING_NS:
            self.finish_timing()
            return
        if now - self.last_progress >= PROGRESS_NS:
            self.last_progress = now
            done = PROGRESS_RECT.copy()
            done.width = int(PROGRESS_RECT.width * elapsed / TIMING_NS)
            panel(self.surface, done, GREEN)
            pygame.display.update(PROGRESS_RECT)

    def finish_timing(self) -> None:
        rec, self.recorder = self.recorder, None
        assert rec is not None
        gaps = rec.tool_gaps
        samples = rec.samples
        stats = analysis.spin_timing(rec.t_ns, samples[:, 0], samples[:, 1], gaps, TIMING_NS)
        self.timing_result = (stats, len(gaps))
        moving = stats.moving_s
        self.timing_note = "Keep spinning, T to redo" if not moving >= MIN_MOVING_S else ""
        self.last_frame = 0

    # ---- drawing ---------------------------------------------------------

    def render(self, now: int) -> None:
        if self.gamepad is not None and now - self.last_info >= INFO_REFRESH_NS:
            self.refresh_info()
        if self.quit_asked_at is not None and now - self.quit_asked_at > QUIT_CONFIRM_NS:
            self.quit_asked_at = None
        s, state, info = self.surface, self.state, self.info
        if state == Screen.ONE_ONLY:
            screens.one_only(s)
        elif info is None or state == Screen.WAITING:
            screens.waiting(s, self.error)
        elif state == Screen.LABEL:
            screens.label(
                s,
                info,
                self.devtree_ms,
                self.labels,
                self.cursor,
                self.preselected,
                self.timing_result,
                self.timing_note,
            )
        elif state == Screen.NEW_LABEL:
            screens.new_label(s, self.typed, self.suggestion, self.label_error)
        elif state == Screen.SUITE:
            screens.suite(s, self.label, self.suite, info.connection)
        elif state == Screen.SETUP:
            screens.setup(s, self.label, self.snap)
        elif state == Screen.RESULTS:
            screens.results(s, self.label, self.run_dir, self.save_error, self.rig_result)
        if self.quit_asked_at is not None:
            screens.quit_overlay(s)
        pygame.display.flip()


def run() -> None:
    pygame.init()
    controller.init()
    surface = pygame.display.set_mode(WINDOW)
    pygame.display.set_caption("Controller Lab")
    try:
        App(surface).loop()
    finally:
        pygame.quit()
