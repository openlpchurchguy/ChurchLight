# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Parse typed scripture references such as "jn 3:16-18", "1 cor 13" or "ps 23".

Pure Python with no database access. Looking a parsed reference up in a
translation (for example, finding the last verse of a whole chapter) happens in
items.py, where the database is available.

Forms understood (case, spacing and periods are forgiving):

    John 3:16   John 3.16   John 3 16        one verse
    John 3:16-18                              a verse range
    John 3                                    a whole chapter
    1 John 1:9   I John 1:9   first john 1:9  numbered books
    Jude 5   Jude 3-5                         single-chapter books (a bare
                                              number means a verse, not a chapter)

Not supported yet: verse lists ("3:16,18") and passages that cross chapters.
"""
import re
from dataclasses import dataclass

from .migrations import _BOOKS

# Canonical names in canonical order: BOOK_NAMES[book_id - 1].
BOOK_NAMES = tuple(_BOOKS)

# Books with a single chapter: "Jude 5" means verse 5.
SINGLE_CHAPTER_BOOKS = frozenset({31, 57, 63, 64, 65})  # Obadiah, Philemon, 2-3 John, Jude

# Largest chapter or verse number accepted (Psalms has 150 chapters and Psalm 119
# has 176 verses). Keeps absurd input from ever reaching the database.
MAX_NUMBER = 999


class ReferenceParseError(ValueError):
    """The text could not be understood as a scripture reference."""


@dataclass(frozen=True)
class PassageRef:
    book_id: int
    chapter: int
    verse_start: int | None = None  # None means the whole chapter
    verse_end: int | None = None    # None means just verse_start


# Extra spellings, by book id. Spaces are ignored when matching, and every
# canonical name is added automatically. Any prefix also works ("lev" finds
# Leviticus, "revel" Revelation); when a prefix fits several books the first
# one in Bible order wins ("ph" is Philippians, "jo" is Joshua), so type a
# little more to reach a later one ("joh" is John, "phil" Philippians, "phile" Philemon).
_ALIASES = {
    1: "gen ge gn", 2: "ex exod exo", 3: "lev le lv", 4: "num nu nm nb",
    5: "deut dt de", 6: "josh jos jsh", 7: "judg jdg jg jdgs", 8: "ru rth",
    9: "1sam 1sa 1sm 1s", 10: "2sam 2sa 2sm 2s",
    11: "1kgs 1ki 1k 1kin", 12: "2kgs 2ki 2k 2kin",
    13: "1chron 1chr 1ch", 14: "2chron 2chr 2ch",
    15: "ezr ez", 16: "neh ne", 17: "esth est es", 18: "jb",
    19: "ps psa psalm pslm psm pss", 20: "prov pro prv pr",
    21: "eccles eccl ecc ec qoh",
    22: "song sos ss songofsongs songofsol cant canticles",
    23: "isa is", 24: "jer je jr", 25: "lam la", 26: "ezek eze ezk",
    27: "dan da dn", 28: "hos ho", 29: "jl", 30: "am", 31: "obad ob",
    32: "jon jnh", 33: "mic mc", 34: "nah na", 35: "hab hb",
    36: "zeph zep zp", 37: "hag hg", 38: "zech zec zc", 39: "mal ml",
    40: "matt mt mat", 41: "mrk mar mk mr", 42: "luk lk",
    43: "jn jhn joh", 44: "ac act", 45: "rom ro rm",
    46: "1cor 1co 1c", 47: "2cor 2co 2c", 48: "gal ga", 49: "eph ephes",
    50: "phil php pp philip", 51: "col",
    52: "1thess 1thes 1th", 53: "2thess 2thes 2th",
    54: "1tim 1ti 1tm", 55: "2tim 2ti 2tm", 56: "tit ti",
    57: "philem phm pm", 58: "heb", 59: "jas jm jam",
    60: "1pet 1pe 1pt 1p", 61: "2pet 2pe 2pt 2p",
    62: "1jn 1jhn 1jo 1j", 63: "2jn 2jhn 2jo 2j", 64: "3jn 3jhn 3jo 3j",
    65: "jud jd", 66: "rev re rv revelations",
}


def _key(text):
    """Lowercase and keep only letters and digits: 'Song of Solomon' -> 'songofsolomon'."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _build_keys():
    keys = {}
    for book_id, name in enumerate(BOOK_NAMES, start=1):
        for k in [_key(name)] + _ALIASES.get(book_id, "").split():
            if keys.setdefault(k, book_id) != book_id:
                raise RuntimeError(f"Book alias {k!r} is used by two books")
    return keys


_KEYS = _build_keys()


def book_name(book_id):
    return BOOK_NAMES[book_id - 1]


def _normalize_ordinals(s):
    """'first john' / '1st john' / 'i john' -> '1 john' (likewise 2 and 3)."""
    s = re.sub(r"^(first|1st)\s*(?=[a-z])", "1 ", s)
    s = re.sub(r"^(second|2nd)\s*(?=[a-z])", "2 ", s)
    s = re.sub(r"^(third|3rd)\s*(?=[a-z])", "3 ", s)
    s = re.sub(r"^iii\s+(?=[a-z])", "3 ", s)
    s = re.sub(r"^ii\s+(?=[a-z])", "2 ", s)
    s = re.sub(r"^i\s+(?=[a-z])", "1 ", s)  # needs a space, so "isaiah" is safe
    return s


