# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The setup wizard's facts: what this computer can do, whether setup has been done, and a practice service.

Nothing here changes a church's own songs, themes or services except `make_practice_service`, which adds
one clearly named practice service on request."""
import logging
import platform
import shutil
import sys
import tempfile
from pathlib import Path

from .. import launcher
from ..importers import pdfrender

log = logging.getLogger("churchlight_presenter.setup")

NO_LIVESTREAM_KEY = "no_livestream"   # "1" when the church chose "I don't livestream" in the setup guide
BACKUP_FRESH_DAYS = 14
DONE_KEY = "setup_done"          # "1" once the wizard has been finished or skipped
PRACTICE_TITLE = "Practice service"
PRACTICE_SONG = "Practice song (you can delete this)"
# Placeholder words, not a real song: the program's own rule is that no real lyrics live in its files.
PRACTICE_TEXT = ("[Verse 1]\nThese are practice words, line one\nThese are practice words, line two\n"
                 "These are practice words, line three\nThese are practice words, line four\n\n"
                 "[Chorus]\nThis is the practice chorus, line one\nThis is the practice chorus, line two")
PRACTICE_VERSE = "John 3:16"


def _writable(folder):
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


def python_check():
    """Python is what runs ChurchLight, so it is installed if this is running; what can still be wrong is the
    version, or Python not being findable by the start file (start_churchlight.bat looks for `python`, then `py`)."""
    version = ".".join(str(n) for n in sys.version_info[:3])
    if sys.version_info < (3, 10):
        return {"id": "python", "label": "Python (the engine that runs ChurchLight)", "ok": False,
                "detail": f"Python {version} is too old. Install Python 3.10 or newer from python.org, "
                          "and tick \"Add python.exe to PATH\" in its installer."}
    detail = f"Python {version} is installed."
    ok = True
    if sys.platform == "win32" and not (shutil.which("python") or shutil.which("py")):
        ok = False
        detail += (" The start file could not find it next time: reinstall Python from python.org and tick "
                   "\"Add python.exe to PATH\" in its installer.")
    return {"id": "python", "label": "Python (the engine that runs ChurchLight)", "ok": ok, "detail": detail}


def checks(controller):
    """Plain-language checks of this computer: a list of {"id", "label", "ok", "detail"}. `ok` False needs attention."""
    out = []
    out.append(python_check())
    found = launcher.find_browser()
    out.append({"id": "browser", "label": "A web browser for the control window", "ok": bool(found),
                "detail": (f"{found[0]} found." if found else
                           "Edge or Chrome was not found. Open the operator address in any browser instead.")})
    pdf = pdfrender.pdf_status()
    out.append({"id": "pdf", "label": "Adding slideshows from PDF files", "ok": bool(pdf["ready"]),
                "detail": pdf["message"]})
    helper = controller.clicker
    windows = sys.platform == "win32"
    if windows:
        ready = bool(helper and helper.available())
        out.append({"id": "clicker", "label": "Clicker and keyboard helper", "ok": ready,
                    "detail": ("The helper that comes with ChurchLight is in place." if ready else
                               "The clicker helper is missing. The program still works; use the keys on this page.")})
    else:
        out.append({"id": "clicker", "label": "Clicker and keyboard helper", "ok": True,
                    "detail": "The clicker helper is for Windows. Not needed on this computer."})
    bibles = controller.translations()
    out.append({"id": "bible", "label": "A Bible to show verses from", "ok": bool(bibles),
                "detail": (", ".join(bibles[:4]) + " installed." if bibles else
                           "No Bible is installed. Add one on the Bible Library tab.")})
    db_path = Path(controller.db.path)
    folders = [db_path.resolve().parent if str(controller.db.path) != ":memory:" else None, controller.media_root]
    problems = [str(f) for f in folders if f is not None and not _writable(f)]
    out.append({"id": "folders", "label": "A place to keep your songs and pictures", "ok": not problems,
                "detail": ("The program can save its files here." if not problems else
                           "The program cannot save to: " + ", ".join(problems) + ". Move the ChurchLight folder "
                           "somewhere you can save files, such as Documents.")})
    return out


def status(controller):
    """What the page needs on every refresh (cheap): whether setup is done and whether it should open by itself."""
    db = controller.db
    done = db.get_setting(DONE_KEY) == "1"
    # A church that already uses the program is not greeted by a wizard it never asked for.
    used = any(db.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() for table in ("songs", "services", "presentations"))
    return {"done": done, "first_run": not done and not used, "platform": platform.system() or "unknown",
            "no_livestream": db.get_setting(NO_LIVESTREAM_KEY) == "1"}


def set_done(controller, done=True):
    controller.db.set_setting(DONE_KEY, "1" if done else "0")
    return bool(done)


def set_no_livestream(controller, value=True):
    controller.db.set_setting(NO_LIVESTREAM_KEY, "1" if value else "0")
    return bool(value)


def ready_checks(controller):
    """The "Ready for Sunday" rows, read-only: [{"id", "label", "ok", "detail", "fix"}]. `fix` names the Settings section
    (or "bibles") that puts a problem right. OBS is checked by the page itself, since it takes a moment."""
    from ..core import backup
    out = []
    view = controller.screens_view()
    assign, monitors = view["assign"], {m["index"]: m for m in view["monitors"]}
    label = controller._screen_label

    def row(id_, name, ok, detail, fix=""):
        out.append({"id": id_, "label": name, "ok": ok, "detail": detail, "fix": fix})

    if not assign:
        row("screens", "Screens", False, "No screen is set up yet. Choose a monitor for the Projector in Settings, Projector windows.", "fixscreens")
    else:
        problems, seen = [], {}
        for name, index in sorted(assign.items()):
            if index not in monitors:
                problems.append(f"{controller._sentence(label(name))} is set to Monitor {index + 1}, which is not connected.")
            elif index in seen:
                problems.append(f"{controller._sentence(label(name))} and {label(seen[index])} are both on Monitor {index + 1}.")
            seen.setdefault(index, name)
        row("screens", "Screens", not problems,
            " ".join(problems) if problems else ", ".join(f"{controller._sentence(label(n))} on Monitor {i + 1}" for n, i in sorted(assign.items())) + ".",
            "" if not problems else "fixscreens")
        opened = set(view["open"])
        closed = [n for n in sorted(assign) if n not in opened]
        row("windows", "Screen windows open", not closed,
            "All screens are open." if not closed else ", ".join(controller._sentence(label(n)) for n in closed) + (" is" if len(closed) == 1 else " are") + " not open yet. Press Open screens.",
            "" if not closed else "windows")
    changes = controller.layout_changes()
    if changes:
        row("layout", "The monitors changed since last time.", False,
            " ".join(changes) + " Check that each screen is on the right monitor (Settings, Projector windows), then press the button.", "layout")
    elif assign and controller._layout_now() and controller.db.get_setting("screen_layout"):
        row("layout", "Monitors", True, "The monitors are arranged the same as the last time the screens were opened.")
    row("bible", "A Bible installed", bool(controller.translations()),
        ", ".join(controller.translations()[:3]) + " installed." if controller.translations() else "No Bible is installed. Add one on the Bible Library tab.",
        "" if controller.translations() else "bibles")
    import time as _time
    copies = backup.list_backups(controller.db.path) if str(controller.db.path) != ":memory:" else []
    age = (_time.time() - copies[0].stat().st_mtime) / 86400 if copies else None
    fresh = age is not None and age <= BACKUP_FRESH_DAYS
    row("backup", "A recent backup", fresh,
        "Your songs and services were backed up recently." if fresh else
        ("The last backup is more than two weeks old." if copies else "There is no backup yet."), "" if fresh else "backup")
    if controller.hook_settings().get("on"):
        row("hook", "Remote button links", True, "Remote button links are on.")
    saved = controller._read_clicker()
    if saved.get("auto"):
        running = bool(controller.clicker and controller.clicker.running())
        row("clicker", "Clicker helper", running, "The clicker helper is running." if running else
            "The clicker helper is not running. Open Settings, Advanced, Clicker helper.", "" if running else "clicker")
    return out


def make_practice_service(controller):
    """Add a practice service (a practice song and one verse) and open it. Returns the service id.
    Makes a new one each time with a number if the name is taken."""
    db = controller.db
    existing = {row["title"] for row in controller.services()}
    title, n = PRACTICE_TITLE, 1
    while title in existing:
        n += 1
        title = f"{PRACTICE_TITLE} {n}"
    song_id = next((r["id"] for r in db.song_summaries() if r["title"] == PRACTICE_SONG), None)
    if song_id is None:
        song_id = controller.create_song(PRACTICE_SONG, "", PRACTICE_TEXT)
    service_id = controller.new_service(title)
    from .controller import LibraryEntry
    controller.add_entry(LibraryEntry("song", song_id, PRACTICE_SONG))
    if controller.translations():
        try:
            controller.add_verse(PRACTICE_VERSE)
        except Exception as err:           # noqa: BLE001 - a missing verse must not spoil the practice service
            log.info("The practice verse was not added: %s", err)
    log.info("Practice service made")
    return service_id
