# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The monitors attached to this computer (Windows only; standard library ctypes), as physical pixels.

Elsewhere the list is empty and the program falls back to asking the person to drag the window."""
import sys


def list_monitors(platform=None):
    """[{"index", "name", "x", "y", "width", "height", "primary"}], the primary monitor first. [] when unknown."""
    if (platform or sys.platform) != "win32":
        return []
    try:
        return _windows_monitors()
    except Exception:  # noqa: BLE001 - never let a display quirk stop the setup guide
        return []


def _windows_monitors():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD), ("szDevice", wintypes.WCHAR * 32)]

    found = []

    def callback(handle, _dc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            r = info.rcMonitor
            found.append({"x": r.left, "y": r.top, "width": r.right - r.left, "height": r.bottom - r.top,
                          "primary": bool(info.dwFlags & 1), "device": info.szDevice})
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT),
                              wintypes.LPARAM)(callback)
    # Ask for real pixels (not scaled ones) while counting, then put the thread back as it was.
    old = None
    try:
        old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        old = None
    try:
        user32.EnumDisplayMonitors(None, None, proc, 0)
    finally:
        if old:
            try:
                user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))
            except (AttributeError, OSError):
                pass
    return label(found)


def label(found):
    """Primary first, then left to right; each gets an index and a plain name like "Monitor 2 (1280x720)"."""
    found = sorted(found, key=lambda m: (not m.get("primary"), m["x"], m["y"]))
    out = []
    for i, m in enumerate(found):
        name = f"Monitor {i + 1}" + (" (main)" if m.get("primary") else "")
        out.append({"index": i, "name": name, "x": m["x"], "y": m["y"], "width": m["width"], "height": m["height"],
                    "primary": bool(m.get("primary"))})
    return out
