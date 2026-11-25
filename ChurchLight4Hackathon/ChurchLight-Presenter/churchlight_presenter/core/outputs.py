# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Outputs: what each screen or overlay receives from the live session.

An Output is a small piece of configuration: a name, a layout, and (for a
native window) which monitor it uses. Every output reads the same LiveState.
`build_payload` turns that state into a plain dict of JSON-friendly values for
one output, and it is the ONLY place a payload is made, which is what makes
the main safety rule structural:

    Speaker notes reach stage layouts only. A public layout (projector,
    livestream overlays) never receives them, whatever the slide contains.

Outputs live in a small settings file on each computer, not in the database:
which monitor is which is a property of the machine, so the database can move
between computers without dragging monitor assignments along.
"""
import json
import os
import re
import time
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .timerstyle import alert_style, projector_style, speaker_style

PAYLOAD_SCHEMA = 1   # bump when the payload shape changes, so old stage pages fail visibly

# The only slide fields that may be sent to a public output. Anything not
# listed here (notes) is withheld by default; adding a Slide field forces a
# decision about which side of this line it belongs on.
PUBLIC_SLIDE_FIELDS = (
    "kind", "item_type", "source_item_id", "text", "segments", "label",
    "reference", "verse_numbers", "continued", "image_path", "theme_id", "title_first",
    "transition", "transition_ms", "image_fit", "duration_ms",
)
STAGE_ONLY_SLIDE_FIELDS = ("notes", "auto_seconds", "end_action", "bible_theme_id", "overlay_theme_id",
                           "part",      # part: a song slide's place in its order; only the operator page uses it
                           "muted", "sound_path", "sound_name", "sound_duration_ms", "sound_loop")   # the sound a page carries goes out as `page_sound`, only to the projector

# Layouts that draw the global Bible theme for verses; the others (lower thirds...) keep the
# slide's own theme, so a verse on the lower third is not restyled by the projector's Bible look.
BIBLE_THEME_LAYOUTS = frozenset({"projector", "ppt_verse"})

# Layouts drawn as a translucent box over the broadcast; they use each slide's overlay theme.
OVERLAY_LAYOUTS = frozenset({"lower_third", "song_lower", "verse_lower"})


def _types(*names):
    return frozenset(names)


@dataclass(frozen=True)
class Layout:
    audience: str                     # "public" (no notes), "stage" (notes allowed) or "confidence"
    #   (the speaker's monitor: no notes, never on air, keeps showing while the public screens are blanked)
    base_types: frozenset | None = None      # item types shown from the base layer; None = all
    overlay_types: frozenset | None = None   # item types shown from the overlay; None = all
    show_next: bool = False           # include the upcoming slide
    show_position: bool = False       # include "slide 4 of 12" counts
    transitions: bool = False         # draw a deck's slide transition, and get next/previous pictures to preload


_NONE = _types()      # shows nothing from that layer

# Keys match the stage names already in use (Song-Lower becomes song_lower).
# These are starting definitions; edit them to match how each overlay behaves.
LAYOUTS = {
    "projector":   Layout("public", transitions=True),    # the native projector: base with overlay on top
    "lower_third": Layout("public"),                      # a generic lower third for the livestream
    "song_lower":  Layout("public", _types("song"), _NONE),
    "song_full":   Layout("public", _types("song"), _NONE),
    "verse_lower": Layout("public", _types("scripture"), _types("scripture")),
    "verse_full":  Layout("public", _types("scripture"), _types("scripture")),
    "ppt_verse":   Layout("public", _types("presentation"), _types("scripture")),
    "confidence":  Layout("confidence"),                  # the speaker's monitor: big plain text, decks mirrored
    "ppt_notes":   Layout("stage", _types("presentation"), None, show_next=True, show_position=True),
    "stage":       Layout("stage", None, None, show_next=True, show_position=True),
}

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


@dataclass(frozen=True)
class Output:
    name: str                          # used in the address /out/<name>; lowercase, digits, - and _
    layout: str                        # a key of LAYOUTS ("Song-Lower" is accepted for "song_lower")
    kind: str = "virtual"              # "native" window on a monitor, or "virtual" web page
    screen: int | None = None          # native only: which monitor (0 = the first)
    theme_id: int | None = None        # optional: draw with this theme instead of each slide's own

    def __post_init__(self):
        object.__setattr__(self, "layout", str(self.layout).strip().lower().replace("-", "_"))
        if not isinstance(self.name, str) or not _NAME_RE.match(self.name):
            raise ValueError(
                f"Output name {self.name!r} must be lowercase letters, digits, - or _ "
                "(up to 40 characters, starting with a letter or digit)")
        if self.layout not in LAYOUTS:
            raise ValueError(
                f"Unknown layout {self.layout!r}; choose from: {', '.join(sorted(LAYOUTS))}")
        if self.kind not in ("native", "virtual"):
            raise ValueError(f"kind must be 'native' or 'virtual', got {self.kind!r}")
        if self.kind == "native":
            if isinstance(self.screen, bool) or not isinstance(self.screen, int) or self.screen < 0:
                raise ValueError("A native output needs a screen number (0, 1, 2...)")
        elif self.screen is not None:
            raise ValueError("Only a native output has a screen number")


# ---------------------------------------------------------------------------
# Building payloads
# ---------------------------------------------------------------------------
def slide_to_dict(slide, include_notes=False):
    """A slide as plain JSON-friendly values. Notes only when `include_notes`."""
    data = {
        "kind": slide.kind,
        "item_type": slide.item_type,
        "source_item_id": slide.source_item_id,
        "text": slide.text,
        "segments": [[number, text] for number, text in slide.segments],
        "label": slide.label,
        "reference": slide.reference,
        "verse_numbers": list(slide.verse_numbers),
        "continued": slide.continued,
        "image_path": slide.image_path,
        "theme_id": slide.theme_id,
        "title_first": slide.title_first,
        "transition": slide.transition,
        "transition_ms": slide.transition_ms,
        "image_fit": slide.image_fit,
    }
    if include_notes:
        data["notes"] = slide.notes
    return data


def _preload(state, layout):
    """Image paths of the slides either side of the live one that this layout would draw."""
    paths = []
    for slide in (state.next_base, state.prev_base):
        if (slide is not None and slide.image_path and slide.kind not in ("video", "audio")
                and (layout.base_types is None or slide.item_type in layout.base_types)
                and slide.image_path not in paths):
            paths.append(slide.image_path)
    return paths


PLAYER_LAYOUTS = frozenset({"projector"})        # the screens that play video and sound (a clip's picture is the projector's)
COUNTDOWN_LAYOUTS = frozenset({"projector", "confidence", "stage", "ppt_notes"})
MESSAGE_LAYOUTS = frozenset({"confidence", "stage", "ppt_notes"})
COUNTDOWN_LINGER = 5          # seconds the page keeps showing 0:00 after a countdown ends, then it goes away


def build_payload(state, output, style_for=None, confidence_theme_id=None, home=None, peek=False,
                  verse_over_theme_id=None, now=None, timer_style=None, confidence_hides_words=True,
                  sound_output=None):
    """What `output` should show for this LiveState, as a plain dict.

    - Public layouts get only the slide fields in PUBLIC_SLIDE_FIELDS, and
      get no content at all while the screen is blanked.
    - Stage layouts also get notes, the upcoming slide and position counts,
      and keep showing while blanked (with `blank: true`, so the page can say so).
    - A layout's `base_types` / `overlay_types` filter which kinds of item it
      draws; a filtered-out layer is None.
    - `style_for(theme_id)` (optional) supplies the look of a theme; each public
      layer then carries a `style` dict. The output's own theme, if it has one,
      wins over the slide's. Stage layers carry no style.
    - `home` (optional, {"image_path", "fit", "when_blank", "when_idle"}): the projector layout is sent this
      picture to show instead of nothing, while the screens are blanked (`when_blank`, default true) and while
      nothing is live (`when_idle`, default false). Other outputs never get it.
    - `verse_over_theme_id` (optional): the overlay theme a verse shown over a slide uses on projector-style
      outputs (instead of the Bible or slide theme). Other outputs ignore it.
    - `media` / `sound` (projector layouts only): the video or sound clip that is the live slide, and the music under the
      slides, as the anchors in core/live.py (position, when, playing, loop, volume, muted) plus the server's clock `now`,
      so the page can work out where each clip is. `sound` on each says whether this screen is the one that makes the
      sound (`sound_output` is its name); the others play their picture silently. While the screens are blanked the
      clip is not sent (the music is: walk-in music goes on under a blank screen).
    - A `verse_over_theme_id` theme with `projector_hide` set keeps the verse shown over a slide off the projector-style
      outputs (they draw the slide alone); the lower-third outputs still show it.
    - `lower_hidden` (LiveState): the lower-third layouts (OVERLAY_LAYOUTS) draw nothing while it is set; every other
      output, and the blank setting, are untouched.
    - `countdown` (LiveState): projector, confidence and stage outputs get {"end", "now", "label", "linger"} while one is
      running; `now` is the server's clock, so the page can tick correctly even if its own clock is off. Lower thirds
      never get it. `message` (an alert) goes to the stage and confidence outputs only, never to the audience.
    - `timer_style` (optional, see timerstyle.py): the look of the countdown and the alert. The countdown carries its
      `style` (the projector's, or the speaker screens' small one) and the alert is sent as `message_style`.
    - `peek` (the operator's own preview frames): a public layout shows what is live even while the screens
      are blanked; the payload says `blank: false` and `peek_blank: true`, so the page can label it.
    """
    layout = LAYOUTS[output.layout]
    stage = layout.audience == "stage"
    confidence = layout.audience == "confidence"
    hidden = state.blank and not stage and not confidence and not peek
    peeking = peek and state.blank and not stage and not confidence
    no_overlay = False
    if style_for is not None and verse_over_theme_id is not None and output.layout in BIBLE_THEME_LAYOUTS:
        over_style = style_for(verse_over_theme_id)
        no_overlay = isinstance(over_style, dict) and bool(over_style.get("projector_hide"))
    lower_off = state.lower_hidden and output.layout in OVERLAY_LAYOUTS and not peek

    def pick(slide, types, over=False):
        if slide is None or hidden or lower_off:
            return None
        if types is not None and slide.item_type not in types:
            return None
        data = slide_to_dict(slide, include_notes=stage)
        if style_for is not None and not stage:
            if output.theme_id is not None:
                theme_id = output.theme_id
            elif confidence:
                theme_id = confidence_theme_id            # one global look, set on the Themes tab
            elif output.layout in OVERLAY_LAYOUTS:
                theme_id = slide.overlay_theme_id
            elif over and verse_over_theme_id is not None and output.layout in BIBLE_THEME_LAYOUTS:
                theme_id = verse_over_theme_id          # a verse over a slideshow: its own look
            elif slide.bible_theme_id is not None and output.layout in BIBLE_THEME_LAYOUTS:
                theme_id = slide.bible_theme_id
            else:
                theme_id = slide.theme_id
            data["style"] = style_for(theme_id)
        return data

    payload = {
        "schema": PAYLOAD_SCHEMA,
        "version": state.version,
        "output": output.name,
        "layout": output.layout,
        "audience": layout.audience,
        "theme_id": output.theme_id,
        "blank": state.blank and not peeking,
        "base": pick(state.base, layout.base_types),
        "overlay": None if no_overlay else pick(state.overlay, layout.overlay_types, over=True),
    }
    if peeking:
        payload["peek_blank"] = True
    if lower_off:
        payload["lower_hidden"] = True      # this lower-third output is switched off; nothing else is
    if state.text_hidden and not stage and not hidden and (confidence_hides_words or not confidence):
        payload["text_hidden"] = True       # the page draws the theme's background without the words (the confidence monitor too)
    if home and output.layout == "projector":
        idle = not hidden and payload["base"] is None and payload["overlay"] is None
        if (hidden and home.get("when_blank", True)) or (idle and home.get("when_idle", False)):
            payload["home"] = {"image_path": home["image_path"], "fit": home["fit"]}
    if output.layout in PLAYER_LAYOUTS:
        clock = time.time() if now is None else now
        plays = output.name == sound_output
        if state.media is not None and not hidden:
            payload["media"] = dict(state.media, now=clock, plays_sound=plays)
        if state.sound is not None:
            payload["sound"] = dict(state.sound, now=clock, plays_sound=plays)
        if state.page_sound is not None and not hidden:
            payload["page_sound"] = dict(state.page_sound, now=clock, plays_sound=plays)
    if state.countdown is not None and output.layout in COUNTDOWN_LAYOUTS:
        end, label = state.countdown
        payload["countdown"] = {"end": end, "now": time.time() if now is None else now, "label": label,
                                "linger": COUNTDOWN_LINGER,
                                "style": (projector_style if output.layout == "projector" else speaker_style)(timer_style)}
    if state.message and output.layout in MESSAGE_LAYOUTS:
        payload["message"] = state.message
        payload["message_style"] = alert_style(timer_style)
    if layout.transitions:
        # Pictures the next slide change may need, so the page has them ready before it is asked.
        payload["transitions"] = True
        payload["preload"] = [] if hidden else _preload(state, layout)
    if layout.show_next:
        payload["next"] = pick(state.next_base, None)
    if layout.show_position:
        payload["position"] = {
            "index": state.base_index, "count": state.base_count,
            "overlay_index": state.overlay_index, "overlay_count": state.overlay_count,
        }
    return payload


# ---------------------------------------------------------------------------
# The local settings file
# ---------------------------------------------------------------------------
def default_outputs():
    """A starting set: a projector on the second monitor, a livestream lower
    third, a stage page and a confidence page. Replace these with the real setup in the settings."""
    return [
        Output("projector", "projector", kind="native", screen=1),
        Output("lower-third", "lower_third"),
        Output("stage", "stage"),
        Output("confidence", "confidence"),
    ]


def default_settings_path(platform=None, env=None, home=None):
    """Where this computer keeps its output settings (outside the database)."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = Path(home) if home else Path.home()
    if platform.startswith("win"):
        return Path(env.get("APPDATA") or home / "AppData" / "Roaming") / "ChurchLight Presenter" / "outputs.json"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "ChurchLight Presenter" / "outputs.json"
    return Path(env.get("XDG_CONFIG_HOME") or home / ".config") / "churchlight-presenter" / "outputs.json"


def _check_unique(outputs):
    names = [o.name for o in outputs]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"Output names must be unique; repeated: {', '.join(dupes)}")


def save_outputs(outputs, path):
    """Write the outputs as JSON, replacing the file only once it is fully written,
    so a crash mid-save can't leave a broken settings file."""
    outputs = list(outputs)
    _check_unique(outputs)
    data = {"version": 1, "outputs": [
        {"name": o.name, "layout": o.layout, "kind": o.kind,
         "screen": o.screen, "theme_id": o.theme_id}
        for o in outputs]}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load_outputs(path):
    """Read the outputs. A missing file gives the defaults. A damaged file raises
    ValueError saying so, instead of quietly replacing someone's settings."""
    path = Path(path)
    if not path.exists():
        return default_outputs()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data["outputs"]
        outputs = [Output(**entry) for entry in entries]
    except (json.JSONDecodeError, KeyError, TypeError) as err:
        raise ValueError(
            f"The output settings file {path} is damaged ({err}). "
            "Fix it, or delete it to start from the defaults.") from err
    _check_unique(outputs)
    return outputs
