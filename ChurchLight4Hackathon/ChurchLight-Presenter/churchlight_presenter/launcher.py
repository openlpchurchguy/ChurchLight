# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Open the operator page in its own plain window (no tabs or address bar).

Chrome and Edge both open a page as an "app window" with `--app=URL`, which feels like a program
rather than a web page and is harder to close by accident. Edge comes with Windows 10 and 11, so
there is nearly always something to use. If neither is found, the computer's default browser opens
the page as an ordinary tab, and if even that fails the caller prints the address.
"""
import logging
import os
import shutil
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

log = logging.getLogger(__name__)

_LINUX_NAMES = ("microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
                "chromium", "chromium-browser")


def browser_candidates(platform=None, env=None):
    """(name, path-or-command) pairs to try, best first."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform.startswith("win"):
        roots = [env.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "ProgramW6432", "LOCALAPPDATA")]
        roots = [r for r in dict.fromkeys(roots) if r]
        found = [("Edge", Path(r) / "Microsoft" / "Edge" / "Application" / "msedge.exe") for r in roots]
        found += [("Chrome", Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe") for r in roots]
        return found
    if platform == "darwin":
        return [("Edge", Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")),
                ("Chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))]
    return [(name, name) for name in _LINUX_NAMES]


def find_browser(platform=None, env=None, which=shutil.which):
    """(name, executable path) of the first Chrome-family browser found, or None."""
    for name, where in browser_candidates(platform, env):
        if isinstance(where, Path):
            if where.is_file():
                return name, str(where)
        else:
            path = which(where)
            if path:
                return name, path
    return None


def clean_window(x, y, width, height):
    """(x, y, width, height) as whole numbers if they describe a window worth reopening, else None.

    A minimized Windows window reports a position around -32000, and a sliver of a window is no use."""
    values = (x, y, width, height)
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v for v in values):
        return None
    x, y, width, height = (int(round(v)) for v in values)
    if not (-10000 < x < 30000 and -10000 < y < 30000 and 400 <= width <= 12000 and 300 <= height <= 12000):
        return None
    return x, y, width, height


def app_command(executable, url, window=None, profile=None):
    """The command that opens `url` as an app window, placed at `window` (x, y, width, height) when known.

    `profile` is a folder for the program's own browser profile. A browser that is already running
    ignores the position flags of a new window (Edge keeps one alive in the background), and a page may
    not move its own window onto another monitor, so the window gets a browser process of its own."""
    command = [executable, f"--app={url}", "--autoplay-policy=no-user-gesture-required"]       # the sound of a video starts without a click
    if profile:
        command += [f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                    "--disable-background-mode", "--disable-sync"]
    window = clean_window(*window) if window else None
    if window:
        command += [f"--window-position={window[0]},{window[1]}", f"--window-size={window[2]},{window[3]}"]
    return command


def kiosk_command(executable, url, monitor, profile):
    """The command that shows `url` full screen with no window frame on `monitor` (a dict with x, y, width, height).

    The window is placed on the monitor first and then made full screen, so it fills that monitor and not the
    one with the mouse. `profile` must be a folder of its own, so the browser starts a new process that obeys it."""
    return [executable, "--kiosk", "--edge-kiosk-type=fullscreen", url, f"--user-data-dir={profile}",
            f"--window-position={int(monitor['x'])},{int(monitor['y'])}",
            f"--window-size={int(monitor['width'])},{int(monitor['height'])}",
            "--no-first-run", "--no-default-browser-check", "--disable-background-mode", "--disable-sync",
            "--disable-session-crashed-bubble", "--disable-infobars", "--disable-features=Translate",
            "--autoplay-policy=no-user-gesture-required"]      # a video or sound slide starts without anyone clicking the page


def open_operator(url, platform=None, env=None, which=shutil.which, start=subprocess.Popen,
                  fallback=webbrowser.open, window=None, profile=None):
    """Open `url` in an app window if possible; returns how it was opened:
    'app window (Edge)', 'browser tab', or None when nothing could be started.
    `window` is where the operator window was last seen (x, y, width, height); `profile` is the folder
    for the browser profile the window runs in (see app_command)."""
    found = find_browser(platform, env, which)
    window = clean_window(*window) if window else None
    if window:
        # Browsers that are already running (Edge keeps one alive in the background) ignore the position
        # flags of a new window, so the page is told its place too and moves itself there.
        url += ("&" if "?" in url else "?") + "place=" + ",".join(str(v) for v in window)
        log.info("Opening the operator window at x=%s y=%s, %sx%s", *window)
    else:
        log.info("No saved place for the operator window; the browser chooses")
    if found:
        name, executable = found
        try:
            start(app_command(executable, url, window, profile), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
            log.info("Opened the operator page in a %s app window", name)
            return f"app window ({name})"
        except OSError as err:
            log.warning("Could not start %s: %s", name, err)
    try:
        if fallback(url):
            log.info("Opened the operator page in the default browser")
            return "browser tab"
    except Exception as err:  # noqa: BLE001 - a missing browser must never stop the program
        log.warning("Could not open the default browser: %s", err)
    return None


def _normal_path(text):
    return str(text).replace('"', "").replace("/", "\\").rstrip("\\").lower()


def processes_using_profile(processes, profile, own_pid=None):
    """The ids of the browser processes started with `--user-data-dir=<profile>` (or a folder inside it).

    `processes` is a list of (pid, command line). Only that exact folder counts, so the church's everyday browser, a
    folder with a similar name and this program itself never match."""
    wanted = _normal_path(profile)
    if not wanted:
        return []
    marker = "--user-data-dir="
    found = []
    for pid, command in processes:
        if own_pid is not None and pid == own_pid:
            continue
        text = str(command or "").replace('"', "").replace("/", "\\").lower()
        start = text.find(marker)
        if start < 0:
            continue
        given = text[start + len(marker):]
        if given.startswith(wanted) and given[len(wanted):len(wanted) + 1] in ("", "\\", " "):
            found.append(pid)
    return found


def list_processes(platform=None, run=subprocess.run):
    """[(pid, command line)] of every running program, on Windows (PowerShell is part of Windows); [] elsewhere or on failure."""
    if (platform or sys.platform) != "win32":
        return []
    script = ("Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    try:
        result = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                     timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:  # noqa: BLE001 - closing is a courtesy; it must never stop a shut-down
        return []
    processes = []
    for line in (result.stdout or "").splitlines():
        pid, _, command = line.partition("\t")
        if pid.strip().isdigit():
            processes.append((int(pid), command))
    return processes


def close_browsers_using(profile, platform=None, lister=list_processes, run=subprocess.run, sleep=time.sleep,
                         wait=3.0, own_pid=None):
    """Close the Edge/Chrome windows that were started with this program's own profile folder (the operator window and
    the screen windows), and leave every other browser alone. Asks politely first, then forces what is left.
    Returns how many were found. Only does anything on Windows."""
    if (platform or sys.platform) != "win32" or not profile:
        return 0
    own_pid = os.getpid() if own_pid is None else own_pid
    pids = processes_using_profile(lister(), profile, own_pid)
    if not pids:
        return 0
    found = len(pids)

    def taskkill(pid_list, force):
        for pid in pid_list:
            command = ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else [])
            try:
                run(command, capture_output=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception:  # noqa: BLE001
                pass

    if wait <= 0:                      # a leftover from an earlier run: nothing to save, so no asking first
        taskkill(pids, force=True)
        log.info("Closed %s leftover browser process(es) of this program's own", found)
        return found
    taskkill(pids, force=False)
    waited = 0.0
    while waited < wait:
        sleep(0.5)
        waited += 0.5
        pids = processes_using_profile(lister(), profile, own_pid)
        if not pids:
            break
    if pids:
        taskkill(pids, force=True)
    log.info("Closed the browser windows that belong to ChurchLight")
    return found
