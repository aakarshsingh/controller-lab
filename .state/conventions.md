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

## Patterns & Idioms

- Sampling is separate from rendering. The sampler runs at about 1 kHz and stores a
  timestamped sample only when the state changes, using `perf_counter_ns`. The UI
  draws at about 60 fps and drops lower during timing checks. Rendering never blocks sampling.
- Use the SDL GameController API through `pygame._sdl2.controller.Controller`, so
  mapping is the same on every pad. Axes are raw ints with no deadzone applied.
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
  synthetic arrays. The hardware path is checked by hand each phase with a real controller.
  There is no FakeSource, test hints or headless app smoke test unless scope asks for them.
- CI is one GitHub Actions job on `windows-latest` that runs ruff and pytest.
- Distribution is `pipx install controller-lab`, plus a later PyInstaller exe
  through a release workflow. The exe is unsigned, so the README warns about SmartScreen.

## Field Notes

- These seed assumptions are unverified on hardware. Confirm them in the first phase:
  - USB detection by `as_joystick().get_power_level() == "wired"`. Check what Series
    pads report over Bluetooth, and note that battery is unknown over USB.
  - The report rate measured by polling `get_axis` depends on SDL's Windows backend:
    RawInput, XInput or WGI. Record the backend in `metrics.json`. Check that a
    1 kHz loop actually sees about 125 Hz on a Series pad over USB.
  - Share is not exposed as a GameController constant. Test whether it can be read
    through `as_joystick()` before promising it.
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
