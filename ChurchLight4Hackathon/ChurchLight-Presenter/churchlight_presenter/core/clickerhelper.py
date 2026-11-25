# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
# ChurchLight Presenter - free software under the GNU Affero General Public License v3 or later.
"""The clicker helper: an AutoHotkey script that turns a presentation clicker's key presses into Trigger URLs.

The program builds the script itself, only from key names and actions on fixed lists, so nothing a web page
sends can become code. The bundled AutoHotkey (vendor/autohotkey, GPLv2, run as a separate program) starts it.
"""
import logging
import re
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

VENDOR_DIR = Path(__file__).resolve().parents[2] / "vendor" / "autohotkey"
EXE_NAME = "AutoHotkey64.exe"
SCRIPT_NAME = "ChurchLight-clicker.ahk"
MAX_BUTTONS = 24

# The names AutoHotkey knows these keys by. Anything else is refused.
KEY_NAMES = frozenset(
    ["PgDn", "PgUp", "Right", "Left", "Up", "Down", "Home", "End", "Space", "Enter", "Esc", "Tab", "Backspace",
     ".", ",", "/", "-", "=", "[", "]"]
    + [chr(c) for c in range(ord("a"), ord("z") + 1)] + [str(d) for d in range(10)] + [f"F{n}" for n in range(1, 13)])
_ACTION = re.compile(r"[a-z_]{1,40}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{20,64}")


def clean_rows(rows, allowed_actions):
    """The rows worth keeping: a known key, an allowed action, each key once. Anything else is dropped."""
    allowed = set(allowed_actions)
    seen = set()
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        key, action = row.get("key"), row.get("action")
        if not isinstance(key, str) or key not in KEY_NAMES or not isinstance(action, str) or action not in allowed:
            continue
        if key.lower() in seen:
            continue
        seen.add(key.lower())
        out.append({"key": key, "action": action})
    return out[:MAX_BUTTONS]


def build_script(port, token, rows):
    """The text of the .ahk file. Each key calls the Trigger URL; if ChurchLight does not answer the key is sent on
    as a normal key, so the clicker is never left dead."""
    if not (isinstance(port, int) and 0 < port < 65536) or not _TOKEN.fullmatch(str(token)):
        raise ValueError("Cannot make the clicker script: the program address or secret is not usable")
    lines = [
        "; ChurchLight clicker helper - made and started by ChurchLight Presenter.",
        "; It contains your secret Trigger URL address. It is rewritten each time the helper starts.",
        "; Ctrl+Alt+F12 turns the clicker on and off.",
        "#Requires AutoHotkey v2.0",
        "#SingleInstance Force",
        "",
        f'BaseUrl := "http://127.0.0.1:{port}/hook/{token}/"',
        "Warned := false",
        "",
        "Press(action, fallback) {",
        "    global Warned",
        "    try {",
        '        req := ComObject("WinHttp.WinHttpRequest.5.1")',
        "        req.SetTimeouts(1000, 1000, 1000, 1000)",
        '        req.Open("GET", BaseUrl . action, false)',
        "        req.Send()",
        "        if (req.Status = 200 or req.Status = 409)",
        "            return",
        "    }",
        "    ; ChurchLight did not answer, or the secret is out of date: pass the key on as a normal key.",
        '    Send "{" . fallback . "}"',
        "    if (!Warned) {",
        "        Warned := true",
        '        TrayTip "ChurchLight did not answer. Is it running?", "ChurchLight clicker"',
        "    }",
        "}",
        "",
        "; When ChurchLight is closed this helper closes too (it asks the program a few times a few seconds apart),",
        "; so it never lingers, even if it was started from the downloaded script.",
        "Misses := 0",
        "CheckAlive() {",
        "    global Misses",
        "    try {",
        '        req := ComObject("WinHttp.WinHttpRequest.5.1")',
        "        req.SetTimeouts(1000, 1000, 1000, 1000)",
        f'        req.Open("GET", "http://127.0.0.1:{port}/", false)',
        "        req.Send()",
        "        Misses := 0",
        "        return",
        "    }",
        "    Misses += 1",
        "    if (Misses >= 3)",
        "        ExitApp",
        "}",
        "SetTimer CheckAlive, 5000",
        "",
    ]
    for row in rows:
        key, action = row["key"], row["action"]
        if key not in KEY_NAMES or not _ACTION.fullmatch(action):
            raise ValueError("Cannot make the clicker script: a button is not usable")
        lines.append(f'${key}::Press("{action}", "{key}")')
    lines += ["", "#SuspendExempt", "^!F12:: {", "    Suspend(-1)",
              '    TrayTip A_IsSuspended ? "Clicker is off." : "Clicker is on.", "ChurchLight clicker"', "}",
              "#SuspendExempt False", "",
              'TrayTip "The clicker is ready. Ctrl+Alt+F12 turns it on and off.", "ChurchLight clicker"', ""]
    return "\r\n".join(lines)


class ClickerHelper:
    """Writes the script into `work_dir` and runs it with the bundled AutoHotkey. Windows only."""

    def __init__(self, work_dir, exe=None, popen=subprocess.Popen, windows=None):
        self.work_dir = Path(work_dir)
        self.exe = Path(exe) if exe else VENDOR_DIR / EXE_NAME
        self._popen = popen
        self._windows = sys.platform == "win32" if windows is None else windows
        self._process = None

    @property
    def script_path(self):
        return self.work_dir / SCRIPT_NAME

    def available(self):
        """True on Windows when the bundled AutoHotkey is in place."""
        return bool(self._windows and self.exe.is_file())

    def running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, port, token, rows):
        """Write the script and run it, replacing a helper that is already running."""
        if not self.available():
            raise OSError("The clicker helper needs Windows and the bundled AutoHotkey")
        script = build_script(port, token, rows)
        self.stop()
        self.script_path.write_text(script, encoding="utf-8-sig", newline="")      # the BOM lets AutoHotkey read any text
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._process = self._popen([str(self.exe), str(self.script_path)], creationflags=flags, cwd=str(self.work_dir))
        log.info("Clicker helper started")

    def stop(self):
        process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except Exception:  # noqa: BLE001
                process.kill()
            log.info("Clicker helper stopped")
        try:
            self.script_path.unlink(missing_ok=True)       # the secret does not stay on disk while the helper is off
        except OSError:
            pass
