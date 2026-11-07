"""Phase 1 spike: detection, live raw values, rig check and report timing."""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime

import pygame
from pygame._sdl2 import controller

from controller_lab import analysis, devtree, rig
from controller_lab.analysis import TimingStats
from controller_lab.detect import ControllerInfo, controller_info
from controller_lab.devtree import DeviceInfo
from controller_lab.rig import RigResult
from controller_lab.sampler import Recorder
from controller_lab.source import AXIS_NAMES, BUTTON_NAMES, Gamepad, Snapshot
from controller_lab.ui.draw import BG, GREEN, GREY, RED, WHITE, panel, text

WINDOW = (960, 600)
FRAME_NS = 1_000_000_000 // 60
INFO_REFRESH_NS = 1_000_000_000
TIMING_NS = 15 * 1_000_000_000
NO_MOTION_NS = 30 * 1_000_000_000
MIN_MOVING_S = 10.0
PROGRESS_NS = 250_000_000
PROGRESS_RECT = pygame.Rect(40, 300, 880, 30)
MOTION_LIMIT = analysis.MOTION_RADIUS * analysis.AXIS_MAX
WAITING_TEXT = "Connect a controller via USB or Bluetooth."
ONE_ONLY_TEXT = "Connect only one controller."


class App:
    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self.running = True
        self.pending: list[pygame.event.Event] = []
        self.gamepad: Gamepad | None = None
        self.info: ControllerInfo | None = None
        self.devtree_ms = 0.0
        self.connected_at = datetime.now(UTC)
        self.too_many = False
        self.snap: Snapshot | None = None
        self.error = ""
        self.rig_result: RigResult | None = None
        self.timing_result: tuple[TimingStats, int] | None = None
        self.timing_note = ""
        self.recorder: Recorder | None = None
        self.timing_start = 0
        self.motion_ns: int | None = None
        self.last_frame = 0
        self.last_progress = 0
        self.last_info = 0

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

    # ---- events ----------------------------------------------------------

    def handle(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_ESCAPE:
                self.running = False
            elif event.key == pygame.K_t and self.gamepad is not None and self.recorder is None:
                self.start_timing()
        elif event.type == pygame.CONTROLLERDEVICEADDED:
            if controller.get_count() > 1:
                self.block_extra()
            elif self.gamepad is None:
                self.connect(event.device_index)
        elif event.type == pygame.CONTROLLERDEVICEREMOVED:
            if self.gamepad is not None and event.instance_id == self.gamepad.instance_id:
                self.disconnect()
            elif self.too_many and controller.get_count() <= 1:
                self.too_many = False
                if controller.get_count() == 1:
                    self.connect(0)

    def block_extra(self) -> None:
        """More than one controller is attached: test nothing until one is left."""
        self.disconnect()
        self.too_many = True

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
        self.rig_result = None
        self.timing_result = None
        self.timing_note = ""
        self.render_message("Rig check running for 5 s. Leave the controller alone.")
        self.rig_result = rig.run_rig(self.rig_pass)

    def rig_pass(self) -> None:
        self.pending.extend(pygame.event.get())
        self.read()

    def disconnect(self) -> None:
        if self.gamepad is not None:
            self.gamepad.close()
        self.gamepad = self.info = self.snap = None
        self.recorder = None
        self.rig_result = self.timing_result = None
        self.timing_note = ""

    # ---- report timing ---------------------------------------------------

    def start_timing(self) -> None:
        self.recorder = Recorder(timing=True)
        self.timing_start = self.last_progress = time.perf_counter_ns()
        self.motion_ns = None
        self.timing_note = ""
        self.screen.fill(BG)
        text(self.screen, "Report timing", (40, 40), 40)
        text(self.screen, "Push the left stick past half way to start,", (40, 100), 30)
        text(self.screen, "then spin it continuously for 15 s.", (40, 135), 30)
        text(self.screen, "Only this bar refreshes while measuring.", (40, 175), 22, GREY)
        panel(self.screen, PROGRESS_RECT)
        pygame.display.flip()

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
            panel(self.screen, done, GREEN)
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

    def render_message(self, message: str) -> None:
        self.render(time.perf_counter_ns())
        panel(self.screen, (40, 520, 880, 50))
        text(self.screen, message, (55, 535), 26)
        pygame.display.flip()

    def render(self, now: int) -> None:
        s = self.screen
        s.fill(BG)
        text(s, "Controller Lab", (40, 30), 40)
        if self.gamepad is None or self.info is None:
            text(s, ONE_ONLY_TEXT if self.too_many else WAITING_TEXT, (40, 260), 36)
            if self.error:
                text(s, self.error, (40, 320), 24, RED)
            text(s, "Esc  quit", (40, 560), 22, GREY)
            pygame.display.flip()
            return

        if now - self.last_info >= INFO_REFRESH_NS:
            self.refresh_info()
        info = self.info
        fields = [
            ("Name", info.name),
            ("Type", info.type),
            ("Connection", info.connection),
            ("Battery", info.battery),
            ("Ids", info.vendor_product),
            ("Device key", info.device_key or "none"),
            ("Source", f"{info.identity_source}   read {self.devtree_ms:.2f} ms"),
            ("Backend", info.backend),
            ("GUID", info.guid),
        ]
        for i, (label, value) in enumerate(fields):
            text(s, label, (40, 80 + i * 25), 22, GREY)
            text(s, value, (170, 80 + i * 25), 22)

        if self.snap is not None:
            for i, name in enumerate(AXIS_NAMES):
                text(s, name, (620, 80 + i * 25), 22, GREY)
                text(s, f"{self.snap[i]:>7}", (670, 80 + i * 25), 22)
            mask = self.snap[6]
            for i, name in enumerate(BUTTON_NAMES):
                pressed = bool(mask >> i & 1)
                x, y = 40 + (i % 7) * 125, 315 + (i // 7) * 34
                panel(s, (x, y, 115, 28), GREEN if pressed else (34, 37, 43))
                text(s, name, (x + 10, y + 5), 22, BG if pressed else WHITE)

        y = 395
        if self.rig_result is not None:
            r = self.rig_result
            colour = GREEN if r.ok else RED
            text(
                s,
                f"Rig  median {r.median_ms:.3f} ms   p99 {r.p99_ms:.3f} ms   "
                f"sleep {r.sleep_ms:.3f} ms   slow passes {r.slow_passes}   "
                f"ok {r.ok}   spin-wait {rig.SPIN_WAIT}",
                (40, y),
                22,
                colour,
            )
        if self.timing_result is not None:
            st, n_gaps = self.timing_result
            text(
                s,
                f"Timing  {st.rate_hz:.1f} Hz   mean {st.mean_ms:.2f} ms   "
                f"jitter {st.jitter_ms:.2f} ms   p99 {st.p99_ms:.2f} ms",
                (40, y + 28),
                22,
            )
            text(
                s,
                f"        gaps >30 ms {st.gaps_over_30ms}   max gap {st.max_gap_ms:.2f} ms   "
                f"intervals {st.n}   tool gaps {n_gaps}",
                (40, y + 52),
                22,
            )
            text(
                s,
                f"        moving {st.moving_s:.1f} s   still intervals {st.still_count}",
                (40, y + 76),
                22,
            )
            text(
                s,
                "Measured from state changes Windows delivers, not true latency.",
                (40, y + 102),
                20,
                GREY,
            )
        if self.timing_note:
            text(s, self.timing_note, (40, 525), 24, RED)
        text(s, "T  report timing (15 s)     Esc  quit", (40, 560), 22, GREY)
        pygame.display.flip()


def run() -> None:
    pygame.init()
    controller.init()
    screen = pygame.display.set_mode(WINDOW)
    pygame.display.set_caption("Controller Lab")
    try:
        App(screen).loop()
    finally:
        pygame.quit()