def _clean(text):
    s = text.strip().lower().replace("–", "-").replace("—", "-")
    s = re.sub(r"(?<=[a-z])\.", "", s)  # "gen." -> "gen"; "3.16" keeps its dot
    return _normalize_ordinals(s)


def _lookup_book(raw):
    key = _key(raw)
    if key in _KEYS:
        return _KEYS[key]
    ids = sorted({book_id for k, book_id in _KEYS.items() if k.startswith(key)})
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise ReferenceParseError(f"Unknown book: {raw.strip()!r}")
    return ids[0]   # several fit: the first in Bible order


def book_candidates(text):
    """Book ids whose name or alias starts with `text`, for type-ahead lists."""
    key = _key(_clean(text))
    if not key:
        return []
    return sorted({book_id for k, book_id in _KEYS.items() if k.startswith(key)})


def reference_hint(text):
    """What the typed text will be read as, for an autofill hint: 'Leviticus 3', 'Leviticus', or ''.

    Never raises. A book with no chapter yet gives just the book's name.
    """
    try:
        ref = parse_reference(text)
        return format_reference(book_name(ref.book_id), ref.chapter, ref.verse_start, ref.verse_end)
    except ReferenceParseError:
        pass
    if not isinstance(text, str):
        return ""
    s = _clean(text)
    m = _SPLIT_RE.match(s)
    book = m["book"] if m else s
    if not re.search(r"[a-z]", book) or not re.fullmatch(r"(?:[1-3]\s*)?[a-z][a-z\s]*", book.strip()):
        return ""
    try:
        return book_name(_lookup_book(book))
    except ReferenceParseError:
        return ""


# book name (with an optional leading 1-3), then the chapter/verse numbers
_SPLIT_RE = re.compile(r"^(?P<book>(?:[1-3]\s*)?[a-z][a-z\s]*?)\s*(?P<nums>\d[\d\s:.\-]*)$")


def _checked(book_id, chapter, verse_start, verse_end, original):
    if chapter < 1 or (verse_start is not None and verse_start < 1):
        raise ReferenceParseError(f"Chapter and verse numbers start at 1: {original.strip()!r}")
    if max(chapter, verse_start or 0, verse_end or 0) > MAX_NUMBER:
        raise ReferenceParseError(
            f"Chapter and verse numbers can't be above {MAX_NUMBER}: {original.strip()!r}")
    if verse_end is not None:
        if verse_end < verse_start:
            raise ReferenceParseError(
                f"The range ends before it starts: {original.strip()!r}")
        if verse_end == verse_start:
            verse_end = None
    return PassageRef(book_id, chapter, verse_start, verse_end)


def parse_reference(text):
    """Parse text into a PassageRef; raises ReferenceParseError (a ValueError)."""
    if not isinstance(text, str):
        raise ReferenceParseError("Type a book and chapter, like John 3:16")
    s = _clean(text)
    if not re.search(r"[a-z]", s):
        raise ReferenceParseError("Type a book and chapter, like John 3:16")
    m = _SPLIT_RE.match(s)
    if not m:
        if re.fullmatch(r"(?:[1-3]\s*)?[a-z][a-z\s]*", s):
            raise ReferenceParseError(f"Add a chapter after {text.strip()!r}, like John 3")
        raise ReferenceParseError(f"Couldn't read {text.strip()!r} as a reference")

    book_id = _lookup_book(m["book"])
    nums = re.sub(r"\s+", " ", m["nums"].strip())

    if book_id in SINGLE_CHAPTER_BOOKS:
        sm = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", nums)
        if sm:
            return _checked(book_id, 1, int(sm[1]), int(sm[2]) if sm[2] else None, text)

    if re.fullmatch(r"\d+", nums):
        return _checked(book_id, int(nums), None, None, text)
    if re.fullmatch(r"\d+\s*-\s*\d+", nums):
        raise ReferenceParseError("Chapter ranges aren't supported yet; enter one chapter at a time")
    if re.fullmatch(r"\d+\s*[:.]\s*\d+\s*-\s*\d+\s*[:.]\s*\d+", nums):
        raise ReferenceParseError(
            "Passages that cross chapters aren't supported yet; add each chapter separately")
    vm = re.fullmatch(r"(\d+)(?:\s*[:.]\s*|\s)(\d+)(?:\s*-\s*(\d+))?", nums)
    if vm:
        if book_id in SINGLE_CHAPTER_BOOKS and int(vm[1]) != 1:
            raise ReferenceParseError(f"{book_name(book_id)} has only one chapter")
        return _checked(book_id, int(vm[1]), int(vm[2]), int(vm[3]) if vm[3] else None, text)
    raise ReferenceParseError(f"Couldn't read {text.strip()!r} as a reference")


def format_reference(name, chapter, verse_start=None, verse_end=None):
    """'John 3', 'John 3:16' or 'John 3:16-17'."""
    if verse_start is None:
        return f"{name} {chapter}"
    if verse_end is None or verse_end == verse_start:
        return f"{name} {chapter}:{verse_start}"
    return f"{name} {chapter}:{verse_start}-{verse_end}"
