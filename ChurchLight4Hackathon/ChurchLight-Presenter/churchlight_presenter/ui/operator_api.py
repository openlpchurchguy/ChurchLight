# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""What the operator web page can ask for, as plain JSON-able dictionaries.

`OperatorApi` wraps an `OperatorController`. Every call takes the controller's lock first, so the
terminal, the web page and several browsers take turns instead of colliding, and the database
(opened with `shared=True`) is only ever touched by one thread at a time.

Nothing here draws anything and nothing imports the web server: the server calls `state()`,
`library()` and `call()`, and a future native window could too.

The operator is trusted with speaker notes (they are the one person who runs the stage), so slide
views here include `notes`. Public screens still get theirs only from `build_payload`.
"""
from ..core import backup, export, timerstyle, usagelog
from ..core.scripture_ref import reference_hint
from .. import BUILD
from ..core.outputs import LAYOUTS, PUBLIC_SLIDE_FIELDS, STAGE_ONLY_SLIDE_FIELDS
from . import obsctl
from . import setup as setup_wizard
from ..core.obsws import ObsError
from .controller import MEDIA_TYPES, NO_CHANGE, ControllerError, LibraryEntry

MAX_TEXT = 2000
MAX_BODY = 20000
MAX_SONG_TEXT = 60000
MAX_NUMBER = 10 ** 9
KINDS = ("song", "presentation")
ENTRY_KINDS = KINDS + ("media",)          # what can be added to a service or sent live (the search lists KINDS only)
MAX_TRAY_PICK = 300


def output_view(output):
    """One output as the operator page needs it: what it is, who it is for, and its page.

    The page uses this to draw a status chip per screen and, if the operator turns previews on,
    to frame `path`. Adding another output to the settings adds a chip with no page changes.
    """
    return {"name": output.name, "layout": output.layout,
            "audience": LAYOUTS[output.layout].audience, "path": f"/view/{output.name}"}


# The buttons a trigger URL may press: (name, what it does, how to run it on the controller). Nothing here
# changes the library or the settings; it is the same short list a remote control would get.
TRIGGERS = (
    ("next", "Next slide", lambda c: c.next()),
    ("previous", "Previous slide", lambda c: c.previous()),
    ("next_item", "Next service item", lambda c: c.next_item()),
    ("previous_item", "Previous service item", lambda c: c.previous_item()),
    ("media_toggle", "Video or sound slide: play or pause (toggle)", lambda c: c.media_toggle()),
    ("media_restart", "Video or sound slide: start again from the beginning", lambda c: c.media_control("restart")),
    ("music_toggle", "Music under the slides: play or pause (toggle)", lambda c: c.sound_toggle()),
    ("blank", "Blank the screens, or bring them back (toggle)", lambda c: c.toggle_blank()),
    ("blank_on", "Blank the screens", lambda c: c.set_blank(True)),
    ("blank_off", "Bring the screens back", lambda c: c.set_blank(False)),
    ("words", "Hide the words, or show them again (toggle)", lambda c: c.toggle_text_hidden()),
    ("words_hide", "Hide the words (keep the background)", lambda c: c.set_text_hidden(True)),
    ("words_show", "Show the words again", lambda c: c.set_text_hidden(False)),
    ("verse", "Verse over the slide: show or hide it (toggle)", lambda c: c.toggle_verse()),
    ("verse_on", "Show the verse over the slide (the one just hidden, or the first verse of the tray)", lambda c: c.verse_on()),
    ("verse_off", "Hide the verse over the slide", lambda c: c.verse_off()),
    ("take_verse", "Show the ready verse", lambda c: c.take_cue()),
    ("verse_forward", "Verse over the slide: next part of the verse, or the next verse once every part has shown", lambda c: c.verse_forward()),
    ("verse_back", "Verse over the slide: previous part of the verse, or the previous verse from its first part", lambda c: c.verse_back()),
)
TRIGGER_NAMES = tuple(name for name, _, _ in TRIGGERS)
TRIGGER_ALIASES = {"hide_verse": "verse_off"}      # the old name keeps working in saved addresses and clicker buttons


class ApiError(Exception):
    """A request the operator page got wrong; `status` is the HTTP code to answer with."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def slide_view(slide):
    """A slide as the operator sees it: every field the public screens get, plus the notes."""
    if slide is None:
        return None
    view = {name: getattr(slide, name) for name in PUBLIC_SLIDE_FIELDS + STAGE_ONLY_SLIDE_FIELDS}
    view["segments"] = [list(pair) for pair in view["segments"]]
    view["verse_numbers"] = list(view["verse_numbers"])
    return view


def _number(args, name, required=True):
    value = args.get(name)
    if value is None:
        if required:
            raise ApiError(f"Missing {name!r}")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not -MAX_NUMBER < value < MAX_NUMBER:
        raise ApiError(f"{name!r} must be a whole number")
    return value


def _text(args, name, limit=MAX_TEXT, required=True):
    value = args.get(name)
    if value is None:
        if required:
            raise ApiError(f"Missing {name!r}")
        return None
    if not isinstance(value, str):
        raise ApiError(f"{name!r} must be text")
    if len(value) > limit:
        raise ApiError(f"{name!r} is too long (more than {limit} characters)")
    return value


