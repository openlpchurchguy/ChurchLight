# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The title and CCLI slide of a song: the title, the writers, the copyright notice and the CCLI numbers.

The text follows the form CCLI asks for: the song title, "Words and music by <writers>", "(c) <year> <owner>.
Used by Permission.", then the song number and the church's license number. Anything that is missing is left out.
"""

POSITIONS = ("before", "after", "off")           # the program-wide choice
SONG_CHOICES = ("before", "after", "never")      # a song's own choice (none stored: follow the program)
DEFAULT_POSITION = "off"                         # what an empty settings table means; the program sets "before" on first start
DEFAULT_WRITERS = "Words and music by"
DEFAULT_PERMISSION = "Used by Permission."


def notice_settings(db):
    """The program-wide choices, with their defaults filled in."""
    position = db.get_setting("notice_slide", DEFAULT_POSITION)
    writers = db.get_setting("notice_writers", None)
    permission = db.get_setting("notice_permission", None)
    return {
        "position": position if position in POSITIONS else DEFAULT_POSITION,
        "writers": DEFAULT_WRITERS if writers is None else writers,
        "permission": DEFAULT_PERMISSION if permission is None else permission,
        "song_number": db.get_setting("notice_song_number", "1") != "0",
        "church_ccli": db.get_setting("church_ccli", "") or "",
    }


def notice_position(settings, song_choice, overrides):
    """"before", "after" or None for one song in one service item."""
    if (overrides or {}).get("notice") == "off":
        return None
    choice = song_choice if song_choice in SONG_CHOICES else settings["position"]
    return None if choice in ("never", "off") else choice


def _copyright_line(text, permission):
    text = " ".join((text or "").split())
    if not text:
        return ""
    if not text.startswith(("©", "(c)", "(C)", "Copyright", "copyright")):
        text = "© " + text
    if not text.endswith((".", "!", "?")):
        text += "."
    return f"{text} {permission.strip()}".strip()


def notice_lines(settings, title, author, copyright_text, song_number):
    """The lines of the slide, the title first."""
    lines = [title]
    author = " ".join((author or "").split())
    if author:
        lines.append(f"{settings['writers'].strip()} {author}".strip())
    line = _copyright_line(copyright_text, settings["permission"])
    if line:
        lines.append(line)
    numbers = []
    if song_number and settings["song_number"]:
        numbers.append(f"CCLI Song #{song_number}")
    if settings["church_ccli"]:
        numbers.append(f"CCLI License #{settings['church_ccli']}")
    if numbers:
        lines.append(" · ".join(numbers))
    return lines
