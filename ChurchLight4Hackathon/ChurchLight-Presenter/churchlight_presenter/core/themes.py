# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Theme styles: how text looks on the public outputs.

A theme's `settings` JSON can hold a "style" object next to the slide-splitting
settings. Styles are validated against a short list of allowed keys, so a theme
can never carry arbitrary CSS to the output pages, and a damaged style stored in
the database is cut down to its valid parts instead of breaking a live screen.

    font_family       "Georgia, serif"          letters, digits, spaces, commas, hyphens, quotes
    font_size         2 to 14                   percent of the screen height
    color             "#ffffff"                 #rgb, #rrggbb or #rrggbbaa
    line_height       1.0 to 2.5
    align             left | center | right
    shadow            true | false              a dark glow behind the text
    uppercase         true | false
    band              true | false              dark band behind lower-third text
    band_color        "#000000" with alpha      e.g. "#000000a0"
    background_color  "#003366"                 fills a full-frame layer (default: none)
    reference_scale   0.4 to 1.2                size of the Bible reference line relative to the text
    reference_pos     auto | above | below      where the Bible reference sits against the verse text (auto: below a
                                                single verse, above a passage with numbered verses)
    balance_lines     true | false              song lyrics only: a line too long for the screen breaks near its
                                                middle instead of leaving a few words alone on the second line
    projector_hide    true | false              overlay themes: when this is the theme for "a verse over a slideshow", the verse
                                                stays off the projector and shows on the lower third only
    v_pos             top | middle | bottom     overlay themes: where the box sits up and down
    h_pos             left | center | right     overlay themes: where the box sits across
    box_width         20 to 100                 overlay themes: width of the box, percent of the screen
    margin            0 to 15                   overlay themes: gap to the screen edge, percent of the screen height
    margin_top        0 to 40                   projector themes: space above the text, percent of the screen height (default 4)
    margin_bottom     0 to 40                   projector themes: space below the text, percent of the screen height (default 4)
    margin_left       0 to 45                   projector themes: space left of the text, percent of the screen width (default 6)
    margin_right      0 to 45                   projector themes: space right of the text, percent of the screen width (default 6)
    max_height        0 to 60                   overlay themes: tallest the box may grow, percent of the screen height (0 = no limit)

