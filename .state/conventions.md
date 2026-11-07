# Conventions

- **Tone:** Be extremely concise. Lead with answers, not reasoning.
  Full natural sentences, no AI-style framing.
- **Spirit:** This is a fun project. Keep ceremony minimal. Add a check only
  where it protects the verdict or catches real breakage.

## Project Topography

- Standalone Python app, greenfield. The repo holds only README and `.gitignore`.
- The source brief is `.state/CLAUDE.md`. It stays local and untracked. Product
  content moves into `requirements.md` during scope.
- Planned layout uses the src layout:
  - `src/controller_lab/__main__.py` is the entry point for `controller-lab` and `python -m controller_lab`.
  - `app.py` is the screen state machine: waiting, detected, checks, results, next.
  - `source.py` samples the gamepad and `detect.py` handles connect, type and connection.
  - `checks/` holds one module per guided check, with prompts, end condition and timeout.
  - `analysis.py` and `scoring.py` are pure, with no pygame imports.
  - `report.py` writes `report.html` and `compare.html`.
  - `ui/` holds panels, the live controller view and the results screen.
  - `src/controller_lab/profiles/` holds one TOML file of thresholds per controller type,
    inside the package so it ships with uv and pipx.
  - `tests/` holds pytest tests only.
- Target is Windows 10/11. Keep the core cross-platform where it costs nothing.
- License is MIT, open source.

## Naming Conventions

- The guided procedures are **checks**, in code, UI and docs. **Tests** always means pytest.
- Modules and functions use `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE`.
- Check modules are named by what they measure, such as `idle_drift.py` and `report_timing.py`.
- A controller label is chosen by the user, such as `Black`, and defaults to `controller-N`.
  It is the identity key for history.
- Connection values are `usb` and `bluetooth`. Use the same strings in folders and JSON.
- Say "controller", never "pad", in user-facing text and new identifiers. "D-pad" is
  the only exception. The SDL wrapper is `Gamepad`, to avoid clashing with pygame's
  `Controller`.

## Patterns & Idioms

- Sampling is separate from rendering. The sampler runs at about 1 kHz and stores a
  timestamped sample only when the state changes, using `perf_counter_ns`. The UI
  draws at about 60 fps and drops lower during timing checks. Rendering never blocks sampling.
- Use the SDL GameController API through `pygame._sdl2.controller.Controller`, so
  mapping is the same on every controller. Axes are raw ints with no deadzone applied.
- Mix sources. Input comes from SDL through XInput. Identity, connection, the USB
  serial or Bluetooth address, and Bluetooth battery come from the Windows device tree
  through `devtree.py`, with SDL as the fallback. Never read the device tree during a
  timing measurement.
- Analysis takes arrays in and returns numbers out. Scoring maps each metric linearly
  from good (100) to bad (0) and clamps, with thresholds from the profile TOML.
- Every check has an automatic end condition and a timeout, and can be skipped or redone.
- A disconnect pauses the run, and reconnecting resumes it.
- Prompts accept both keyboard and controller. A means continue, B means skip.
  Never use Guide, since it opens Game Bar.
- Results say what the software can measure and say plainly what it can't.
  Input arrival time at Windows is not true latency.
- `metrics.json` and the profiles carry a version, so old runs stay comparable.
- Type hints throughout. Prefer plain functions and dataclasses over class hierarchies.

## Tooling & Commands

- Python 3.11+, which gives high-resolution `time.sleep` on Windows.
- Use uv for packaging and envs, with `pyproject.toml` and a committed `uv.lock`.
- Dependencies: `pygame-ce` (2.5.8 ships SDL 2.32.10), `numpy`, `matplotlib`.
- Commands:
  - `uv sync` sets up the env.
  - `uv run controller-lab` runs the app.
  - `uv run pytest` runs the tests.
  - `uv run ruff check .` and `uv run ruff format .` lint and format.
- Testing is lean. Pytest covers only `analysis.py` and `scoring.py`, using
  synthetic arrays. The hardware path is checked unattended each phase with one wired
  controller, through scratch scripts that send keystrokes and capture the window with a
  scratch `USERPROFILE`. Checks that need hands run together in Phase 8.
  There is no FakeSource, test hints or headless app smoke test unless scope asks for them.
- CI is one GitHub Actions job on `windows-latest` that runs ruff and pytest.
- Distribution is `pipx install controller-lab`, plus a later PyInstaller exe
  through a release workflow. The exe is unsigned, so the README warns about SmartScreen.

## Field Notes

