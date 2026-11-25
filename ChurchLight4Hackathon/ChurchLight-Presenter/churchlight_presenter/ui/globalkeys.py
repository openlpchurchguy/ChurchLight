# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Global (system-wide) shortcuts, claimed only while the operator switches them on.

Nothing is claimed from the operating system until the operator turns the switch On, and only the
keys they mapped are claimed. Turning it Off (or closing the program) hands every key back at once.
The Off state is the start-up state: "On" is never remembered, only the key mapping is.

The operating-system part is a small *backend* (register a key, release them all, report presses).
Windows has one, built on the standard library's ctypes. Elsewhere there is none, and the page says
so. Tests use a fake backend, so everything except the real key claim is checked without Windows.
"""
import json
import logging
import queue
import sys
import threading

log = logging.getLogger("churchlight.globalkeys")

# What a global key can do: id, label, and the name of the controller method that does it.
ACTIONS = (
    ("next", "Next slide", "next"),
    ("previous", "Previous slide", "previous"),
    ("next_item", "Next service item", "next_item"),
    ("previous_item", "Previous service item", "previous_item"),
    ("blank", "Blank screens", "toggle_blank"),
    ("hide_verse", "Hide the verse", "hide_verse"),
    ("take_cue", "Show the ready verse", "take_cue"),
    ("hide_text", "Hide the words (keep the background)", "toggle_text_hidden"),      # last, so the ids of the others never change
)
SETTING = "global_keys"

MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x1, 0x2, 0x4
MODIFIER_NAMES = (("Ctrl", MOD_CONTROL), ("Alt", MOD_ALT), ("Shift", MOD_SHIFT))     # the order combos are written in

# Windows virtual-key codes for the keys a combo may use.
KEY_CODES = {}
KEY_CODES.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
KEY_CODES.update({str(d): 0x30 + d for d in range(10)})
KEY_CODES.update({"F%d" % n: 0x70 + n - 1 for n in range(1, 25)})
KEY_CODES.update({"Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28, "PageUp": 0x21, "PageDown": 0x22,
                  "End": 0x23, "Home": 0x24, "Insert": 0x2D, "Delete": 0x2E, "Space": 0x20,
                  ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA, "'": 0xDE, "[": 0xDB, "]": 0xDD,
                  "-": 0xBD, "=": 0xBB, "`": 0xC0, "\\": 0xDC})


_BY_LOWER = {name.lower(): name for name in KEY_CODES}     # "f8", "PAGEUP" and "right" are all fine to type


class ComboError(ValueError):
    pass


def parse_combo(text):
    """"Ctrl+Alt+Right" -> (modifier flags, virtual-key code, the combo written in the standard way).

    Needs at least one modifier unless the key is an F key (a bare letter, digit or arrow would be
    taken from every program). Raises ComboError with a plain explanation."""
    if not isinstance(text, str) or not text.strip():
        raise ComboError("Press a key combination")
    parts = [p.strip() for p in text.split("+")]
    if "" in parts:                                   # "Ctrl++" for the plus key is not offered
        raise ComboError("That combination is not valid")
    *mods, key = parts
    key = _BY_LOWER.get(key.lower(), key)
    if key not in KEY_CODES:
        raise ComboError("That key cannot be used")
    flags = 0
    for name in mods:
        for known, bit in MODIFIER_NAMES:
            if name.lower() == known.lower():
                flags |= bit
                break
        else:
            raise ComboError("Unknown modifier " + name)
    if len(set(m.lower() for m in mods)) != len(mods):
        raise ComboError("That combination is not valid")
    if not flags and not (key.startswith("F") and key[1:].isdigit()):
        raise ComboError("Use Ctrl, Alt or Shift with that key (only the F keys can go alone)")
    return flags, KEY_CODES[key], format_combo(flags, key)


def format_combo(flags, key):
    return "+".join([name for name, bit in MODIFIER_NAMES if flags & bit] + [key])


class WindowsBackend:
    """Claims keys with the Windows RegisterHotKey call, on one background thread (the call and the
    message loop that hears the press must share a thread)."""

    WM_HOTKEY, PM_REMOVE, MOD_NOREPEAT = 0x0312, 0x0001, 0x4000
    ERROR_HOTKEY_ALREADY_REGISTERED = 1409

    def __init__(self):
        self._commands = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()
        self.on_press = lambda hotkey_id: None

    def _start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="global-keys", daemon=True)
                self._thread.start()

    def _ask(self, *command):
        self._start()
        reply = queue.Queue()
        self._commands.put((command, reply))
        return reply.get(timeout=5)

    def register(self, hotkey_id, flags, code):
        """-> (True, "") or (False, why)."""
        return self._ask("register", hotkey_id, flags, code)

    def unregister_all(self):
        if self._thread is not None:
            self._ask("clear")

    def close(self):
        if self._thread is not None:
            self._ask("stop")
            self._thread = None

    def _run(self):
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        message = wintypes.MSG()
        held = set()
        while True:
            try:
                command, reply = self._commands.get(timeout=0.02)
            except queue.Empty:
                command = reply = None
            if command is not None:
                kind = command[0]
                if kind == "register":
                    _, hotkey_id, flags, code = command
                    if user32.RegisterHotKey(None, hotkey_id, flags | self.MOD_NOREPEAT, code):
                        held.add(hotkey_id)
                        reply.put((True, ""))
                    else:
                        why = ("in use by another program"
                               if kernel32.GetLastError() == self.ERROR_HOTKEY_ALREADY_REGISTERED
                               else "Windows refused this key")
                        reply.put((False, why))
                else:
                    for hotkey_id in list(held):
                        user32.UnregisterHotKey(None, hotkey_id)
                    held.clear()
                    reply.put(True)
                    if kind == "stop":
                        return
            while user32.PeekMessageW(ctypes.byref(message), None, self.WM_HOTKEY, self.WM_HOTKEY, self.PM_REMOVE):
                try:
                    self.on_press(int(message.wParam))
                except Exception:                  # a press must never kill the listener
                    log.exception("Global key handler failed")


def default_backend():
    return WindowsBackend() if sys.platform == "win32" else None


class GlobalKeys:
    def __init__(self, controller, backend=None):
        self.c = controller
        self.backend = backend
        self.armed = False
        self._status = {}                                   # action id -> "active" or why it was not claimed
        self.combos = self._load()
        if backend is not None:
            backend.on_press = self._pressed

    # -- the saved mapping ---------------------------------------------------
    def _load(self):
        try:
            saved = json.loads(self.c.db.get_setting(SETTING) or "{}")
        except ValueError:
            return {}
        combos, taken = {}, set()
        for action_id, _, _ in ACTIONS:
            try:
                _, _, standard = parse_combo(saved.get(action_id))
            except (ComboError, AttributeError):
                continue
            if standard not in taken:
                combos[action_id] = standard
                taken.add(standard)
        return combos

    def _save(self):
        self.c.db.set_setting(SETTING, json.dumps(self.combos) if self.combos else None)

    # -- what the page shows -----------------------------------------------
    def view(self):
        rows = []
        for action_id, label, _ in ACTIONS:
            combo = self.combos.get(action_id)
            if combo is None:
                status = "none"
            elif not self.armed:
                status = "ready"
            else:
                status = self._status.get(action_id, "active")
            rows.append({"id": action_id, "label": label, "combo": combo, "status": status})
        return {"available": self.backend is not None, "armed": self.armed, "actions": rows}

    # -- the operator's choices ------------------------------------------------
    def set_combo(self, action_id, combo):
        """Map (or, with combo None or "", unmap) one action. While On, the change takes effect at once."""
        from .controller import ControllerError
        if action_id not in {a[0] for a in ACTIONS}:
            raise ControllerError("Unknown action")
        if combo in (None, ""):
            self.combos.pop(action_id, None)
        else:
            try:
                _, _, standard = parse_combo(combo)
            except ComboError as err:
                raise ControllerError(str(err)) from err
            for other, mapped in self.combos.items():
                if other != action_id and mapped == standard:
                    label = next(a[1] for a in ACTIONS if a[0] == other)
                    raise ControllerError(f"{standard} is already used for “{label}”")
            self.combos[action_id] = standard
        self._save()
        if self.armed:
            self._claim()
        self.c._notify()

    def arm(self):
        from .controller import ControllerError
        if self.backend is None:
            raise ControllerError("Global shortcuts need Windows; they are not available here")
        if not self.combos:
            raise ControllerError("Choose at least one key first")
        self.armed = True
        self._claim()
        log.info("Global shortcuts on: %s", ", ".join(sorted(self.combos.values())))
        self.c._notify()

    def disarm(self):
        if self.backend is not None:
            self.backend.unregister_all()
        was = self.armed
        self.armed = False
        self._status = {}
        if was:
            log.info("Global shortcuts off")
            self.c._notify()

    def close(self):
        if self.backend is not None:
            self.backend.unregister_all()
            self.backend.close()
        self.armed = False

    # -- the keys themselves -----------------------------------------------------
    def _claim(self):
        self.backend.unregister_all()
        self._status = {}
        for index, (action_id, _, _) in enumerate(ACTIONS, start=1):
            combo = self.combos.get(action_id)
            if combo is None:
                continue
            flags, code, _ = parse_combo(combo)
            ok, why = self.backend.register(index, flags, code)
            self._status[action_id] = "active" if ok else why

    def _pressed(self, hotkey_id):
        if not self.armed or not 1 <= hotkey_id <= len(ACTIONS):
            return
        action_id, _, method = ACTIONS[hotkey_id - 1]
        try:
            getattr(self.c, method)()
        except Exception:                                   # nothing to advance to, etc.: not an error to the room
            log.info("Global key %s had nothing to do", action_id, exc_info=True)
