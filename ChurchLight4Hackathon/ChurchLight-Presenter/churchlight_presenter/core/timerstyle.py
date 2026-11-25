# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""How the countdown and the speaker alert look: fonts, colors, sizes and where they sit.

One small set of settings for the whole program, stored as JSON under the `countdown_style` setting and kept in
memory by the controller (the web server reads it from its own threads and must not touch the database).
Every field has a default, so a missing or damaged saved value can never leave the screen without a style.

    projector_mode                                                    "clock" (big clock) or "bar" (scrolling bar)
    font                                                              shared by everything below
    clock_x, clock_y, clock_w, clock_h                                the big clock's box, in percent of the screen
    size, label_size, color, ended_color, backdrop                    the big clock's text (sizes: percent of the box height)
    bar_x, bar_y, bar_w, bar_h                                        the scrolling bar's box, in percent of the screen
    bar_bg, bar_color, bar_size, bar_speed, bar_radius                the bar's look (size: percent of the bar height; speed: seconds for the words to cross the screen width)
    bar_every, bar_text, bar_zero_text                                how often it scrolls and what it says
    speaker_font, speaker_color, speaker_ended_color                  the small timer's own font and colors (a saved style from before
                                                                      these existed starts with the projector's, then they are separate)
    speaker_size, speaker_position, speaker_backdrop                  the small timer on the stage / confidence screens
    alert_bg, alert_color, alert_size, alert_position                 the alert bar on the stage / confidence screens

