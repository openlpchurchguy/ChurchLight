# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""LiveSession: what is on screen right now.

It holds the loaded slide list, which slide is live, and an optional overlay
layer (for example a Bible verse shown over a slideshow slide). Showing or
hiding the overlay never moves the slide underneath, which is the point: the
deck stays where it is while a verse comes and goes.

Pure Python with no GUI. It is thread-safe, because the web server thread
reads the state while the operator window changes it. Anything that draws
(a projector window, a stage page) never reads the session directly; it takes
a `LiveState` snapshot, or hears about changes through `subscribe`.

The "next" slide shown on stage screens is derived from the list whenever a
snapshot is taken; it is never stored, so it can't go stale.
"""
import logging
import threading
import time
from dataclasses import dataclass

from .slides import Slide

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiveState:
    """An immutable snapshot of the live output, safe to hand to any thread."""
    version: int                  # increases on every real change; pollers compare it
    blank: bool                   # the operator has blanked the public screens
    base: Slide | None            # the live slide, or None if nothing is live
    base_index: int | None
    base_count: int
    next_base: Slide | None       # derived: the slide after `base` (the first, if none is live)
    prev_base: Slide | None       # derived: the slide before `base`
    overlay: Slide | None         # the overlay slide, only while it is visible
    overlay_loaded: bool          # an overlay exists but may be hidden
    overlay_index: int | None
    overlay_count: int
    text_hidden: bool = False     # the operator hid the words on the public screens (the backgrounds stay)
    lower_hidden: bool = False    # the operator hid the lower-third outputs only (the projector is untouched)
    countdown: tuple | None = None   # (end time in epoch seconds, label) while a countdown is running, else None
    message: str = ""             # an alert for the speaker and band screens (stage and confidence); never the audience
    media: dict | None = None     # the video or sound clip that is the live slide: see LiveSession._play_view
    sound: dict | None = None     # the music playing under the slides (its own layer, see LiveSession.sound_play)
    page_sound: dict | None = None   # the sound file a slideshow page carries, playing while that page is live


MEDIA_KINDS = ("video", "audio")       # slide kinds the projector page plays instead of drawing


def clip_position(view, now):
    """Where a clip is, in seconds, at time `now` (epoch seconds): the position it had when it last changed, plus the
    time since if it is playing, wrapped round if it loops and held at its length otherwise. `view` is a media or
    sound dict from a LiveState. The pages do this same sum with the server's clock, so they never ask for progress."""
    position = view["pos"] + (max(0.0, now - view["at"]) if view["playing"] else 0.0)      # before `at` it has not started
    length = view.get("duration") or 0
    if length > 0:
        position = position % length if view["loop"] else min(position, length)
    return max(0.0, position)


