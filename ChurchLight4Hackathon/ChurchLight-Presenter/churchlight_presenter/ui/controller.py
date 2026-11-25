# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The operator's brain: everything a window or a terminal needs, with no window in it.

`OperatorController` ties the database, the slide builder, the live session and
the outputs together, and offers the actions an operator takes: search the
library, build a service, send things live, step through them, show and hide a
Bible verse over whatever is up. A PySide6 window (or the console in
`console.py`) only calls these methods and draws the results, so the behavior
is tested here without any display.

Two ways something becomes live, as in OpenLP:

  * "service": the service list is loaded, and stepping moves through all of it.
  * "item": one library item is sent straight live on its own (a song someone
    asked for); it replaces what was live, and the service is untouched.

A Bible verse is an overlay in either mode. Editing the service while it is
live keeps the same slide up (found again by its service item) and keeps the
verse showing.
"""
import contextlib
import functools
import hmac
import json
import logging
import re
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .. import launcher
from ..importers.common import ImporterError
from ..importers.themes import import_themes
from ..core.items import ItemRef, add_to_service
from ..core import backup, clickerhelper, timerstyle, usagelog
from ..core.mediacheck import check_video, describe_video
from ..core.live import MEDIA_KINDS, LiveSession, clip_position
from ..core.outputs import PLAYER_LAYOUTS, build_payload, default_outputs
from ..core.scripture_ref import format_reference, parse_reference
from ..core.database import _UNSET
from ..core.slides import NOTICE_LABEL, NOTICE_PART, BuildSettings, SlideBuilder
from ..core.songnotice import (POSITIONS as NOTICE_POSITIONS, SONG_CHOICES as NOTICE_SONG_CHOICES, notice_position,
                               notice_settings)
from ..core.songtext import (SongTextError, format_song_text, parse_song_text, replace_order_line,
                             skipped_parts)
from .screenwindows import ScreenWindowError, ScreenWindows
from ..core.themes import (ASPECTS, KINDS, KIND_NAMES, ThemeStyles, overlay_char_capacity, style_for_kind,
                           theme_kind, validate_build, validate_style)
from ..core.autoplay import OVERRIDE_KEYS, check_autoplay, next_step, timing_for
from ..core.transitions import check_transition
from .globalkeys import GlobalKeys, default_backend

log = logging.getLogger(__name__)
NO_CHANGE = object()          # "leave this setting as it is" (None already means "clear it")

LIBRARY_LIMIT = 200
SCREEN_SHAPES = ("16:9", "4:3", "16:10")      # the shapes a real screen can be told to have
DEFAULT_SHAPE = "16:9"
SHAPE_LAYOUTS = ("projector", "confidence")     # only these screens are a real surface whose shape matters
HOME_FITS = ("fit", "fill")
_HOME_IMAGE = re.compile(r"^backgrounds/[0-9a-f]{16}\.[a-z]{3,4}$")
_KEEP = object()
SUGGEST_LIMIT = 8
RECENT_LIMIT = 6
# A number at the start of a title: "055 Amazing Grace", "#55 - Title", "55. Title". "10,000 Reasons" and
# "55th Anniversary" are not numbered.
_TITLE_NUMBER = re.compile(r"^\s*#?(\d{1,6})(?=[\s.:)\-\u2013\u2014]|$)")


def _song_label(row):
    """What the operator sees for a song: its operator name when it has one, else its title."""
    return (row["operator_name"] or row["title"]) if "operator_name" in row.keys() else row["title"]


def title_number(title):
    """The number a title starts with, as a string without leading zeros ("055 ..." -> "55"), or None."""
    match = _TITLE_NUMBER.match(title or "")
    return (match.group(1).lstrip("0") or "0") if match else None


class ControllerError(Exception):
    """A problem the operator should be told about, worded for them."""


MEDIA_TYPES = ("image", "video", "audio")      # what the Media Library holds; the search box can narrow to one


def _media_detail(row):
    """"Picture", or "Video 1:30" / "Sound 0:45" for the search results."""
    word = {"image": "Picture", "video": "Video", "audio": "Sound"}.get(row["media_type"], "File")
    ms = row["duration_ms"]
    if row["media_type"] == "image" or not ms:
        return word
    seconds = int(round(ms / 1000))
    return f"{word} {seconds // 60}:{seconds % 60:02d}"


@dataclass(frozen=True)
class LibraryEntry:
    kind: str            # "song", "presentation" or "media" (a video or sound clip)
    id: int
    title: str
    detail: str = ""     # author for a song, page count for a deck, length for a video or sound
    media_type: str = ""  # for kind "media": "image", "video" or "audio"


@dataclass(frozen=True)
class Tray:
    """A passage held ready to show: the verses of one chapter (or part of it), not yet on screen."""
    translation_id: int
    translation: str          # abbreviation, e.g. "KJV"
    book_id: int
    book: str
    chapter: int
    reference: str            # what was loaded: "John 3" or "John 3:1-21"
    verses: tuple             # ((number, text), ...)

    @property
    def title(self):
        """The title drawn over the verses: always the book and chapter."""
        return format_reference(self.book, self.chapter)


@dataclass(frozen=True)
class ServiceRow:
    item_id: int
    position: int
    item_type: str
    title: str
    is_live: bool        # the slide on screen came from this item
    theme_id: int | None = None   # the item's own projector theme (None: it follows the service or Bible theme)
    overlay_theme_id: int | None = None   # the item's own overlay theme (None: it follows the service)
    timing: dict | None = None    # decks only: {"custom": bool, "seconds": n, "end_action": name}
    parts: dict | None = None     # songs only: {"total": n, "used": n} parts of the song's order
    slides: int | None = None     # Bible items only: how many slides the passage became
    note: str | None = None       # Bible items only: a plain-words warning when something will not sit well
    slide_list: tuple = ()        # Bible items only: one short line per slide ("v16-17  For God so loved...")
    outline: tuple | None = None  # songs only: every part (left-out ones too) with the slides it made, for the options list
    slide_numbers: tuple = ()     # Bible items only: each slide's number in the whole service (matches slide_list)
    last_slide: bool = False      # the live slide is this item's last: the next one belongs to another item (or none)
    deck_slides: tuple = ()       # slideshow items only: ({"index": slide number in the whole service, "label": "Offering" or "Slide 7"}, ...)


def _one_notification(method):
    """Run a method so that all the changes it causes tell listeners once, when it finishes."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._batch():
            return method(self, *args, **kwargs)
    return wrapper


def clickerhelper_actions():
    """The Trigger URL actions a clicker button may press (the list lives with the web API)."""
    from .operator_api import TRIGGER_ALIASES, TRIGGER_NAMES
    return TRIGGER_NAMES + tuple(TRIGGER_ALIASES)


