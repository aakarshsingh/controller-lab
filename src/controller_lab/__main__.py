"""Entry point for `controller-lab` and `python -m controller_lab`."""

import os

# SDL reads these when the joystick subsystem starts, so they must be set before
# pygame is imported anywhere. Pads are read through XInput only.
SDL_HINTS = {
    "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1",
    "SDL_JOYSTICK_RAWINPUT": "0",
    "SDL_JOYSTICK_WGI": "0",
    "SDL_JOYSTICK_HIDAPI_XBOX": "0",
}


def main() -> None:
    os.environ.update(SDL_HINTS)
    from controller_lab import app

    app.run()


if __name__ == "__main__":
    main()