class LiveSession:
    def __init__(self, slides=(), clock=None):
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._listeners = []
        self._version = 0
        self._blank = False
        self._text_hidden = False
        self._lower_hidden = False
        self._countdown = None
        self._message = ""
        self._page = None               # the sound a slideshow page carries, started when that page becomes live
        self._page_for = None
        self._media_volume = 1.0        # shared by every clip, kept when the slide changes
        self._media_muted = False
        self._sound = None              # the music under the slides: a dict, or None
        self._sound_volume = 1.0
        self._sound_muted = False
        self._reset(slides)

    def _reset(self, slides):
        self._slides = tuple(slides)
        self._index = None          # None = nothing live yet
        self._overlay = ()
        self._overlay_index = 0
        self._overlay_visible = False
        self._play = None           # playback of the live slide when it is a video or sound clip
        self._play_for = None       # which slide that playback belongs to
        self._page = None           # playback of the sound file a slideshow page carries
        self._page_for = None

    # -- reading ------------------------------------------------------------

    @property
    def slides(self):
        with self._lock:
            return self._slides

    @property
    def version(self):
        with self._lock:
            return self._version

    def state(self):
        with self._lock:
            return self._snapshot()

    def _snapshot(self):
        count = len(self._slides)
        if self._index is None:
            base, upcoming, earlier = None, (self._slides[0] if count else None), None
        else:
            base = self._slides[self._index]
            upcoming = self._slides[self._index + 1] if self._index + 1 < count else None
            earlier = self._slides[self._index - 1] if self._index > 0 else None
        loaded = bool(self._overlay)
        shown = self._overlay[self._overlay_index] if loaded and self._overlay_visible else None
        return LiveState(
            version=self._version, blank=self._blank,
            base=base, base_index=self._index, base_count=count, next_base=upcoming, prev_base=earlier,
            overlay=shown, overlay_loaded=loaded,
            overlay_index=self._overlay_index if loaded else None,
            overlay_count=len(self._overlay),
            text_hidden=self._text_hidden,
            lower_hidden=self._lower_hidden,
            countdown=self._countdown, message=self._message,
            media=self._play_view(base), sound=self._sound_view(), page_sound=self._page_view(base),
        )

    # -- video and sound clips ----------------------------------------------
    # A clip plays in the projector page, not here. This only remembers what the operator asked for, as an "anchor": the
    # position the clip had at the moment `at` (and whether it was playing). Every page works out where the clip is now
    # from that, so nothing is sent while it plays, and several pages stay together.

    START_LEAD = 0.5                  # seconds a newly live clip waits, so the screen has the slide up before the clip starts
    MAX_TRANSITION_LEAD = 2.0         # and the slide's own transition, when the screen will play one (never more than this)

    def _start_lead(self, slide, previous):
        """How long after Go live a new clip starts: a moment for the screens to react, plus the slide's transition when
        the move is inside the same slideshow (the only move that plays one)."""
        lead = self.START_LEAD
        if (previous is not None and previous == (slide.item_type, slide.source_item_id) and slide.item_type == "presentation"
                and slide.kind not in MEDIA_KINDS           # a video page changes plainly: the screen plays no transition for it
                and slide.transition not in (None, "", "none") and (slide.transition_ms or 0) > 0):
            lead += min(self.MAX_TRANSITION_LEAD, slide.transition_ms / 1000.0)
        return lead

    def _sync_play(self):
        """A clip that has just become the live slide starts playing from the top, a moment later (see `_start_lead`);
        leaving it forgets it. The same slide rebuilt (a theme was edited) keeps playing."""
        slide = self._slides[self._index] if self._index is not None and self._index < len(self._slides) else None
        previous = getattr(self, "_last_slide_ctx", None)
        self._last_slide_ctx = (slide.item_type, slide.source_item_id) if slide is not None else None
        if slide is None or slide.sound_path is None:
            self._page, self._page_for = None, None
        else:
            page_key = (self._index, slide.source_item_id, slide.sound_path)
            if page_key != self._page_for:
                self._page_for = page_key
                self._page = {"playing": True, "pos": 0.0, "at": self._clock() + self._start_lead(slide, previous), "loop": slide.sound_loop}
        if slide is None or slide.kind not in MEDIA_KINDS:
            self._play, self._play_for = None, None
            return
        key = (self._index, slide.source_item_id, slide.image_path)
        if key != self._play_for:
            self._play_for = key
            self._play = {"playing": True, "pos": 0.0, "at": self._clock() + self._start_lead(slide, previous), "loop": False}

    def _play_view(self, slide):
        play = self._play
        if play is None or slide is None or slide.kind not in MEDIA_KINDS:
            return None
        return {"kind": slide.kind, "src": slide.image_path, "name": slide.label or "",
                "duration": (slide.duration_ms or 0) / 1000.0, "playing": play["playing"], "pos": play["pos"],
                "at": play["at"], "loop": play["loop"], "volume": self._media_volume, "muted": self._media_muted,
                "silent": slide.muted}

    def _page_view(self, slide):
        page = self._page
        if page is None or slide is None or slide.sound_path is None:
            return None
        return {"kind": "audio", "src": slide.sound_path, "name": slide.sound_name or slide.label or "",
                "duration": (slide.sound_duration_ms or 0) / 1000.0, "playing": page["playing"], "pos": page["pos"],
                "at": page["at"], "loop": page["loop"], "volume": self._media_volume, "muted": self._media_muted}

    def _sound_view(self):
        sound = self._sound
        if sound is None:
            return None
        return dict(sound, volume=self._sound_volume, muted=self._sound_muted)

    @staticmethod
    def _length(view_slide):
        return (view_slide.duration_ms or 0) / 1000.0 if view_slide is not None else 0.0

    def _now_at(self, clip, length):
        """The clip's position right now (pauses and seeks start from it)."""
        return clip_position({"pos": clip["pos"], "at": clip["at"], "playing": clip["playing"], "loop": clip["loop"],
                              "duration": length}, self._clock())

    def _live_page_sound(self):
        slide = self._slides[self._index] if self._index is not None else None
        return (self._page, slide) if self._page is not None and slide is not None and slide.sound_path else (None, None)

    def _page_change(self, edit):
        def change():
            page, slide = self._live_page_sound()
            if page is None:
                return False
            return edit(page, (slide.sound_duration_ms or 0) / 1000.0) is not False
        return self._apply(change)

    def page_sound_pause(self):
        def edit(page, length):
            if not page["playing"]:
                return False
            self._set_clip(page, length, playing=False)
        return self._page_change(edit)

    def page_sound_resume(self):
        def edit(page, length):
            at_end = length > 0 and not page["loop"] and self._now_at(page, length) >= length - 0.05
            if page["playing"] and not at_end:
                return False
            self._set_clip(page, length, playing=True, **({"pos": 0.0} if at_end else {}))
        return self._page_change(edit)

    def page_sound_restart(self):
        def edit(page, length):
            self._set_clip(page, length, pos=0.0, playing=True)
        return self._page_change(edit)

    def page_sound_set_loop(self, on):
        def edit(page, length):
            if page["loop"] == bool(on):
                return False
            self._set_clip(page, length, loop=bool(on))
        return self._page_change(edit)

    def page_sound_finished(self, at):
        def edit(page, length):
            if page["at"] != at or not page["playing"] or page["loop"]:
                return False
            self._set_clip(page, length, pos=length, playing=False)
        return self._page_change(edit)

    def _live_clip(self):
        slide = self._slides[self._index] if self._index is not None else None
        return (self._play, slide) if self._play is not None and slide is not None else (None, None)

    def _set_clip(self, clip, length, **fields):
        """Re-anchor a clip: its position now, then the changes asked for."""
        clip["pos"] = fields.pop("pos") if "pos" in fields else self._now_at(clip, length)
        clip["at"] = self._clock()
        clip.update(fields)

    def now(self):
        """The session's clock, in epoch seconds (a test's fake clock when it was given one)."""
        return self._clock()

    def media_play(self):
        """Play (or resume) the live clip. A clip that has finished starts again from the top."""
        def change():
            clip, slide = self._live_clip()
            if clip is None:
                return False
            length = self._length(slide)
            at_end = length > 0 and not clip["loop"] and self._now_at(clip, length) >= length - 0.05
            if clip["playing"] and not at_end:
                return False
            self._set_clip(clip, length, playing=True, **({"pos": 0.0} if at_end else {}))
            return True
        return self._apply(change)

    def media_pause(self):
        def change():
            clip, slide = self._live_clip()
            if clip is None or not clip["playing"]:
                return False
            self._set_clip(clip, self._length(slide), playing=False)
            return True
        return self._apply(change)

    def media_restart(self):
        def change():
            clip, slide = self._live_clip()
            if clip is None:
                return False
            self._set_clip(clip, self._length(slide), pos=0.0, playing=True)
            return True
        return self._apply(change)

    def media_seek(self, seconds):
        """Jump to `seconds` (kept inside the clip). The clip keeps playing or stays paused, as it was."""
        def change():
            clip, slide = self._live_clip()
            if clip is None:
                return False
            length = self._length(slide)
            target = max(0.0, float(seconds))
            if length > 0:
                target = min(target, length)
            self._set_clip(clip, length, pos=target)
            return True
        return self._apply(change)

    def media_set_loop(self, on):
        def change():
            clip, slide = self._live_clip()
            if clip is None or clip["loop"] == bool(on):
                return False
            self._set_clip(clip, self._length(slide), loop=bool(on))
            return True
        return self._apply(change)

    def media_finished(self, at):
        """The controller's timer: the clip that was anchored at `at` has run to its end. Ignored if it was changed since."""
        def change():
            clip, slide = self._live_clip()
            if clip is None or clip["at"] != at or not clip["playing"] or clip["loop"]:
                return False
            length = self._length(slide)
            self._set_clip(clip, length, pos=length, playing=False)
            return True
        return self._apply(change)

    def set_media_volume(self, volume):
        def change():
            new = min(1.0, max(0.0, float(volume)))
            changed = new != self._media_volume
            self._media_volume = new
            return changed
        return self._apply(change)

    def set_media_muted(self, muted):
        def change():
            changed = bool(muted) != self._media_muted
            self._media_muted = bool(muted)
            return changed
        return self._apply(change)

    # music under the slides: a clip of its own that keeps playing whatever the slides do
    def sound_play(self, src, name, duration_ms, loop=False):
        """Start `src` as the music under the slides, replacing any music already playing."""
        def change():
            self._sound = {"src": src, "name": name or "", "duration": (duration_ms or 0) / 1000.0,
                           "playing": True, "pos": 0.0, "at": self._clock(), "loop": bool(loop)}
            return True
        return self._apply(change)

    def _sound_change(self, edit):
        def change():
            if self._sound is None:
                return False
            return edit(self._sound) is not False
        return self._apply(change)

    def sound_pause(self):
        def edit(sound):
            if not sound["playing"]:
                return False
            self._set_clip(sound, sound["duration"], playing=False)
        return self._sound_change(edit)

    def sound_resume(self):
        def edit(sound):
            at_end = (sound["duration"] > 0 and not sound["loop"]
                      and self._now_at(sound, sound["duration"]) >= sound["duration"] - 0.05)
            if sound["playing"] and not at_end:
                return False
            self._set_clip(sound, sound["duration"], playing=True, **({"pos": 0.0} if at_end else {}))
        return self._sound_change(edit)

    def sound_restart(self):
        def edit(sound):
            self._set_clip(sound, sound["duration"], pos=0.0, playing=True)
        return self._sound_change(edit)

    def sound_seek(self, seconds):
        def edit(sound):
            target = max(0.0, float(seconds))
            if sound["duration"] > 0:
                target = min(target, sound["duration"])
            self._set_clip(sound, sound["duration"], pos=target)
        return self._sound_change(edit)

    def sound_set_loop(self, on):
        def edit(sound):
            if sound["loop"] == bool(on):
                return False
            self._set_clip(sound, sound["duration"], loop=bool(on))
        return self._sound_change(edit)

    def sound_stop(self):
        """Take the music away altogether."""
        def change():
            changed = self._sound is not None
            self._sound = None
            return changed
        return self._apply(change)

    def sound_finished(self, at):
        def edit(sound):
            if sound["at"] != at or not sound["playing"] or sound["loop"]:
                return False
            self._set_clip(sound, sound["duration"], pos=sound["duration"], playing=False)
        return self._sound_change(edit)

    def set_sound_volume(self, volume):
        def change():
            new = min(1.0, max(0.0, float(volume)))
            changed = new != self._sound_volume
            self._sound_volume = new
            return changed
        return self._apply(change)

    def set_sound_muted(self, muted):
        def change():
            changed = bool(muted) != self._sound_muted
            self._sound_muted = bool(muted)
            return changed
        return self._apply(change)

    # -- listening ----------------------------------------------------------

    def subscribe(self, callback):
        """Call `callback(LiveState)` after every real change. Returns a function
        that removes the subscription. A callback that raises is logged and
        skipped; it can never break the session or the other listeners."""
        with self._lock:
            self._listeners.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._listeners:
                    self._listeners.remove(callback)
        return unsubscribe

    def _apply(self, change):
        """Run `change()` under the lock. If it reports a change, bump the version
        and notify listeners (outside the lock, so they may call back in)."""
        with self._lock:
            if not change():
                return False
            self._sync_play()
            self._version += 1
            state = self._snapshot()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(state)
            except Exception:
                log.exception("A LiveSession listener failed")
        return True

    # -- the base layer -----------------------------------------------------

    def load(self, slides):
        """Replace the slide list (a service, or one item sent live). Nothing goes on
        screen until the operator picks a slide. The overlay is dropped; the
        blank setting is kept, so a blanked screen stays blank."""
        def change():
            self._reset(slides)
            return True
        return self._apply(change)

    def reload(self, slides, pick=None):
        """Replace the slide list after an edit, in one step, and go back to the slide `pick(new slides)` names (an index, or None).

        A video, sound or slideshow-page sound that is playing on a slide that is still there carries on from where it was, instead
        of starting again, and the pages hear of only one change."""
        def change():
            old = (self._play, self._play_for, self._page, self._page_for)
            self._reset(slides)
            if pick is not None:
                index = pick(self._slides)
                if index is not None and 0 <= index < len(self._slides):
                    self._index = index
            self._carry_playback(*old)
            return True
        return self._apply(change)

    def _carry_playback(self, play, play_for, page, page_for):
        slide = self._slides[self._index] if self._index is not None and self._index < len(self._slides) else None
        if slide is None:
            return
        if play is not None and play_for is not None and slide.kind in MEDIA_KINDS \
                and play_for[1:] == (slide.source_item_id, slide.image_path):
            self._play, self._play_for = play, (self._index, slide.source_item_id, slide.image_path)
        if page is not None and page_for is not None and slide.sound_path is not None \
                and page_for[1:] == (slide.source_item_id, slide.sound_path):
            self._page, self._page_for = page, (self._index, slide.source_item_id, slide.sound_path)

    def go_to(self, index):
        """Make slide `index` live. Raises IndexError if there is no such slide."""
        def change():
            if not 0 <= index < len(self._slides):
                raise IndexError(f"No slide {index}; there are {len(self._slides)}")
            changed = index != self._index
            self._index = index
            return changed
        return self._apply(change)

    def go_to_item(self, source_item_id):
        """Make the first slide of a service item live ("jump to this item")."""
        with self._lock:
            found = next((i for i, slide in enumerate(self._slides) if slide.source_item_id == source_item_id), None)
        if found is None:
            raise ValueError(f"No slide comes from service item {source_item_id}")
        return self.go_to(found)             # outside the lock: see the note above set_blank

    def next(self):
        """Advance one slide; the first press starts at slide 0. False at the end."""
        def change():
            if not self._slides:
                return False
            if self._index is None:
                self._index = 0
                return True
            if self._index + 1 < len(self._slides):
                self._index += 1
                return True
            return False
        return self._apply(change)

    def previous(self):
        """Go back one slide. False at the start or if nothing is live."""
        def change():
            if self._index is None or self._index == 0:
                return False
            self._index -= 1
            return True
        return self._apply(change)

    def clear(self):
        """Take everything off: no live slide and no overlay. (The blank setting stays.)"""
        def change():
            changed = self._index is not None or bool(self._overlay)
            self._index = None
            self._overlay, self._overlay_index, self._overlay_visible = (), 0, False
            return changed
        return self._apply(change)

    # -- the overlay layer --------------------------------------------------

    def show_overlay(self, slides=None, index=0):
        """Show an overlay on top of the live slide without moving it.

        With `slides` (for example the slides built for a verse), load them and
        show them. With no argument, bring back the overlay that was hidden.
        """
        def change():
            if slides is not None:
                new = tuple(slides)
                if not new:
                    raise ValueError("An overlay needs at least one slide")
                if not 0 <= index < len(new):
                    raise IndexError(f"No overlay slide {index}; there are {len(new)}")
                self._overlay, self._overlay_index, self._overlay_visible = new, index, True
                return True
            if not self._overlay:
                raise ValueError("No overlay is loaded; pass the slides to show")
            changed = not self._overlay_visible
            self._overlay_visible = True
            return changed
        return self._apply(change)

    def hide_overlay(self):
        """Hide the overlay but keep it loaded, so `show_overlay()` can bring it back."""
        def change():
            changed = self._overlay_visible
            self._overlay_visible = False
            return changed
        return self._apply(change)

    def clear_overlay(self):
        """Hide the overlay and forget it."""
        def change():
            changed = bool(self._overlay)
            self._overlay, self._overlay_index, self._overlay_visible = (), 0, False
            return changed
        return self._apply(change)

    def overlay_next(self):
        """Step to the next overlay slide (the second half of a long verse)."""
        def change():
            if self._overlay and self._overlay_index + 1 < len(self._overlay):
                self._overlay_index += 1
                return True
            return False
        return self._apply(change)

    def overlay_previous(self):
        def change():
            if self._overlay and self._overlay_index > 0:
                self._overlay_index -= 1
                return True
            return False
        return self._apply(change)

    # -- blanking -----------------------------------------------------------

    # Listeners are told of a change outside the lock (see _apply). So no method may call another changing method while it
    # holds the lock itself: the listeners would then run with the lock still held, and a listener that waits for another
    # lock (the web server's) can deadlock with a thread holding that one and waiting for this. A toggle therefore passes
    # None, which means "the opposite of now", decided inside the one locked change.
    def set_blank(self, blank=True):
        """Blank (or restore) the public screens. Stage screens keep showing. None toggles."""
        def change():
            new = (not self._blank) if blank is None else bool(blank)
            changed = new != self._blank
            self._blank = new
            return changed
        return self._apply(change)

    def toggle_blank(self):
        return self.set_blank(None)

    def set_text_hidden(self, hidden=True):
        """Hide (or bring back) the words on the public screens, leaving the theme's background. None toggles."""
        def change():
            new = (not self._text_hidden) if hidden is None else bool(hidden)
            changed = new != self._text_hidden
            self._text_hidden = new
            return changed
        return self._apply(change)

    def toggle_text_hidden(self):
        return self.set_text_hidden(None)

    def set_lower_hidden(self, hidden=True):
        """Hide (or bring back) the lower-third outputs only. The projector and the blank setting are not touched. None toggles."""
        def change():
            new = (not self._lower_hidden) if hidden is None else bool(hidden)
            changed = new != self._lower_hidden
            self._lower_hidden = new
            return changed
        return self._apply(change)

    def toggle_lower_hidden(self):
        return self.set_lower_hidden(None)

    def start_countdown(self, end, label=""):
        """Count down to `end` (epoch seconds). Replaces a countdown already running. It is not tied to the
        slides: loading, blanking or clearing never touches it."""
        def change():
            new = (float(end), str(label or ""))
            changed = new != self._countdown
            self._countdown = new
            return changed
        return self._apply(change)

    def stop_countdown(self):
        def change():
            changed = self._countdown is not None
            self._countdown = None
            return changed
        return self._apply(change)

    def set_message(self, text):
        """Set (or, with empty text, clear) the alert shown on the stage and confidence screens."""
        def change():
            new = str(text or "").strip()
            changed = new != self._message
            self._message = new
            return changed
        return self._apply(change)

    def refresh(self):
        """Make every output redraw although nothing on screen changed (a theme was edited)."""
        return self._apply(lambda: True)