- Verified in Phase 1 on Black, 2026-11-07, with XInput only via the four SDL hints:
  - Backend letter is `x` over both USB and Bluetooth LE.
  - USB: XInput hides the real ids. GUID gives 045E:02FF and the name
    `Xbox One Controller`, while Windows has `USB\VID_045E&PID_0B12`. Power level
    reads `empty` for about the first second, then `wired`. Batteries in or out
    makes no difference.
  - Bluetooth LE: GUID gives the real 045E:0B13 and the name `Xbox Series X
    Controller`. Power level stays `empty`, so XInput gives no battery here.
  - Triggers are separate axes reading 0 to 32767 on both connections.
  - Loop: median about 1.01 to 1.02 ms and p99 1.7 to 2.6 ms with
    `time.sleep(0.0005)`, which actually sleeps about 0.95 ms. Spin-wait not
    needed.
  - Report timing: USB 115.4 Hz, 8.66 ms mean, p99 11.1 ms. Bluetooth 53.9 Hz,
    18.56 ms mean, p99 44.9 ms, with 109 gaps over 30 ms. That is well off the
    Gamepadla Bluetooth reference of about 13 ms.
  - Timing from state changes can't tell a still stick from a dropout. Long gaps
    appear whenever the stick pauses. Phase 1A's `still_mask` handles this.
  - Normal Windows scheduling gives a few slow passes per second, 17 to 47 tool
    gaps per 20 s. Excluding them works as designed.
  - Share via `as_joystick()` is still untested.
- Windows device tree, researched after Phase 1 and built into `devtree.py` in
  Phase 1A. Readable through `ctypes` and `cfgmgr32` in 0.4 to 1.6 ms, with no new
  dependency:
  - The XInput HID device (`HID\...IG_00`) has the physical controller as an
    ancestor. On USB that is `USB\VID_045E&PID_0B12\<serial>`, with
    `USB\VID_045E&PID_02FF&IG_00` in between. On Bluetooth LE it is
    `BTHLE\Dev_<mac>`, and the ids appear in the child names as `VID&02045E_PID&0B13`.
  - Real type and connection come from that ancestor: a `USB\` root means USB, and
    `BTHLE` means Bluetooth. The USB serial or the Bluetooth address identifies the
    controller itself, but the two differ for the same controller.
  - Bluetooth battery is stored on the `BTHLE\Dev_<mac>` node as property
    `{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2`, a percentage byte, with its update
    time in pid 7. It read 82, matching Windows Settings. It may only refresh now
    and then.
  - Verified live on both connections in Phase 1A. Black's USB serial and White's
    differ, so the key tells identical controllers apart.
  - The device-tree node appears 20 to 30 ms before SDL's `CONTROLLERDEVICEADDED`,
    every time across 10 connects. So it is ready when the app connects.
  - On a Bluetooth reconnect, Windows hands back the previous session's battery
    with its old update time, and may not refresh it for minutes. A stale 100% was
    seen. `detect.battery_text` shows `reading…` for 10 s, then the value with its
    age, until a value newer than the connection arrives.
- A controller paired over Bluetooth falls back to Bluetooth within about 1 s of its
  USB cable being unplugged, and moves back to USB on replug. The device key changes
  with it, from serial to address. A rule that treats a changed key as a different
  controller would misfire on this. Phase 3 has such a rule, so the architect needs
  to decide on it before Phase 3.
- Spin timing, Phase 1A, on Black:
  - USB: 122.7 Hz, 8.15 ms mean, p99 16.2 ms, 0 gaps over 30 ms, 13.1 s moving with
    one 2 s hold.
  - Bluetooth LE: 111.0 Hz, 9.01 ms mean, p99 16.7 ms, 0 gaps. That is far better
    than Phase 1's 54 Hz on the same laptop, so this link varies from session to
    session. Noted, not chased.
  - `still_count` counts still intervals, not stretches. A 2 s hold at the rim gives
    about 30, because tiny value changes split it up.
  - Past half a turn of expected travel, a dropout and a hold look alike, because
    angle steps wrap at ±π. At 3 turns a second, that's a dropout of over 0.67 s.
  - Only one controller is attached at a time, as the architect decided on
    2026-11-07, so slot-to-device matching is not needed.
- Library limits: there is no trigger rumble, only the low and high body motors.
- Checks that involve the person, reaction and tracking, never count in a
  single-controller score. They count only in compare mode, with a significance check.
- Reference figures for a healthy Series pad come from Gamepadla. USB averages
  about 8.3 ms with a 12.2 ms max, and Bluetooth about 13 ms with a 20.9 ms max.
  Rate is about 111 to 125 Hz, and circle error about 7.9%.
- Maintainer rig: Windows 11, three Series pads labelled Black, White and Red,
  connected over USB-C and Bluetooth. There is no Xbox Wireless Adapter.
- Verified in Phase 0: uv-managed CPython 3.12.16 runs pygame-ce 2.5.8 with
  SDL 2.32.10, so the 3.12 pin works.
- The wheel `include` list in `pyproject.toml` names `**/*.py` and
  `profiles/*.toml`. Any other data file inside the package must be added there,
  or it won't ship.
- Driving the app unattended, learned in Phase 2:
  - `.venv\Scripts\python.exe` is a redirector that starts the real interpreter as a
    child, so the window's process isn't the one launched. Kill the tree.
  - Find the window by title `Controller Lab` and class `pygame`. The Windows Terminal
    tab can carry the same title.
  - `SendInput` keys need a real scan code from `MapVirtualKeyW`, or SDL ignores them.
    Text goes through `KEYEVENTF_UNICODE`. An Alt tap before `SetForegroundWindow`
    lets the driver take focus.
  - `PrintWindow` with flag 3 captures the 960×600 client area.
