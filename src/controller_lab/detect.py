"""Works out controller type, connection, battery and SDL backend.

Device info comes from the Windows device tree when it has exactly one match, and
from SDL's name, GUID and power level otherwise. Input always comes from SDL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from controller_lab.devtree import DeviceInfo
    from controller_lab.source import Gamepad

MICROSOFT = 0x045E
SONY = 0x054C
XBOX_SERIES_PIDS = {0x0B12, 0x0B13, 0x0B20, 0x0B21, 0x0B22}
XBOX_ONE_PIDS = {0x02D1, 0x02DD, 0x02E0, 0x02E3, 0x02EA, 0x02FD, 0x0B00, 0x0B05}
DUALSENSE_PIDS = {0x0CE6, 0x0DF2}

# Windows hands back the last session's battery on reconnect, with its old update
# time. A value written after the connection, or just before it, counts as fresh.
FRESH_MARGIN = timedelta(seconds=5)
READING_FOR = timedelta(seconds=10)

# GUID byte 14 is the SDL driver signature.
BACKENDS = {"x": "xinput", "r": "rawinput", "w": "wgi", "h": "hidapi"}


@dataclass(frozen=True)
class ControllerInfo:
    name: str
    type: str
    guid: str
    backend: str
    connection: str
    battery: str
    vendor_product: str
    device_key: str | None
    identity_source: str
    sdl_name: str


def guid_byte(guid: str, i: int) -> int:
    return int(guid[2 * i : 2 * i + 2], 16)


def vendor_product(guid: str) -> tuple[int, int]:
    """Vendor and product ids, stored little-endian in GUID bytes 4-5 and 8-9."""
    try:
        vendor = guid_byte(guid, 4) | guid_byte(guid, 5) << 8
        product = guid_byte(guid, 8) | guid_byte(guid, 9) << 8
    except (ValueError, IndexError):
        return 0, 0
    return vendor, product


def backend(guid: str) -> str:
    try:
        char = chr(guid_byte(guid, 14))
    except (ValueError, IndexError):
        return "unknown:?"
    return BACKENDS.get(char, f"unknown:{char if char.isprintable() else '?'}")


def controller_type(name: str, vendor: int, product: int) -> str:
    lowered = name.lower()
    if "series" in lowered or (vendor == MICROSOFT and product in XBOX_SERIES_PIDS):
        return "Xbox Series"
    if "dualsense" in lowered or (vendor == SONY and product in DUALSENSE_PIDS):
        return "DualSense"
    if "xbox one" in lowered or (vendor == MICROSOFT and product in XBOX_ONE_PIDS):
        return "Xbox One"
    return "generic"


def connection(power_level: str) -> str:
    return "usb" if power_level == "wired" else "bluetooth"


def ids(vendor: int, product: int) -> str:
    return f"{vendor:04X}:{product:04X}"


def age_text(age: timedelta) -> str:
    seconds = int(age.total_seconds())
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} days"


def battery_text(dev: DeviceInfo, connected_at: datetime, now: datetime) -> str:
    """`wired` over USB. Over Bluetooth, the percentage when Windows has read it
    since the connection, `reading…` for the first 10 s otherwise, and then the
    stored percentage with its age."""
    if dev.connection == "usb":
        return "wired"
    if dev.battery_pct is None:
        return "unknown"
    updated = dev.battery_updated
    if updated is not None and updated >= connected_at - FRESH_MARGIN:
        return f"{dev.battery_pct}%"
    if now - connected_at < READING_FOR:
        return "reading…"
    if updated is None:
        return f"{dev.battery_pct}%, age unknown"
    return f"{dev.battery_pct}%, read {age_text(now - updated)} ago"


def controller_info(
    gamepad: Gamepad,
    devices: list[DeviceInfo],
    connected_at: datetime,
    now: datetime | None = None,
) -> ControllerInfo:
    sdl_vendor, sdl_product = vendor_product(gamepad.guid)
    if len(devices) == 1:
        dev = devices[0]
        same_ids = (sdl_vendor, sdl_product) == (dev.vendor, dev.product)
        kind = controller_type("" if not same_ids else gamepad.name, dev.vendor, dev.product)
        battery = battery_text(dev, connected_at, now or datetime.now(UTC))
        return ControllerInfo(
            name=gamepad.name if same_ids or kind == "generic" else f"{kind} Controller",
            type=kind,
            guid=gamepad.guid,
            backend=backend(gamepad.guid),
            connection=dev.connection,
            battery=battery,
            vendor_product=ids(dev.vendor, dev.product),
            device_key=dev.device_key,
            identity_source="devtree",
            sdl_name=gamepad.name,
        )
    power = gamepad.power_level
    return ControllerInfo(
        name=gamepad.name,
        type=controller_type(gamepad.name, sdl_vendor, sdl_product),
        guid=gamepad.guid,
        backend=backend(gamepad.guid),
        connection=connection(power),
        battery=power,
        vendor_product=ids(sdl_vendor, sdl_product),
        device_key=None,
        identity_source="sdl",
        sdl_name=gamepad.name,
    )
