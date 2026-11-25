# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Takes a screen window's button off the Windows taskbar, so there is nothing to right-click and close.

Windows only, and quiet about every failure: if it does not work the window simply keeps its taskbar button. The
browser's window appears a moment after it is started, so it is looked for for a few seconds."""
import logging
import sys
import threading
import time

log = logging.getLogger("churchlight_presenter.screens")

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_HIDE, SW_SHOWNA = 0, 8
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020          # no size, move, z-order or activation; frame changed
TH32CS_SNAPPROCESS = 0x2


def hide_from_taskbar_later(pid, tries=30, wait=0.5, platform=None):
    """In the background, keep looking for the browser's window (made by this process or one it started) and take its
    taskbar button away. Returns the thread, or None where this does nothing (not Windows)."""
    if (platform or sys.platform) != "win32":
        return None
    thread = threading.Thread(target=_work, args=(pid, tries, wait), name="screen-taskbar", daemon=True)
    thread.start()
    return thread


def _work(pid, tries, wait):
    for _ in range(tries):
        try:
            if _hide_windows_of(pid):
                return
        except Exception:  # noqa: BLE001 - never trouble the program over a taskbar button
            log.debug("Could not take the screen window off the taskbar", exc_info=True)
            return
        time.sleep(wait)


def _family(pid):
    """The process and everything it started."""
    import ctypes
    from ctypes import wintypes

    class ENTRY(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    parents = {}
    try:
        entry = ENTRY()
        entry.dwSize = ctypes.sizeof(ENTRY)
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    family = {int(pid)}
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in family and child not in family:
                family.add(child)
                grew = True
    return family


def _hide_windows_of(pid):
    import ctypes
    from ctypes import wintypes

    user = ctypes.windll.user32
    family = _family(pid)
    done = []
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _lparam):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value not in family or not user.IsWindowVisible(hwnd):
            return True
        name = ctypes.create_unicode_buffer(64)
        user.GetClassNameW(hwnd, name, 64)
        if name.value != "Chrome_WidgetWin_1":             # the browser's own top-level window
            return True
        get, set_ = user.GetWindowLongPtrW, user.SetWindowLongPtrW
        get.restype = ctypes.c_ssize_t
        style = get(hwnd, GWL_EXSTYLE)
        if not style & WS_EX_TOOLWINDOW:
            set_(hwnd, GWL_EXSTYLE, (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
            user.ShowWindow(hwnd, SW_HIDE)                  # Windows re-reads the style when the window is shown again
            user.ShowWindow(hwnd, SW_SHOWNA)
            user.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
        done.append(hwnd)
        return True

    user.EnumWindows(proc_type(each), 0)
    return bool(done)
