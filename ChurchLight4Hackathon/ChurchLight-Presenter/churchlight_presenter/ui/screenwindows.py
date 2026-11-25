# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Shows a page full screen on a chosen monitor, in a browser window of its own, and closes it again.

Today the setup guide's Test button is the only user: it shows the white TEST page on the monitor a church picked.
Each screen gets its own browser profile folder, so the browser starts a separate process that obeys the window
position (a browser that is already running ignores it). Only the program's own screen addresses are ever opened."""
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from .. import launcher
from ..core import monitors as monitor_list
from . import winhide

log = logging.getLogger("churchlight_presenter.screens")


class ScreenWindowError(Exception):
    """Plain words for the operator."""


class ScreenWindows:
    def __init__(self, profile_root=None, list_monitors=monitor_list.list_monitors, find_browser=launcher.find_browser,
                 popen=subprocess.Popen, platform=None, close_leftovers=launcher.close_browsers_using,
                 hide_button=winhide.hide_from_taskbar_later):
        self.profile_root = Path(profile_root) if profile_root else None
        self._list = list_monitors
        self._find = find_browser
        self._popen = popen
        self._platform = platform or sys.platform
        self._close_leftovers = close_leftovers      # (profile folder, platform=, wait=) -> closes browsers left over from before
        self._open = {}                       # output name -> (process, monitor index)
        self._hide_button = hide_button       # (pid, platform=) -> takes the window's taskbar button away (Windows only)
        self._wanted = {}                     # output name -> (url, monitor index, operator_at): windows that must stay open
        self._tries = {}                      # output name -> times it was reopened lately
        self._watching = None                 # (thread, stop event)

    def monitors(self):
        return self._list()

    def available(self):
        return bool(self._list()) and self._find() is not None

    def is_open(self, name):
        entry = self._open.get(name)
        if entry is None:
            return False
        if entry[0].poll() is not None:        # closed by hand (Alt+F4)
            del self._open[name]
            return False
        return True

    def open_names(self):
        return sorted(n for n in list(self._open) if self.is_open(n))

    def monitor_at(self, point):
        """The index of the monitor holding the point (x, y in pixels), or None."""
        if not point:
            return None
        for m in self._list():
            if m["x"] <= point[0] < m["x"] + m["width"] and m["y"] <= point[1] < m["y"] + m["height"]:
                return m["index"]
        return None

    def show(self, name, url, monitor_index, operator_at=None):
        """Open `url` full screen on the monitor with this index; replaces a window already open for `name`.

        It is refused on the monitor `operator_at` (where the operator's own window is), since the full-screen window
        would cover it and nothing there could be clicked to close it."""
        found = self._find()
        if found is None:
            raise ScreenWindowError("Edge or Chrome was not found, so ChurchLight cannot open the screen by itself.")
        listed = {m["index"]: m for m in self._list()}
        monitor = listed.get(monitor_index)
        if monitor is None:
            raise ScreenWindowError("That monitor is not connected any more. Choose another.")
        if self.profile_root is None:
            raise ScreenWindowError("There is nowhere to keep the screen window's settings.")
        if self.monitor_at(operator_at) == monitor_index:
            raise ScreenWindowError("That monitor is the one this window is on, so the screen would cover it. Choose the "
                                    "monitor your projector is on.")
        self.hide(name)
        profile = self.profile_root / f"screen-{name}"
        # A browser left over from an earlier run of this screen would take the new address and ignore its position and
        # full-screen flags (or keep showing its old page), so any such leftover is closed first.
        try:
            self._close_leftovers(str(profile), platform=self._platform, wait=0)
        except Exception:  # noqa: BLE001 - a courtesy; opening the screen goes on regardless
            log.debug("Could not look for leftover windows", exc_info=True)
        command = launcher.kiosk_command(found[1], url, monitor, str(profile))
        try:
            process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as err:
            raise ScreenWindowError(f"The browser could not be started: {err}") from err
        self._open[name] = (process, monitor_index)
        self._wanted[name] = (url, monitor_index, operator_at)
        try:
            self._hide_button(process.pid, platform=self._platform)
        except Exception:  # noqa: BLE001 - the button stays; nothing else is affected
            log.debug("Could not hide the taskbar button", exc_info=True)
        log.info("Screen %s opened full screen on monitor %s: %s", name, monitor_index + 1, url)

    def hide(self, name):
        self._wanted.pop(name, None)           # closed on purpose: it must not come back
        entry = self._open.pop(name, None)
        if entry is None:
            return False
        process = entry[0]
        if process.poll() is None:
            self._kill(process)
        log.info("Screen %s closed", name)
        return True

    def _kill(self, process):
        try:
            if self._platform == "win32":          # the browser's helper processes go with it
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                process.terminate()
                process.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass

    def close_all(self):
        self.stop_watching()
        for name in list(self._wanted) + list(self._open):
            self.hide(name)

    # --- a screen window closed by accident (Alt+F4, the taskbar) comes back by itself
    MAX_REOPENS = 5            # per screen per minute: a browser that dies at once is not restarted forever

    def reopen_missing(self, now=None):
        """Open again every wanted screen whose window is gone. Returns the names reopened."""
        now = time.time() if now is None else now
        back = []
        for name, (url, monitor_index, operator_at) in list(self._wanted.items()):
            if self.is_open(name):
                continue
            recent = [t for t in self._tries.get(name, []) if now - t < 60]
            if len(recent) >= self.MAX_REOPENS:
                continue
            self._tries[name] = recent + [now]
            try:
                self.show(name, url, monitor_index, operator_at)
                back.append(name)
                log.info("Screen %s was closed, so it was opened again", name)
            except ScreenWindowError as err:
                log.info("Screen %s could not be opened again: %s", name, err)
        return back

    def start_watching(self, every=2.0):
        if self._watching is not None:
            return
        stop = threading.Event()

        def run():
            while not stop.wait(every):
                try:
                    self.reopen_missing()
                except Exception:  # noqa: BLE001
                    log.debug("Screen check failed", exc_info=True)
        thread = threading.Thread(target=run, name="screen-watch", daemon=True)
        self._watching = (thread, stop)
        thread.start()

    def stop_watching(self):
        if self._watching is not None:
            self._watching[1].set()
            self._watching = None
