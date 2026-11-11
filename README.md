# controller-lab

A self-driven controller tester for Windows. Plug in a controller, follow the prompts on
screen, and get a verdict on its sticks, triggers, buttons, D-pad and connection. It
saves a report you can share, and it ranks the controllers you've tested on the same PC.

It was built to answer one question honestly: is a controller faulty, or is the player
still adjusting?

## Quick start

1. Install [uv](https://docs.astral.sh/uv/), for example with
   `winget install --id=astral-sh.uv -e`.
2. Double-click `controller-lab.cmd`. If uv is missing, the window prints the install
   command and waits for a key.
3. Connect **one** controller over USB or Bluetooth and follow the prompts.

From a terminal, `uv run controller-lab` does the same.

Every prompt can be answered with the controller alone. A continues, the D-pad picks,
B goes back before the checks and skips during them. On the keyboard, `Space` or
`Enter` continue, `S` skips, `R` redoes, `Backspace` goes back, and `Esc` quits after a
second press.

## One controller at a time

Attach only the controller you're testing. With two attached, the app waits on "Connect
only one controller". If a controller disconnects mid-check, the run pauses and asks
"Is this still <label>?" when it's back, then restarts that check. A run never mixes
USB and Bluetooth.

## Before you start

The Setup screen lists the conditions for a fair test:

- Plug the controller in directly, not through a hub.
- On Bluetooth, charge the controller or fit fresh batteries.
- Close Steam, DS4Windows and other remappers.
- Keep the app's window focused. If it loses focus, the running check is voided.
- Rest the controller flat.

## What it tests

| Check | Measures |
|---|---|
| Idle drift | Where each stick rests untouched for 30 s, its noise, and stray presses |
| Centring | How cleanly each stick returns to centre after 24 pushes, and snapback |
| Circularity | How round each stick's range is, and its weakest direction |
| Slow sweep | Resolution near centre and jumps when a stick moves slowly |
| Triggers | Rest value, full travel, resolution and jumps |
| Button taps | Missed or extra presses, and chatter |
| D-pad | Ghost presses on neighbouring directions, and diagonals |
| Holds and combos | Dropouts while buttons, triggers and sticks are held together |
| Sprint and turn | RT sag while the left stick swings |
| Skill flicks | Right stick flicks registering the wrong way |
| Throttle hold | How steadily RT holds at 25, 50 and 75% |
| Report timing | Report rate, average and 99th percentile interval, and gaps, from a 15 s spin |
| Haptics | Whether you felt each motor. Not scored |
| Tracking | How closely each stick follows a target. Compare only |

The **Full** suite runs all of them, and suits USB. The **Connection** suite runs idle
drift, A and B taps, holds and report timing, and suits a controller's second
connection.

Each category is scored from 0 to 100 against a profile for the controller type. A
controller is **Faulty** if any category is under 60 or an automatic fail hits, such as
drift over 8%, chatter, a dropout while held, or a USB gap over 100 ms. It shows **Minor
issues** if any category is between 60 and 79, and **Healthy** otherwise. Categories a
run didn't measure show "not tested" and don't count.

## What it can't measure

- Timing is when Windows receives the input, not true end-to-end latency.
- A constant delay added by the connection can't be detected by software.
- Tracking depends on the person, so it is left out of a single controller's score. It
  only counts in compare, along with how steady RT stays across the throttle hold, and
  only as significant with at least 2 runs per controller. RT's fast wobble while held
  part way does count, under Triggers.

## How Bluetooth is judged

Bluetooth speed depends a lot on the PC's adapter, so the app doesn't judge a controller's
Bluetooth timing against a fixed figure. It compares it with the median of the other
controllers tested over Bluetooth on the same PC. Until another controller has a
Bluetooth run, Bluetooth timing shows "not tested: needs another controller tested over
Bluetooth on this PC". Compare judges every controller again once there are others, so
the first one gets a score too. Gamepadla's Bluetooth figure for an Xbox Series
controller, about 13 ms average, is shown only as context.

## Results and compare

Every run is saved here:

```
%USERPROFILE%\ControllerLab\results\
  compare.html                      every controller side by side
  <label>\
    <YYYYMMDD-HHMMSS>_<usb|bluetooth>\
      report.html                   the run's verdict, scores, plots and raw metrics
      metrics.json                  everything measured, with tool and profile versions
      samples\<check>.csv           raw timestamped input for each check
```

On the Results screen, X on the controller or `O` on the keyboard opens the run's
`report.html`. Results stay on screen if the controller is turned off or unplugged.

Press `C` on the Results screen to write `compare.html` and open it. It ranks the
controllers, shows a table per category, USB against Bluetooth, and overlaid stick
circles. Both HTML files are single files you can copy anywhere. Neither shows a
controller's serial number or Bluetooth address.

For a fair comparison, test the controllers in a rotating order, such as Black, White,
Red and then White, Red, Black.

## Labels

A label, such as `Black`, names a controller, and the app suggests the label last used
with the controller in front of it. Labels are just folders under `results`:

- **Rename** a label by renaming its folder.
- **Delete** a label, or a single run, by deleting its folder.
- **Merge** two labels by moving the run folders from one into the other.

Close the app first, and open compare again afterwards.

## Development

```
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

MIT licensed.
