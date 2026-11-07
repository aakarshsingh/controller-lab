"""Main loop and screen state machine."""

from __future__ import annotations

import math
import time
import webbrowser
from dataclasses import asdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import pygame
from pygame._sdl2 import controller

from controller_lab import devtree, history, report, rig, scoring
from controller_lab.checks import registry
from controller_lab.checks.base import INTRO_S, Check, CheckData, EndCondition, Outcome, Phase
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
PROGRESS_NS = 250_000_000
QUIT_CONFIRM_NS = 3 * 1_000_000_000
END_CARD_NS = 5 * 1_000_000_000
SUITES = registry.SUITES
SAMPLE_HEADER = "t_ns,lx,ly,rx,ry,lt,rt,buttons"
SAMPLE_WIDTH = 7
# What writing a report can raise: files, odd values in metrics.json, and matplotlib.
REPORT_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError)
DIFFERENT_CONTROLLER = "This looks like a different controller. Pick its label."


class Screen(StrEnum):
    WAITING = "waiting"
    ONE_ONLY = "one_only"
    LABEL = "label"
    NEW_LABEL = "new_label"
    SUITE = "suite"
    SETUP = "setup"
    RIG = "rig"
    CHECK = "check"
    CHECK_END = "check_end"
    FOCUS_LOST = "focus_lost"
    PAUSED = "paused"
    RESULTS = "results"


# A run is in progress in these states. Device events pause it instead of resetting.
RUN_STATES = {Screen.CHECK, Screen.CHECK_END, Screen.FOCUS_LOST, Screen.PAUSED}
# The device tree is never read in these states, so it can't delay sampling.
NO_DEVTREE = {Screen.RIG, Screen.CHECK, Screen.CHECK_END, Screen.FOCUS_LOST}
# Backspace goes back one screen before the checks start. B does too, and skips during them.
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
    pygame.K_c: "compare",
}
BUTTON_ACTIONS = {
    pygame.CONTROLLER_BUTTON_A: "continue",
    pygame.CONTROLLER_BUTTON_B: "b",
    pygame.CONTROLLER_BUTTON_DPAD_UP: "up",
    pygame.CONTROLLER_BUTTON_DPAD_DOWN: "down",
}
# A press made during the rig check must not act on the screen that follows it.
PRESS_EVENTS = (pygame.KEYDOWN, pygame.CONTROLLERBUTTONDOWN, pygame.TEXTINPUT)