Colors are #rgb, #rrggbb or #rrggbbaa. Pure Python; no window.
"""
import math
import re

DEFAULTS = {
    "projector_mode": "clock",
    "font": "'Open Sans', Arial, sans-serif",
    "clock_x": 30, "clock_y": 34, "clock_w": 40, "clock_h": 32,
    "color": "#ffffff",
    "ended_color": "#ffe58a",
    "size": 62,
    "label_size": 18,
    "backdrop": "#00000000",
    "bar_x": 0, "bar_y": 88, "bar_w": 100, "bar_h": 9,
    "bar_bg": "#000000cc",
    "bar_color": "#ffffff",
    "bar_size": 60,
    "bar_speed": 14,
    "bar_radius": 0,
    "bar_every": 1,
    "bar_text": "{minutes} until service begins",
    "bar_zero_text": "Service is beginning",
    "speaker_font": "'Open Sans', Arial, sans-serif",
    "speaker_color": "#ffffff",
    "speaker_ended_color": "#ffe58a",
    "speaker_size": 7,
    "speaker_position": "top-right",
    "speaker_backdrop": "#000000",
    "alert_bg": "#ffe58a",
    "alert_color": "#000000",
    "alert_size": 7,
    "alert_position": "bottom",
}

MODES = ("clock", "bar")
BAR_EVERY = (1, 2, 5, 10)
RECTS = {"clock": ("clock_x", "clock_y", "clock_w", "clock_h"), "bar": ("bar_x", "bar_y", "bar_w", "bar_h")}
_MIN_W, _MIN_H = 5, 3          # the smallest box, in percent of the screen

SPEAKER_POSITIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
ALERT_POSITIONS = ("top", "bottom")

_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FONT = re.compile(r"^[A-Za-z0-9 ,'\"\-]{1,120}$")


def _color(value):
    if not isinstance(value, str) or not _COLOR.match(value):
        raise ValueError("must be a color like #ffffff")
    return value.lower()


def _font(value):
    if not isinstance(value, str) or not _FONT.match(value):
        raise ValueError("may only use letters, digits, spaces, commas, hyphens and quotes")
    return value.strip()


def _size(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return round(float(value), 1) if value != int(value) else int(value)
    return check


def _choice(options):
    def check(value):
        if value not in options:
            raise ValueError("must be one of: " + ", ".join(options))
        return value
    return check


def _text(limit, allow_empty):
    def check(value):
        if not isinstance(value, str):
            raise ValueError("must be text")
        value = value.strip()
        if not value and not allow_empty:
            raise ValueError("cannot be empty")
        if len(value) > limit:
            raise ValueError(f"is too long ({limit} characters at most)")
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("cannot contain control characters")
        return value
    return check


def _whole(options):
    def check(value):
        if isinstance(value, bool) or value not in options:
            raise ValueError("must be one of: " + ", ".join(str(o) for o in options))
        return int(value)
    return check


RULES = {
    "projector_mode": _choice(MODES), "font": _font,
    "clock_x": _size(0, 100), "clock_y": _size(0, 100), "clock_w": _size(_MIN_W, 100), "clock_h": _size(_MIN_H, 100),
    "color": _color, "ended_color": _color, "size": _size(10, 95), "label_size": _size(4, 40), "backdrop": _color,
    "bar_x": _size(0, 100), "bar_y": _size(0, 100), "bar_w": _size(_MIN_W, 100), "bar_h": _size(_MIN_H, 100),
    "bar_bg": _color, "bar_color": _color, "bar_size": _size(20, 95), "bar_speed": _size(4, 60), "bar_radius": _size(0, 50),
    "bar_every": _whole(BAR_EVERY), "bar_text": _text(120, True), "bar_zero_text": _text(120, True),
    "speaker_font": _font, "speaker_color": _color, "speaker_ended_color": _color,
    "speaker_size": _size(3, 20), "speaker_position": _choice(SPEAKER_POSITIONS), "speaker_backdrop": _color,
    "alert_bg": _color, "alert_color": _color, "alert_size": _size(3, 14), "alert_position": _choice(ALERT_POSITIONS),
}


def _rect_fits(style, keys):
    x, y, w, h = (style[k] for k in keys)
    return x + w <= 100.05 and y + h <= 100.05


def check(changes, current=None):
    """The validated version of `changes` (a dict of some fields). Raises ValueError, naming the field.
    With `current` (the saved style), a box that would stick out of the screen is refused too."""
    if not isinstance(changes, dict) or not changes:
        raise ValueError("Choose a setting to change")
    out = {}
    for key, value in changes.items():
        rule = RULES.get(key)
        if rule is None:
            raise ValueError(f"There is no countdown setting called {key!r}")
        try:
            out[key] = rule(value)
        except ValueError as err:
            raise ValueError(f"{key.replace('_', ' ')} {err}") from None
    merged = dict(clean(current), **out)
    for name, keys in RECTS.items():
        if any(k in out for k in keys) and not _rect_fits(merged, keys):
            raise ValueError(f"The {name} box must stay inside the screen")
    return out


SPEAKER_FOLLOWS = (("speaker_font", "font"), ("speaker_color", "color"), ("speaker_ended_color", "ended_color"))


def _valid(raw, key):
    try:
        RULES[key](raw[key])
        return {key: True}
    except (ValueError, KeyError):
        return {}


def clean(raw):
    """A complete style from whatever was saved: every field valid, bad or missing ones back to the default."""
    style = dict(DEFAULTS)
    if isinstance(raw, dict):
        raw = dict(raw)
        if "position" in raw:                # the first version's clock (a position and sizes in screen heights)
            for old in ("position", "size", "label_size"):
                raw.pop(old, None)
        for key, rule in RULES.items():
            if key in raw:
                try:
                    style[key] = rule(raw[key])
                except ValueError:
                    pass
        for mine, shared in SPEAKER_FOLLOWS:   # an older saved style: the stage timer starts out like the projector's
            if mine not in raw or mine not in _valid(raw, mine):
                style[mine] = style[shared]
        for keys in RECTS.values():           # a box that does not fit goes back to its standard place
            if not _rect_fits(style, keys):
                for k in keys:
                    style[k] = DEFAULTS[k]
    return style


def projector_style(style):
    """What the page needs to draw the countdown on the projector: the big clock and the scrolling bar."""
    s = clean(style)
    return {"mode": s["projector_mode"], "font": s["font"], "color": s["color"], "ended_color": s["ended_color"],
            "size": s["size"], "label_size": s["label_size"], "backdrop": s["backdrop"],
            "box": {"x": s["clock_x"], "y": s["clock_y"], "w": s["clock_w"], "h": s["clock_h"]},
            "bar": {"x": s["bar_x"], "y": s["bar_y"], "w": s["bar_w"], "h": s["bar_h"], "bg": s["bar_bg"],
                    "color": s["bar_color"], "size": s["bar_size"], "speed": s["bar_speed"], "radius": s["bar_radius"],
                    "every": s["bar_every"], "text": s["bar_text"], "zero_text": s["bar_zero_text"]}}


def speaker_style(style):
    """The same, for the small timer on the stage and confidence screens."""
    s = clean(style)
    return {"font": s["speaker_font"], "color": s["speaker_color"], "ended_color": s["speaker_ended_color"], "size": s["speaker_size"],
            "label_size": math.floor(max(2.0, s["speaker_size"] * 0.35) * 10 + 0.5) / 10, "position": s["speaker_position"],
            "backdrop": s["speaker_backdrop"]}


def alert_style(style):
    """What the page needs to draw the alert bar."""
    s = clean(style)
    return {"font": s["font"], "bg": s["alert_bg"], "color": s["alert_color"], "size": s["alert_size"],
            "position": s["alert_position"]}
