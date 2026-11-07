"""Reads controller identity, connection and battery from the Windows device tree.

XInput hides the real ids over USB and gives no battery over Bluetooth, so this
walks from each XInput HID device up to the physical controller through
`cfgmgr32`. Read only, stdlib only, and no pygame imports. Returns nothing off
Windows or when `cfgmgr32` fails.
"""

from __future__ import annotations

import ctypes
import re
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

CR_SUCCESS = 0
CM_GETIDLIST_FILTER_ENUMERATOR = 0x1
CM_GETIDLIST_FILTER_PRESENT = 0x100
DEVPROP_TYPE_BYTE = 0x3
DEVPROP_TYPE_FILETIME = 0x10
MAX_DEVICE_ID_LEN = 400
GENERIC_PRODUCT = 0x02FF  # what XInput reports for every Xbox controller over USB
BATTERY_FMTID = "104EA319-6EE2-4701-BD47-8DDBF425BBE5"
FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)

# Matches VID_045E&PID_0B12 on USB and VID&02045E_PID&0B13 on Bluetooth LE.
VIDPID = re.compile(r"VID[_&](?:02)?([0-9A-F]{4})[_&]PID[_&]([0-9A-F]{4})", re.IGNORECASE)


@dataclass(frozen=True)
class DeviceInfo:
    vendor: int
    product: int
    connection: str
    device_key: str | None
    battery_pct: int | None
    battery_updated: datetime | None


class _DevPropKey(ctypes.Structure):
    _fields_ = [("fmtid", ctypes.c_ubyte * 16), ("pid", wintypes.ULONG)]


def _key(fmtid: str, pid: int) -> _DevPropKey:
    k = _DevPropKey()
    ctypes.memmove(k.fmtid, uuid.UUID(fmtid).bytes_le, 16)
    k.pid = pid
    return k


class _CfgMgr:
    """The few `cfgmgr32` calls used, with argument types set."""

    def __init__(self) -> None:
        cm = ctypes.WinDLL("cfgmgr32")  # type: ignore[attr-defined]
        ul, pul, dw = wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), wintypes.DWORD
        sig = {
            "CM_Get_Device_ID_List_SizeW": [pul, wintypes.LPCWSTR, ul],
            "CM_Get_Device_ID_ListW": [wintypes.LPCWSTR, wintypes.LPWSTR, ul, ul],
            "CM_Locate_DevNodeW": [ctypes.POINTER(dw), wintypes.LPCWSTR, ul],
            "CM_Get_Parent": [ctypes.POINTER(dw), dw, ul],
            "CM_Get_Device_IDW": [dw, wintypes.LPWSTR, ul, ul],
            "CM_Get_DevNode_PropertyW": [
                dw,
                ctypes.POINTER(_DevPropKey),
                pul,
                ctypes.c_void_p,
                pul,
                ul,
            ],
        }
        for name, args in sig.items():
            fn = getattr(cm, name)
            fn.argtypes = args
            fn.restype = wintypes.DWORD
        self.cm = cm
        self.battery_key = _key(BATTERY_FMTID, 2)
        self.updated_key = _key(BATTERY_FMTID, 7)

    def id_list(self, enumerator: str) -> list[str]:
        flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
        size = wintypes.ULONG()
        if self.cm.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), enumerator, flags):
            return []
        buf = ctypes.create_unicode_buffer(size.value)
        if self.cm.CM_Get_Device_ID_ListW(enumerator, buf, size.value, flags):
            return []
        return [s for s in ctypes.wstring_at(buf, size.value).split("\0") if s]

    def locate(self, instance_id: str) -> int | None:
        node = wintypes.DWORD()
        ok = self.cm.CM_Locate_DevNodeW(ctypes.byref(node), instance_id, 0) == CR_SUCCESS
        return node.value if ok else None

    def parent(self, node: int) -> int | None:
        out = wintypes.DWORD()
        ok = self.cm.CM_Get_Parent(ctypes.byref(out), node, 0) == CR_SUCCESS
        return out.value if ok else None

    def device_id(self, node: int) -> str:
        buf = ctypes.create_unicode_buffer(MAX_DEVICE_ID_LEN)
        if self.cm.CM_Get_Device_IDW(node, buf, MAX_DEVICE_ID_LEN, 0):
            return ""
        return buf.value

    def _property(self, node: int, key: _DevPropKey, want_type: int, value: ctypes._SimpleCData):
        ptype = wintypes.ULONG()
        size = wintypes.ULONG(ctypes.sizeof(value))
        r = self.cm.CM_Get_DevNode_PropertyW(
            node, ctypes.byref(key), ctypes.byref(ptype), ctypes.byref(value), ctypes.byref(size), 0
        )
        return value.value if r == CR_SUCCESS and ptype.value == want_type else None

    def battery(self, node: int) -> int | None:
        pct = self._property(node, self.battery_key, DEVPROP_TYPE_BYTE, ctypes.c_ubyte())
        return pct if pct is not None and 0 <= pct <= 100 else None

    def battery_updated(self, node: int) -> datetime | None:
        ticks = self._property(node, self.updated_key, DEVPROP_TYPE_FILETIME, ctypes.c_uint64())
        if not ticks:
            return None
        return FILETIME_EPOCH + timedelta(microseconds=ticks // 10)


_cfgmgr: _CfgMgr | None = None


def find_controllers() -> list[DeviceInfo]:
    """One entry per present XInput controller, or [] when it can't be read."""
    global _cfgmgr
    if sys.platform != "win32":
        return []
    try:
        if _cfgmgr is None:
            _cfgmgr = _CfgMgr()
        return _find(_cfgmgr)
    except (OSError, AttributeError, ValueError):
        return []


def _find(cm: _CfgMgr) -> list[DeviceInfo]:
    found = []
    for hid in cm.id_list("HID"):
        if "IG_" not in hid.upper():
            continue  # only XInput-compatible HID collections carry IG_xx
        chain = []
        node = cm.locate(hid)
        while node is not None and len(chain) < 32:
            chain.append((node, cm.device_id(node)))
            node = cm.parent(node)
        info = _from_chain(cm, chain)
        if info is not None:
            found.append(info)
    return found


def _from_chain(cm: _CfgMgr, chain: list[tuple[int, str]]) -> DeviceInfo | None:
    for i, (_, did) in enumerate(chain):
        m = VIDPID.search(did)
        if not m or int(m.group(2), 16) == GENERIC_PRODUCT:
            continue
        vendor, product = int(m.group(1), 16), int(m.group(2), 16)
        if did.upper().startswith("USB\\"):
            # A composite interface (&MI_xx) sits below the device that has the serial.
            usb = next((d for _, d in chain[i:] if "&MI_" not in d.upper()), did)
            serial = usb.split("\\")[-1]
            key = None if "&" in serial else serial
            return DeviceInfo(vendor, product, "usb", key, None, None)
        bthle = next(((n, d) for n, d in chain if d.upper().startswith("BTHLE\\DEV_")), None)
        if bthle is None:
            return DeviceInfo(vendor, product, "bluetooth", None, None, None)
        node, bdid = bthle
        address = bdid.split("\\")[1].split("_", 1)[-1].upper()
        return DeviceInfo(
            vendor, product, "bluetooth", address, cm.battery(node), cm.battery_updated(node)
        )
    return None