def _clean(value: Any) -> Any:
    """Make metrics JSON-safe: NaN and infinity become None."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


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
        self.label_note = ""
        self.typed = ""
        self.suggestion = ""
        self.label_error = ""
        # The run
        self.rig_result: RigResult | None = None
        self.run_dir: Path | None = None
        self.run_info: ControllerInfo | None = None
        self.started_at = ""
        self.save_error = ""
        self.checks: list[Check] = []
        self.check_index = 0
        self.results: dict[str, dict[str, Any]] = {}
        self.connection_dropped = False
        self.profile: scoring.Profile | None = None
        self.score: scoring.RunScore | None = None
        # The check in progress
        self.recorder: Recorder | None = None
        self.phase_index = 0
        self.phase_starts: list[int] = []
        self.phase_end: EndCondition | None = None
        # A phase with an intro starts counting only once its get-ready banner is over.
        self.counting_from = 0
        self.counting_starts: list[int] = []
        self.phase_fired = False
        self.rumble_ok: dict[int, bool] = {}
        self.answers: dict[int, bool] = {}
        self.outcome: Outcome | None = None
        self.end_at = 0
        # Paused: why, and what to do once the controller is confirmed
        self.pause_reason = ""
        self.resume_redo = True
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

            if self.state == Screen.CHECK:
                self.tick_check(now)
            elif self.state == Screen.CHECK_END:
                self.tick_end_card(now)

            if self.state == Screen.CHECK and self.check.is_timing:
                self.render_timing(now)
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

    @property
    def check(self) -> Check:
        return self.checks[self.check_index]

    @property
    def phase(self) -> Phase:
        return self.check.phases[self.phase_index]

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
            self.close_quit_confirm()
        elif event.type == pygame.WINDOWFOCUSLOST:
            if self.state == Screen.CHECK:
                self.focus_lost()
        elif event.type == pygame.CONTROLLERBUTTONDOWN:
            ours = self.gamepad is not None and event.instance_id == self.gamepad.instance_id
            if ours and not self.close_quit_confirm() and event.button in BUTTON_ACTIONS:
                self.act(BUTTON_ACTIONS[event.button])
        elif event.type == pygame.CONTROLLERDEVICEADDED:
            self.on_added(event.device_index)
        elif event.type == pygame.CONTROLLERDEVICEREMOVED:
            self.on_removed(event.instance_id)

    def handle_key(self, key: int) -> None:
        if self.state == Screen.NEW_LABEL:
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
        state = self.state
        if state in RUN_STATES:
            self.act_run(action)
            return
        if action == "b":
            action = "back"
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
        elif state == Screen.RESULTS and action == "compare":
            self.compare()

    def act_run(self, action: str) -> None:
        state = self.state
        if state == Screen.CHECK and self.phase.answer and action in ("continue", "b", "back"):
            # In an answer phase, A or Enter means yes and B or Backspace means no.
            self.answers[self.phase_index] = action == "continue"
            self.next_phase(time.perf_counter_ns())
            return
        if action == "b":
            action = "skip"
        if state == Screen.CHECK:
            if action == "skip":
                self.recorder = None
                self.record(Outcome("skipped", message="Skipped"))
                self.advance()
            elif action == "redo":
                self.begin_check()
        elif state == Screen.CHECK_END:
            assert self.outcome is not None
            voided = self.outcome.status == "voided"
            if action == "redo" or (voided and action == "continue"):
                self.begin_check()
            elif action == "continue" or (voided and action == "skip"):
                self.advance()
        elif state == Screen.FOCUS_LOST:
            if action in ("continue", "redo"):
                self.begin_check()
            elif action == "skip":
                self.advance()
        elif state == Screen.PAUSED and self.pause_reason == "confirm":
            if action == "continue":
                self.resume()
            elif action == "skip":
                self.abandon_run(different=False)

    def act_label(self, action: str) -> None:
        new_row = len(self.labels)
        if action == "up":
            self.cursor = max(0, self.cursor - 1)
        elif action == "down":
            self.cursor = min(new_row, self.cursor + 1)
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
        self.label_note = ""
        connection = self.info.connection if self.info is not None else "usb"
        self.suite = "full" if connection == "usb" else "connection"
        self.go(Screen.SUITE)

    # ---- controllers -----------------------------------------------------

    def on_added(self, device_index: int) -> None:
        if self.state in RUN_STATES:
            if controller.get_count() > 1:
                self.interrupt("one_only")
            elif self.gamepad is None:
                self.reconnect(device_index)
        elif controller.get_count() > 1:
            self.block_extra()
        elif self.gamepad is None:
            self.connect(device_index)

    def on_removed(self, instance_id: int) -> None:
        ours = self.gamepad is not None and instance_id == self.gamepad.instance_id
        if self.state in RUN_STATES:
            if ours:
                self.interrupt("removed")
            elif self.gamepad is None and self.pause_reason == "one_only":
                count = controller.get_count()
                if count == 1:
                    self.reconnect(0)
                elif count == 0:
                    self.pause_reason = "removed"
                    self.last_frame = 0
        elif ours:
            self.disconnect()
            self.go(Screen.WAITING)
        elif self.state == Screen.ONE_ONLY and controller.get_count() <= 1:
            self.go(Screen.WAITING)
            if controller.get_count() == 1:
                self.connect(0)

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

    def open_gamepad(self, device_index: int) -> bool:
        try:
            self.gamepad = Gamepad(device_index)
        except pygame.error as exc:
            self.error = f"Could not open the controller: {exc}"
            return False
        self.error = ""
        self.connected_at = datetime.now(UTC)
        self.refresh_info()
        return self.info is not None

    def connect(self, device_index: int) -> None:
        if self.open_gamepad(device_index):
            self.label_note = ""
            self.enter_label()

    def enter_label(self) -> None:
        assert self.info is not None
        self.labels = history.list_labels()
        self.preselected = history.label_for_device(self.info.device_key)
        names = [name for name, _ in self.labels]
        self.cursor = names.index(self.preselected) if self.preselected in names else len(names)
        self.suite = SUITES[0]
        self.rig_result = None
        self.go(Screen.LABEL)

    def disconnect(self) -> None:
        if self.gamepad is not None:
            self.gamepad.close()
        self.gamepad = self.info = self.snap = None
        self.recorder = None

    def test_another(self) -> None:
        self.disconnect()
        self.go(Screen.WAITING)
        count = controller.get_count()
        if count > 1:
            self.go(Screen.ONE_ONLY)
        elif count == 1:
            self.connect(0)

    # ---- interruptions ---------------------------------------------------

    def interrupt(self, reason: str) -> None:
        """Pause the run. A check that was running starts again on resume."""
        if self.state != Screen.PAUSED:
            # After a finished check, resume moves on. Otherwise the check runs again.
            self.resume_redo = self.state != Screen.CHECK_END
        if self.state == Screen.CHECK:
            self.recorder = None
            run = self.run_info
            if reason == "removed" and run is not None and run.connection == "bluetooth":
                self.connection_dropped = True
                self.save_run_metrics()
        self.disconnect()
        self.pause_reason = reason
        self.go(Screen.PAUSED)

    def reconnect(self, device_index: int) -> None:
        """A controller came back during a run: wait for the run's connection, then check it."""
        if not self.open_gamepad(device_index):
            return
        assert self.info is not None and self.run_info is not None
        new, run = self.info, self.run_info
        if new.connection != run.connection:
            self.pause_reason = "other_connection"
        elif new.device_key and run.device_key and new.device_key != run.device_key:
            self.abandon_run(different=True)
            return
        else:
            self.pause_reason = "confirm"
        self.last_frame = 0

    def resume(self) -> None:
        self.pause_reason = ""
        if self.resume_redo:
            self.begin_check()
        else:
            self.advance()

    def abandon_run(self, different: bool) -> None:
        """Keep what the run saved, mark the interrupted check, and pick a label again."""
        if self.resume_redo and self.checks and self.check.id not in self.results:
            self.record(Outcome("interrupted", message="The controller disconnected"))
        self.save_run_metrics()
        self.checks = []
        self.pause_reason = ""
        self.label_note = DIFFERENT_CONTROLLER if different else ""
        self.enter_label()

    def focus_lost(self) -> None:
        self.recorder = None
        self.record(Outcome("voided", message="Focus was lost"))
        self.save_run_metrics()
        self.go(Screen.FOCUS_LOST)

    # ---- rig and run -----------------------------------------------------

    def run_rig(self) -> None:
        screens.rig(self.surface)
        pygame.display.flip()
        self.score = None
        try:
            ctype = self.info.type if self.info is not None else ""
            self.profile = scoring.load_profile(scoring.profile_for(ctype))
        except scoring.ProfileError as exc:
            self.profile = None
            self.run_dir = None
            self.save_error = f"Could not load the scoring profile: {exc}"
            self.go(Screen.RESULTS)
            return
        p = self.profile
        self.rig_result = rig.run_rig(self.rig_pass, p.loop_median_ms, p.loop_p99_ms)
        self.pending = [e for e in self.pending if e.type not in PRESS_EVENTS]
        self.start_run()

    def rig_pass(self) -> None:
        self.pending.extend(pygame.event.get())
        self.read()

    def start_run(self) -> None:
        self.refresh_info()
        if self.info is None:
            self.go(Screen.WAITING)
            return
        self.run_info = self.info
        self.checks = registry.suite(self.suite)
        self.check_index = 0
        self.results = {}
        self.connection_dropped = False
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        try:
            self.run_dir = history.new_run_dir(self.label, self.info.connection)
            self.save_error = ""
        except (OSError, ValueError) as exc:
            self.run_dir = None
            self.save_error = f"Could not save the run: {exc}"
            self.go(Screen.RESULTS)
            return
        self.save_run_metrics()
        if self.checks:
            self.begin_check()
        else:
            self.go(Screen.RESULTS)

    def begin_check(self) -> None:
        check = self.check
        if check.is_timing and not (self.rig_result is not None and self.rig_result.ok):
            self.record(Outcome("not_tested", message="This PC's polling loop was too slow"))
            self.save_run_metrics()
            self.advance()
            return
        self.results.pop(check.id, None)
        self.recorder = Recorder(timing=check.is_timing)
        self.phase_index = 0
        self.phase_starts = []
        self.counting_starts = []
        self.rumble_ok = {}
        self.answers = {}
        self.start_phase(time.perf_counter_ns())
        self.outcome = None
        self.go(Screen.CHECK)

    def tick_check(self, now: int) -> None:
        if now < self.counting_from:
            return
        phase, elapsed = self.phase, now - self.counting_from
        if not self.phase_fired:
            self.phase_fired = True
            if phase.rumble is not None:
                self.rumble_ok[self.phase_index] = self.start_rumble(*phase.rumble)
        snap = self.snap
        ended = snap is not None and (
            (phase.end is not None and phase.end(snap))
            or (self.phase_end is not None and self.phase_end(now, snap))
        )
        if ended or (phase.duration_s is not None and elapsed >= phase.duration_s * 1e9):
            self.next_phase(now)
        elif elapsed >= phase.timeout_s * 1e9:
            if phase.timeout_ends_check:
                self.finish_check(now, timed_out=True)
            else:
                self.next_phase(now)

    def next_phase(self, now: int) -> None:
        self.phase_index += 1
        if self.phase_index == len(self.check.phases):
            self.finish_check(now, timed_out=False)
        else:
            self.start_phase(now)
            self.last_frame = 0

    def start_phase(self, now: int) -> None:
        """Begin the current phase, with a fresh end condition when it counts something."""
        self.phase_starts.append(now)
        self.counting_from = now + (int(INTRO_S * 1e9) if self.phase.intro else 0)
        self.counting_starts.append(self.counting_from)
        self.phase_fired = False
        until = self.phase.until
        self.phase_end = until(self.counting_from) if until is not None else None

    def start_rumble(self, low: float, high: float, ms: int) -> bool:
        """Run the body motors. Returns what SDL reports, or False when it can't."""
        if self.gamepad is None:
            return False
        try:
            return bool(self.gamepad.rumble(low, high, ms))
        except pygame.error:
            return False

    def finish_check(self, now: int, timed_out: bool) -> None:
        rec, self.recorder = self.recorder, None
        assert rec is not None
        data = CheckData(
            t_ns=rec.t_ns,
            samples=rec.samples.reshape(-1, SAMPLE_WIDTH),
            tool_gaps=rec.tool_gaps,
            phase_starts_ns=tuple(self.phase_starts),
            end_ns=now,
            timed_out=timed_out,
            counting_starts_ns=tuple(self.counting_starts),
            rumble_ok=tuple(self.rumble_ok.get(i) for i in range(len(self.check.phases))),
            answers=tuple(self.answers.get(i) for i in range(len(self.check.phases))),
        )
        self.outcome = self.check.analyse(data)
        self.save_samples(data)
        self.record(self.outcome)
        self.save_run_metrics()
        self.end_at = now
        self.go(Screen.CHECK_END)

    def tick_end_card(self, now: int) -> None:
        voided = self.outcome is not None and self.outcome.status == "voided"
        if not voided and now - self.end_at >= END_CARD_NS:
            self.advance()

    def advance(self) -> None:
        self.check_index += 1
        if self.check_index < len(self.checks):
            self.begin_check()
        else:
            self.finish_run()

    def finish_run(self) -> None:
        """Score the run, save it with its score, and show Results."""
        if self.profile is not None and self.run_info is not None:
            data = _clean(self.run_data())
            # Bluetooth timing is judged against the other controllers on this PC.
            bt = self.run_info.connection == "bluetooth"
            runs = history.load_runs() if bt else None
            self.score = scoring.score_run(data, self.profile, runs)
        self.save_run_metrics()
        if self.run_dir is not None:
            self.show_writing("Writing report…")
            try:
                report.write_report(self.run_dir)
            except REPORT_ERRORS as exc:  # a broken plot must not lose the run
                self.save_error = f"Could not write the report: {exc}"
        self.go(Screen.RESULTS)

    def show_writing(self, message: str) -> None:
        screens.writing(self.surface, message)
        pygame.display.flip()

    def compare(self) -> None:
        """Write compare.html from every run on this PC and open it in the browser."""
        self.show_writing("Writing compare…")
        try:
            path = report.write_compare(history.results_root())
            webbrowser.open(path.as_uri())
        except REPORT_ERRORS as exc:  # show it on Results rather than crash
            self.save_error = f"Could not write the comparison: {exc}"
        self.last_frame = 0

    def record(self, outcome: Outcome) -> None:
        check = self.check
        self.results[check.id] = {
            "title": check.title,
            "status": outcome.status,
            "message": outcome.message,
            "metrics": outcome.metrics,
        }

    def save_samples(self, data: CheckData) -> None:
        if self.run_dir is None:
            return
        path = self.run_dir / "samples" / f"{self.check.id}.csv"
        table = np.column_stack([data.t_ns, data.samples]).astype(np.int64)
        try:
            path.parent.mkdir(exist_ok=True)
            np.savetxt(path, table, fmt="%d", delimiter=",", header=SAMPLE_HEADER, comments="")
        except OSError as exc:
            self.save_error = f"Could not save the samples: {exc}"

    def run_data(self) -> dict[str, Any]:
        assert self.run_info is not None
        return {
            "label": self.label,
            **asdict(self.run_info),
            "suite": self.suite,
            "started_at": self.started_at,
            "rig": asdict(self.rig_result) if self.rig_result is not None else None,
            "connection_dropped": self.connection_dropped,
            "checks": self.results,
        }

    def save_run_metrics(self) -> None:
        if self.run_dir is None or self.run_info is None:
            return
        data = self.run_data()
        if self.profile is not None:
            data["profile"] = self.profile.key
            data["profile_version"] = self.profile.version
        if self.score is not None:
            data["score"] = self.score.to_dict()
        try:
            history.save_metrics(self.run_dir, _clean(data))
        except OSError as exc:
            self.save_error = f"Could not save the run: {exc}"

    # ---- drawing ---------------------------------------------------------

    def phase_progress(self, now: int) -> tuple[float, float]:
        """Seconds left in the phase, and the fraction done."""
        phase = self.phase
        limit = phase.duration_s if phase.duration_s is not None else phase.timeout_s
        elapsed = max(0, now - self.counting_from) / 1e9
        return max(0.0, limit - elapsed), min(1.0, elapsed / limit)

    def render_timing(self, now: int) -> None:
        """During a timing check, draw once, then refresh only the progress bar."""
        if self.quit_asked_at is not None and now - self.quit_asked_at > QUIT_CONFIRM_NS:
            self.quit_asked_at = None
            self.last_frame = 0
        if self.last_frame == 0:
            self.last_frame = self.last_progress = now
            self.draw_check(now)
            if self.quit_asked_at is not None:
                screens.quit_overlay(self.surface)
            pygame.display.flip()
        elif now - self.last_progress >= PROGRESS_NS:
            self.last_progress = now
            # Only the spin phase fills the bar. While waiting for movement it stays empty.
            fraction = self.phase_progress(now)[1] if self.phase.duration_s else 0.0
            done = screens.PROGRESS_RECT.copy()
            done.width = int(done.width * fraction)
            if done.width:
                panel(self.surface, done, GREEN)
            pygame.display.update(screens.PROGRESS_RECT)

    def draw_check(self, now: int) -> None:
        left, fraction = self.phase_progress(now)
        screens.check(
            self.surface,
            self.check,
            self.phase,
            self.check_index,
            len(self.checks),
            self.phase_index,
            left,
            fraction,
            max(0, self.counting_from - now) / 1e9,
            max(0, now - self.counting_from) / 1e9,
            None if self.check.is_timing else self.snap,
        )

    def render(self, now: int) -> None:
        if (
            self.gamepad is not None
            and self.state not in NO_DEVTREE
            and now - self.last_info >= INFO_REFRESH_NS
        ):
            self.refresh_info()
        if self.quit_asked_at is not None and now - self.quit_asked_at > QUIT_CONFIRM_NS:
            self.quit_asked_at = None
        s, state, info = self.surface, self.state, self.info
        if state == Screen.ONE_ONLY:
            screens.one_only(s)
        elif state == Screen.PAUSED:
            run = self.run_info.connection if self.run_info is not None else ""
            screens.paused(s, self.pause_reason, self.label, run)
        elif state == Screen.CHECK:
            self.draw_check(now)
        elif state == Screen.CHECK_END:
            assert self.outcome is not None
            left = max(0.0, (END_CARD_NS - (now - self.end_at)) / 1e9)
            last = self.check_index == len(self.checks) - 1
            screens.check_end(
                s, self.check, self.outcome, self.check_index, len(self.checks), left, last
            )
        elif state == Screen.FOCUS_LOST:
            screens.focus_lost(s, self.check, self.check_index, len(self.checks))
        elif state == Screen.RESULTS:
            screens.results(
                s,
                self.label,
                self.run_dir,
                self.save_error,
                self.rig_result,
                self.results,
                self.score,
            )
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
                self.label_note,
            )
        elif state == Screen.NEW_LABEL:
            screens.new_label(s, self.typed, self.suggestion, self.label_error)
        elif state == Screen.SUITE:
            screens.suite(s, self.label, self.suite, info.connection)
        elif state == Screen.SETUP:
            screens.setup(s, self.label, self.snap)
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
