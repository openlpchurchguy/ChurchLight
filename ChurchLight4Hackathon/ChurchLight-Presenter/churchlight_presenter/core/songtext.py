# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Songs as plain text, for people to type, paste and fix.

    [Verse 1]
    Amazing grace how sweet the sound
    That saved a wretch like me

    [Chorus]
    ...

    Order: Verse 1, Chorus, Verse 1, Chorus

A line like `[Verse 1]` starts a part (the name is whatever you like; "Verse", "Chorus", "Bridge",
"Pre-Chorus", "Intro", "Ending" and "Tag" are recognized). An optional last line, `Order:`, lists the
parts in the order they are sung, with repeats; without it each part is sung once, top to bottom.

If the text has no `[headings]` at all (lyrics pasted straight from somewhere), each block separated
by a blank line becomes "Verse 1", "Verse 2" and so on, and can be renamed afterwards.

`parse_song_text` raises `SongTextError` with a sentence an operator can act on.
"""
import re
from dataclasses import dataclass, field

MAX_TEXT = 60000
MAX_SECTIONS = 80
MAX_LABEL = 60

_HEADING = re.compile(r"^\s*\[([^\[\]]{1,%d})\]\s*$" % MAX_LABEL)
_ORDER = re.compile(r"^\s*order\s*:(.*)$", re.IGNORECASE)

_TYPES = {
    "verse": "verse", "v": "verse", "chorus": "chorus", "refrain": "chorus", "c": "chorus",
    "bridge": "bridge", "b": "bridge", "pre-chorus": "pre_chorus", "prechorus": "pre_chorus",
    "pre chorus": "pre_chorus", "intro": "intro", "outro": "outro", "ending": "outro",
    "tag": "tag", "instrumental": "instrumental",
}


class SongTextError(ValueError):
    """The typed song cannot be understood; the message says what to change."""


@dataclass
class SongText:
    sections: list = field(default_factory=list)      # (label, section_type, lyrics)
    order: list = field(default_factory=list)         # labels in the order sung


def section_type_for(label):
    """'Verse 2' -> 'verse', 'Pre-Chorus' -> 'pre_chorus', anything unrecognized -> 'other'."""
    word = re.sub(r"[\s\d]+$", "", label.strip().lower())
    return _TYPES.get(word, "other")


def _clean(lines):
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(line.rstrip() for line in lines)


def parse_song_text(text):
    if not isinstance(text, str):
        raise SongTextError("The song words must be text")
    if len(text) > MAX_TEXT:
        raise SongTextError(f"That is too long for one song (more than {MAX_TEXT} characters)")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    order_text, body = None, []
    for line in lines:
        match = _ORDER.match(line)
        if match and not _HEADING.match(line):
            if order_text is not None:
                raise SongTextError("There is more than one 'Order:' line; keep just one")
            order_text = match.group(1)
        else:
            body.append(line)

    blocks, current = [], None            # [(label or None, [lines])]
    for line in body:
        heading = _HEADING.match(line)
        if heading:
            current = (heading.group(1).strip(), [])
            blocks.append(current)
        elif current is None:
            if line.strip():
                current = (None, [line])
                blocks.append(current)
        else:
            current[1].append(line)

    named = [b for b in blocks if b[0] is not None]
    if blocks and not named:                       # no headings at all: paragraphs become verses
        text_body = _clean(body)
        paragraphs = [p for p in re.split(r"\n\s*\n", text_body) if p.strip()]
        blocks = [(f"Verse {i}", p.split("\n")) for i, p in enumerate(paragraphs, 1)]
        if order_text is not None and order_text.strip():
            raise SongTextError("'Order:' needs headings such as [Verse 1] and [Chorus] to refer to")
        order_text = None
    elif any(b[0] is None for b in blocks):
        raise SongTextError("Put a heading such as [Verse 1] before the first line of words")

    if not blocks:
        raise SongTextError("The song has no words yet")
    if len(blocks) > MAX_SECTIONS:
        raise SongTextError(f"A song can have at most {MAX_SECTIONS} parts")

    result, seen = SongText(), {}
    for label, block_lines in blocks:
        lyrics = _clean(list(block_lines))
        key = label.lower()
        if key in seen:
            raise SongTextError(f"Two parts are both called [{label}]; give each its own name")
        if not lyrics:
            raise SongTextError(f"[{label}] has no words; add some or remove the heading")
        seen[key] = label
        result.sections.append((label, section_type_for(label), lyrics))

    if order_text is not None and order_text.strip():
        for name in (part.strip() for part in order_text.split(",")):
            if not name:
                continue
            if name.lower() not in seen:
                raise SongTextError(f"'Order:' names [{name}], but there is no part with that name")
            result.order.append(seen[name.lower()])
    if not result.order:
        result.order = [label for label, _, _ in result.sections]
    return result


def format_song_text(sections, order=None):
    """The editable text for a song. `sections` is (label, lyrics) pairs; `order` the labels sung."""
    sections = [(label, lyrics) for label, lyrics in sections]
    parts = [f"[{label}]\n{lyrics.strip()}" for label, lyrics in sections]
    text = "\n\n".join(parts)
    labels = [label for label, _ in sections]
    if order and list(order) != labels:
        text += "\n\nOrder: " + ", ".join(order)
    return text


def skipped_parts(overrides, total):
    """The positions (in the song's order) a service item leaves out, as a set.

    An item stores `parts_skip` (positions) and `parts_of` (how many parts the order had when the
    choice was made). If the song's order has since changed length the choice no longer lines up, so
    it is ignored and the whole song is sung. Leaving out every part is also ignored.
    """
    own = overrides or {}
    skip, was = own.get("parts_skip"), own.get("parts_of")
    if not isinstance(skip, list) or was != total:
        return set()
    positions = {p for p in skip if isinstance(p, int) and not isinstance(p, bool) and 0 <= p < total}
    return set() if len(positions) >= total else positions


def replace_order_line(text, order):
    """`text` with any 'Order:' line replaced by `order` (a comma-separated string; blank = none)."""
    lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
             if not (_ORDER.match(line) and not _HEADING.match(line))]
    body = "\n".join(lines).rstrip()
    order = (order or "").strip()
    return body + ("\n\nOrder: " + order if order else "")
