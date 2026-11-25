# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""ItemRef: a description of "what to show", independent of where it came from.

A saved service item is an ItemRef stored in the database. Something sent live
straight from the library is an ItemRef that was never saved. The slide builder
only ever sees ItemRefs, so both paths behave identically.
"""
import json
from dataclasses import dataclass, field

from .database import ITEM_TYPES
from .scripture_ref import book_name, format_reference, parse_reference

# service_items columns that describe the content (everything but id/service/position)
_REF_FIELDS = (
    "song_id", "arrangement_id", "translation_id", "book_id", "chapter",
    "verse_start", "verse_end", "media_id", "presentation_id", "theme_id",
    "overlay_theme_id", "title", "body",
)


@dataclass(frozen=True)
class ItemRef:
    item_type: str                       # song | scripture | media | presentation | announcement | blank
    item_id: int | None = None           # the saved service item's id; None if never saved
    song_id: int | None = None
    arrangement_id: int | None = None    # None = the song's default arrangement
    translation_id: int | None = None
    book_id: int | None = None
    chapter: int | None = None
    verse_start: int | None = None
    verse_end: int | None = None         # None = just verse_start
    media_id: int | None = None
    presentation_id: int | None = None
    theme_id: int | None = None          # None = the service's theme (the projector look)
    overlay_theme_id: int | None = None  # None = the service's overlay theme (lower-third look)
    title: str | None = None
    body: str | None = None
    # per-item adjustments such as {"lines_per_slide": 2}; excluded from hashing
    overrides: dict = field(default_factory=dict, hash=False)

    def __post_init__(self):
        if self.item_type not in ITEM_TYPES:
            raise ValueError(f"Unknown item type: {self.item_type!r}")

    # -- construction ------------------------------------------------------

    @classmethod
    def from_row(cls, row):
        """Build from a service_items row."""
        raw = row["overrides"]
        return cls(
            item_type=row["item_type"], item_id=row["id"],
            overrides=json.loads(raw) if raw else {},
            **{name: row[name] for name in _REF_FIELDS},
        )

    @classmethod
    def song(cls, song_id, arrangement_id=None, **kw):
        return cls("song", song_id=song_id, arrangement_id=arrangement_id, **kw)

    @classmethod
    def passage(cls, translation_id, book_id, chapter, verse_start, verse_end=None, **kw):
        return cls("scripture", translation_id=translation_id, book_id=book_id,
                   chapter=chapter, verse_start=verse_start, verse_end=verse_end, **kw)

    @classmethod
    def presentation(cls, presentation_id, **kw):
        return cls("presentation", presentation_id=presentation_id, **kw)

    @classmethod
    def media(cls, media_id, **kw):
        return cls("media", media_id=media_id, **kw)

    @classmethod
    def announcement(cls, title=None, body=None, **kw):
        return cls("announcement", title=title, body=body, **kw)

    @classmethod
    def blank(cls, **kw):
        return cls("blank", **kw)

    @classmethod
    def from_reference(cls, db, translation, text, **kw):
        """Turn typed text such as 'jn 3:16-18' or 'ps 23' into a scripture ItemRef.

        `translation` is an id or abbreviation. Raises ValueError (including
        ReferenceParseError) if the text can't be read or the passage doesn't
        exist in that translation, so a search box can report it immediately.
        """
        trans = db.get_translation(translation)
        if trans is None:
            raise ValueError(f"Unknown translation: {translation!r}")
        ref = parse_reference(text)
        if ref.verse_start is None:  # whole chapter: run to the translation's last verse
            start, end = 1, db.last_verse(trans["id"], ref.book_id, ref.chapter)
            verses = [] if end is None else db.get_verses(
                trans["id"], ref.book_id, ref.chapter, start, end)
        else:
            start, end = ref.verse_start, ref.verse_end
            verses = db.get_verses(trans["id"], ref.book_id, ref.chapter, start, end)
        if not verses:
            shown = format_reference(
                book_name(ref.book_id), ref.chapter, ref.verse_start, ref.verse_end)
            raise ValueError(f"{shown} was not found in {trans['abbreviation']}")
        return cls.passage(trans["id"], ref.book_id, ref.chapter, start, end, **kw)

    # -- saving ------------------------------------------------------------

    def to_item_fields(self):
        """The non-empty content fields, as keyword arguments for add_service_item."""
        values = {name: getattr(self, name) for name in _REF_FIELDS}
        return {k: v for k, v in values.items() if v is not None}


def add_to_service(db, service_id, ref, position=None):
    """Save an ItemRef as a service item; returns the new item id."""
    return db.add_service_item(
        service_id, ref.item_type, position,
        overrides=ref.overrides or None, **ref.to_item_fields())


def load_service(db, service_id):
    """A service's items, in order, as ItemRefs."""
    return [ItemRef.from_row(row) for row in db.get_service_items(service_id)]