class OperatorController:
    def __init__(self, db, outputs=None, build_settings=None, translation=None, media_root=None):
        self.db = db
        self.media_root = Path(media_root) if media_root else None
        # A real run starts every verse on its own slide, and "fit as many as will fit" measures the projector.
        # Settings passed in (tests, scripts) are taken as they are, with the fixed character limit.
        self.builder = SlideBuilder(db, build_settings if build_settings is not None
                                    else BuildSettings(scripture_split="verse"))
        self.builder.use_capacity = build_settings is None
        self.session = LiveSession()
        self.styles = ThemeStyles(db)
        self.builder.bible_theme_id = self.bible_theme_id
        self.outputs = list(outputs) if outputs is not None else default_outputs()
        self._sync_projector_aspect()
        self.service_id = None
        self.mode = None                      # None, "service" or "item"
        self._overlay_slides = ()
        self._item_entry = None               # the library entry that is live on its own (mode "item")
        self._tray = None
        self._cue = None                                  # (service id, item id) of the ready verse
        self._confidence_hides_words = self.db.get_setting("confidence_hides_words") != "0"   # Settings, Screens
        self._timer_style = self._read_timer_style()       # the countdown and alert look, kept in memory for the server
        self._home = self._read_home()                    # the home screen picture, kept in memory for the server
        self._hook = self._read_hook()                    # the trigger-URL secret, kept in memory for the server
        self._overlay_from_tray = False                   # the verse on top came from the tray (not a typed reference)
        self._translation = translation
        self._listeners = []
        self._changed = threading.Condition()
        self._version = 0
        self._batch_depth = 0
        self._batch_dirty = False
        self._lock = threading.RLock()
        self._server = None
        self.screen_windows = ScreenWindows()              # full-screen windows on chosen monitors; see set_screen_profile_dir
        self.imports = None                   # made when the server starts (needs the media folder)
        self.session.subscribe(lambda state: self._notify())
        # Timed slide advance (decks): see _rearm_timer.
        self._timer_lock = threading.Lock()
        self._timer = None
        self._timer_key = None
        self._timer_gen = 0
        self._timer_paused = False
        self._timer_scale = 1.0               # tests shrink this so a "second" is a few milliseconds
        self.session.subscribe(lambda state: self._rearm_timer())
        # Video and sound clips: when one runs to its end the program moves on (see _rearm_clip_timers).
        self._clip_lock = threading.Lock()
        self._clip_timers = {}                # "media" / "sound" -> [key, timer, generation]
        self.media_advance = True             # go to the next item when a video or sound slide ends
        self._clip_problem = None             # (file the screens could not play, plain sentence), until that file is no longer live
        self.session.subscribe(lambda state: self._rearm_clip_timers())
        self.session.subscribe(self._drop_verse_off_decks)
        self.shutdown_requested = threading.Event()     # set by the Settings tab's shut-down button; the main loop then closes everything
        self.clicker = None                   # a core.clickerhelper.ClickerHelper once a run gives it a folder
        self.usage_log = None                 # a core.usagelog.UsageLog once a run gives it a file (see set_usage_log)
        self._usage_key = None                # the song that is live now, so moving between its slides logs nothing
        self.session.subscribe(self._log_song_usage)
        self.global_keys = GlobalKeys(self, default_backend())     # off until the operator turns it on

    VERSE_DECK_ONLY = "A verse goes on top of a slideshow only. Put a slideshow slide live first."

    @staticmethod
    def _is_deck(slide):
        return slide is not None and slide.item_type == "presentation"

    def verse_over_ok(self):
        """Verses may be put over the live slide only while that slide is a deck's."""
        return self._is_deck(self.session.state().base)

    def _need_deck_live(self):
        if not self.verse_over_ok():
            raise ControllerError(self.VERSE_DECK_ONLY)

    def _drop_verse_off_decks(self, state):
        """Moving off a deck takes a showing verse down (it stays loaded behind the deck only)."""
        if state.overlay is not None and not self._is_deck(state.base):
            self.session.hide_overlay()

    def set_usage_log(self, path):
        """Start writing the song usage log to `path` (None stops it)."""
        self.usage_log = usagelog.UsageLog(path) if path else None

    # -- clicker helper (Windows): the bundled AutoHotkey runs a script built here ---------------------------------
    def set_clicker_dir(self, folder):
        """Where the helper's script is written (None: no helper). Tests pass their own helper in `self.clicker`."""
        self.clicker = clickerhelper.ClickerHelper(folder) if folder else None

    def _read_clicker(self):
        try:
            raw = json.loads(self.db.get_setting("clicker") or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        rows = raw.get("rows") if isinstance(raw.get("rows"), list) else []
        return {"rows": clickerhelper.clean_rows(rows, clickerhelper_actions()), "auto": raw.get("auto") is True}

    def clicker_view(self):
        """What the page shows: {"available", "running", "auto", "rows"}."""
        helper = self.clicker
        saved = self._read_clicker()
        return {"available": bool(helper and helper.available()), "running": bool(helper and helper.running()),
                "auto": saved["auto"], "rows": saved["rows"]}

    def start_clicker(self, rows, auto=None):
        """Remember the buttons and run the helper. `rows` are cleaned here: only listed keys and actions get in."""
        helper = self.clicker
        if helper is None or not helper.available():
            raise ControllerError("The clicker helper needs Windows and the AutoHotkey that comes with ChurchLight")
        if self._server is None:
            raise ControllerError("The program's web server is not running")
        hook = self.hook_settings()
        if not hook["on"]:
            raise ControllerError("Turn Remote button links on first (Settings, Advanced)")
        rows = clickerhelper.clean_rows(rows, clickerhelper_actions())
        if not rows:
            raise ControllerError("Add at least one button with a key")
        saved = {"rows": rows, "auto": self._read_clicker()["auto"] if auto is None else bool(auto)}
        self.db.set_setting("clicker", json.dumps(saved))
        try:
            helper.start(self._server.address[1], hook["token"], rows)
        except (OSError, ValueError) as err:
            raise ControllerError(str(err)) from err
        self._notify()

    def set_screen_profile_dir(self, folder):
        """Where the browser keeps its settings for the screens it opens on monitors (beside the database)."""
        self.screen_windows.profile_root = Path(folder)

    def show_screen(self, name, monitor_index, operator_at=None, test=True, word=None):
        """Show the output `name` (only a configured output can be opened) full screen on a monitor. With `test` it is
        the white TEST page (the setup guide's Test); without, the real screen. It stays until hide_screen, and is
        refused on the monitor the operator's own window is on."""
        if not any(o.name == name for o in self.outputs):
            raise ControllerError(f"There is no screen called {name!r}")
        if self._server is None:
            raise ControllerError("The program's web server is not running")
        port = self._server.address[1]
        try:
            address = f"http://127.0.0.1:{port}/view/{name}?kiosk=1"
            if word:                                                  # the screen's name in big letters, until the guide moves on
                address += "&test=1&word=" + urllib.parse.quote(word)
            elif test:
                address += "&test=1"
            self.screen_windows.show(name, address, monitor_index, operator_at=operator_at)
        except ScreenWindowError as err:
            raise ControllerError(str(err)) from err

    def hide_screen(self, name):
        self.screen_windows.hide(name)

    # --- ChurchLight's own projector windows: each screen the church chose a monitor for opens as a full-screen
    # Edge/Chrome window, so video is played by the browser itself rather than by an OBS/XSplit browser source.
    def _read_screens(self):
        try:
            raw = json.loads(self.db.get_setting("screens") or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        names = {o.name for o in self.outputs}
        assign = raw.get("assign") if isinstance(raw.get("assign"), dict) else {}
        assign = {k: v for k, v in assign.items()
                  if k in names and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        return {"assign": assign, "auto": raw.get("auto") is not False}       # on unless the church turned it off

    def screens_view(self):
        """{"available", "monitors", "open", "assign", "auto", "outputs": [{"name", "layout"}]} for the Settings fold."""
        saved = self._read_screens()
        windows = self.screen_windows
        return {"available": windows.available(), "monitors": windows.monitors(), "open": windows.open_names(),
                "assign": saved["assign"], "auto": saved["auto"],
                "outputs": [{"name": o.name, "layout": o.layout} for o in self.outputs]}

    def _screen_label(self, name):
        """A screen's name in plain words for a message: "the Projector", "the Stage screen", ..."""
        word = self.screen_word(name)
        return {"PROJECTOR": "the Projector", "STAGE": "the Stage screen", "CONFIDENCE": "the Confidence screen"}.get(word, f"the {name} screen")

    @staticmethod
    def _sentence(text):
        return text[:1].upper() + text[1:]

    def _refuse_shared_monitor(self, assign):
        """Two screens on one monitor would sit on top of each other: refuse, in plain words, naming both."""
        seen = {}
        for name, index in sorted(assign.items()):
            if index in seen:
                raise ControllerError(f"{self._sentence(self._screen_label(seen[index]))} is already on Monitor {index + 1}, so "
                                      f"{self._screen_label(name)} cannot go there too. Choose a different monitor, or leave one of them unset.")
            seen[index] = name

    def set_screens(self, assign, auto):
        """Save which monitor each screen opens on ({output name: monitor index}; leave a screen out for none)."""
        names = {o.name for o in self.outputs}
        clean = {}
        for name, index in (assign or {}).items():
            if name not in names:
                raise ControllerError(f"There is no screen called {name!r}")
            if isinstance(index, bool) or not isinstance(index, int) or index < 0:
                raise ControllerError("Choose a monitor from the list")
            clean[name] = index
        self._refuse_shared_monitor(clean)
        self.db.set_setting("screens", json.dumps({"assign": clean, "auto": bool(auto)}))
        self._notify()

    def screen_word(self, name):
        """The word the setup guide shows on a screen so the church can tell which monitor it is on."""
        layout = next((o.layout for o in self.outputs if o.name == name), "")
        if layout == "confidence":
            return "CONFIDENCE"
        if layout in ("stage", "ppt_notes"):
            return "STAGE"
        if layout in ("projector", "ppt_verse", "song_full", "verse_full"):
            return "PROJECTOR"
        return name.upper()[:20]

    def assign_screen(self, name, monitor_index, operator_at=None, identify=False):
        """The setup guide: this screen lives on this monitor from now on (None = no monitor). It opens at once as the
        real screen, stays open, and opens again every time ChurchLight starts."""
        saved = self._read_screens()
        if monitor_index is not None:
            self._refuse_shared_monitor(dict(saved["assign"], **{name: monitor_index}))       # before anything opens
        if monitor_index is None:
            saved["assign"].pop(name, None)
            self.set_screens(saved["assign"], saved["auto"])
            self.hide_screen(name)
            return
        self.show_screen(name, monitor_index, operator_at, test=False, word=self.screen_word(name) if identify else None)
        # (refused, with nothing saved, on the operator's monitor)
        saved["assign"][name] = monitor_index
        self.set_screens(saved["assign"], True)

    def screen_positions(self):
        """{output name: (x, y)} of the monitor each assigned screen is on, for the OBS setup."""
        where = {m["index"]: (m["x"], m["y"]) for m in self.screen_windows.monitors()}
        return {n: where[i] for n, i in self._read_screens()["assign"].items() if i in where}

    def _layout_now(self):
        return [{"index": m["index"], "x": m["x"], "y": m["y"], "width": m["width"], "height": m["height"]}
                for m in sorted(self.screen_windows.monitors(), key=lambda m: m["index"])]

    def save_layout(self):
        """Remember how the monitors are arranged right now (called when the screens opened without a problem)."""
        now = self._layout_now()
        if now:
            self.db.set_setting("screen_layout", json.dumps(now))
        return bool(now)

    def layout_changes(self):
        """Plain sentences about how the monitors differ from the last time the screens opened; [] when the same,
        when nothing was saved yet, or when this computer cannot list its monitors."""
        try:
            saved = json.loads(self.db.get_setting("screen_layout") or "[]")
        except ValueError:
            return []
        now = self._layout_now()
        if not saved or not now or not isinstance(saved, list):
            return []
        out = []
        if len(saved) != len(now):
            out.append(f"There were {len(saved)} monitors last time and there are {len(now)} now.")
        before = {m.get("index"): m for m in saved if isinstance(m, dict)}
        for m in now:
            old = before.get(m["index"])
            if old is None:
                continue
            if (old.get("width"), old.get("height")) != (m["width"], m["height"]):
                out.append(f"Monitor {m['index'] + 1} was {old.get('width')}×{old.get('height')} and is now {m['width']}×{m['height']}.")
            elif (old.get("x"), old.get("y")) != (m["x"], m["y"]):
                out.append(f"Monitor {m['index'] + 1} has moved to a different place in the arrangement.")
        return out

    def open_screens(self, operator_at=None):
        """Open every screen that has a monitor, as the real screen. Returns the plain-words problems (a screen whose
        monitor is gone, or is the operator's own); the others still open."""
        problems = []
        taken = {}
        for name, index in sorted(self._read_screens()["assign"].items()):
            if index in taken:                                       # an older saved setup with two screens on one monitor
                problems.append(f"{self._sentence(self._screen_label(name))} was not opened: {self._screen_label(taken[index])} is already on "
                                f"Monitor {index + 1}. Choose a different monitor for one of them.")
                continue
            taken[index] = name
            try:
                self.show_screen(name, index, operator_at, test=False)
            except ControllerError as err:
                problems.append(f"{name}: {err}")
        if taken and not problems and not self.layout_changes():
            self.save_layout()          # remembered the first time and kept fresh while it is unchanged; a CHANGED layout is not
        return problems               # overwritten here (the screens also open by themselves at start), so the warning is still there

    def close_screens(self):
        self.screen_windows.close_all()

    def auto_open_screens(self, delay=3.0):
        """Called once at program start: open the screens when the church ticked "open at start". It waits a moment so
        the operator window is up first, and never opens one over the place the operator window was last seen."""
        saved = self._read_screens()
        if not saved["auto"] or not saved["assign"]:
            return False
        window = self.operator_window()
        point = (window[0] + window[2] // 2, window[1] + window[3] // 2) if window else None

        def run():
            import time
            time.sleep(delay)
            for problem in self.open_screens(point):
                log.info("Screen not opened at start: %s", problem)
        threading.Thread(target=run, name="screens-at-start", daemon=True).start()
        return True

    def request_shutdown(self):
        """Ask the program to close everything down (the clicker helper, the servers, the database). The caller
        that runs the program notices and does it after the answer to the page has gone out."""
        log.info("Shut-down requested from the operator page")
        self.shutdown_requested.set()

    def stop_clicker(self):
        if self.clicker is not None:
            self.clicker.stop()
        self._notify()

    def set_clicker_auto(self, on):
        saved = self._read_clicker()
        saved["auto"] = bool(on)
        self.db.set_setting("clicker", json.dumps(saved))
        self._notify()

    def auto_start_clicker(self):
        """Called once at program start: run the helper when the church asked for that."""
        saved = self._read_clicker()
        if not saved["auto"] or not saved["rows"]:
            return False
        try:
            self.start_clicker(saved["rows"])
        except ControllerError as err:
            log.info("The clicker helper did not start by itself: %s", err)
            return False
        return True

    def _sync_clicker(self):
        """The secret changed or Trigger URLs went off: a running helper must not keep the old secret."""
        helper = self.clicker
        if helper is None or not helper.running():
            return
        hook = self.hook_settings()
        saved = self._read_clicker()
        if hook["on"] and saved["rows"] and self._server is not None:
            helper.start(self._server.address[1], hook["token"], saved["rows"])
        else:
            helper.stop()

    def _log_song_usage(self, state):
        """Write a row each time a song goes live. Leaving it (another item, Clear) and coming back logs it again."""
        slide = state.base
        if slide is None or slide.item_type != "song":
            self._usage_key = None
            return
        item = self._item_entry if self.mode == "item" else None
        key = ("item", item.id) if item is not None else ("service", slide.source_item_id)
        if key == self._usage_key:
            return
        self._usage_key = key
        if self.usage_log is None:
            return
        try:
            if item is not None:
                song_id = item.id
            else:
                row = self.db.get_service_item(slide.source_item_id)
                song_id = row["song_id"] if row else None
            song = self.db.get_song(song_id) if song_id is not None else None
            if song is not None:
                self.usage_log.append(datetime.now(), song)
        except Exception:  # noqa: BLE001 - a full disk or a locked file must never stop the service
            log.exception("Could not write the song usage log")

    @property
    def lock(self):
        """Hold this while using the controller from more than one thread (the terminal and the
        operator web page both do). It is re-entrant, so a method may call another."""
        return self._lock

    # -- change notification -------------------------------------------------
    # Anything an operator window shows can change: the live slide, the service list, a theme,
    # the library. Each change bumps `version` and calls the subscribers, so a window (or the
    # operator web page) redraws when told and never has to poll blindly.
    @property
    def version(self):
        """A number that grows every time something an operator window shows may have changed."""
        with self._changed:
            return self._version

    def subscribe(self, callback):
        """Call `callback()` whenever anything an operator window shows may have changed.

        Callbacks run on the thread that made the change. One that raises is logged and
        skipped; it cannot break the change or the other listeners. Returns an unsubscribe function.
        """
        self._listeners.append(callback)

        def unsubscribe():
            if callback in self._listeners:
                self._listeners.remove(callback)
        return unsubscribe

    def wait_for_change(self, since, timeout):
        """Block until `version` differs from `since` (or `timeout` seconds pass); returns the version."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while self._version == since:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._changed.wait(remaining)
            return self._version

    def library_changed(self):
        """Tell listeners the library has new content (an import finished in this process)."""
        self._notify()

    def _notify(self):
        with self._changed:
            if self._batch_depth:
                self._batch_dirty = True
                return
        self._fire()

    def _fire(self):
        with self._changed:
            self._version += 1
            self._changed.notify_all()
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # noqa: BLE001 - a broken listener must not break the operator
                log.exception("A change listener failed")

    @contextlib.contextmanager
    def _batch(self):
        with self._changed:
            self._batch_depth += 1
        try:
            yield
        finally:
            with self._changed:
                self._batch_depth -= 1
                fire = self._batch_depth == 0 and self._batch_dirty
                if fire:
                    self._batch_dirty = False
            if fire:
                self._fire()

    # -- translations --------------------------------------------------------
    def translations(self):
        return [row["abbreviation"] for row in self.db.conn.execute(
            "SELECT abbreviation FROM translations ORDER BY abbreviation")]

    @property
    def translation(self):
        """The Bible translation typed references use (the first installed, unless set)."""
        installed = self.translations()
        for name in (self._translation, self.db.get_setting("default_translation")):
            if name and name in installed:
                return name
        return installed[0] if installed else None

    @translation.setter
    def translation(self, abbreviation):
        if abbreviation is not None and self.db.get_translation(abbreviation) is None:
            raise ControllerError(f"Translation {abbreviation!r} is not installed")
        self._translation = abbreviation
        self.db.set_setting("default_translation", abbreviation)
        self._notify()

    # -- library -------------------------------------------------------------
    def search_library(self, text="", kinds=("song", "presentation"), media_type=None):
        """Songs, decks and (when "media" is asked for) Media Library files matching `text`, everything when empty.

        `media_type` ("image", "video" or "audio") narrows the Media Library files."""
        text = (text or "").strip()
        entries = []
        if "media" in kinds:
            low = text.lower()
            for r in self.db.media_summaries():
                if media_type and r["media_type"] != media_type:
                    continue
                if low and low not in r["name"].lower():
                    continue
                entries.append(LibraryEntry("media", r["id"], r["name"], _media_detail(r), r["media_type"]))
        if "song" in kinds:
            rows = self.db.search_songs(text) if text else self.db.search_song()
            # drafts are unfinished: they never show up where a song could be run
            entries += [LibraryEntry("song", r["id"], _song_label(r), r["author"] or "")
                        for r in rows if not r["is_draft"]]
        if "presentation" in kinds:
            for r in self.db.search_presentation(name=text or None):
                entries.append(LibraryEntry("presentation", r["id"], r["name"],
                                            f"{r['page_count']} pages"))
        if not text:
            entries.sort(key=lambda e: (e.kind, e.title.lower()))
        return entries[:LIBRARY_LIMIT]

    def suggest_library(self, text="", limit=SUGGEST_LIMIT, kind=None):
        media_type = kind if kind in MEDIA_TYPES else None        # the box's Pictures / Videos / Sound filters
        if media_type:
            kind = "media"
        """Names to offer while someone types in a search box (the box is empty: what was used lately).

        Digits match the number a title starts with, ignoring zeros: 55 finds 055, then 550 to 559.
        Then names that start with the text, then names with a word that does, then everything else
        the search finds (author, words of the song)."""
        text = (text or "").strip()
        if not text:
            return [e for e in self._recent_entries(RECENT_LIMIT) if kind in (None, e.kind)]
        picked, seen = [], set()

        def take(entry):
            if kind not in (None, entry.kind):          # the search box's Songs / Slideshows filter
                return
            if (entry.kind, entry.id) not in seen:
                seen.add((entry.kind, entry.id))
                picked.append(entry)

        if text.isdigit():
            wanted = text.lstrip("0") or "0"
            numbered = []
            for row in self.db.search_song():
                label = _song_label(row)
                number = None if row["is_draft"] else title_number(label)
                if number is not None and number.startswith(wanted):
                    numbered.append((int(number), label.lower(), LibraryEntry("song", row["id"], label)))
            for row in self.db.search_presentation():
                number = title_number(row["name"])
                if number is not None and number.startswith(wanted):
                    numbered.append((int(number), row["name"].lower(), LibraryEntry("presentation", row["id"], row["name"])))
            for _number, _title, entry in sorted(numbered, key=lambda t: t[:2]):
                take(entry)
        low = text.lower()

        def tier(entry):
            title = entry.title.lower()
            if title.startswith(low):
                return 0
            return 1 if any(word.startswith(low) for word in re.split(r"\W+", title)) else 2

        for entry in sorted(self.search_library(text, ("song", "presentation", "media"), media_type), key=tier):     # sorted() is stable: the search's order holds inside a tier
            take(entry)
        return [LibraryEntry(e.kind, e.id, e.title, media_type=e.media_type) for e in picked[:limit]]

    def _recent_entries(self, limit):
        entries = []
        for kind, item_id in self.db.recent_library_items(limit * 2):
            if kind == "song":
                row = self.db.get_song(item_id)
                if row is not None and not row["is_draft"]:
                    entries.append(LibraryEntry("song", item_id, _song_label(row)))
            else:
                row = self.db.get_presentation(item_id)
                if row is not None:
                    entries.append(LibraryEntry("presentation", item_id, row["name"]))
        return entries[:limit]

    @staticmethod
    def ref_for(entry):
        if entry.kind == "song":
            return ItemRef.song(entry.id)
        if entry.kind == "presentation":
            return ItemRef.presentation(entry.id)
        if entry.kind == "media":
            return ItemRef.media(entry.id)
        raise ControllerError(f"Cannot show a {entry.kind!r}")

    def _build(self, ref, over=False):
        """Slides for `ref`. `over`: they are a verse to show over a slideshow, so they are also fitted to the verse-over
        theme's box on the projector (when one is chosen), as well as to the lower third."""
        try:
            # A library item sent straight live looks like the open service's items: the service's themes
            # (the program's starting themes when no service is open).
            if self.service_id is not None:
                projector, overlay = self._service_theme_id(), self._service_overlay_theme_id()
            else:
                projector, overlay = self.default_theme_id, self.default_overlay_theme_id
            # The verse-over look is the projector's overlay only (build_payload applies it there); the lower
            # third keeps its own theme, and the verse is fitted to both boxes.
            return self.builder.build_item(ref, projector, overlay, verse_split=self.service_verse_split(),
                                           verse_over_theme_id=self.verse_over_theme_id if over else None)
        except ValueError as err:
            log.warning("Could not build a %s item: %s", ref.item_type, err)
            raise ControllerError(str(err)) from err

    def preview(self, entry):
        """The slides an entry would show, without putting anything on screen."""
        return self._build(self.ref_for(entry))

    def verse_ref(self, text, translation=None):
        translation = translation or self.translation
        if translation is None:
            raise ControllerError("No Bible translation is installed yet")
        try:
            return ItemRef.from_reference(self.db, translation, text)
        except ValueError as err:
            raise ControllerError(str(err)) from err

    def preview_verse(self, text, translation=None):
        """The slides a typed reference would show, for checking before going live."""
        return self._build(self.verse_ref(text, translation))

    # -- the service ---------------------------------------------------------
    @_one_notification
    def new_service(self, title, service_date=None):
        service_id = self.db.create_service(title, service_date or date.today().isoformat(),
                                            theme_id=self.default_theme_id,
                                            overlay_theme_id=self.default_overlay_theme_id)
        log.info("Created service %s", service_id)
        self.open_service(service_id)
        return service_id

    @_one_notification
    def open_service(self, service_id):
        if self.db.get_service(service_id) is None:
            raise ControllerError(f"There is no service number {service_id}")
        log.info("Opened service %s", service_id)
        with self._lock:
            self.service_id = service_id
            self.mode = "service"
            self._overlay_slides = ()
            self.session.load(self.builder.build_service(service_id))
        self._notify()

    def services(self):
        return list(self.db.list_services())

    def need_service(self):
        """Raise the usual "open a service first" error if none is open."""
        self._need_service()

    def service_rows(self):
        if self.service_id is None:
            return []
        live = self.session.state().base
        live_item = live.source_item_id if live is not None and self.mode == "service" else None
        at = self.session.state().base_index
        slides = self.session.slides
        on_last = (live_item is not None and at is not None
                   and (at + 1 >= len(slides) or slides[at + 1].source_item_id != live_item))
        verses = self._verse_summaries() if self.mode == "service" else {}
        song_slides = self._song_slides() if self.mode == "service" else {}
        scripture_numbers = {}
        if self.mode == "service":
            for number, slide in enumerate(self.session.slides):
                if slide.item_type == "scripture" and slide.source_item_id is not None:
                    scripture_numbers.setdefault(slide.source_item_id, []).append(number)
        scripture_numbers = {k: tuple(v) for k, v in scripture_numbers.items()}
        deck_slides = {}
        if self.mode == "service":
            for number, slide in enumerate(self.session.slides):
                if slide.item_type == "presentation" and slide.source_item_id is not None:
                    deck_slides.setdefault(slide.source_item_id, []).append({"index": number, "label": slide.label})
        return [ServiceRow(r["id"], r["position"], r["item_type"], r["display_title"],
                           live_item is not None and r["id"] == live_item, r["theme_id"], r["overlay_theme_id"],
                           self._deck_timing(r) if r["item_type"] == "presentation" else None,
                           self._song_parts_summary(r) if r["item_type"] == "song" else None,
                           *verses.get(r["id"], (None, None, ())),
                           self._song_outline(r, song_slides.get(r["id"], ())) if r["item_type"] == "song" else None,
                           scripture_numbers.get(r["id"], ()),
                           bool(on_last and r["id"] == live_item),
                           tuple(deck_slides.get(r["id"], ())))
                for r in self.db.get_service_outline(self.service_id)]

    def _song_slides(self):
        """{item id: [(slide number in the whole service, slide), ...]} for the songs of the loaded service."""
        groups = {}
        for number, slide in enumerate(self.session.slides):
            if slide.item_type == "song" and slide.source_item_id is not None:
                groups.setdefault(slide.source_item_id, []).append((number, slide))
        return groups

    def _song_outline(self, outline_row, slides):
        """A song item's parts in order (parts this service leaves out included), each with its slides.

        Every slide carries its number in the whole service, so the page can mark the live one and jump to one."""
        item = self.db.get_service_item(outline_row["id"])
        order = self._song_order(item["song_id"], item["arrangement_id"])
        own = json.loads(item["overrides"]) if item["overrides"] else {}
        skip = skipped_parts(own, len(order))
        made = {}
        for number, slide in slides:
            made.setdefault(slide.part, []).append(
                {"index": number, "line": (slide.text.strip().splitlines() or [""])[0], "continued": slide.continued})
        parts = [{"index": i, "label": row["label"], "skipped": i in skip,
                  "first_line": (row["lyrics"].strip().splitlines() or [""])[0],
                  "slides": made.get(i, [])}
                 for i, row in enumerate(order)]
        if NOTICE_PART in made:             # the title and CCLI slide: listed first or last, and not one of the song's parts
            first = min(s["index"] for s in made[NOTICE_PART]) < min(
                (s["index"] for i in made if i != NOTICE_PART for s in made[i]), default=10 ** 9)
            row = {"index": NOTICE_PART, "label": NOTICE_LABEL, "skipped": False, "first_line": "",
                   "slides": made[NOTICE_PART], "notice": True}
            parts = [row] + parts if first else parts + [row]
        elif own.get("notice") == "off":    # switched off for this service: keep a way to switch it back on, where it would be
            song = self.db.get_song(item["song_id"])
            where = notice_position(notice_settings(self.db), song["notice_slide"] if song else None, None)
            if where is not None:
                row = {"index": NOTICE_PART, "label": NOTICE_LABEL, "skipped": True, "first_line": "",
                       "slides": [], "notice": True}
                parts = [row] + parts if where == "before" else parts + [row]
        return tuple(parts)

    @staticmethod
    def _slide_line(slide):
        numbers = slide.verse_numbers
        tag = ""
        if numbers:
            tag = "v" + (str(numbers[0]) if len(numbers) == 1 else f"{numbers[0]}–{numbers[-1]}") + "  "
        text = " ".join(slide.text.split())
        return tag + (text if len(text) <= 70 else text[:67] + "...")

    def _verse_summaries(self):
        """{item id: (number of slides, warning or None)} for the Bible items of the loaded service.

        The warning is only for something the operator should know before the service: a verse so long it
        had to be split in the middle, or text that has to shrink to fit the lower third's height cap."""
        groups = {}
        for slide in self.session.slides:
            if slide.item_type == "scripture" and slide.source_item_id is not None:
                groups.setdefault(slide.source_item_id, []).append(slide)
        out = {}
        for item_id, slides in groups.items():
            notes = []
            if any(s.continued for s in slides):
                notes.append("a long verse is split across slides")
            for slide in slides:
                style = self.styles.get(slide.overlay_theme_id) if slide.overlay_theme_id is not None else None
                limit = overlay_char_capacity(style, shrink=1)
                if limit is not None and len(slide.text) > limit:
                    notes.append("shrinks on the lower third")
                    break
            lines = tuple(self._slide_line(s) for s in slides)
            out[item_id] = (len(slides), " · ".join(notes) or None, lines)
        return out

    def _song_order(self, song_id, arrangement_id=None):
        """The song's parts as sung, in order: a list of section rows (repeats included)."""
        if arrangement_id is None:
            default = self.db.get_default_arrangement(song_id)
            arrangement_id = default["id"] if default else None
        if arrangement_id is not None:
            return list(self.db.get_arrangement_sections(arrangement_id))
        return list(self.db.get_sections(song_id))

    def _song_parts_summary(self, outline_row):
        item = self.db.get_service_item(outline_row["id"])
        order = self._song_order(item["song_id"], item["arrangement_id"])
        own = json.loads(item["overrides"]) if item["overrides"] else {}
        return {"total": len(order), "used": len(order) - len(skipped_parts(own, len(order)))}

    def song_parts(self, item_id):
        """The song's order for one service item, with which parts this service skips."""
        self._need_service()
        item = self.db.get_service_item(item_id)
        if item is None or item["service_id"] != self.service_id or item["item_type"] != "song":
            raise ControllerError(f"There is no song item number {item_id} in this service")
        order = self._song_order(item["song_id"], item["arrangement_id"])
        own = json.loads(item["overrides"]) if item["overrides"] else {}
        skip = skipped_parts(own, len(order))
        return {"item_id": item_id, "parts": [
            {"index": i, "label": row["label"], "skipped": i in skip,
             "first_line": (row["lyrics"].strip().splitlines() or [""])[0]}
            for i, row in enumerate(order)]}

    @_one_notification
    def set_item_parts(self, item_id, skip):
        """Choose which parts of a song this service leaves out (positions in the song's order).

        An empty list puts the whole song back. At least one part must stay.
        """
        info = self.song_parts(item_id)
        total = len(info["parts"])
        if (not isinstance(skip, list) or any(isinstance(p, bool) or not isinstance(p, int) for p in skip)
                or any(not 0 <= p < total for p in skip)):
            raise ControllerError("Choose parts of this song from its list")
        skip = sorted(set(skip))
        if len(skip) >= total:
            raise ControllerError("Keep at least one part of the song")
        item = self.db.get_service_item(item_id)
        overrides = {k: v for k, v in (json.loads(item["overrides"]) if item["overrides"] else {}).items()
                     if k not in ("parts_skip", "parts_of")}
        if skip:
            overrides.update(parts_skip=skip, parts_of=total)
        self.db.update_service_item(item_id, overrides=overrides or None)
        self._edited()

    def _deck_timing(self, outline_row):
        item = self.db.get_service_item(outline_row["id"])
        deck = self.db.get_presentation(item["presentation_id"]) if item["presentation_id"] else None
        if deck is None:
            return None
        own = json.loads(item["overrides"]) if item["overrides"] else {}
        custom = any(k in own for k in OVERRIDE_KEYS)
        seconds, end = timing_for(deck["auto_seconds"], deck["end_action"], own)
        return {"custom": custom, "seconds": seconds, "end_action": end}

    def _need_service(self):
        if self.service_id is None:
            raise ControllerError("Open or create a service first")

    def _edited(self):
        self._reload_preserving_live()
        self._notify()

    def _refuse_draft(self, entry):
        if entry.kind == "song":
            song = self.db.get_song(entry.id)
            if song is not None and song["is_draft"]:
                raise ControllerError(f"{song['title']!r} is a draft. Publish it first to use it in a service.")

    @_one_notification
    def add_entry(self, entry, position=None):
        self._need_service()
        self._refuse_draft(entry)
        item_id = add_to_service(self.db, self.service_id, self.ref_for(entry), position)
        log.info("Added %s %s to service %s", entry.kind, entry.id, self.service_id)
        self._edited()
        return item_id

    @_one_notification
    def add_verse(self, text, position=None, translation=None):
        self._need_service()
        item_id = add_to_service(self.db, self.service_id, self.verse_ref(text, translation),
                                 position)
        log.info("Added a Bible reference to service %s", self.service_id)
        self._edited()
        return item_id

    @_one_notification
    def add_announcement(self, title, body, position=None):
        self._need_service()
        item_id = add_to_service(self.db, self.service_id,
                                 ItemRef.announcement(title, body), position)
        self._edited()
        return item_id

    @_one_notification
    def remove_item(self, item_id):
        self._need_service()
        row = self.db.get_service_item(item_id)
        self._undo_item = dict(row) if row is not None else None          # one step back: "Removed X. Undo"
        self.db.remove_service_item(item_id)
        log.info("Removed service item %s", item_id)
        self._edited()

    @_one_notification
    def undo_remove_item(self):
        """Put back the item removed last, where it was. Only while that service is still the one open."""
        self._need_service()
        row, self._undo_item = getattr(self, "_undo_item", None), None
        if row is None or row["service_id"] != self.service_id:
            raise ControllerError("There is nothing to put back")
        self.db.restore_service_item(row)
        log.info("Put back a removed service item")
        self._edited()

    @_one_notification
    def move_item(self, item_id, new_position):
        self._need_service()
        self.db.move_service_item(item_id, new_position)
        log.info("Moved service item %s to position %s", item_id, new_position)
        self._edited()

    def _reload_preserving_live(self):
        """Rebuild the service's slides after an edit and put the operator back where they were."""
        with self._lock:
            if self.mode != "service":
                return                       # a single item is live; the edit doesn't touch it
            old = self.session.state()
            old_slides = self.session.slides
            keep = None
            if old.base is not None:
                item = old.base.source_item_id
                offset = sum(1 for s in old_slides[:old.base_index] if s.source_item_id == item)
                keep = (item, offset, old.base.verse_numbers[:1])
            def pick(slides):
                if keep is None:
                    return None
                same = [i for i, s in enumerate(slides) if s.source_item_id == keep[0]]
                if not same:
                    return None
                # Verses that regroup (one per slide <-> several together) keep the verse that was on screen.
                holder = [i for i in same if keep[2] and keep[2][0] in slides[i].verse_numbers]
                return holder[0] if holder else same[min(keep[1], len(same) - 1)]

            self.session.reload(self.builder.build_service(self.service_id), pick)       # a playing video keeps playing
            if old.overlay_loaded and self._overlay_slides:
                self.session.show_overlay(self._overlay_slides, index=old.overlay_index)
                if old.overlay is None:
                    self.session.hide_overlay()

    # -- timed advance -----------------------------------------------------------
    # A deck slide may carry `auto_seconds`. Whenever the live slide changes (the operator pressed
    # Next, or the timer did), the countdown restarts for the new slide, so a manual press always
    # gives a full delay on the slide it lands on. No countdown runs while the screens are blanked,
    # a verse is showing over the slide, nothing is live, or the operator has paused it.
    def _timer_target(self, state):
        slide = state.base
        if (slide is None or state.blank or state.overlay is not None or self._timer_paused
                or not slide.auto_seconds or slide.kind in MEDIA_KINDS):          # a video page moves on when the video ends
            return None
        return (state.base_index, slide.source_item_id, slide.image_path, slide.auto_seconds)

    def _rearm_timer(self):
        with self._timer_lock:
            state = self.session.state()
            key = self._timer_target(state)
            if key == self._timer_key and (key is None or self._timer is not None):
                return
            self._cancel_timer()
            self._timer_key = key
            if key is None:
                return
            self._timer_gen += 1
            gen = self._timer_gen
            self._timer = threading.Timer(key[3] * self._timer_scale, self._timer_fired, (gen,))
            self._timer.daemon = True
            self._timer.start()

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._timer_gen += 1

    def _timer_fired(self, gen):
        with self._lock:
            with self._timer_lock:
                if gen != self._timer_gen:
                    return
                self._timer = None
                self._timer_key = None
            state = self.session.state()
            slide = state.base
            if slide is None or not slide.auto_seconds:
                return
            slides = self.session.slides
            index = state.base_index
            after = index + 1 < len(slides) and slides[index + 1].source_item_id == slide.source_item_id
            step = next_step(index, len(slides), after, slide.end_action)
            log.info("Timer: slide %d of %d -> %s", index + 1, len(slides), step)
            if step == "next":
                self.session.next()
            elif step == "first":
                first = next(i for i, s in enumerate(slides) if s.source_item_id == slide.source_item_id)
                self.session.go_to(first)
                if first == index:             # a one-slide deck: nothing changed, keep counting
                    self._rearm_timer()
            elif step == "blank":
                self.session.set_blank(True)
        self._notify()

    # -- video and sound clips ----------------------------------------------------
    # The pages play the clips themselves; the program only keeps where each one is (core.live) and, so it can
    # move on when a clip ends, one timer per clip set for its remaining time. A pause, seek, restart or new
    # clip changes the clip's anchor, so the timer is re-set; a timer that fires for an old anchor does nothing.
    CLIP_GRACE = 0.25                         # seconds past the end before a clip counts as finished

    def _clip_target(self, view):
        if view is None or not view["playing"] or view["loop"] or not view["duration"]:
            return None
        left = view["duration"] - (view["pos"] + (self.session.now() - view["at"]))      # a clip that starts later (`at` ahead) ends later too
        return (view["at"], view["duration"]), max(0.0, left) + self.CLIP_GRACE

    def _rearm_clip_timers(self):
        state = self.session.state()
        with self._clip_lock:
            for which, view in (("media", state.media), ("sound", state.sound), ("page", state.page_sound)):
                target = self._clip_target(view)
                held = self._clip_timers.get(which)
                key = target[0] if target else None
                if held is not None and held[0] == key and (key is None or held[1] is not None):
                    continue
                if held is not None and held[1] is not None:
                    held[1].cancel()
                gen = (held[2] if held else 0) + 1
                timer = None
                if target is not None:
                    timer = threading.Timer(target[1] * self._timer_scale, self._clip_fired, (which, gen, key[0]))
                    timer.daemon = True
                self._clip_timers[which] = [key, timer, gen]
                if timer is not None:
                    timer.start()

    def _clip_fired(self, which, gen, at):
        with self._lock:
            with self._clip_lock:
                held = self._clip_timers.get(which)
                if held is None or held[2] != gen:
                    return
                held[1] = None
            if which == "sound":
                self.session.sound_finished(at)
            elif which == "page":
                self.session.page_sound_finished(at)
            elif self.session.media_finished(at) and self.media_advance:
                state = self.session.state()
                if state.base_index is not None and state.base_index + 1 < len(self.session.slides):
                    log.info("A clip ended: going to the next slide")
                    self.session.next()
        self._notify()

    def set_media_advance(self, on):
        self.media_advance = bool(on)
        self._notify()

    def media_control(self, action, value=None):
        """One control for the clip that is live: play, pause, restart, seek (seconds), loop, volume (0-1), mute."""
        with self._lock:
            s = self.session
            if action not in ("volume", "mute") and s.state().media is None:
                raise ControllerError("No video or sound slide is live")
            ops = {"play": lambda: s.media_play(), "pause": lambda: s.media_pause(),
                   "restart": lambda: s.media_restart(), "seek": lambda: s.media_seek(value),
                   "loop": lambda: s.media_set_loop(value), "volume": lambda: s.set_media_volume(value),
                   "mute": lambda: s.set_media_muted(value)}
            if action not in ops:
                raise ControllerError(f"Unknown clip control {action!r}")
            ops[action]()
        self._notify()

    def sound_control(self, action, value=None):
        """One control for the music under the slides: pause, resume, restart, seek, loop, volume, mute, stop."""
        with self._lock:
            s = self.session
            if action not in ("volume", "mute") and s.state().sound is None:
                raise ControllerError("No music is playing")
            ops = {"pause": lambda: s.sound_pause(), "resume": lambda: s.sound_resume(),
                   "restart": lambda: s.sound_restart(), "seek": lambda: s.sound_seek(value),
                   "loop": lambda: s.sound_set_loop(value), "volume": lambda: s.set_sound_volume(value),
                   "mute": lambda: s.set_sound_muted(value), "stop": lambda: s.sound_stop()}
            if action not in ops:
                raise ControllerError(f"Unknown music control {action!r}")
            ops[action]()
        self._notify()

    def media_toggle(self):
        """Play or pause the live clip (one button for a clicker)."""
        view = self.session.state().media
        self.media_control("pause" if view is not None and view["playing"] else "play")

    def sound_toggle(self):
        view = self.session.state().sound
        self.sound_control("pause" if view is not None and view["playing"] else "resume")

    def page_sound_control(self, action, value=None):
        """Controls for the sound file the live slideshow page carries: pause, resume, restart, loop."""
        with self._lock:
            s = self.session
            if s.state().page_sound is None:
                raise ControllerError("The live slide has no sound of its own")
            ops = {"pause": lambda: s.page_sound_pause(), "resume": lambda: s.page_sound_resume(),
                   "restart": lambda: s.page_sound_restart(), "loop": lambda: s.page_sound_set_loop(value)}
            if action not in ops:
                raise ControllerError(f"Unknown page sound control {action!r}")
            ops[action]()
        self._notify()

    def play_music(self, media_id, loop=False):
        """Start a sound file as the music under the slides."""
        with self._lock:
            asset = self.db.get_media_asset(media_id)
            if asset is None or asset["media_type"] != "audio":
                raise ControllerError("Choose a sound file (MP3 or WAV) to play as music")
            self.session.sound_play(asset["path"], asset["name"], asset["duration_ms"] or 0, loop)
        self._notify()

    def sound_output(self):
        """The one screen that makes the sound: the first output drawn with the projector layout."""
        for output in self.outputs:
            if output.layout in PLAYER_LAYOUTS:
                return output.name
        return None

    def media_view(self):
        """What the Run page shows about clips: the live clip, the music, and whether the program moves on at the end."""
        state = self.session.state()
        now = self.session.now()

        def show(view):
            if view is None:
                return None
            return dict(view, position=clip_position(view, now), now=now)
        live = show(state.media)
        problem = None
        if self._clip_problem and live and live.get("src") == self._clip_problem[0]:
            problem = self._clip_problem[1]
        return {"live": live, "music": show(state.sound), "page": show(state.page_sound), "advance": self.media_advance,
                "sound_output": self.sound_output(), "problem": problem}

    def report_clip_problem(self, src, output=""):
        """A screen says it cannot play the live video (it is in a format the browser cannot show, or the file is damaged).

        Only the video that is live counts, and only the first report per file changes anything, so a screen that keeps
        asking cannot make the page flicker. The operator page shows the message with what to do about it."""
        state = self.session.state()
        live = state.media
        if live is None or live.get("src") != str(src):
            return
        if self._clip_problem and self._clip_problem[0] == live["src"]:
            return
        name = live.get("name") or "This video"
        where = f"The \"{output}\" screen" if output else "A screen"
        problem, _ = (None, None)
        if self.media_root:
            problem, _ = check_video(Path(self.media_root) / live["src"])
        if problem:
            text = (f"{where} could not play \"{name}\". It is probably saved in a format browsers, OBS and XSplit cannot show. "
                    "Press Clear live, then re-save the video as an H.264 MP4 with HandBrake (see \"How to prepare a video\" in "
                    "the Media Library tab) and add the new file.")
        else:
            text = (f"{where} had trouble playing \"{name}\". The file looks like the right kind, so the computer may be too busy "
                    "or the video too heavy. Press Clear live and play it again. If it keeps happening, re-save it with HandBrake's "
                    "\"Fast 1080p30\" preset (see \"How to prepare a video\" in the Media Library tab).")
        self._clip_problem = (live["src"], text)
        log.warning("Screen %s could not play %s", output or "?", live["src"])
        self._notify()

    # -- the video and sound library ---------------------------------------------
    def _video_flag(self, path, seconds=None):
        """{"level": "problem" or "warning", "text"} when a video in the library may not play well, else None.

        It reads only the movie header, and the answer is remembered until the file changes, so listing the library stays quick."""
        try:
            info = Path(path).stat()
        except OSError:
            return None, ""
        key = (str(path), info.st_size, info.st_mtime_ns, seconds)
        cache = self.__dict__.setdefault("_video_flags", {})
        if key not in cache:
            problem, warnings = check_video(path, seconds)
            flag = None
            if problem:
                flag = {"level": "problem", "text": problem}
            elif warnings:
                flag = {"level": "warning", "text": " ".join(warnings)}
            cache[key] = (flag, describe_video(path, seconds))
        return cache[key]

    def media_list(self):
        """The Media Library: pictures, video and sound that were added to it."""
        root = Path(self.media_root) if self.media_root else None
        rows = []
        for r in self.db.media_summaries():
            size = 0
            flag = None
            spec = ""
            if root is not None:
                with contextlib.suppress(OSError):
                    size = (root / r["path"]).stat().st_size
                if r["media_type"] == "video":
                    flag, spec = self._video_flag(root / r["path"], (r["duration_ms"] or 0) / 1000.0) or (None, "")
            row = dict(id=r["id"], name=r["name"], kind=r["media_type"], path=r["path"],
                       seconds=(r["duration_ms"] or 0) / 1000.0, uses=r["uses"], decks=r["decks"], size=size)
            if flag:
                row["flag"] = flag
            if spec:
                row["spec"] = spec
            rows.append(row)
        return rows

    def media_sources(self):
        """Pictures that came from somewhere other than the Media Library, so one page can show where everything is:
        the pictures inside each slideshow, the pictures themes use as backgrounds, and the home screen picture."""
        decks = [dict(id=r["id"], name=r["name"], pages=r["page_count"],
                      pictures=[p["image_path"] for p in self.db.get_presentation_pages(r["id"])
                                if not p["media_id"]][:4])
                 for r in self.db.presentation_summaries()]
        decks = [d for d in decks if d["pictures"]]
        themes = []
        for theme in self.theme_details():
            style = theme.get("style") or {}
            image = style.get("background_image") if isinstance(style, dict) else None
            if image:
                themes.append({"id": theme["id"], "name": theme["name"], "path": image})
        home = self.home_screen().get("image")
        return {"decks": decks, "themes": themes, "home": home}

    @_one_notification
    def add_media_file(self, batch, duration_seconds=0):
        """Store an uploaded video or sound file in the library; `duration_seconds` is what the page read from the file."""
        if self.imports is None:
            raise ControllerError("Video and sound need a media folder; start the program with --media")
        try:
            seconds = float(duration_seconds or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if not 0 <= seconds < 24 * 3600:
            seconds = 0.0
        path, name, kind = self.imports.take_media(batch, seconds or None)
        title = self._title(name, "name")
        if kind == "image":
            seconds = 0.0
        media_id = self.db.insert_media_asset(title, path, kind, None, int(round(seconds * 1000)) or None)
        log.info("Added %s %s (%s, %.1f s)", kind, media_id, path, seconds)
        return media_id

    @_one_notification
    def rename_media(self, media_id, name):
        name = self._title(name, "name")
        if self.db.get_media_asset(media_id) is None:
            raise ControllerError(f"There is no file number {media_id} in the Media Library")
        self.db.update_media_asset(media_id, name=name)

    @_one_notification
    def delete_media(self, media_id):
        asset = self.db.get_media_asset(media_id)
        if asset is None:
            raise ControllerError(f"There is no file number {media_id} in the Media Library")
        row = next(r for r in self.db.media_summaries() if r["id"] == media_id)
        self._refuse_if_used(asset["name"], row["uses"])
        if row["decks"]:
            raise ControllerError(f"{asset['name']!r} is used in {row['decks']} slideshow{'s' if row['decks'] != 1 else ''}. "
                                  "Take it out of those slideshows first.")
        state = self.session.state()
        live = (state.media, state.sound, state.page_sound)
        if any(view is not None and view["src"] == asset["path"] for view in live) \
                or (state.base is not None and state.base.image_path == asset["path"]):
            raise ControllerError(f"{asset['name']!r} is on the screens now. Clear it first.")
        try:
            self.db.delete_media_asset(media_id)
        except sqlite3.IntegrityError as err:
            raise ControllerError(f"{asset['name']!r} is still used in a service") from err
        if self.media_root is not None:
            root = Path(self.media_root).resolve()
            with contextlib.suppress(OSError):
                target = (root / asset["path"]).resolve()
                if target.is_relative_to(root) and target.is_file():
                    target.unlink()
        log.info("Deleted media %s", media_id)

    def timer_view(self):
        """What the Run page shows about timed advance."""
        slide = self.session.state().base
        available = slide is not None and bool(slide.auto_seconds)
        return {"available": available, "paused": self._timer_paused,
                "running": available and self._timer is not None,
                "seconds": slide.auto_seconds if available else 0,
                "end_action": slide.end_action if available else "stop"}

    @_one_notification
    def set_timer_paused(self, paused):
        """Pause or resume timed advance. Resuming gives the current slide a full delay."""
        self._timer_paused = bool(paused)
        self._rearm_timer()
        self._notify()

    # -- going live ----------------------------------------------------------
    @_one_notification
    def go_live_entry(self, entry):
        """Send one library item straight live, replacing what was live."""
        self._refuse_draft(entry)
        slides = self._build(self.ref_for(entry))
        log.info("Live (single item): %s %s, %d slides", entry.kind, entry.id, len(slides))
        with self._lock:
            self.mode = "item"
            self._item_entry = entry
            self._overlay_slides = ()
            self.session.load(slides)
            self.session.go_to(0)
        self._notify()

    @_one_notification
    def go_live_service_item(self, item_id):
        """Go live on the first slide of a service item, returning to the service if needed."""
        self._need_service()
        log.info("Live (service item %s)", item_id)
        with self._lock:
            if self.mode != "service":
                self.mode = "service"
                self._overlay_slides = ()
                self.session.load(self.builder.build_service(self.service_id))
            try:
                self.session.go_to_item(item_id)
            except ValueError as err:
                raise ControllerError("That item has nothing to show") from err
        self._notify()

    def step_item(self, delta):
        """Go live on the next (delta=1) or previous (delta=-1) service item.

        With nothing live from the service, forward starts at the first item and
        back does nothing. At either end it stays put and returns False. Items with
        nothing to show are skipped."""
        if self.service_id is None:
            return False
        rows = self.service_rows()
        current = next((i for i, r in enumerate(rows) if r.is_live), None)
        if current is None:
            order = range(len(rows)) if delta > 0 else range(0)
        elif delta > 0:
            order = range(current + 1, len(rows))
        else:
            order = range(current - 1, -1, -1)
        for i in order:
            try:
                self.go_live_service_item(rows[i].item_id)
                return True
            except ControllerError:
                continue
        return False

    def next_item(self):
        return self.step_item(1)

    def previous_item(self):
        return self.step_item(-1)

    def go_to_slide(self, index):
        try:
            return self.session.go_to(index)
        except IndexError as err:
            raise ControllerError(f"There is no slide {index + 1}") from err

    def next(self):
        return self.session.next()

    def previous(self):
        return self.session.previous()

    @_one_notification
    def clear(self):
        log.info("Cleared the live output")
        with self._lock:
            self._overlay_slides = ()
            self.session.clear()
        self._notify()

    def toggle_blank(self):
        blank = self.session.toggle_blank()
        log.info("Blank toggled")
        return blank

    def toggle_text_hidden(self):
        """Take the words off the public screens (the theme background stays); again to bring them back."""
        self.session.toggle_text_hidden()
        hidden = self.session.state().text_hidden
        log.info("Words %s", "hidden" if hidden else "shown")
        return hidden

    def toggle_lower_hidden(self):
        """Take the lower-third outputs off the air (the projector stays); again to bring them back."""
        self.session.toggle_lower_hidden()
        hidden = self.session.state().lower_hidden
        log.info("Lower thirds %s", "hidden" if hidden else "shown")
        return hidden

    # -- how the countdown and the alert look ------------------------------------
    def _read_timer_style(self):
        try:
            raw = json.loads(self.db.get_setting("countdown_style") or "{}")
        except ValueError:
            raw = {}
        return timerstyle.clean(raw)

    def confidence_hides_words(self):
        """Whether Hide words also hides the words on the confidence monitor (on unless a church turns it off)."""
        return self._confidence_hides_words

    @_one_notification
    def set_confidence_hides_words(self, value):
        self._confidence_hides_words = bool(value)
        self.db.set_setting("confidence_hides_words", "1" if value else "0")
        self.session.refresh()
        self._notify()

    def timer_style(self):
        """The complete countdown/alert style (kept in memory: the web server reads it from its own threads)."""
        return dict(self._timer_style)

    @_one_notification
    def set_timer_style(self, changes):
        """Change some fields of the countdown and alert look (see core/timerstyle.py)."""
        try:
            checked = timerstyle.check(changes, self._timer_style)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        style = {**self._timer_style, **checked}
        self.db.set_setting("countdown_style", json.dumps(style))
        self._timer_style = style
        self.session.refresh()                            # every output page redraws with the new look
        self._notify()

    @_one_notification
    def reset_timer_style(self):
        self.db.set_setting("countdown_style", None)
        self._timer_style = timerstyle.clean({})
        self.session.refresh()
        self._notify()

    # -- the song report -------------------------------------------------------
    @staticmethod
    def _report_line(n, song):
        extra = [part for part in (song["author"], f"CCLI {song['ccli_number']}" if song["ccli_number"] else "") if part]
        return f"{n}. {song['title']}" + (" (" + ", ".join(extra) + ")" if extra else "")

    def report_service(self):
        """Add this service's songs to the song report, as a section named with the service and its date.
        Pressing it again for the same service updates that section."""
        if self.service_id is None:
            raise ControllerError("Open a service first")
        service = self.db.get_service(self.service_id)
        try:
            _section, count, replaced = self.db.report_service_songs(self.service_id)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        log.info("Reported %d song(s) for service %s", count, self.service_id)
        return {"title": service["title"], "date": service["service_date"], "songs": count, "replaced": replaced}

    def song_report(self):
        """The report as sections (each with its songs and a text version) and the whole list as one text."""
        sections = {}
        for row in self.db.get_song_report():
            sec = sections.setdefault(row["section"], {
                "section": row["section"], "title": row["service_title"], "date": row["service_date"],
                "reported_at": row["reported_at"], "songs": []})
            sec["songs"].append({"title": row["title"], "author": row["author"] or "", "copyright": row["copyright"] or "",
                                 "ccli": row["ccli_number"] or ""})
        out = list(sections.values())
        for sec in out:
            lines = [self._report_line(n, {"title": s["title"], "author": s["author"], "ccli_number": s["ccli"]})
                     for n, s in enumerate(sec["songs"], start=1)]
            sec["text"] = "\n".join([f"{sec['title']} — {sec['date']}"] + lines)
        return {"sections": out, "text": "\n\n".join(sec["text"] for sec in out)}

    def remove_report_section(self, section):
        if isinstance(section, bool) or not isinstance(section, int):
            raise ControllerError("Choose a section of the report")
        self._undo_report = self.db.report_section_rows(section)
        if not self.db.delete_report_section(section):
            raise ControllerError("That part of the report is already gone")

    def undo_remove_report(self):
        rows, self._undo_report = getattr(self, "_undo_report", None), None
        if not rows:
            raise ControllerError("There is nothing to put back")
        self.db.restore_report_rows(rows)

    # -- countdown and speaker alert ---------------------------------------------
    def start_countdown(self, at=None, minutes=None, label="", now=None):
        """Show a countdown on the projector (and a small timer on the speaker screens). Give `at`, a clock time
        today as "HH:MM" (24 hours), or `minutes` from now. Returns the end time (epoch seconds)."""
        import datetime
        now = time.time() if now is None else now
        label = str(label or "").strip()[:80]
        if (at is None) == (minutes is None):
            raise ControllerError("Give a clock time or a number of minutes")
        if at is not None:
            try:
                hour, minute = (int(part) for part in str(at).strip().split(":")[:2])
                clock = datetime.datetime.fromtimestamp(now).replace(hour=hour, minute=minute, second=0, microsecond=0)
            except (ValueError, TypeError):
                raise ControllerError("Enter the time like 10:00") from None
            end = clock.timestamp()
            if end <= now:
                raise ControllerError("That time has already passed today")
        else:
            try:
                length = float(minutes)
            except (ValueError, TypeError):
                raise ControllerError("Enter the minutes as a number") from None
            if not 0 < length <= 24 * 60:
                raise ControllerError("Enter between 1 and 1440 minutes")
            end = now + length * 60
        self.session.start_countdown(end, label)
        log.info("Countdown started (%s)", label or "no label")
        return end

    def stop_countdown(self):
        self.session.stop_countdown()
        log.info("Countdown stopped")

    def set_alert(self, text):
        """Show `text` on the stage and confidence screens only (empty text clears it)."""
        text = " ".join(str(text or "").split())[:140]
        self.session.set_message(text)
        log.info("Speaker alert %s", "set" if text else "cleared")
        return text

    def countdown_view(self):
        state = self.session.state()
        end, label = state.countdown if state.countdown else (None, "")
        return {"end": end, "label": label, "now": time.time(), "message": state.message}

    # -- the verse overlay -----------------------------------------------------
    @_one_notification
    def show_verse(self, text, translation=None):
        """Show a typed reference over whatever is live (a deck stays where it is)."""
        self._need_deck_live()
        slides = self._build(self.verse_ref(text, translation), over=True)
        log.info("Showing verse overlay %s (%d slides)", slides[0].reference if slides else "?", len(slides))
        with self._lock:
            self._overlay_slides = tuple(slides)
            self._overlay_from_tray = False
            self.session.show_overlay(slides)
        self._notify()
        return slides

    def hide_verse(self):
        return self.session.hide_overlay()

    # -- the verse tray ------------------------------------------------------------
    # A passage is loaded into the tray ahead of time; the operator then picks which verses go
    # on screen, one at a time or several together. The tray is only a holding area: it never
    # touches what is live until a verse is shown, and it does not change the service.
    @property
    def tray(self):
        return self._tray

    @_one_notification
    def load_tray(self, text, translation=None):
        """Hold a chapter ("John 3") or part of one ("John 3:1-21") ready to show."""
        ref = self.verse_ref(text, translation)
        end = ref.verse_end if ref.verse_end is not None else ref.verse_start
        rows = self.db.get_verses(ref.translation_id, ref.book_id, ref.chapter, ref.verse_start, end)
        book = self.db.get_book(ref.book_id)
        trans = self.db.get_translation(ref.translation_id)
        whole_chapter = parse_reference(text).verse_start is None     # (the ref itself spells out 1..last)
        loaded = (format_reference(book["name"], ref.chapter) if whole_chapter
                  else format_reference(book["name"], ref.chapter, ref.verse_start, ref.verse_end))
        tray = Tray(ref.translation_id, trans["abbreviation"], ref.book_id, book["name"], ref.chapter,
                    loaded, tuple((r["verse"], r["text"]) for r in rows))
        log.info("Loaded the verse tray: %s %s (%d verses)", tray.reference, tray.translation, len(rows))
        with self._lock:
            self._tray = tray
        self._notify()
        return tray

    @_one_notification
    def clear_tray(self):
        with self._lock:
            had = self._tray is not None
            self._tray = None
        if had:
            log.info("Cleared the verse tray")
            self._notify()

    def _tray_needed(self):
        if self._tray is None:
            raise ControllerError("Load a chapter into the verse tray first")
        return self._tray

    def _belongs_to_tray(self, slide):
        tray = self._tray
        return (tray is not None and slide is not None and self._overlay_from_tray
                and (slide.reference or "").startswith(tray.title + ":") and slide.label == tray.translation)

    def tray_live_numbers(self):
        """Verse numbers of the tray that are on screen right now (empty if none)."""
        with self._lock:
            slide = self.session.state().overlay
            return tuple(slide.verse_numbers) if self._belongs_to_tray(slide) else ()

    @_one_notification
    def show_tray_verses(self, numbers):
        """Put the chosen tray verses over the live deck slide. They share a slide when they fit."""
        with self._lock:
            self._need_deck_live()
            tray = self._tray_needed()
            known = dict(tray.verses)
            chosen = sorted({int(n) for n in numbers})
            if not chosen:
                raise ControllerError("Choose at least one verse to show")
            missing = [n for n in chosen if n not in known]
            if missing:
                raise ControllerError("The tray has no verse " + ", ".join(str(n) for n in missing))
            try:
                slides = self.builder.build_passage(tray.translation_id, tray.book_id,
                                                    [(n, known[n]) for n in chosen], tray.chapter,
                                                    self._service_theme_id(), self.bible_theme_id,
                                                    self._service_overlay_theme_id(),
                                                    verse_split=self.service_verse_split(),
                                                    verse_over_theme_id=self.verse_over_theme_id)
            except ValueError as err:
                raise ControllerError(str(err)) from err
            log.info("Showing tray verses %s of %s %s", chosen, tray.title, tray.translation)
            self._overlay_slides = tuple(slides)
            self._overlay_from_tray = True
            self.session.show_overlay(slides)
        self._notify()
        return slides

    @_one_notification
    def add_tray_to_service(self, numbers, position=None, one_per_slide=False):
        """Add chosen tray verses to the open service, without touching what is live.

        Verses that are next to each other in the tray become one service item (John 3:16-18);
        separated ones become separate items. Each keeps the tray's "John 3" over "16) text" look.
        """
        self._need_service()
        with self._lock:
            tray = self._tray_needed()
            known = [n for n, _ in tray.verses]
            chosen = sorted({int(n) for n in numbers})
            if not chosen:
                raise ControllerError("Choose at least one verse to add")
            missing = [n for n in chosen if n not in known]
            if missing:
                raise ControllerError("The tray has no verse " + ", ".join(str(n) for n in missing))
            runs, run = [], []
            for n in chosen:
                if run and known.index(n) != known.index(run[-1]) + 1:
                    runs.append(run)
                    run = []
                run.append(n)
            runs.append(run)
            ids = []
            for offset, run in enumerate(runs):
                ref = ItemRef.passage(tray.translation_id, tray.book_id, tray.chapter, run[0],
                                      run[-1] if run[-1] != run[0] else None,
                                      overrides={"title_first": True, **({"one_per_slide": True} if one_per_slide else {})})
                ids.append(add_to_service(self.db, self.service_id, ref,
                                          None if position is None else position + offset))
            log.info("Added %d tray item(s) to service %s", len(ids), self.service_id)
            self._edited()
        return ids

    # -- cue and take -------------------------------------------------------------
    # Cue adds a passage to the service without touching the screens and remembers it as "ready".
    # Take puts the ready item live (replacing what is on screen, not going over it). Nothing is timed:
    # the cue stays until it is taken, replaced, cleared, or its item leaves the service.
    @property
    def cue(self):
        """The ready item as {"item_id", "title"}, or None."""
        mine = self._cue
        if mine is None or self.service_id is None or mine[0] != self.service_id:
            return None
        for row in self.db.get_service_outline(self.service_id):
            if row["id"] == mine[1]:
                return {"item_id": row["id"], "title": row["display_title"]}
        return None

    @_one_notification
    def cue_tray(self, numbers, to_end=False):
        """Add tray verses to the service, one verse per slide, and make the first one ready.

        With `to_end`, the first chosen verse and everything after it in the chapter is added."""
        self._need_service()
        with self._lock:
            tray = self._tray_needed()
            chosen = sorted({int(n) for n in numbers})
            if chosen and to_end:
                chosen = [n for n, _ in tray.verses if n >= chosen[0]]
        ids = self.add_tray_to_service(chosen, one_per_slide=True)
        with self._lock:
            self._cue = (self.service_id, ids[0])
        self._notify()
        return ids

    @_one_notification
    def take_cue(self):
        """Make the ready item live. The blank setting is left as it is."""
        ready = self.cue
        if ready is None:
            raise ControllerError("Nothing is ready. Cue a verse first.")
        self.go_live_service_item(ready["item_id"])
        self._cue = None
        self._notify()
        return ready

    def clear_cue(self):
        if self._cue is not None:
            self._cue = None
            self._notify()

    @_one_notification
    def tray_step(self, delta):
        """Show the verse after (delta 1) or before (delta -1) the one from the tray on screen."""
        if delta not in (1, -1):
            raise ControllerError("Step by one verse at a time")
        with self._lock:
            tray = self._tray_needed()
            state = self.session.state()
            slide = (self._overlay_slides[state.overlay_index]
                     if state.overlay_loaded and self._overlay_slides else None)
            numbers = [n for n, _ in tray.verses]
            if not self._belongs_to_tray(slide):
                if delta < 0:
                    raise ControllerError("No verse from the tray is showing yet")
                target = numbers[0] if numbers else None
            elif delta > 0:
                later = [n for n in numbers if n > max(slide.verse_numbers)]
                if not later:
                    raise ControllerError(f"That is the last verse of {tray.reference}")
                target = later[0]
            else:
                earlier = [n for n in numbers if n < min(slide.verse_numbers)]
                if not earlier:
                    raise ControllerError(f"That is the first verse of {tray.reference}")
                target = earlier[-1]
            if target is None:
                raise ControllerError("The tray is empty")
        return self.show_tray_verses([target])

    def reshow_verse(self):
        self._need_deck_live()
        try:
            return self.session.show_overlay()
        except ValueError as err:
            raise ControllerError("No verse has been shown yet") from err

    def verse_showing(self):
        return self.session.state().overlay is not None

    def verse_on(self):
        """Put a verse over the slide: the one just hidden if there is one, otherwise the first verse of the tray.
        Already showing means nothing to do."""
        if self.verse_showing():
            return
        try:
            self.reshow_verse()
        except ControllerError as hidden_error:
            if self._tray is None:
                raise
            try:
                self.tray_step(1)
            except ControllerError:
                raise hidden_error from None

    def verse_off(self):
        """Take the verse off the slide (it can be brought back with verse_on). Nothing showing is fine."""
        if self.verse_showing():
            self.hide_verse()

    def toggle_verse(self):
        if self.verse_showing():
            self.verse_off()
        else:
            self.verse_on()

    def verse_next(self):
        return self.session.overlay_next()

    def verse_forward(self):
        """One forward button for a verse over a slideshow: the next part of the verse showing, and only when every
        part has been shown, the next verse of the tray (the first part of it). Never skips a part."""
        state = self.session.state()
        showing = state.overlay is not None
        if showing and self._overlay_slides and state.overlay_index + 1 < len(self._overlay_slides):
            self.session.overlay_next()
            return
        if showing and not self._belongs_to_tray(state.overlay):
            raise ControllerError("That was the last part of the verse")
        self.tray_step(1)

    def verse_back(self):
        """The same, backwards: the previous part, and from the first part, the previous verse of the tray, landing
        on its last part so the parts can be walked back one at a time."""
        state = self.session.state()
        if state.overlay is None:
            raise ControllerError("No verse is showing")
        if state.overlay_index > 0:
            self.session.overlay_previous()
            return
        if not self._belongs_to_tray(state.overlay):
            raise ControllerError("That is the first part of the verse")
        self.tray_step(-1)
        while self.session.overlay_next():
            pass

    def verse_previous(self):
        return self.session.overlay_previous()

    # -- managing the library ---------------------------------------------------------
    # Songs are edited as plain text (see core/songtext.py). Anything a service uses is protected:
    # it cannot be deleted until it is taken out of those services.
    @staticmethod
    def _title(text, what="title"):
        text = (text or "").strip()
        if not text:
            raise ControllerError(f"The {what} cannot be empty")
        if len(text) > 200:
            raise ControllerError(f"The {what} is too long (200 characters at most)")
        return text

    @staticmethod
    def _author(text):
        text = (text or "").strip()
        if len(text) > 200:
            raise ControllerError("The author is too long (200 characters at most)")
        return text or None

    @staticmethod
    def _parse_song(text):
        try:
            return parse_song_text(text)
        except SongTextError as err:
            raise ControllerError(str(err)) from err

    @staticmethod
    def _ccli(text):
        text = (text or "").strip()
        if not text:
            return None
        if not text.isdigit() or len(text) > 10:
            raise ControllerError("The CCLI song number is only digits (up to 10), for example 7068424")
        return text

    def church_ccli(self):
        """The church's own CCLI license number (stored once, not per song), or ''."""
        return self.db.get_setting("church_ccli", "")

    @_one_notification
    def set_church_ccli(self, text):
        text = (text or "").strip()
        if text and (not text.isdigit() or len(text) > 12):
            raise ControllerError("The CCLI license number is only digits (up to 12)")
        self.db.set_setting("church_ccli", text or None)
        self._edited()                       # the title and CCLI slides carry it

    def library_overview(self):
        """Everything in the library with how many services use each thing (for the manage page)."""
        return {
            "church_ccli": self.church_ccli(),
            "songs": [dict(id=r["id"], title=r["title"], operator_name=r["operator_name"] or "", author=r["author"] or "", uses=r["uses"],
                          draft=bool(r["is_draft"]), ccli=r["ccli_number"] or "")
                      for r in self.db.song_summaries()],
            "decks": [dict(id=r["id"], name=r["name"], pages=r["page_count"], uses=r["uses"],
                           transition=r["transition"], transition_ms=r["transition_ms"],
                           auto_seconds=r["auto_seconds"], end_action=r["end_action"], image_fit=r["image_fit"],
                           live=self.deck_is_live(r["id"]), has_video=bool(r["has_video"]))
                      for r in self.db.presentation_summaries()],
            "media": self.media_list(),
            "media_sources": self.media_sources(),
            "translations": [dict(id=r["id"], abbreviation=r["abbreviation"], name=r["name"],
                                  verses=r["verse_count"], uses=r["uses"])
                             for r in self.db.translation_summaries()],
        }

    def song_for_editing(self, song_id):
        """A song as the editor shows it: the parts as text, the order as its own line, the CCLI number."""
        song = self.db.get_song(song_id)
        if song is None:
            raise ControllerError(f"There is no song number {song_id}")
        sections = self.db.get_sections(song_id)
        arrangement = self.db.get_default_arrangement(song_id)
        order = ([r["label"] for r in self.db.get_arrangement_sections(arrangement["id"])]
                 if arrangement else [s["label"] for s in sections])
        return {"id": song_id, "title": song["title"], "author": song["author"] or "",
                "draft": bool(song["is_draft"]), "ccli": song["ccli_number"] or "",
                "copyright": song["copyright"] or "", "notice": song["notice_slide"] or "",
                "operator_name": song["operator_name"] or "", "theme_id": song["theme_id"],
                "text": format_song_text([(s["label"], s["lyrics"]) for s in sections], None),
                "order": ", ".join(order)}

    @staticmethod
    def _words(text, order):
        """The words with the separate order box folded in (an empty box keeps any 'Order:' line in the text)."""
        if order is None:
            return text
        if not isinstance(order, str):
            raise ControllerError("The order must be text")
        if not order.strip():
            return text                      # an empty box keeps any 'Order:' line typed in the words
        return replace_order_line(text if isinstance(text, str) else "", order)

    def check_song_text(self, text, order=None):
        """What typed song words would become, or a ControllerError saying what to fix."""
        parsed = self._parse_song(self._words(text, order))
        return {"sections": [{"label": label, "type": kind, "lines": lyrics.count("\n") + 1}
                             for label, kind, lyrics in parsed.sections],
                "order": parsed.order}

    @_one_notification
    def create_song(self, title, author, text, draft=False, ccli=None, order=None, theme_id=None,
                    copyright_text=None, notice=None, operator_name=None):
        title, author = self._title(title), self._author(author)
        parsed = self._parse_song(self._words(text, order))
        theme_id = self._song_theme_choice(theme_id)
        song_id = self.db.create_song_with_sections(title, parsed.sections, order=parsed.order,
                                                    author=author, is_draft=draft,
                                                    ccli_number=self._ccli(ccli))
        if theme_id is not None:
            self.db.update_song(song_id, theme_id=theme_id)
        if copyright_text is not None or notice is not None or operator_name is not None:
            self.db.update_song(song_id, copyright=self._copyright(copyright_text) if copyright_text is not None else None,
                                notice_slide=self._notice_choice(notice),
                                operator_name=self._operator_name(operator_name) if operator_name is not None else _UNSET)
        log.info("Created song %s with %d parts", song_id, len(parsed.sections))
        self._notify()
        return song_id

    @_one_notification
    def update_song(self, song_id, title, author, text, draft=None, ccli=None, order=None, theme_id=NO_CHANGE,
                    copyright_text=None, notice=NO_CHANGE, operator_name=None):
        """Save new words. `draft` True makes it a draft, False publishes it, None leaves it as it is.

        `ccli` None leaves the CCLI number alone; an empty string clears it. `theme_id` is the song's own
        projector theme: a theme number, None for "follow the service", or leave it out to keep it."""
        title, author = self._title(title), self._author(author)
        parsed = self._parse_song(self._words(text, order))
        if draft:
            uses = dict((r["id"], r["uses"]) for r in self.db.song_summaries()).get(song_id, 0)
            if uses:
                raise ControllerError(f"{title!r} is used in {uses} service{'s' if uses != 1 else ''}, "
                                      "so it cannot become a draft. Take it out of those services first.")
        try:
            extra = {} if draft is None else {"is_draft": bool(draft)}
            if ccli is not None:
                extra["ccli_number"] = self._ccli(ccli)
            if theme_id is not NO_CHANGE:
                extra["theme_id"] = self._song_theme_choice(theme_id)
            if copyright_text is not None:
                extra["copyright"] = self._copyright(copyright_text)
            if notice is not NO_CHANGE:
                extra["notice_slide"] = self._notice_choice(notice)
            if operator_name is not None:
                extra["operator_name"] = self._operator_name(operator_name)
            self.db.replace_song(song_id, title, parsed.sections, parsed.order, author, **extra)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        log.info("Edited song %s (%d parts)", song_id, len(parsed.sections))
        self._edited()                       # a service that uses it shows the new words at once

    @staticmethod
    def _operator_name(text):
        """The operator's own name for a song (a hymnal number, say); empty means "use the title"."""
        text = " ".join((text or "").split())
        if len(text) > 120:
            raise ControllerError("The operator name is too long (120 characters at most)")
        return text or None

    @staticmethod
    def _copyright(text):
        text = " ".join((text or "").split())
        if len(text) > 300:
            raise ControllerError("The copyright line is too long (300 characters at most)")
        return text or None

    @staticmethod
    def _notice_choice(choice):
        """A song's own place for its title and CCLI slide: before, after, never, or None (follow the program)."""
        if choice in (None, ""):
            return None
        if choice not in NOTICE_SONG_CHOICES:
            raise ControllerError("Choose before, after or never for the title and CCLI slide")
        return choice

    # -- the title and CCLI slide -------------------------------------------------------------
    def notice_view(self):
        return notice_settings(self.db)

    @_one_notification
    def set_notice_settings(self, position, writers, permission, song_number):
        """The program-wide choices for the title and CCLI slide of every song."""
        if position not in NOTICE_POSITIONS:
            raise ControllerError("Choose before the song, after the song or off")
        writers, permission = " ".join((writers or "").split()), " ".join((permission or "").split())
        if len(writers) > 60 or len(permission) > 80:
            raise ControllerError("That wording is too long")
        self.db.set_setting("notice_slide", position)
        self.db.set_setting("notice_writers", writers)
        self.db.set_setting("notice_permission", permission)
        self.db.set_setting("notice_song_number", "1" if song_number else "0")
        self._edited()

    @_one_notification
    def set_item_notice(self, item_id, off):
        """Leave the title and CCLI slide out of this song in this service (or put it back)."""
        self._need_service()
        item = self.db.get_service_item(item_id)
        if item is None or item["service_id"] != self.service_id or item["item_type"] != "song":
            raise ControllerError(f"There is no song item number {item_id} in this service")
        overrides = {k: v for k, v in (json.loads(item["overrides"]) if item["overrides"] else {}).items() if k != "notice"}
        if off:
            overrides["notice"] = "off"
        self.db.update_service_item(item_id, overrides=overrides or None)
        self._edited()

    def _song_theme_choice(self, theme_id):
        """A song's own theme must be an existing projector theme (None = follow the service)."""
        if theme_id is None:
            return None
        row = self.db.get_theme(theme_id)
        if row is None:
            raise ControllerError("That theme no longer exists")
        if theme_kind(self.db.get_theme_settings(theme_id)) != "projector":
            raise ControllerError("A song can only have a projector theme")
        return theme_id

    @_one_notification
    def publish_song(self, song_id):
        """Turn a draft into a finished song that can be searched and used in services."""
        song = self.db.get_song(song_id)
        if song is None:
            raise ControllerError(f"There is no song number {song_id}")
        self.db.update_song(song_id, is_draft=False)
        log.info("Published song %s", song_id)
        self._notify()

    @_one_notification
    def delete_song(self, song_id):
        song = self.db.get_song(song_id)
        if song is None:
            raise ControllerError(f"There is no song number {song_id}")
        self._refuse_if_used(song["title"], dict((r["id"], r["uses"]) for r in self.db.song_summaries())[song_id])
        try:
            self.db.delete_song(song_id)
        except sqlite3.IntegrityError as err:
            raise ControllerError(f"{song['title']!r} is still used in a service") from err
        log.info("Deleted song %s", song_id)
        self._notify()

    MAX_BATCH_DELETE = 500

    def _delete_many(self, ids, delete_one, what):
        """Delete several library items one by one. An item that cannot go (it is used in a service) is skipped with its
        reason and the rest are deleted. A fresh backup is made first, since a batch cannot be undone. Returns
        {"deleted": n, "skipped": [{"id", "reason"}], "backed_up": bool}."""
        unique = []
        for item in ids:
            if isinstance(item, int) and not isinstance(item, bool) and item not in unique:
                unique.append(item)
        if not unique:
            raise ControllerError(f"Choose at least one {what} to delete")
        if len(unique) > self.MAX_BATCH_DELETE:
            raise ControllerError(f"Delete at most {self.MAX_BATCH_DELETE} {what}s at a time")
        backed_up = backup.make_backup(self.db.path, force=True) is not None
        deleted, skipped = 0, []
        for item in unique:
            try:
                delete_one(item)
                deleted += 1
            except ControllerError as err:
                skipped.append({"id": item, "reason": str(err)})
        log.info("Batch delete of %ss: %d deleted, %d skipped, backup %s", what, deleted, len(skipped), backed_up)
        return {"deleted": deleted, "skipped": skipped, "backed_up": backed_up}

    @_one_notification
    def delete_songs(self, song_ids):
        return self._delete_many(song_ids, self.delete_song, "song")

    @_one_notification
    def delete_decks(self, deck_ids):
        return self._delete_many(deck_ids, self.delete_deck, "slideshow")

    @staticmethod
    def _refuse_if_used(name, uses):
        if uses:
            raise ControllerError(
                f"{name!r} is used in {uses} service{'s' if uses != 1 else ''}. "
                "Take it out of those services first.")

    @_one_notification
    def rename_deck(self, deck_id, name):
        name = self._title(name, "name")
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        self.db.rename_presentation(deck_id, name)
        log.info("Renamed deck %s", deck_id)
        self._edited()

    @_one_notification
    def set_deck_autoplay(self, deck_id, seconds, end_action):
        """Choose a deck's default timed advance (0 seconds = manual) and ending."""
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        try:
            seconds, end_action = check_autoplay(seconds, end_action)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self.db.set_presentation_autoplay(deck_id, seconds, end_action)
        log.info("Deck %s timing: %d s, then %s", deck_id, seconds, end_action)
        self._reload_everything_for_decks()

    def _reload_everything_for_decks(self):
        """Rebuild what is loaded so a deck setting change applies at once, keeping the live slide."""
        self._edited()
        with self._lock:                      # a deck sent live on its own is not part of the service
            entry = self._item_entry
            if self.mode == "item" and entry is not None and entry.kind == "presentation":
                state = self.session.state()
                self.session.load(self._build(self.ref_for(entry)))
                if state.base_index is not None:
                    self.session.go_to(min(state.base_index, len(self.session.slides) - 1))
        self._notify()

    @_one_notification
    def set_item_autoplay(self, item_id, seconds=None, end_action=None):
        """Give one deck in the service its own timing; pass no values to follow the deck again."""
        self._need_service()
        row = self.db.get_service_item(item_id)
        if row is None or row["service_id"] != self.service_id or row["item_type"] != "presentation":
            raise ControllerError(f"There is no slideshow item number {item_id} in this service")
        overrides = {k: v for k, v in (json.loads(row["overrides"]) if row["overrides"] else {}).items()
                     if k not in OVERRIDE_KEYS}
        if seconds is not None or end_action is not None:
            deck = self.db.get_presentation(row["presentation_id"])
            try:
                seconds, end_action = check_autoplay(
                    deck["auto_seconds"] if seconds is None else seconds,
                    deck["end_action"] if end_action is None else end_action)
            except ValueError as err:
                raise ControllerError(str(err)) from err
            overrides.update(auto_seconds=seconds, end_action=end_action)
        self.db.update_service_item(item_id, overrides=overrides or None)
        self._edited()

    @_one_notification
    def set_deck_transition(self, deck_id, transition, milliseconds):
        """Choose how a deck's slides change on the projector, and apply it to what is loaded now."""
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        try:
            transition, milliseconds = check_transition(transition, milliseconds)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self.db.set_presentation_transition(deck_id, transition, milliseconds)
        log.info("Deck %s transition: %s, %d ms", deck_id, transition, milliseconds)
        self._edited()
        with self._lock:                      # a deck sent live on its own is not part of the service
            entry = self._item_entry
            if self.mode == "item" and entry is not None and (entry.kind, entry.id) == ("presentation", deck_id):
                state = self.session.state()
                self.session.load(self._build(self.ref_for(entry)))
                if state.base_index is not None:
                    self.session.go_to(min(state.base_index, len(self.session.slides) - 1))
        self._notify()

    @_one_notification
    def set_deck_image_fit(self, deck_id, image_fit):
        """Choose how a deck's pictures fill the screen, and apply it to what is loaded now."""
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        if image_fit not in ("fit", "fill", "stretch"):
            raise ControllerError("Choose fit, fill or stretch")
        self.db.set_presentation_image_fit(deck_id, image_fit)
        log.info("Deck %s picture size: %s", deck_id, image_fit)
        self._edited()
        with self._lock:
            entry = self._item_entry
            if self.mode == "item" and entry is not None and (entry.kind, entry.id) == ("presentation", deck_id):
                state = self.session.state()
                self.session.load(self._build(self.ref_for(entry)))
                if state.base_index is not None:
                    self.session.go_to(min(state.base_index, len(self.session.slides) - 1))
        self._notify()

    @_one_notification
    def delete_deck(self, deck_id):
        deck = self.db.get_presentation(deck_id)
        if deck is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        uses = dict((r["id"], r["uses"]) for r in self.db.presentation_summaries())[deck_id]
        self._refuse_if_used(deck["name"], uses)
        paths = [p["image_path"] for p in self.db.get_presentation_pages(deck_id)]
        try:
            self.db.delete_presentation(deck_id)
        except sqlite3.IntegrityError as err:
            raise ControllerError(f"{deck['name']!r} is still used in a service") from err
        removed = self._remove_unused_images(paths)
        live = self.session.state().base
        if live is not None and live.image_path in paths:
            self.clear()                      # its pictures are gone; do not leave a broken slide up
        log.info("Deleted deck %s (%d image files removed)", deck_id, removed)
        self._notify()

    # -- the deck editor ---------------------------------------------------------
    def deck_is_live(self, deck_id):
        """True while a slide of this deck is the one on the outputs (blanked or not)."""
        with self._lock:
            base = self.session.state().base
            if base is None or base.item_type != "presentation":
                return False
            if self.mode == "item":
                entry = self._item_entry
                return entry is not None and (entry.kind, entry.id) == ("presentation", deck_id)
            item = self.db.get_service_item(base.source_item_id) if base.source_item_id else None
            return item is not None and item["presentation_id"] == deck_id

    def deck_for_editing(self, deck_id):
        """A deck as the editor shows it: its pages in order, the services that use it, and whether it is live."""
        deck = self.db.get_presentation(deck_id)
        if deck is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        return {
            "id": deck_id, "name": deck["name"], "live": self.deck_is_live(deck_id),
            "services": [{"id": r["id"], "title": r["title"], "date": r["service_date"]}
                         for r in self.db.presentation_service_titles(deck_id)],
            "deck": {"auto_seconds": deck["auto_seconds"], "end_action": deck["end_action"],
                     "transition": deck["transition"], "transition_ms": deck["transition_ms"]},
            "pages": [self._page_view(p) for p in self.db.get_presentation_pages(deck_id)],
        }

    def _page_view(self, p):
        clip = self.db.get_media_asset(p["media_id"]) if p["media_id"] else None
        sound = self.db.get_media_asset(p["sound_id"]) if p["sound_id"] else None
        return {"id": p["id"], "number": p["page_number"], "image": p["image_path"],
                "kind": "video" if clip is not None and clip["media_type"] == "video" else "image",
                "notes": p["notes"] or "", "title": p["title"] or "", "skipped": bool(p["skipped"]),
                "auto_seconds": p["auto_seconds"], "transition": p["transition"], "transition_ms": p["transition_ms"],
                "muted": bool(p["muted"]), "sound_id": p["sound_id"], "sound_name": sound["name"] if sound else "",
                "sound_loop": bool(p["sound_loop"])}

    def _editable_deck(self, deck_id):
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        if self.deck_is_live(deck_id):
            raise ControllerError("This slideshow is live right now, so it cannot be changed. "
                                  "Clear it or move to another item first.")

    @staticmethod
    def _page_ids(ids):
        if (not isinstance(ids, list) or not ids or len(ids) > 2000
                or any(isinstance(i, bool) or not isinstance(i, int) for i in ids)):
            raise ControllerError("Choose one or more slides first")
        return ids

    def _deck_pages_changed(self, deck_id, what):
        log.info("Deck %s: %s", deck_id, what)
        self._reload_everything_for_decks()

    @_one_notification
    def reorder_deck_pages(self, deck_id, ordered_ids):
        self._editable_deck(deck_id)
        try:
            self.db.reorder_presentation_pages(deck_id, self._page_ids(ordered_ids))
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "reordered slides")

    @_one_notification
    def delete_deck_pages(self, deck_id, page_ids):
        self._editable_deck(deck_id)
        try:
            paths = self.db.delete_presentation_pages(deck_id, self._page_ids(page_ids))
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._remove_unused_images(paths)
        self._deck_pages_changed(deck_id, f"deleted {len(paths)} slide(s)")

    @_one_notification
    def duplicate_deck_pages(self, deck_id, page_ids):
        self._editable_deck(deck_id)
        try:
            new_ids = self.db.duplicate_presentation_pages(deck_id, self._page_ids(page_ids))
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, f"duplicated {len(new_ids)} slide(s)")
        return new_ids

    @_one_notification
    def skip_deck_pages(self, deck_id, page_ids, skipped):
        self._editable_deck(deck_id)
        try:
            self.db.set_presentation_pages_skipped(deck_id, self._page_ids(page_ids), bool(skipped))
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "skip changed")

    _NOT_GIVEN = object()

    @_one_notification
    def set_deck_pages_timing(self, deck_id, page_ids, seconds=_NOT_GIVEN, transition=_NOT_GIVEN, ms=_NOT_GIVEN):
        """Set the delay and/or transition of the chosen slides. None follows the deck again;
        an argument left out is not touched. The transition and its length are given together."""
        self._editable_deck(deck_id)
        page_ids = self._page_ids(page_ids)
        given = self._NOT_GIVEN
        changes = {}
        if seconds is not given:
            if seconds is not None:
                try:
                    seconds, _ = check_autoplay(seconds, "stop")
                except ValueError as err:
                    raise ControllerError(str(err)) from err
            changes["auto_seconds"] = seconds
        if transition is not given or ms is not given:
            if transition is given or ms is given or (transition is None) != (ms is None):
                raise ControllerError("Choose a transition and its length together")
            if transition is not None:
                try:
                    transition, ms = check_transition(transition, ms)
                except ValueError as err:
                    raise ControllerError(str(err)) from err
            changes.update(transition=transition, transition_ms=ms)
        if not changes:
            raise ControllerError("Choose a delay or a transition to set")
        try:
            self.db.set_presentation_pages_timing(deck_id, page_ids, **changes)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "timing changed")

    @_one_notification
    def set_deck_page_notes(self, deck_id, page_id, notes):
        self._editable_deck(deck_id)
        if not isinstance(notes, str) or len(notes) > 5000:
            raise ControllerError("Speaker notes must be text of up to 5000 characters")
        try:
            self.db.set_presentation_page_notes(deck_id, page_id, notes)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "notes changed")

    @_one_notification
    def set_deck_page_title(self, deck_id, page_id, title):
        self._editable_deck(deck_id)
        if not isinstance(title, str) or len(title.strip()) > 60:
            raise ControllerError("A slide title can be up to 60 characters")
        try:
            self.db.set_presentation_page_title(deck_id, page_id, title)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "title changed")

    def _take_pictures(self, batch, exactly_one=False):
        if self.imports is None:
            raise ControllerError("Pictures need a media folder; start the program with --media")
        return self.imports.take_pictures(batch, "decks/added", exactly_one)

    @_one_notification
    def add_deck_pictures(self, deck_id, batch, after_page_id=None):
        """Add the uploaded pictures as new slides after `after_page_id` (the end when None)."""
        self._editable_deck(deck_id)
        paths = self._take_pictures(batch)
        try:
            new_ids = self.db.add_presentation_pages(deck_id, [(p, None) for p in paths], after_page_id)
        except ValueError as err:
            self._remove_unused_images(paths)
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, f"added {len(new_ids)} picture(s)")
        return new_ids

    @_one_notification
    def add_deck_library(self, deck_id, media_ids, after_page_id=None):
        """Add pictures and videos from the Media Library as new slides after `after_page_id` (the end when None)."""
        self._editable_deck(deck_id)
        ids = self._page_ids(media_ids)
        entries = []
        for media_id in ids:
            asset = self.db.get_media_asset(media_id)
            if asset is None or asset["media_type"] not in ("image", "video"):
                raise ControllerError("Choose pictures or videos from the Media Library")
            entries.append((asset["path"], None, None, media_id))
        try:
            new_ids = self.db.add_presentation_pages(deck_id, entries, after_page_id)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, f"added {len(new_ids)} file(s) from the Media Library")
        return new_ids

    @_one_notification
    def set_deck_pages_sound(self, deck_id, page_ids, muted=_UNSET, sound_id=_UNSET, sound_loop=_UNSET):
        """Mute a video page's own sound and/or give the pages a sound file from the Media Library (None removes it)."""
        self._editable_deck(deck_id)
        ids = self._page_ids(page_ids)
        try:
            self.db.set_presentation_pages_sound(deck_id, ids, muted, sound_id, sound_loop)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, "changed the sound of some slides")

    @_one_notification
    def replace_deck_picture(self, deck_id, page_id, batch):
        """Give one slide a new picture; its place, notes and skip setting stay."""
        self._editable_deck(deck_id)
        (path,) = self._take_pictures(batch, exactly_one=True)
        try:
            old = self.db.replace_presentation_page_image(deck_id, page_id, path)
        except ValueError as err:
            self._remove_unused_images([path])
            raise ControllerError(str(err)) from err
        self._remove_unused_images([old])
        self._deck_pages_changed(deck_id, "replaced a picture")

    # -- replace slides from a new PDF ---------------------------------------------------------
    # The new PDF is imported as a temporary slideshow (a "replace slides" import job). Page N of it then replaces
    # slide N of the chosen slideshow: only the picture changes, so the title, notes, skip setting, timing and
    # transition stay. Pages past the end are added; slides the new PDF no longer has are kept unless asked.
    def _picture_hash(self, rel):
        import hashlib
        if self.media_root is None:
            return None
        path = self.media_root / rel
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def _replacement_source(self, deck_id, job_id):
        if self.imports is None:
            raise ControllerError("Replacing slides needs a media folder; start the program with --media")
        source_id = self.imports.replacement(job_id, deck_id)
        if self.db.get_presentation(source_id) is None:
            raise ControllerError("The new pages are gone; choose the file again")
        return source_id

    def deck_replace_plan(self, deck_id, job_id):
        """What replacing would do, in plain numbers (nothing is changed)."""
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        source_id = self._replacement_source(deck_id, job_id)
        old = self.db.get_presentation_pages(deck_id)
        new = self.db.get_presentation_pages(source_id)
        changed, same, slides = [], [], []
        for number, (was, now) in enumerate(zip(old, new), start=1):
            a, b = self._picture_hash(was["image_path"]), self._picture_hash(now["image_path"])
            identical = a is not None and a == b
            (same if identical else changed).append(number)
            slides.append({"number": number, "same": identical, "old": was["image_path"], "new": now["image_path"],
                           "title": was["title"] or ""})
        return {"deck_id": deck_id, "job": job_id, "old_pages": len(old), "new_pages": len(new),
                "changed": changed, "same": same, "slides": slides,
                "added": max(0, len(new) - len(old)),
                "extra": list(range(len(new) + 1, len(old) + 1))}

    @_one_notification
    def replace_deck_slides(self, deck_id, job_id, remove_extra=False, keep=(), replace_same=False):
        """Do it: replace the changed pictures, add the new pages, and deal with slides the PDF no longer has:
        they are skipped (kept in the slideshow, left out of the service) or, with `remove_extra`, deleted.

        `keep` lists slide numbers to leave exactly as they are (the new picture is not used for them).
        `replace_same` also replaces the slides that look identical (they are left alone otherwise)."""
        self._editable_deck(deck_id)
        if (not isinstance(keep, (list, tuple)) or any(isinstance(n, bool) or not isinstance(n, int) for n in keep)):
            raise ControllerError("Choose slides by their numbers")
        keep = set(keep)
        plan = self.deck_replace_plan(deck_id, job_id)
        source_id = self._replacement_source(deck_id, job_id)
        old = self.db.get_presentation_pages(deck_id)
        new = self.db.get_presentation_pages(source_id)
        freed = []
        try:
            replaced = [n for n in sorted(plan["changed"] + (plan["same"] if replace_same else [])) if n not in keep]
            plan["replaced"] = replaced
            for number in replaced:
                freed.append(self.db.replace_presentation_page_image(deck_id, old[number - 1]["id"], new[number - 1]["image_path"]))
            if plan["added"]:
                self.db.add_presentation_pages(deck_id, [(p["image_path"], p["notes"], p["title"]) for p in new[len(old):]])
            if plan["extra"]:
                if remove_extra:
                    freed.extend(self.db.delete_presentation_pages(deck_id, [old[n - 1]["id"] for n in plan["extra"]]))
                else:       # kept, but skipped: they stay in the slideshow and can be put back with one click
                    self.db.set_presentation_pages_skipped(deck_id, [old[n - 1]["id"] for n in plan["extra"]], True)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self.db.delete_presentation(source_id)            # the temporary slideshow; the pictures now used here stay
        self._remove_unused_images(freed + [p["image_path"] for p in new])
        self._deck_pages_changed(deck_id, f"replaced {len(plan['replaced'])} slide(s), added {plan['added']}")
        return plan

    @_one_notification
    def cancel_deck_replacement(self, deck_id, job_id):
        """Throw away the imported new pages without changing the slideshow."""
        source_id = self._replacement_source(deck_id, job_id)
        paths = [p["image_path"] for p in self.db.get_presentation_pages(source_id)]
        self.db.delete_presentation(source_id)
        self._remove_unused_images(paths)
        self._notify()

    def _entries_from(self, deck_id, page_ids):
        """(picture, notes, title) for the chosen pages of a deck, in that deck's own order."""
        if self.db.get_presentation(deck_id) is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        wanted = set(self._page_ids(page_ids))
        pages = [p for p in self.db.get_presentation_pages(deck_id) if p["id"] in wanted]
        if len(pages) != len(wanted):
            raise ControllerError("Those slides are not in that slideshow")
        return [(p["image_path"], p["notes"], p["title"]) for p in pages]

    @_one_notification
    def add_deck_pages_from(self, deck_id, source_id, page_ids, after_page_id=None):
        """Copy slides of another deck into this one (the other deck is not changed or locked)."""
        self._editable_deck(deck_id)
        if source_id == deck_id:
            raise ControllerError("Choose a different slideshow. To copy slides inside this slideshow, use Duplicate")
        entries = self._entries_from(source_id, page_ids)
        try:
            new_ids = self.db.add_presentation_pages(deck_id, entries, after_page_id)
        except ValueError as err:
            raise ControllerError(str(err)) from err
        self._deck_pages_changed(deck_id, f"copied {len(new_ids)} slide(s) from deck {source_id}")
        return new_ids

    @_one_notification
    def copy_slides_to_media(self, deck_id, page_ids):
        """Copy the picture of each chosen slide into the Media Library (the slideshow is not changed, so this works while it
        is live). Video slides are already Media Library files and are skipped. Returns {"added": [media ids], "skipped": n}."""
        if self.media_root is None:
            raise ControllerError("The Media Library needs a media folder; start the program with --media")
        deck = self.db.get_presentation(deck_id)
        if deck is None:
            raise ControllerError(f"There is no slideshow number {deck_id}")
        wanted = set(self._page_ids(page_ids))
        pages = self.db.get_presentation_pages(deck_id)
        chosen = [(number, p) for number, p in enumerate(pages, 1) if p["id"] in wanted]
        if len(chosen) != len(wanted):
            raise ControllerError("Those slides are not in that slideshow")
        root = Path(self.media_root).resolve()
        sources = []
        total = 0
        skipped = 0
        for number, page in chosen:
            if page["media_id"] is not None:
                skipped += 1
                continue
            source = (root / page["image_path"]).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ControllerError(f"The picture of slide {number} is missing from the media folder")
            total += source.stat().st_size
            sources.append((number, page, source))
        if not sources:
            raise ControllerError("Those slides are already Media Library files" if skipped else "Choose at least one slide")
        target_dir = root / "media"
        target_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target_dir).free
        if total + 512 * 1024 ** 2 > free:
            raise ControllerError("This computer's disk does not have enough free space to copy those pictures")
        taken = {row["name"].lower() for row in self.db.media_summaries()}
        added = []
        copied = []
        try:
            for number, page, source in sources:
                base = self._title(page["title"] or f"{deck['name'][:150]} - slide {number}", "name")
                name, n = base, 1
                while name.lower() in taken:
                    n += 1
                    name = f"{base} ({n})"
                taken.add(name.lower())
                target = target_dir / (secrets.token_hex(8) + source.suffix.lower())
                shutil.copyfile(source, target)
                copied.append(target)
                added.append(self.db.insert_media_asset(name, "media/" + target.name, "image", None, None))
        except Exception:
            for target in copied:
                with contextlib.suppress(OSError):
                    target.unlink()
            for media_id in added:
                with contextlib.suppress(Exception):
                    self.db.delete_media_asset(media_id)
            raise
        log.info("Copied %d slide(s) of deck %s into the Media Library", len(added), deck_id)
        return {"added": added, "skipped": skipped}

    @_one_notification
    def split_deck(self, deck_id, page_ids, name):
        """Make a new deck from the chosen slides. The original is not changed, so this works while it is live."""
        name = self._title(name, "name")
        entries = self._entries_from(deck_id, page_ids)
        new_id = self.db.add_presentation(name, entries, source_type="images")
        log.info("Made deck %s from %d slide(s) of deck %s", new_id, len(entries), deck_id)
        self._notify()
        return new_id

    def _remove_unused_images(self, relative_paths):
        """Delete slide images no other deck uses, only ever inside the media folder."""
        if self.media_root is None:
            return 0
        root = self.media_root.resolve()
        removed = 0
        for rel in relative_paths:
            still_used = self.db.conn.execute(
                "SELECT 1 FROM presentation_pages WHERE image_path = ? "
                "UNION SELECT 1 FROM media_assets WHERE path = ? LIMIT 1", (rel, rel)).fetchone()
            if still_used:                    # another slide, or the Media Library, still uses this file
                continue
            try:
                target = (root / rel).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    continue
                target.unlink()
                removed += 1
                parent = target.parent
                if parent != root and not any(parent.iterdir()):
                    parent.rmdir()
            except (OSError, ValueError):
                log.warning("Could not remove an unused slide image", exc_info=True)
        return removed

    @_one_notification
    def delete_translation(self, abbreviation):
        row = self.db.get_translation(abbreviation)
        if row is None:
            raise ControllerError(f"No Bible named {abbreviation!r} is installed")
        uses = next(r["uses"] for r in self.db.translation_summaries() if r["id"] == row["id"])
        self._refuse_if_used(abbreviation, uses)
        try:
            self.db.delete_translation(row["id"])
        except sqlite3.IntegrityError as err:
            raise ControllerError(f"{abbreviation!r} is still used in a service") from err
        if self._translation == abbreviation:
            self._translation = None
        log.info("Deleted Bible %s", abbreviation)
        self._notify()

    # -- themes ------------------------------------------------------------------
    def themes(self):
        return list(self.db.list_themes())

    _style_for_kind = staticmethod(style_for_kind)

    @_one_notification
    def save_theme(self, name, style, theme_id=None, build=None, kind=None):
        """Create a theme (or change one's name and look) and redraw the outputs at once.

        `kind` ("projector", "overlay" or "confidence") is chosen when a theme is made and cannot be
        changed afterwards. `style` uses the keys listed in core/themes.py; a bad value is refused with
        a message naming it. `build` (optional) holds the slide-splitting settings. Anything else stored
        in the theme is kept.
        """
        name = (name or "").strip()
        if not name:
            raise ControllerError("A theme needs a name")
        if kind is not None and kind not in KINDS:
            raise ControllerError("A theme is for the projector, an overlay or the confidence monitor")
        try:
            clean = validate_style(style)
            clean_build = validate_build(build) if build is not None else None
            if theme_id is None:
                kind = kind or "projector"
                settings = {"kind": kind, "style": self._style_for_kind(kind, clean)}
                settings.update(clean_build or {})
                theme_id = self.db.add_theme(name, settings)
            else:
                row = self.db.get_theme(theme_id)
                if row is None:
                    raise ControllerError(f"There is no theme number {theme_id}")
                settings = self.db.get_theme_settings(theme_id)
                current = theme_kind(settings)
                if kind is not None and kind != current:
                    raise ControllerError(
                        f"This is a {KIND_NAMES[current].lower()} theme; its kind cannot be changed. "
                        "Make a new theme for the other output.")
                settings["kind"] = current
                settings["style"] = self._style_for_kind(current, clean)
                if clean_build is not None:
                    for key in ("lines_per_slide", "scripture_max_chars", "scripture_split", "chars_per_line", "song_count"):
                        settings.pop(key, None)
                    settings.update(clean_build)
                self.db.update_theme(theme_id, name=name, settings=settings)
        except ControllerError:
            raise
        except ValueError as err:
            raise ControllerError(str(err)) from err
        except Exception as err:             # a duplicate name is the usual cause
            if "UNIQUE" in str(err):
                raise ControllerError(f"A theme named {name!r} already exists") from err
            raise
        self.styles.clear()
        log.info("Saved theme %s", theme_id)
        self._prune_backgrounds()
        self._rebuild_for_themes()
        return theme_id

    @_one_notification
    def import_themes(self, batch):
        """Add the themes from an uploaded themes file. Returns the importer's report."""
        if self.imports is None:
            raise ControllerError("Importing needs a media folder; start the program with --media")
        path, folder = self.imports.take_themes_file(batch)
        try:
            report = import_themes(self.db, path, self.media_root)
        except ImporterError as err:
            raise ControllerError(str(err)) from err
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        self.styles.clear()
        if report.added:
            self._notify()                      # without this the page is never told, and the Themes list stays as it was
        log.info("Imported %d theme(s)", len(report.added))
        return report

    def import_background(self, batch):
        """Store an uploaded picture for use as a projector background; returns its media path.

        Nothing uses it until a theme is saved with it."""
        if self.imports is None:
            raise ControllerError("Pictures need a media folder; start the program with --media")
        return self.imports.take_background(batch)

    def _prune_backgrounds(self, grace_seconds=3600):
        """Delete background pictures no theme uses (a fresh upload is kept for an hour, for unsaved edits)."""
        if self.media_root is None:
            return
        folder = Path(self.media_root) / "backgrounds"
        if not folder.is_dir():
            return
        used = set()
        for row in self.db.list_themes():
            used.add((self.db.get_theme_settings(row["id"]) or {}).get("style", {}).get("background_image"))
        used.add(self.home_screen()["image"])                  # the home screen picture lives in the same folder
        cutoff = time.time() - grace_seconds
        for file in folder.iterdir():
            try:
                if "backgrounds/" + file.name not in used and file.is_file() and file.stat().st_mtime < cutoff:
                    file.unlink()
            except OSError:
                log.warning("Could not remove an unused background picture", exc_info=True)

    def _rebuild_for_themes(self):
        """Rebuild the open service so slide-splitting changes show, then redraw the outputs."""
        if self.service_id is not None:
            self._reload_preserving_live()
        self.session.refresh()

    def theme_details(self):
        """Every theme with its kind, look, slide settings and where it is used (the Themes tab)."""
        out = []
        for row in self.db.list_themes():
            settings = self.db.get_theme_settings(row["id"]) or {}
            build = {k: settings[k] for k in ("lines_per_slide", "scripture_max_chars", "scripture_split", "chars_per_line", "song_count")
                     if k in settings}
            out.append({"id": row["id"], "name": row["name"], "kind": theme_kind(settings),
                        "style": self.styles.get(row["id"]), "build": build,
                        "usage": self._theme_usage(row["id"])})
        return out

    def _theme_usage(self, theme_id):
        usage = self.db.theme_usage(theme_id)
        usage["default"] = theme_id in (self.default_theme_id, self.default_overlay_theme_id)
        usage["bible"] = self.bible_theme_id == theme_id
        usage["verse_over"] = self.verse_over_theme_id == theme_id
        usage["confidence"] = self.confidence_theme_id == theme_id
        usage["outputs"] = sum(1 for o in self.outputs if o.theme_id == theme_id)
        return usage

    @_one_notification
    def duplicate_theme(self, theme_id, name=None):
        row = self.db.get_theme(theme_id)
        if row is None:
            raise ControllerError(f"There is no theme number {theme_id}")
        taken = {r["name"] for r in self.db.list_themes()}
        base = (name or f"{row['name']} copy").strip()
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base} {n}", n + 1
        new_id = self.db.add_theme(candidate, self.db.get_theme_settings(theme_id))
        self.styles.clear()
        self._notify()
        return new_id

    @_one_notification
    def delete_theme(self, theme_id):
        """Delete a theme. Services and items that used it go back to following the default look."""
        if self.db.get_theme(theme_id) is None:
            raise ControllerError(f"There is no theme number {theme_id}")
        self.db.delete_theme(theme_id)
        for key in ("default_theme", "default_overlay_theme", "bible_theme", "confidence_theme", "verse_over_theme"):
            if self.db.get_setting(key) == str(theme_id):
                self.db.set_setting(key, None)
        for i, output in enumerate(self.outputs):
            if output.theme_id == theme_id:
                self.set_output_theme(output.name, None)
        self.styles.clear()
        self._sync_bible_theme()
        self._prune_backgrounds()
        self._rebuild_for_themes()

    def _setting_theme(self, key):
        value = self.db.get_setting(key)
        if value is None or not value.isdigit():
            return None
        return int(value) if self.db.get_theme(int(value)) is not None else None

    def _service_theme_id(self):
        service = self.db.get_service(self.service_id) if self.service_id is not None else None
        return None if service is None else service["theme_id"]

    def service_verse_split(self):
        """The open service's own verse layout: "verse", "chars", or None (follow the theme)."""
        service = self.db.get_service(self.service_id) if self.service_id is not None else None
        return None if service is None else service["verse_split"]

    @_one_notification
    def set_service_verse_split(self, value):
        """How the open service splits verses into slides: "verse", "chars", or None to follow the theme."""
        self._need_service()
        if value in ("", "theme"):
            value = None
        if value not in (None, "verse", "chars"):
            raise ControllerError("Choose one verse per slide, as many as will fit, or follow the theme")
        self.db.update_service(self.service_id, verse_split=value)
        log.info("Service %s verse layout: %s", self.service_id, value or "follows the theme")
        self._rebuild_for_themes()

    def _sync_projector_aspect(self):
        name = next((o.name for o in self.outputs if o.layout == "projector"), None)
        self.builder.projector_aspect = ASPECTS[self.screen_shape(name)] if name else ASPECTS["16:9"]

    def _service_overlay_theme_id(self):
        service = self.db.get_service(self.service_id) if self.service_id is not None else None
        return None if service is None else service["overlay_theme_id"]

    @property
    def default_theme_id(self):
        """The projector theme new services start with (None for no theme)."""
        return self._setting_theme("default_theme")

    @property
    def default_overlay_theme_id(self):
        """The overlay theme new services start with."""
        return self._setting_theme("default_overlay_theme")

    @property
    def bible_theme_id(self):
        """The theme Bible verses use on the projector unless the item or an output picks another."""
        return self._setting_theme("bible_theme")

    @property
    def confidence_theme_id(self):
        """The one global look of the confidence monitor."""
        return self._setting_theme("confidence_theme")

    @property
    def verse_over_theme_id(self):
        """The overlay theme a verse shown over a slideshow uses on the projector only (None: the Bible theme). The lower
        third keeps its own theme."""
        return self.styles.verse_over_theme_id

    def _sync_bible_theme(self):
        self.builder.bible_theme_id = self.bible_theme_id

    def _check_theme(self, theme_id, kind):
        """Refuse an unknown theme, or one made for a different output than `kind`."""
        if theme_id is None:
            return
        row = self.db.get_theme(theme_id)
        if row is None:
            raise ControllerError(f"There is no theme number {theme_id}")
        actual = theme_kind(self.db.get_theme_settings(theme_id))
        if actual != kind:
            raise ControllerError(
                f"{row['name']!r} is a {KIND_NAMES[actual].lower()} theme; "
                f"choose a {KIND_NAMES[kind].lower()} theme here")

    @staticmethod
    def _kind(kind):
        if kind not in ("projector", "overlay"):
            raise ControllerError("Choose the projector or the overlay")
        return kind

    @_one_notification
    def set_default_theme(self, theme_id, kind="projector"):
        """The projector or overlay theme that new services start with."""
        kind = self._kind(kind)
        self._check_theme(theme_id, kind)
        self.db.set_setting("default_theme" if kind == "projector" else "default_overlay_theme", theme_id)
        self._notify()

    @_one_notification
    def set_bible_theme(self, theme_id):
        self._check_theme(theme_id, "projector")
        self.db.set_setting("bible_theme", theme_id)
        self._sync_bible_theme()
        self._rebuild_for_themes()

    @_one_notification
    def set_verse_over_theme(self, theme_id):
        """The overlay theme for a verse shown over a slideshow, used on the projector only (None: it looks like the Bible
        theme). The lower third keeps the service's lower-third theme."""
        self._check_theme(theme_id, "overlay")
        self.db.set_setting("verse_over_theme", theme_id)
        self.styles.reload()
        self._rebuild_for_themes()

    @_one_notification
    def set_confidence_theme(self, theme_id):
        """The confidence monitor has one global look; it is not chosen per service."""
        self._check_theme(theme_id, "confidence")
        self.db.set_setting("confidence_theme", theme_id)
        self.styles.reload()
        self._rebuild_for_themes()

    @_one_notification
    def set_service_theme(self, theme_id, kind="projector"):
        """The projector or overlay theme for the open service."""
        self._need_service()
        kind = self._kind(kind)
        self._check_theme(theme_id, kind)
        field = "theme_id" if kind == "projector" else "overlay_theme_id"
        self.db.update_service(self.service_id, **{field: theme_id})
        self._rebuild_for_themes()

    @_one_notification
    def set_item_theme(self, item_id, theme_id, kind="projector"):
        """Give one service item its own projector or overlay theme (None: follow the service)."""
        self._need_service()
        kind = self._kind(kind)
        self._check_theme(theme_id, kind)
        row = self.db.get_service_item(item_id)
        if row is None or row["service_id"] != self.service_id:
            raise ControllerError(f"There is no item number {item_id} in this service")
        field = "theme_id" if kind == "projector" else "overlay_theme_id"
        self.db.update_service_item(item_id, **{field: theme_id})
        self._rebuild_for_themes()

    @_one_notification
    def set_output_theme(self, output_name, theme_id):
        """Give one output its own theme (or None to follow each slide's theme)."""
        from dataclasses import replace
        for i, output in enumerate(self.outputs):
            if output.name == output_name:
                if theme_id is not None and self.db.get_theme(theme_id) is None:
                    raise ControllerError(f"There is no theme number {theme_id}")
                self.outputs[i] = replace(output, theme_id=theme_id)
                if self._server is not None:
                    self._server.set_outputs(self.outputs)
                self.session.refresh()
                return
        raise ControllerError(f"There is no output named {output_name!r}")

    # -- the operator window's place -------------------------------------------------
    def operator_window(self):
        """Where the operator window was last seen, (x, y, width, height), or None."""
        try:
            values = json.loads(self.db.get_setting("operator_window") or "null")
        except ValueError:
            return None
        return launcher.clean_window(*values) if isinstance(values, list) and len(values) == 4 else None

    def set_operator_window(self, x, y, width, height):
        """Remember the operator window's place so the next start opens it there. Quiet: no page refreshes.
        Returns False (and keeps the old place) for a minimized or sliver-sized window."""
        window = launcher.clean_window(x, y, width, height)
        if window is None:
            return False
        if window != self.operator_window():
            self.db.set_setting("operator_window", json.dumps(window))
            log.info("Operator window is now at x=%s y=%s, %sx%s", *window)
        return True

    # -- outputs ---------------------------------------------------------------
    def screen_shape(self, name):
        """The shape the operator said this output's real screen has ("16:9" unless changed)."""
        try:
            shapes = json.loads(self.db.get_setting("screen_shapes") or "{}")
        except ValueError:
            shapes = {}
        shape = shapes.get(name) if isinstance(shapes, dict) else None
        return shape if shape in SCREEN_SHAPES and self._shape_layout(name) else DEFAULT_SHAPE

    def _shape_layout(self, name):
        return any(o.name == name and o.layout in SHAPE_LAYOUTS for o in self.outputs)

    @_one_notification
    def set_screen_shape(self, name, shape):
        """Remember an output's screen shape; the operator window draws its previews in that shape."""
        if name not in {o.name for o in self.outputs}:
            raise ValueError(f"There is no screen called {name!r}")
        if not self._shape_layout(name):
            raise ValueError("Only the projector and the confidence monitor have a screen shape")
        if shape not in SCREEN_SHAPES:
            raise ValueError("Choose one of: " + ", ".join(SCREEN_SHAPES))
        try:
            shapes = json.loads(self.db.get_setting("screen_shapes") or "{}")
        except ValueError:
            shapes = {}
        shapes = dict(shapes) if isinstance(shapes, dict) else {}
        shapes[name] = shape
        self.db.set_setting("screen_shapes", json.dumps(shapes))
        old = self.builder.projector_aspect
        self._sync_projector_aspect()
        if self.builder.projector_aspect != old:         # "as many as will fit" depends on the screen's shape
            self._rebuild_for_themes()
        self._notify()

    # -- the home screen ---------------------------------------------------------
    # A picture the projector shows while the screens are blanked, e.g. between parts of the service.
    def home_screen(self):
        """{"image": media path or None, "fit": "fit"|"fill", "on": bool}. Kept in memory, because the
        web server reads it from its own threads and must not touch the database."""
        return dict(self._home)

    def _read_home(self):
        try:
            raw = json.loads(self.db.get_setting("home_screen") or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        image = raw.get("image")
        return {"image": image if isinstance(image, str) and _HOME_IMAGE.match(image) else None,
                "fit": raw.get("fit") if raw.get("fit") in HOME_FITS else "fit",
                "on": raw.get("on") is not False,
                "idle": raw.get("idle") is not False}

    def _home_for_payload(self):
        home = self._home
        if not home["image"] or not (home["on"] or home["idle"]):
            return None
        return {"image_path": home["image"], "fit": home["fit"], "when_blank": home["on"], "when_idle": home["idle"]}

    @_one_notification
    def set_home_screen(self, image=_KEEP, fit=None, on=None, idle=None):
        """Choose the home screen picture (None removes it), how it fits, whether blanking shows it (`on`)
        and whether it shows while nothing is live (`idle`)."""
        home = self.home_screen()
        if image is not _KEEP:
            if image is not None:
                if self.media_root is None or not _HOME_IMAGE.match(image) or not (Path(self.media_root) / image).is_file():
                    raise ControllerError("That picture is not available. Choose it again")
            home["image"] = image
        if fit is not None:
            if fit not in HOME_FITS:
                raise ControllerError("Choose fit or fill")
            home["fit"] = fit
        if on is not None:
            home["on"] = bool(on)
        if idle is not None:
            home["idle"] = bool(idle)
        self.db.set_setting("home_screen", json.dumps(home))
        self._home = home
        self.session.refresh()
        self._notify()

    # -- trigger URLs ----------------------------------------------------------------
    # A secret address (/hook/<secret>/next ...) that Stream Deck, XSplit or any program can open to press
    # a few safe buttons. The secret is long and random; whoever has it can do only what the list allows.
    def server_addresses(self):
        """Other computers' way in: the LAN addresses the server answers on ([] while it is loopback-only)."""
        server = self._server
        if server is None:
            return {"port": None, "lan": []}
        host, port = server.address
        if host in ("127.0.0.1", "::1", "localhost"):
            return {"port": port, "lan": []}
        found = []
        if host in ("0.0.0.0", "::", ""):
            import socket
            try:
                for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                    ip = info[4][0]
                    if not ip.startswith("127.") and ip not in found:
                        found.append(ip)
            except OSError:
                pass
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probe.connect(("192.0.2.1", 9))          # no packet is sent; this only picks the outgoing interface
                ip = probe.getsockname()[0]
                probe.close()
                if not ip.startswith("127.") and ip not in found:
                    found.insert(0, ip)
            except OSError:
                pass
        else:
            found.append(host)
        return {"port": port, "lan": found}

    def hook_settings(self):
        """{"on": bool, "token": str}. Kept in memory, because the web server checks it from its own threads."""
        return dict(self._hook)

    def _read_hook(self):
        try:
            raw = json.loads(self.db.get_setting("hook") or "{}")
        except ValueError:
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        token = raw.get("token")
        if not (isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_\-]{20,64}", token)):
            token = secrets.token_urlsafe(24)
            self.db.set_setting("hook", json.dumps({"on": raw.get("on") is not False, "token": token}))
        return {"on": raw.get("on") is not False, "token": token}

    def hook_check(self, token):
        """True if triggers are switched on and `token` is the secret (compared in constant time)."""
        hook = self._hook
        return bool(hook["on"]) and isinstance(token, str) and hmac.compare_digest(
            token.encode("utf-8"), hook["token"].encode("utf-8"))

    @_one_notification
    def set_hook(self, on=None, new_token=False):
        """Switch trigger URLs on or off, or make a new secret (the old addresses stop working at once)."""
        hook = self.hook_settings()
        if on is not None:
            hook["on"] = bool(on)
        if new_token:
            hook["token"] = secrets.token_urlsafe(24)
        self.db.set_setting("hook", json.dumps(hook))
        self._hook = hook
        log.info("Trigger URLs %s%s", "on" if hook["on"] else "off", ", new secret" if new_token else "")
        self._sync_clicker()
        self._notify()

    def set_blank(self, blank):
        self.session.set_blank(bool(blank))

    def set_text_hidden(self, hidden):
        self.session.set_text_hidden(bool(hidden))

    def payloads(self):
        """What every configured output shows right now, keyed by output name."""
        state = self.session.state()
        home = self._home_for_payload()
        return {o.name: build_payload(state, o, self.styles.get, self.styles.confidence_theme_id, home,
                                  verse_over_theme_id=self.styles.verse_over_theme_id, timer_style=self._timer_style,
                                  confidence_hides_words=self._confidence_hides_words, sound_output=self.sound_output())
                for o in self.outputs}

    def serve(self, media_root=None, host="127.0.0.1", port=0, source_url=None, operator=True):
        """Start the local web server for browser-source outputs (and, by default, the operator
        page); returns it. The database must have been opened with `shared=True` for the operator
        page, because its requests arrive on other threads."""
        from ..server.app import OutputServer   # imported here: the core never needs the server
        from .operator_api import OperatorApi
        if media_root is not None and self.media_root is None:
            self.media_root = Path(media_root)
        if self._server is None:
            self._server = OutputServer(self.session, self.outputs, host=host, port=port,
                                        media_root=media_root, source_url=source_url,
                                        style_for=self.styles.get,
                                        confidence_theme=lambda: self.styles.confidence_theme_id,
                                        verse_over_theme=lambda: self.styles.verse_over_theme_id,
                                        timer_style=self.timer_style,
                                        confidence_words=self.confidence_hides_words,
                                        sound_output=self.sound_output,
                                        home=self._home_for_payload, hook=self.hook_check,
                                        operator=OperatorApi(self) if operator else None).start()
            log.info("Output server listening at %s", self._server.url)
        if operator and self.imports is None and self.media_root is not None:
            from .imports import ImportManager
            self.imports = ImportManager(self)
        return self._server

    def close(self):
        if self.clicker is not None:        # first, so nothing that goes wrong below can leave the helper running
            self.clicker.stop()
        self.screen_windows.close_all()
        self.global_keys.close()
        with self._clip_lock:
            for held in self._clip_timers.values():
                if held[1] is not None:
                    held[1].cancel()
            self._clip_timers = {}
        if self.imports is not None:
            self.imports.close()
            self.imports = None
        if self._server is not None:
            self._server.close()
            self._server = None