def _kind(args):
    """Which look a theme call is about: the projector (the default) or the overlay."""
    kind = args.get("kind", "projector")
    if kind not in ("projector", "overlay"):
        raise ApiError("'kind' must be projector or overlay")
    return kind


def _list(args, name):
    value = args.get(name)
    if not isinstance(value, list) or len(value) > 2000:
        raise ApiError(f"{name!r} must be a list")
    return value


def _seconds(args, name):
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value < 24 * 3600:
        raise ApiError(f"{name!r} must be a number of seconds")
    return float(value)


def _fraction(args, name):
    value = args.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ApiError(f"{name!r} must be a number from 0 to 1")
    return float(value)


def _flag(args, name):
    value = args.get(name)
    if not isinstance(value, bool):
        raise ApiError(f"{name!r} must be true or false")
    return value


def _entry(args):
    kind = _text(args, "kind")
    if kind not in ENTRY_KINDS:
        raise ApiError(f"'kind' must be one of {', '.join(ENTRY_KINDS)}")
    return LibraryEntry(kind, _number(args, "id"), "")


class OperatorApi:
    def __init__(self, controller):
        self.c = controller
        self._pdf = None
        self._actions = {
            "new_service": self._new_service, "open_service": self._open_service,
            "add": self._add, "add_verse": self._add_verse,
            "add_announcement": self._add_announcement,
            "remove": self._remove, "move": self._move, "undo_remove": lambda a: self.c.undo_remove_item(),
            "undo_report_remove": lambda a: self.c.undo_remove_report(),
            "go_item": self._go_item, "go_entry": self._go_entry, "go_slide": self._go_slide,
            "next": lambda a: self.c.next(), "previous": lambda a: self.c.previous(),
            "next_item": lambda a: self.c.next_item(), "previous_item": lambda a: self.c.previous_item(),
            "blank": lambda a: {"blank": self.c.toggle_blank()}, "clear": lambda a: self.c.clear(),
            "text_hide": lambda a: {"text_hidden": self.c.toggle_text_hidden()},
            "lower_hide": lambda a: {"lower_hidden": self.c.toggle_lower_hidden()},
            "report_service": lambda a: {"reported": self.c.report_service()},
            "report_list": lambda a: {"report": self.c.song_report()},
            "report_remove": lambda a: self.c.remove_report_section(_number(a, "section")),
            "timer_style_set": lambda a: self.c.set_timer_style(a.get("style")),
            "timer_style_reset": lambda a: self.c.reset_timer_style(),
            "confidence_words_set": lambda a: self.c.set_confidence_hides_words(_flag(a, "value")),
            "countdown_start": self._countdown_start, "countdown_stop": lambda a: self.c.stop_countdown(),
            "alert_set": lambda a: {"alert": self.c.set_alert(_text(a, "text", 300, required=False))},
            "hook_set": lambda a: self.c.set_hook(on=_flag(a, "on")),
            "hook_new_token": lambda a: self.c.set_hook(new_token=True),
            "clicker_start": lambda a: self.c.start_clicker(a.get("rows") if isinstance(a.get("rows"), list) else []),
            "clicker_stop": lambda a: self.c.stop_clicker(),
            "shutdown": lambda a: self.c.request_shutdown(),
            "obs_connect": self._obs_connect, "obs_setup": self._obs_setup, "obs_project": self._obs_project,
            "obs_auto_set": lambda a: obsctl.set_auto(self.c, _flag(a, "on")),
            "screen_monitors": lambda a: {"monitors": self.c.screen_windows.monitors(),
                                          "available": self.c.screen_windows.available(),
                                          "open": self.c.screen_windows.open_names()},
            "screen_show": self._screen_show,
            "screen_hide": lambda a: self.c.hide_screen(_text(a, "name", 60)),
            "screen_assign": self._screen_assign,
            "screens_get": lambda a: self.c.screens_view(),
            "screens_set": self._screens_set,
            "screens_open": lambda a: {"problems": self.c.open_screens(self._point(a))},
            "screens_close": lambda a: self.c.close_screens(),
            "setup_checks": lambda a: {"checks": setup_wizard.checks(self.c)},
            "setup_done": lambda a: {"done": setup_wizard.set_done(self.c, _flag(a, "value"))},
            "setup_no_livestream": lambda a: {"no_livestream": setup_wizard.set_no_livestream(self.c, _flag(a, "value"))},
            "ready_checks": lambda a: {"checks": setup_wizard.ready_checks(self.c), "no_livestream": setup_wizard.status(self.c)["no_livestream"]},
            "layout_accept": lambda a: {"saved": self.c.save_layout()},
            "backup_now": lambda a: {"backed_up": backup.make_backup(self.c.db.path, force=True) is not None},
            "setup_practice": lambda a: {"id": setup_wizard.make_practice_service(self.c)},
            "clicker_auto_set": lambda a: self.c.set_clicker_auto(_flag(a, "on")),
            "hide_verse": lambda a: self.c.hide_verse(),
            "tray_load": self._tray_load, "tray_clear": lambda a: self.c.clear_tray(),
            "tray_show": self._tray_show, "tray_step": self._tray_step, "tray_add": self._tray_add,
            "tray_cue": self._tray_cue, "cue_take": lambda a: self.c.take_cue(), "cue_clear": lambda a: self.c.clear_cue(),
            "reference_hint": lambda a: {"hint": reference_hint(_text(a, "text", 200))},
            "verse_next": lambda a: self.c.verse_next(),
            "verse_previous": lambda a: self.c.verse_previous(),
            "set_translation": self._set_translation,
            "screen_shape_set": self._screen_shape_set, "home_set": self._home_set, "global_keys_set": self._global_keys_set, "global_keys_arm": self._global_keys_arm,
            "preview": self._preview,
            "song_get": self._song_get, "song_check": self._song_check,
            "song_save": self._song_save, "song_delete": self._song_delete, "songs_delete": self._songs_delete,
            "church_ccli": lambda a: self.c.set_church_ccli(_text(a, "number", 40, False) or ""),
            "item_parts": self._item_parts,
            "deck_replace_plan": lambda a: self.c.deck_replace_plan(_number(a, "deck_id"), _number(a, "job")),
            "deck_replace_apply": lambda a: self.c.replace_deck_slides(_number(a, "deck_id"), _number(a, "job"), _flag(a, "remove_extra"),
                a.get("keep") or [], bool(a.get("replace_same"))),
            "deck_replace_cancel": lambda a: self.c.cancel_deck_replacement(_number(a, "deck_id"), _number(a, "job")),
            "notice_set": lambda a: self.c.set_notice_settings(
                _text(a, "position", 10), _text(a, "writers", 60, False) or "", _text(a, "permission", 80, False) or "",
                _flag(a, "song_number")),
            "item_notice": lambda a: self.c.set_item_notice(_number(a, "item_id"), _flag(a, "off")),
            "song_publish": lambda a: self.c.publish_song(_number(a, "id")),
            "deck_autoplay": lambda a: self.c.set_deck_autoplay(
                _number(a, "id"), _number(a, "seconds"), _text(a, "end_action", 40)),
            "item_autoplay": self._item_autoplay,
            "timer_pause": lambda a: self.c.set_timer_paused(_flag(a, "paused")),
            "deck_image_fit": lambda a: self.c.set_deck_image_fit(_number(a, "id"), _text(a, "fit", 10)),
            "window_place": lambda a: {"saved": self.c.set_operator_window(
                _number(a, "x"), _number(a, "y"), _number(a, "width"), _number(a, "height"))},
            "service_verse_split": lambda a: self.c.set_service_verse_split(_text(a, "value", 10, False) or None),
            "deck_transition": self._deck_transition, "deck_rename": self._deck_rename, "deck_delete": self._deck_delete, "decks_delete": self._decks_delete,
            "bible_delete": self._bible_delete,
            "deck_get": lambda a: self.c.deck_for_editing(_number(a, "id")),
            "deck_pages_reorder": lambda a: self.c.reorder_deck_pages(_number(a, "id"), _list(a, "order")),
            "deck_pages_delete": lambda a: self.c.delete_deck_pages(_number(a, "id"), _list(a, "pages")),
            "deck_pages_duplicate": lambda a: {"new": self.c.duplicate_deck_pages(_number(a, "id"), _list(a, "pages"))},
            "deck_pages_skip": lambda a: self.c.skip_deck_pages(_number(a, "id"), _list(a, "pages"), _flag(a, "skipped")),
            "deck_pages_add": lambda a: {"new": self.c.add_deck_pictures(
                _number(a, "id"), _text(a, "batch", 40), _number(a, "after", False))},
            "deck_page_replace": lambda a: self.c.replace_deck_picture(
                _number(a, "id"), _number(a, "page"), _text(a, "batch", 40)),
            "deck_pages_from": lambda a: {"new": self.c.add_deck_pages_from(
                _number(a, "id"), _number(a, "source"), _list(a, "pages"), _number(a, "after", False))},
            "deck_pages_to_media": lambda a: self.c.copy_slides_to_media(_number(a, "id"), _list(a, "pages")),
            "deck_split": lambda a: {"id": self.c.split_deck(
                _number(a, "id"), _list(a, "pages"), _text(a, "name", 200))},
            "deck_pages_timing": self._deck_pages_timing,
            "deck_page_notes": lambda a: self.c.set_deck_page_notes(
                _number(a, "id"), _number(a, "page"), _text(a, "notes", 5000, False) or ""),
            "deck_page_title": lambda a: self.c.set_deck_page_title(
                _number(a, "id"), _number(a, "page"), _text(a, "title", 200, False) or ""),
            "theme_save": self._theme_save, "theme_delete": lambda a: self.c.delete_theme(_number(a, "id")),
            "theme_duplicate": lambda a: {"id": self.c.duplicate_theme(_number(a, "id"))},
            "theme_import": self._theme_import,
            "default_theme": lambda a: self.c.set_default_theme(_number(a, "id", False), _kind(a)),
            "bible_theme": lambda a: self.c.set_bible_theme(_number(a, "id", False)),
            "confidence_theme": lambda a: self.c.set_confidence_theme(_number(a, "id", False)),
            "verse_over_theme": lambda a: self.c.set_verse_over_theme(_number(a, "id", False)),
            "service_theme": lambda a: self.c.set_service_theme(_number(a, "id", False), _kind(a)),
            "item_theme": lambda a: self.c.set_item_theme(_number(a, "item_id"), _number(a, "id", False), _kind(a)),
            "deck_add_library": lambda a: {"page_ids": self.c.add_deck_library(
                _number(a, "id"), a.get("media_ids"), _number(a, "after_page_id", False))},
            "deck_pages_sound": self._deck_pages_sound,
            "page_sound_pause": lambda a: self.c.page_sound_control("pause"), "page_sound_resume": lambda a: self.c.page_sound_control("resume"),
            "page_sound_restart": lambda a: self.c.page_sound_control("restart"),
            "page_sound_loop": lambda a: self.c.page_sound_control("loop", _flag(a, "on")),
            "media_add": self._media_add,
            "clip_problem": lambda a: self.c.report_clip_problem(_text(a, "src", 500), _text(a, "output", 100, False)),
            "media_rename": lambda a: self.c.rename_media(_number(a, "id"), _text(a, "name", 200)),
            "media_delete": lambda a: self.c.delete_media(_number(a, "id")),
            "media_play": lambda a: self.c.media_control("play"), "media_pause": lambda a: self.c.media_control("pause"),
            "media_restart": lambda a: self.c.media_control("restart"),
            "media_seek": lambda a: self.c.media_control("seek", _seconds(a, "seconds")),
            "media_loop": lambda a: self.c.media_control("loop", _flag(a, "on")),
            "media_volume": lambda a: self.c.media_control("volume", _fraction(a, "volume")),
            "media_mute": lambda a: self.c.media_control("mute", _flag(a, "on")),
            "media_advance": lambda a: self.c.set_media_advance(_flag(a, "on")),
            "music_play": lambda a: self.c.play_music(_number(a, "id"), a.get("loop", False) is True),
            "music_pause": lambda a: self.c.sound_control("pause"), "music_resume": lambda a: self.c.sound_control("resume"),
            "music_restart": lambda a: self.c.sound_control("restart"), "music_stop": lambda a: self.c.sound_control("stop"),
            "music_seek": lambda a: self.c.sound_control("seek", _seconds(a, "seconds")),
            "music_loop": lambda a: self.c.sound_control("loop", _flag(a, "on")),
            "music_volume": lambda a: self.c.sound_control("volume", _fraction(a, "volume")),
            "music_mute": lambda a: self.c.sound_control("mute", _flag(a, "on")),
            "import_batch": self._import_batch, "background_import": self._background_import, "import_start": self._import_start,
        }

    # -- change waiting (does not take the lock: it must not block other requests) ---------
    @property
    def version(self):
        return self.c.version

    def wait_for_change(self, since, timeout):
        return self.c.wait_for_change(since, timeout)

    # -- reading -------------------------------------------------------------------
    def _pdf_status(self):
        """Whether PDF import can work here. Looked up once: adding the renderer needs a restart anyway."""
        if self._pdf is None:
            from ..importers import pdfrender
            self._pdf = pdfrender.pdf_status()
        return self._pdf

    def state(self):
        """Everything the page draws, in one dictionary."""
        with self.c.lock:
            live = self.c.session.state()
            service = self.c.db.get_service(self.c.service_id) if self.c.service_id else None
            return {
                "schema": 1,
                "version": self.c.version,
                "service": None if service is None else {
                    "id": service["id"], "title": service["title"],
                    "date": service["service_date"], "theme_id": service["theme_id"],
                    "overlay_theme_id": service["overlay_theme_id"],
                    "verse_split": service["verse_split"]},
                "themes": self.c.theme_details(),
                "default_theme": self.c.default_theme_id,
                "default_overlay_theme": self.c.default_overlay_theme_id,
                "confidence_theme": self.c.confidence_theme_id,
                "bible_theme": self.c.bible_theme_id,
                "verse_over_theme": self.c.verse_over_theme_id,
                "notice": self.c.notice_view(),
                "mode": self.c.mode,
                "timer": self.c.timer_view(),
                "clips": self.c.media_view(),
                "items": [{"item_id": r.item_id, "position": r.position, "type": r.item_type,
                           "title": r.title, "live": r.is_live, "theme_id": r.theme_id, "overlay_theme_id": r.overlay_theme_id,
                           "timing": r.timing, "parts": r.parts, "slides": r.slides, "note": r.note,
                           "slide_list": list(r.slide_list), "slide_numbers": list(r.slide_numbers), "last_slide": r.last_slide, "deck_slides": list(r.deck_slides), "outline": None if r.outline is None else list(r.outline)}
                          for r in self.c.service_rows()],
                "live": {
                    "blank": live.blank, "text_hidden": live.text_hidden, "lower_hidden": live.lower_hidden, "index": live.base_index, "count": live.base_count,
                    "base": slide_view(live.base), "next": slide_view(live.next_base),
                    "overlay": slide_view(live.overlay), "overlay_loaded": live.overlay_loaded,
                    "overlay_index": live.overlay_index, "overlay_count": live.overlay_count,
                },
                "global_keys": self.c.global_keys.view(),
                "verse_over_ok": self.c.verse_over_ok(),
                "cue": self.c.cue,
                "home": self.c.home_screen(),
                "countdown": self.c.countdown_view(),
                "confidence_hides_words": self.c.confidence_hides_words(),
                "timer_style": {"values": self.c.timer_style(), "defaults": timerstyle.DEFAULTS},
                "pdf": self._pdf_status(),
                "clicker": self.c.clicker_view(),
                "hook": {**self.c.hook_settings(), "actions": [{"name": n, "label": l} for n, l, _ in TRIGGERS]},
                "translation": self.c.translation,
                "translations": self.c.translations(),
                "tray": self._tray_view(),
                "services": [{"id": r["id"], "title": r["title"], "date": r["service_date"]}
                             for r in self.c.services()[:100]],
                "build_label": BUILD,
                "outputs": self._outputs(),
                "server": self.c.server_addresses(),
                "setup": setup_wizard.status(self.c),
                "obs": obsctl.view(self.c),
                "can_import": self.c.imports is not None,
                "imports": self.c.imports.jobs() if self.c.imports is not None else [],
            }

    def _tray_view(self):
        tray = self.c.tray
        if tray is None:
            return None
        return {"reference": tray.reference, "title": tray.title, "translation": tray.translation,
                "verses": [[n, text] for n, text in tray.verses],
                "live": list(self.c.tray_live_numbers())}

    def _outputs(self):
        return [dict(output_view(o), shape=self.c.screen_shape(o.name)) for o in self.c.outputs]

    def manage(self):
        """Everything in the library, with how much each thing is used (the Library tab)."""
        with self.c.lock:
            return self.c.library_overview()

    def export_usage(self):
        """(file name, content type, bytes): the song usage log, as CCLI's reporting wants it."""
        log_file = self.c.usage_log
        return usagelog.FILE_NAME, "text/csv; charset=utf-8", (log_file.read() if log_file else b"")

    def export_songs(self, ids, fmt, everything=False):
        """(file name, content type, bytes): one song as .xml/.txt, several as a .zip. `everything` = every finished song."""
        with self.c.lock:
            db = self.c.db
            if everything:
                ids = [r["id"] for r in db.conn.execute("SELECT id FROM songs WHERE is_draft = 0 ORDER BY title COLLATE NOCASE")]
            try:
                return export.export_songs(db, ids, fmt)
            except export.ExportError as err:
                raise ApiError(str(err)) from err

    def export_slideshows(self, ids, everything=False, skip_video=False):
        """(file name, content type, path of a temporary .zip the caller deletes) for a slideshow bundle.
        `skip_video` leaves out the slideshows that have a video page (which cannot be exported yet) instead of refusing."""
        with self.c.lock:
            db = self.c.db
            if everything:
                ids = [r["id"] for r in db.conn.execute("SELECT id FROM presentations ORDER BY name COLLATE NOCASE")]
            if skip_video:
                with_video = {r["id"] for r in db.presentation_summaries() if r["has_video"]}
                ids = [i for i in ids if i not in with_video]
                if not ids:
                    raise ApiError("Every slideshow has video, and slideshows with video cannot be exported yet")
            try:
                shows = export.slideshow_snapshot(db, ids)
            except export.ExportError as err:
                raise ApiError(str(err)) from err
        try:
            path = export.write_bundle_tempfile(shows, self.c.media_root)
        except export.ExportError as err:
            raise ApiError(str(err), status=409) from err
        return export.bundle_file_name(shows), "application/zip", path

    def export_themes(self, ids, everything=False):
        """(file name, content type, path of a temporary .zip the caller deletes) for a themes file.
        `everything` = every theme except the starters that were never changed."""
        with self.c.lock:
            try:
                themes = export.theme_snapshot(self.c.db, None if everything else ids)
            except export.ExportError as err:
                raise ApiError(str(err)) from err
        try:
            path = export.write_themes_tempfile(themes, self.c.media_root)
        except export.ExportError as err:
            raise ApiError(str(err), status=409) from err
        return export.themes_file_name(themes), "application/zip", path

    def save_upload(self, batch, filename, stream, length, kind):
        """Store one uploaded file in an import batch (no lock: it is only disk work)."""
        if self.c.imports is None:
            raise ApiError("Importing needs a media folder; start the program with --media")
        try:
            return self.c.imports.save_upload(batch, filename, stream, length, kind)
        except ControllerError as err:
            raise ApiError(str(err)) from err

    def trigger(self, name):
        """Press one of the TRIGGERS buttons. ApiError 404 for any other name; 409 when it cannot happen now."""
        name = TRIGGER_ALIASES.get(name, name)
        for key, _label, run in TRIGGERS:
            if key == name:
                break
        else:
            raise ApiError("Not found", status=404)
        with self.c.lock:
            try:
                run(self.c)
            except ControllerError as err:
                raise ApiError(str(err), status=409) from err
        return {"ok": True, "action": name}

    def library(self, text="", kinds=KINDS + ("media",)):
        """Songs, slideshows and Media Library files. `kinds` may also be one of image, video, audio (only those files)."""
        media_type = None
        if isinstance(kinds, str) and kinds in MEDIA_TYPES:
            media_type, kinds = kinds, ("media",)
        elif isinstance(kinds, str):
            kinds = (kinds,) if kinds in KINDS else KINDS + ("media",)
        with self.c.lock:
            return [{"kind": e.kind, "id": e.id, "title": e.title, "detail": e.detail, "media_type": e.media_type}
                    for e in self.c.search_library(text, kinds, media_type)]

    def suggest(self, text="", kind=None):
        """Names for the dropdown under a search box: only kind, id and title."""
        with self.c.lock:
            return [dict({"kind": e.kind, "id": e.id, "title": e.title}, **({"media_type": e.media_type} if e.media_type else {}))
                    for e in self.c.suggest_library(text, kind=kind if kind in KINDS or kind in MEDIA_TYPES else None)]

    # -- doing ---------------------------------------------------------------------
    def _media_add(self, a):
        media_id = self.c.add_media_file(_text(a, "batch"), a.get("duration", 0))
        warnings = list(self.c.imports.media_warnings)
        return {"id": media_id, "warning": " ".join(warnings)} if warnings else {"id": media_id}

    def call(self, action, args=None):
        """Run one named action. Returns a dictionary (always with `ok` and `version`)."""
        args = {} if args is None else args
        if not isinstance(args, dict):
            raise ApiError("'args' must be an object")
        handler = self._actions.get(action)
        if handler is None:
            raise ApiError(f"Unknown action {action!r}", status=404)
        if action == "shutdown":                   # never waits for the lock: it must work when the program has seized up
            self.c.request_shutdown()
            return {"ok": True, "version": self.c.version}
        if action in self._NETWORK_ACTIONS:        # these wait for OBS: they take the lock only for their own reads and writes
            try:
                result = handler(args)
            except ObsError as err:
                raise ApiError(str(err)) from err
            reply = {"ok": True}
            if isinstance(result, dict):
                reply.update(result)
            with self.c.lock:
                reply["version"] = self.c.version
            return reply
        with self.c.lock:
            try:
                result = handler(args)
            except (ControllerError, ObsError) as err:
                raise ApiError(str(err)) from err
            except (ValueError, IndexError) as err:
                raise ApiError(str(err)) from err
            reply = {"ok": True}
            if isinstance(result, dict):
                reply.update(result)
            reply["version"] = self.c.version
            return reply

    @staticmethod
    def _point(a):
        point = a.get("operator_at")
        if point is not None:
            if (not isinstance(point, list) or len(point) != 2
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)):
                raise ApiError("'operator_at' must be two numbers")
            point = (point[0], point[1])
        return point

    def _screen_show(self, a):
        return self.c.show_screen(_text(a, "name", 60), _number(a, "monitor"), self._point(a))

    def _screen_assign(self, a):
        monitor = None if a.get("monitor") is None else _number(a, "monitor")
        return self.c.assign_screen(_text(a, "name", 60), monitor, self._point(a), identify=a.get("identify") is True)

    def _screens_set(self, a):
        assign = a.get("assign")
        if not isinstance(assign, dict):
            raise ApiError("'assign' must be a table of screen names and monitors")
        return self.c.set_screens(assign, _flag(a, "auto"))

    _NETWORK_ACTIONS = frozenset({"obs_connect", "obs_setup", "obs_project"})

    @staticmethod
    def _obs_target(a):
        password = a.get("password")
        if password is not None and (not isinstance(password, str) or len(password) > 200):
            raise ApiError("'password' must be text")
        port = a.get("port")
        if not isinstance(port, int) or isinstance(port, bool):
            raise ApiError("The port must be a number")
        return _text(a, "host", 100), port, password

    def _obs_connect(self, a):
        host, port, password = self._obs_target(a)
        return obsctl.connect(self.c, host, port, password or None)

    def _obs_setup(self, a):
        host, port, password = self._obs_target(a)
        wanted = a.get("scenes")
        if not isinstance(wanted, list) or not all(isinstance(x, str) for x in wanted):
            raise ApiError("'scenes' must be a list")
        remember = a.get("remember", True)
        if not isinstance(remember, bool):
            raise ApiError("'remember' must be true or false")
        return obsctl.setup(self.c, host, port, password or None, wanted, remember)

    def _obs_project(self, a):
        monitors = a.get("monitors")
        if not isinstance(monitors, dict):
            raise ApiError("'monitors' must be an object")
        remember = a.get("remember")
        if remember is not None and not isinstance(remember, bool):
            raise ApiError("'remember' must be true or false")
        if a.get("port") is None:                                   # nothing typed: use what was saved
            return obsctl.project(self.c, monitors)
        host, port, password = self._obs_target(a)
        return obsctl.project(self.c, monitors, host, port, password or None, remember)

    def _new_service(self, a):
        title = _text(a, "title").strip()
        if not title:
            raise ApiError("A service needs a name")
        return {"id": self.c.new_service(title)}

    def _open_service(self, a):
        self.c.open_service(_number(a, "id"))

    def _add(self, a):
        return {"item_id": self.c.add_entry(_entry(a), _number(a, "position", False))}

    def _add_verse(self, a):
        return {"item_id": self.c.add_verse(_text(a, "text"), _number(a, "position", False))}

    def _add_announcement(self, a):
        title = _text(a, "title", 200, False)
        title = (title or "").strip()
        body = _text(a, "body", MAX_BODY, False) or ""
        if not title:                       # no title typed: use the start of the text
            first = next((line.strip() for line in body.splitlines() if line.strip()), "")
            title = first if len(first) <= 40 else first[:37].rstrip() + "..."
        if not title:
            raise ApiError("Type a title or the text for the announcement")
        return {"item_id": self.c.add_announcement(title, body,
                                                   _number(a, "position", False))}

    def _remove(self, a):
        self.c.remove_item(_number(a, "item_id"))

    def _move(self, a):
        self.c.move_item(_number(a, "item_id"), _number(a, "position"))

    def _go_item(self, a):
        self.c.go_live_service_item(_number(a, "item_id"))

    def _go_entry(self, a):
        self.c.go_live_entry(_entry(a))

    def _go_slide(self, a):
        self.c.go_to_slide(_number(a, "index"))

    def _tray_load(self, a):
        add = a.get("add", False)
        if not isinstance(add, bool):
            raise ApiError("'add' must be true or false")
        if add:
            self.c.need_service()          # refuse before loading, so a failed add leaves the tray alone
        tray = self.c.load_tray(_text(a, "text"), _text(a, "translation", 40, False))
        out = {"verses": len(tray.verses)}
        if add:
            if not tray.verses:
                raise ApiError("That passage has no verses in this Bible")
            out["item_ids"] = self.c.add_tray_to_service([n for n, _ in tray.verses])
        return out

    @staticmethod
    def _verse_numbers(a):
        numbers = a.get("verses")
        if (not isinstance(numbers, list) or not numbers or len(numbers) > MAX_TRAY_PICK
                or any(isinstance(n, bool) or not isinstance(n, int) for n in numbers)):
            raise ApiError("'verses' must be a list of verse numbers")
        return numbers

    def _tray_show(self, a):
        return {"slides": len(self.c.show_tray_verses(self._verse_numbers(a)))}

    def _tray_add(self, a):
        return {"item_ids": self.c.add_tray_to_service(self._verse_numbers(a), _number(a, "position", False))}

    def _tray_cue(self, a):
        to_end = a.get("to_end", False)
        if not isinstance(to_end, bool):
            raise ApiError("'to_end' must be true or false")
        return {"item_ids": self.c.cue_tray(self._verse_numbers(a), to_end)}

    def _tray_step(self, a):
        self.c.tray_step(_number(a, "delta"))

    def _countdown_start(self, a):
        label = _text(a, "label", 80, required=False) or ""
        if a.get("at") is not None:
            self.c.start_countdown(at=_text(a, "at", 10), label=label)
        else:
            minutes = a.get("minutes")
            if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
                raise ApiError("'minutes' must be a number")
            self.c.start_countdown(minutes=minutes, label=label)

    def _home_set(self, a):
        kwargs = {}
        if "image" in a:
            image = a["image"]
            if image is not None and not isinstance(image, str):
                raise ApiError("'image' must be text or null")
            kwargs["image"] = image
        if "fit" in a:
            kwargs["fit"] = _text(a, "fit", 10)
        if "on" in a:
            if not isinstance(a["on"], bool):
                raise ApiError("'on' must be true or false")
            kwargs["on"] = a["on"]
        if "idle" in a:
            if not isinstance(a["idle"], bool):
                raise ApiError("'idle' must be true or false")
            kwargs["idle"] = a["idle"]
        self.c.set_home_screen(**kwargs)

    def _screen_shape_set(self, a):
        try:
            self.c.set_screen_shape(_text(a, "name", 40), _text(a, "shape", 10))
        except ValueError as err:
            raise ApiError(str(err))

    def _global_keys_set(self, a):
        combo = a.get("combo")
        if combo is not None and not isinstance(combo, str):
            raise ApiError("'combo' must be text")
        self.c.global_keys.set_combo(_text(a, "action", 40), combo)

    def _global_keys_arm(self, a):
        on = a.get("on")
        if not isinstance(on, bool):
            raise ApiError("'on' must be true or false")
        if on:
            self.c.global_keys.arm()
        else:
            self.c.global_keys.disarm()

    def _set_translation(self, a):
        self.c.translation = _text(a, "abbreviation")

    def _preview(self, a):
        return {"slides": [slide_view(s) for s in self.c.preview(_entry(a))]}

    # -- managing the library -------------------------------------------------------
    def _song_get(self, a):
        return self.c.song_for_editing(_number(a, "id"))

    def _item_parts(self, a):
        skip = a.get("skip")
        if not isinstance(skip, list) or len(skip) > 200:
            raise ApiError("'skip' must be a list of part positions")
        self.c.set_item_parts(_number(a, "item_id"), skip)

    def _song_check(self, a):
        """Answers 200 either way: `valid` says whether the words can be saved, `error` why not."""
        try:
            return dict(self.c.check_song_text(_text(a, "text", MAX_SONG_TEXT),
                                               _text(a, "order", 2000, False)), valid=True)
        except ControllerError as err:
            return {"valid": False, "error": str(err)}

    def _song_save(self, a):
        title, author, text = _text(a, "title"), _text(a, "author", required=False) or "", \
            _text(a, "text", MAX_SONG_TEXT)
        song_id = _number(a, "id", False)
        draft = a.get("draft")
        if draft is not None and not isinstance(draft, bool):
            raise ApiError("'draft' must be true or false")
        order, ccli = _text(a, "order", 2000, False), _text(a, "ccli", 40, False)
        theme = NO_CHANGE
        if "theme_id" in a:                      # a number picks a theme, null follows the service
            theme = None if a["theme_id"] is None else _number(a, "theme_id")
        copyright_text = _text(a, "copyright", 300, False)
        operator_name = _text(a, "operator_name", 120, False)
        notice = NO_CHANGE
        if "notice" in a:                        # "before", "after", "never", or "" to follow the program
            notice = _text(a, "notice", 10, False) or None
        if song_id is None:
            return {"id": self.c.create_song(title, author, text, draft=bool(draft), ccli=ccli, order=order,
                                             theme_id=None if theme is NO_CHANGE else theme,
                                             copyright_text=copyright_text, operator_name=operator_name,
                                             notice=None if notice is NO_CHANGE else notice)}
        self.c.update_song(song_id, title, author, text, draft=draft, ccli=ccli, order=order, theme_id=theme,
                           copyright_text=copyright_text, notice=notice, operator_name=operator_name)
        return {"id": song_id}

    def _theme_save(self, a):
        name = _text(a, "name", 80).strip()
        style, build = a.get("style"), a.get("build")
        if not isinstance(style, dict) or (build is not None and not isinstance(build, dict)):
            raise ApiError("'style' and 'build' must be objects")
        kind = a.get("kind")
        if kind is not None and kind not in ("projector", "overlay", "confidence"):
            raise ApiError("'kind' must be projector, overlay or confidence")
        return {"id": self.c.save_theme(name, style, _number(a, "id", False), build, kind)}

    @staticmethod
    def _id_list(a):
        ids = a.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise ApiError("'ids' must be a list of numbers")
        if len(ids) > 500:
            raise ApiError("Delete at most 500 at a time")
        return ids

    def _songs_delete(self, a):
        return self.c.delete_songs(self._id_list(a))

    def _decks_delete(self, a):
        return self.c.delete_decks(self._id_list(a))

    def _song_delete(self, a):
        self.c.delete_song(_number(a, "id"))

    def _item_autoplay(self, a):
        if a.get("seconds") is None and a.get("end_action") is None:
            return self.c.set_item_autoplay(_number(a, "item_id"))
        self.c.set_item_autoplay(_number(a, "item_id"), _number(a, "seconds", False),
                                 _text(a, "end_action", 40, False))

    def _deck_pages_timing(self, a):
        """Only the settings that are present are changed; null puts a setting back to the deck's."""
        args = {}
        if "seconds" in a:
            args["seconds"] = _number(a, "seconds", False)
        if "transition" in a or "ms" in a:
            args["transition"] = _text(a, "transition", 40, False)
            args["ms"] = _number(a, "ms", False)
        self.c.set_deck_pages_timing(_number(a, "id"), _list(a, "pages"), **args)

    def _deck_transition(self, a):
        self.c.set_deck_transition(_number(a, "id"), _text(a, "transition", 40), _number(a, "milliseconds"))

    def _deck_rename(self, a):
        self.c.rename_deck(_number(a, "id"), _text(a, "name"))

    def _deck_delete(self, a):
        self.c.delete_deck(_number(a, "id"))

    def _bible_delete(self, a):
        self.c.delete_translation(_text(a, "abbreviation", 40))

    def _import_batch(self, a):
        if self.c.imports is None:
            raise ApiError("Importing needs a media folder; start the program with --media")
        return {"batch": self.c.imports.new_batch()}

    def _theme_import(self, a):
        report = self.c.import_themes(_text(a, "batch"))
        return {"added": report.added, "duplicates": report.duplicates, "notes": report.warnings[:25]}

    def _deck_pages_sound(self, a):
        """Mute a video page, and/or give pages a sound from the Media Library. Only what is sent is changed."""
        args = {}
        if "muted" in a:
            args["muted"] = _flag(a, "muted")
        if "sound_loop" in a:
            args["sound_loop"] = _flag(a, "sound_loop")
        if "sound_id" in a:
            args["sound_id"] = _number(a, "sound_id", False)
        if not args:
            raise ApiError("Nothing to change")
        self.c.set_deck_pages_sound(_number(a, "id"), a.get("page_ids"), **args)

    def _background_import(self, a):
        return {"path": self.c.import_background(_text(a, "batch"))}

    def _import_start(self, a):
        if self.c.imports is None:
            raise ApiError("Importing needs a media folder; start the program with --media")
        kind = _text(a, "kind", 20)
        abbreviation = _text(a, "abbreviation", 16, False)
        if abbreviation and not abbreviation.replace("-", "").replace("_", "").isalnum():
            raise ApiError("The Bible abbreviation can only use letters, numbers, - and _")
        return {"job": self.c.imports.start(kind, _text(a, "batch", 40), abbreviation,
                                            _text(a, "name", 200, False),
                                            replace_for=_number(a, "replace_for", False))}