A theme is for one kind of output (`kind` in its settings, next to `style`): "projector" (the room
screen), "overlay" (the lower-third picture laid over the broadcast: always a translucent box) or
"confidence" (the speaker's monitor: plain, large text). A theme without a kind is a projector theme.
"""
import logging
import re

log = logging.getLogger(__name__)

_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT = re.compile(r"^[A-Za-z0-9 ,'\"\-]{1,120}$")


def _number(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return float(value)
    return check


def _color(value):
    if not isinstance(value, str) or not _COLOR.match(value):
        raise ValueError("must be a color like #ffffff (or #ffffffcc with transparency)")
    return value.lower()


def _font(value):
    if not isinstance(value, str) or not _FONT.match(value):
        raise ValueError("may only use letters, digits, spaces, commas, hyphens and quotes")
    return value.strip()


def _number_font(value):
    return "same" if value == "same" else _font(value)


def _choice(*options):
    def check(value):
        if value not in options:
            raise ValueError(f"must be one of: {', '.join(options)}")
        return value
    return check


_BACKGROUND_PATH = re.compile(r"^backgrounds/[0-9a-f]{16}\.(png|jpg|jpeg|gif|webp)$")


def _background_image(value):
    if not isinstance(value, str) or not _BACKGROUND_PATH.match(value):
        raise ValueError("must be a picture uploaded through the theme editor")
    return value


def _flag(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


STYLE_KEYS = {
    "font_family": _font, "font_size": _number(2, 20), "color": _color,
    "line_height": _number(1.0, 2.5), "align": _choice("left", "center", "right"),
    "shadow": _flag, "uppercase": _flag, "band": _flag, "band_color": _color,
    "background_color": _color, "reference_scale": _number(0.4, 1.2),
    "v_pos": _choice("top", "middle", "bottom"), "h_pos": _choice("left", "center", "right"),
    "box_width": _number(20, 100), "margin": _number(0, 15), "max_height": _number(0, 60),
    "margin_top": _number(0, 40), "margin_bottom": _number(0, 40),
    "margin_left": _number(0, 45), "margin_right": _number(0, 45),
    "background_image": _background_image, "background_fit": _choice("fill", "fit"),
    "background_dim": _number(0, 80),
    "title_scale": _number(0.6, 2.0), "number_font": _number_font,
    "reference_pos": _choice("auto", "above", "below"), "balance_lines": _flag, "projector_hide": _flag,
}

# Overlay height cap: the text shrinks to fit, but never below this share of the theme's own size.
MIN_SHRINK = 0.6
SCREEN_ASPECT = 16 / 9       # the broadcast frame the overlay is laid over


def overlay_char_capacity(style, shrink=None):
    """About how many characters of verse text fit in an overlay box at its smallest allowed size.

    None when the theme sets no height cap. `shrink` is how small the text may get as a share of its size
    (MIN_SHRINK by default; 1 asks what fits without shrinking at all). Everything is measured in percent of the screen height,
    the way the output page sizes it: the box is `max_height` tall, minus its padding and the
    reference line, and the text may shrink to MIN_SHRINK of the theme's font size. The estimate
    leans cautious (average letter 0.5 em wide, a fifth of each line lost to word wrap) so a slide
    split by it still fits when the page draws it.
    """
    style = style or {}
    cap = style.get("max_height") or 0
    if not cap:
        return None
    size = (style.get("font_size") or 5) * (MIN_SHRINK if shrink is None else shrink)
    line_height = style.get("line_height") or 1.25
    reference = size * (style.get("reference_scale") or 0.7) * 1.25
    room = cap - 3.0 - reference                                  # 1.5vh padding top and bottom
    lines = max(1, int(room // (size * line_height)))
    width = (style.get("box_width") or 90) * SCREEN_ASPECT - 6.0 * SCREEN_ASPECT   # 3vw padding each side
    per_line = max(8, int(width / (size * 0.5) * 0.8))
    return max(40, lines * per_line)


ASPECTS = {"16:9": 16 / 9, "16:10": 16 / 10, "4:3": 4 / 3}


def projector_char_capacity(style, aspect=SCREEN_ASPECT):
    """About how many characters of verse text fill a projector slide, from the theme's real text size.

    Measured in percent of the screen height, the way the output page sizes things: the theme's margins
    (4% top and bottom, 6% of the width at the sides, unless the theme sets its own), the band behind the text if the theme has one, the
    reference line above the verse, then lines of text at the theme's size and spacing. Letters average half
    an em wide (a little more in capitals) and 15% of each line is lost to word wrap. The estimate leans
    cautious, because the projector does not shrink text to fit. A 4:3 screen holds less per line, a taller
    or bigger-text slide holds fewer lines. `themes.js` has the same formula for the theme builder's readout.
    """
    style = style or {}
    size = style.get("font_size") or 7
    line_height = style.get("line_height") or 1.3
    title = style.get("title_scale") or 1.2
    width = aspect * 100
    height = 100 - style.get("margin_top", 4) - style.get("margin_bottom", 4)
    room = width * (100 - style.get("margin_left", 6) - style.get("margin_right", 6)) / 100
    if style.get("band") is True:
        height, room = height - 3, room - width * 0.06
    lines = max(1, int((height - size * title * (line_height + 0.35)) // (size * line_height)))
    return max(40, int(lines * projector_line_chars(style, aspect) * 0.9))


def projector_max_lines(style, aspect=SCREEN_ASPECT):
    """How many lines of verse text a projector slide holds under the reference line, at the theme's real size."""
    style = style or {}
    size = style.get("font_size") or 7
    line_height = style.get("line_height") or 1.3
    title = style.get("title_scale") or 1.2
    height = 100 - style.get("margin_top", 4) - style.get("margin_bottom", 4)
    if style.get("band") is True:
        height -= 3
    return max(1, int((height - size * title * (line_height + 0.35)) // (size * line_height)))


def overlay_fit_shape(style, shrink=None):
    """(line width in em, number of lines) a lower third holds at the smallest size its text may shrink to,
    or None when the theme sets no height cap. The same measures as overlay_char_capacity, without the guesses
    about letter width: callers measure the real text with the theme's font."""
    style = style or {}
    cap = style.get("max_height") or 0
    if not cap:
        return None
    size = (style.get("font_size") or 5) * (MIN_SHRINK if shrink is None else shrink)
    line_height = style.get("line_height") or 1.25
    reference = size * (style.get("reference_scale") or 0.7) * 1.25
    lines = max(1, int((cap - 3.0 - reference) // (size * line_height)))
    width = (style.get("box_width") or 90) * SCREEN_ASPECT - 6.0 * SCREEN_ASPECT
    return width / size, lines


def projector_line_room_em(style, aspect=SCREEN_ASPECT):
    """How wide one projector text line is, in em (1 em = the theme's text size): the screen's width less the theme's
    side margins (and the band's padding), divided by the text size."""
    style = style or {}
    size = style.get("font_size") or 7
    room = aspect * 100 * (100 - style.get("margin_left", 6) - style.get("margin_right", 6)) / 100
    if style.get("band") is True:
        room -= aspect * 100 * 0.06
    return room / size


def projector_line_chars(style, aspect=SCREEN_ASPECT):
    """About how many characters fit on ONE line of the projector at the theme's text size (the lyric
    width a song line has before the screen wraps it). Cautious, like projector_char_capacity."""
    style = style or {}
    size = style.get("font_size") or 7
    room = aspect * 100 * (100 - style.get("margin_left", 6) - style.get("margin_right", 6)) / 100
    if style.get("band") is True:
        room -= aspect * 100 * 0.06
    letter = 0.62 if style.get("uppercase") else 0.5
    return max(8, int(room / (size * letter) * 0.85))


KINDS = ("projector", "overlay", "confidence")
KIND_NAMES = {"projector": "Projector", "overlay": "Overlay (lower third)", "confidence": "Confidence monitor"}


def theme_kind(settings):
    """The kind stored in a theme's settings; a theme from before kinds existed is a projector theme."""
    kind = (settings or {}).get("kind")
    return kind if kind in KINDS else "projector"


def validate_style(style):
    """The cleaned style dict, or ValueError naming the first problem. For saving."""
    if not isinstance(style, dict):
        raise ValueError("A style must be a set of name: value pairs")
    clean = {}
    for key, value in style.items():
        check = STYLE_KEYS.get(key)
        if check is None:
            raise ValueError(f"Unknown style setting {key!r}; choose from: {', '.join(STYLE_KEYS)}")
        try:
            clean[key] = check(value)
        except ValueError as err:
            raise ValueError(f"Style setting {key!r} {err}") from None
    _check_margins(clean)
    return clean


MARGIN_KEYS = ("margin_top", "margin_bottom", "margin_left", "margin_right")
MIN_TEXT_AREA = 20          # percent of the screen the margins must leave for the text, each way


def _check_margins(clean):
    """The margins together must leave room for text (ValueError in plain words if not)."""
    if clean.get("margin_top", 4) + clean.get("margin_bottom", 4) > 100 - MIN_TEXT_AREA:
        raise ValueError(f"The top and bottom margins must leave at least {MIN_TEXT_AREA}% of the screen height for the text")
    if clean.get("margin_left", 6) + clean.get("margin_right", 6) > 100 - MIN_TEXT_AREA:
        raise ValueError(f"The left and right margins must leave at least {MIN_TEXT_AREA}% of the screen width for the text")


def safe_style(style):
    """Like validate_style but for reading: invalid parts are dropped (and logged)."""
    if not isinstance(style, dict):
        return {}
    clean = {}
    for key, value in style.items():
        try:
            clean.update(validate_style({key: value}))
        except ValueError as err:
            log.warning("Ignoring bad theme style: %s", err)
    try:
        _check_margins(clean)
    except ValueError as err:
        log.warning("Ignoring the margins of a theme: %s", err)
        for key in MARGIN_KEYS:
            clean.pop(key, None)
    return clean


class ThemeStyles:
    """The style of every theme, kept in memory for building output payloads.

    The web server asks for styles from its own threads, and a SQLite connection
    may only be used by the thread that made it. So styles are loaded up front
    (and again on `reload`/`clear`, which the operator side calls after editing a
    theme) and `get` never touches the database.
    """

    def __init__(self, db):
        self.db = db
        self._styles = {}
        self.confidence_theme_id = None
        self.verse_over_theme_id = None       # the overlay theme for a verse shown over a slideshow
        self.reload()

    def reload(self):
        """Read every theme's style from the database. Call from the thread that owns it."""
        loaded = {}
        for row in self.db.list_themes():
            settings = self.db.get_theme_settings(row["id"]) or {}
            loaded[row["id"]] = safe_style(settings.get("style"))
        self._styles = loaded          # one assignment: readers see the old or the new set
        chosen = self.db.get_setting("confidence_theme")
        self.confidence_theme_id = int(chosen) if chosen and chosen.isdigit() and int(chosen) in loaded else None
        over = self.db.get_setting("verse_over_theme")
        self.verse_over_theme_id = int(over) if over and over.isdigit() and int(over) in loaded else None

    clear = reload

    def get(self, theme_id):
        """The theme's style dict ({} when it has none or is unknown), None for no theme."""
        if theme_id is None:
            return None
        return dict(self._styles.get(theme_id, {}))


# -- build settings (how text is split into slides) -------------------------------------------------
# These live in the same theme `settings` JSON, next to "style".
BUILD_KEYS = {
    "lines_per_slide": lambda v: _whole(v, 1, 12),
    "scripture_max_chars": lambda v: _whole(v, 40, 1000),
    "chars_per_line": lambda v: _whole(v, 0, 150),
    "scripture_split": _choice("chars", "verse"),
    "song_count": lambda v: "screen" if v == "characters" else _choice("lines", "screen")(v),     # "characters" was the first name
}


def _whole(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("must be a whole number")
    if not low <= value <= high:
        raise ValueError(f"must be between {low} and {high}")
    return int(value)


def validate_build(build):
    """The cleaned build-settings dict, or ValueError naming the first problem."""
    if not isinstance(build, dict):
        raise ValueError("Build settings must be a set of name: value pairs")
    clean = {}
    for key, value in build.items():
        check = BUILD_KEYS.get(key)
        if check is None:
            raise ValueError(f"Unknown slide setting {key!r}; choose from: {', '.join(BUILD_KEYS)}")
        try:
            clean[key] = check(value)
        except ValueError as err:
            raise ValueError(f"Slide setting {key!r} {err}") from None
    return clean


def style_for_kind(kind, style):
    """An overlay is always a translucent box: its band is on and never fully solid. Pictures are for the
    room screen only."""
    style = dict(style)
    if kind != "projector":
        for key in ("background_image", "background_fit", "background_dim"):
            style.pop(key, None)
    if kind == "overlay":
        style["band"] = True
        color = style.get("band_color", "#101820d0")
        alpha = int(color[7:9], 16) if len(color) == 9 else 255
        style["band_color"] = color[:7] + format(min(alpha, 0xE6), "02x")
    return style


def theme_entry(name, settings):
    """A theme's portable form: its name, kind, look and slide settings (nothing about where it is used)."""
    settings = settings or {}
    return {"name": name, "kind": theme_kind(settings), "style": dict(settings.get("style") or {}),
            "build": {k: settings[k] for k in BUILD_KEYS if k in settings}}


def is_unchanged_starter(name, settings):
    """True for a theme that still matches the starter of the same name exactly."""
    starter = STARTER_THEMES.get(name)
    return starter is not None and theme_entry(name, settings) == theme_entry(name, starter)


# -- starter themes ---------------------------------------------------------------------------------
# Plain colors only: pictures and gradients wait for the background feature.
def _projector(font, size, color, bg, lines=4, **more):
    style = {"font_family": font, "font_size": size, "color": color, "line_height": 1.3, "align": "center",
             "shadow": True, "uppercase": False, "band": True, "band_color": "#000000b0",
             "background_color": bg, "reference_scale": 0.6}
    style.update(more)
    return {"kind": "projector", "style": style, "lines_per_slide": lines}


def _overlay(v, h, width=70, size=5, align="left", **more):
    style = {"font_family": "Arial, Helvetica, sans-serif", "font_size": size, "color": "#ffffff",
             "line_height": 1.25, "align": align, "shadow": False, "uppercase": False, "band": True,
             "band_color": "#101820d0", "reference_scale": 0.7, "v_pos": v, "h_pos": h,
             "box_width": width, "margin": 4}
    style.update(more)
    return {"kind": "overlay", "style": style}


def _confidence(size, color, bg, family="Arial, Helvetica, sans-serif", **more):
    style = {"font_family": family, "font_size": size, "color": color, "line_height": 1.2, "align": "center",
             "shadow": False, "uppercase": False, "background_color": bg, "reference_scale": 0.5}
    style.update(more)
    return {"kind": "confidence", "style": style}


STARTER_THEMES = {
    "Classic white on black": _projector("Arial, Helvetica, sans-serif", 7, "#ffffff", "#000000"),
    "Deep blue": _projector("Georgia, serif", 7, "#ffffff", "#0b2a5b", band_color="#0b2a5bcc"),
    "Large print": _projector("Arial, Helvetica, sans-serif", 10, "#ffff66", "#000000", lines=3,
                              band_color="#000000cc"),
    "Warm paper": _projector("Georgia, serif", 6.5, "#2b2118", "#f3e9d6", shadow=False,
                             band_color="#f3e9d6e6"),
    "Overlay: bottom center": _overlay("bottom", "center", 80, align="center"),
    "Overlay: bottom left": _overlay("bottom", "left", 55),
    "Overlay: bottom right": _overlay("bottom", "right", 55),
    "Overlay: top left": _overlay("top", "left", 55),
    "Overlay: top center": _overlay("top", "center", 80, align="center"),
    "Overlay: middle right": _overlay("middle", "right", 40, size=4.5),
    "Confidence: white, large": _confidence(11, "#ffffff", "#000000"),
    "Confidence: yellow, huge": _confidence(15, "#ffe600", "#000000"),
    "Confidence: black on white": _confidence(12, "#000000", "#ffffff"),
}

STARTER_FLAG = "starter_themes"
STARTER_VERSION = "2"


def ensure_starter_themes(db):
    """Add the starter themes once per starter version. Returns how many were added.

    The first run on an empty database adds everything. A database that already has themes (from
    before themes had kinds) gets only the overlay and confidence starters, once, so nothing the
    church made is touched and a starter the church deleted never comes back.
    """
    if db.get_setting(STARTER_FLAG) == STARTER_VERSION:
        return 0
    existing = {row["name"] for row in db.list_themes()}
    only_new_kinds = bool(existing)
    added = 0
    for name, settings in STARTER_THEMES.items():
        if name in existing or (only_new_kinds and settings["kind"] == "projector"):
            continue
        db.add_theme(name, settings)
        added += 1
    db.set_setting(STARTER_FLAG, STARTER_VERSION)
    return added
