# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""SQLite access layer for ChurchLight Presenter.

Schema lives in migrations.py. Errors propagate to the caller (no printing),
and every write is transactional.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

from .migrations import migrate

# Sentinel so callers can tell "leave unchanged" from "set to empty/NULL".
_UNSET = object()

ITEM_TYPES = ("song", "scripture", "media", "presentation", "announcement", "blank")

# Which references each service item type must carry (the schema's CHECK
# constraint enforces the same rules; these give friendlier errors first).
_REQUIRED_REFS = {
    "song": ("song_id",),
    "scripture": ("translation_id", "book_id", "chapter", "verse_start"),
    "media": ("media_id",),
    "presentation": ("presentation_id",),
    "announcement": (),
    "blank": (),
}

# Columns a caller may set on a service item besides position and overrides.
_ITEM_FIELDS = (
    "song_id", "arrangement_id", "translation_id", "book_id", "chapter",
    "verse_start", "verse_end", "media_id", "presentation_id", "theme_id",
    "overlay_theme_id", "title", "body",
)


def _iso_date(value):
    """Normalize a date or 'YYYY-MM-DD' string to 'YYYY-MM-DD'; ValueError if invalid."""
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(value).isoformat()


def item_overrides(row):
    """Decode a service item's `overrides` JSON into a dict ({} when empty)."""
    raw = row["overrides"]
    return json.loads(raw) if raw else {}


def _like(term):
    """LIKE pattern that treats user input literally (escapes % and _)."""
    escaped = term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _fts_query(text):
    """Turn free text into a safe FTS5 query: each word is a quoted prefix term."""
    terms = [t.replace('"', '""') for t in text.split() if t.strip()]
    return " ".join(f'"{t}"*' for t in terms)


def _page_entry(entry):
    """(image_path, notes, title, media_id) from an image path, a (path, notes) pair, a (path, notes, title) triple or a
    (path, notes, title, media_id) quadruple; media_id is the Media Library file the page shows, or None."""
    if isinstance(entry, str):
        return entry, None, None, None
    entry = tuple(entry) + (None,) * (4 - len(entry))
    return entry[0], entry[1], entry[2], entry[3]


class DatabaseManager:
    def __init__(self, db_path, shared=False):
        """`shared=True` lets several threads use this database, which is only safe if the
        caller takes turns (the operator controller's `lock` does exactly that)."""
        self.path = str(db_path)               # ":memory:" for a throwaway database
        self.conn = sqlite3.connect(db_path, timeout=30, check_same_thread=not shared)
        self.conn.row_factory = sqlite3.Row  # row["title"] instead of row[1]
        self.conn.execute("PRAGMA foreign_keys = ON")  # must precede migrate()
        self._in_tx = False
        migrate(self.conn)

    # -- lifecycle / transactions -----------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        """Commit on success, roll back on error. Nested calls join the outer one."""
        if self._in_tx:
            yield
            return
        self._in_tx = True
        try:
            with self.conn:
                yield
        finally:
            self._in_tx = False

    # -- generic helpers ---------------------------------------------------
    # Table/column names come only from fixed methods below, never from user
    # input. Values always go through `?` placeholders.

    def _insert(self, table, values):
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with self.transaction():
            cur = self.conn.execute(
                f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values())
            )
        return cur.lastrowid

    def _update(self, table, row_id, fields):
        fields = {c: v for c, v in fields.items() if v is not _UNSET}
        if not fields:
            return False
        assignments = ", ".join(f"{c} = ?" for c in fields)
        with self.transaction():
            cur = self.conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                (*fields.values(), row_id),
            )
        return cur.rowcount > 0

    def _delete(self, table, row_id):
        with self.transaction():
            cur = self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
        return cur.rowcount > 0

    def _get(self, table, row_id):
        return self.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()

    def _search(self, table, **filters):
        clauses, params = [], []
        for col, term in filters.items():
            if term:
                clauses.append(f"{col} LIKE ? ESCAPE '\\'")
                params.append(_like(term))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.conn.execute(
            f"SELECT * FROM {table}{where} ORDER BY id", params
        ).fetchall()

    # -- songs -------------------------------------------------------------

    def insert_song(self, title, author=None, copyright=None, ccli_number=None,
                    default_key=None, notes=None, is_draft=0):
        return self._insert("songs", {
            "title": title, "author": author, "copyright": copyright,
            "ccli_number": ccli_number, "default_key": default_key, "notes": notes,
            "is_draft": 1 if is_draft else 0,
        })

    def update_song(self, song_id, title=_UNSET, author=_UNSET, copyright=_UNSET,
                    ccli_number=_UNSET, default_key=_UNSET, notes=_UNSET, is_draft=_UNSET, theme_id=_UNSET,
                    notice_slide=_UNSET, operator_name=_UNSET):
        return self._update("songs", song_id, {
            "title": title, "author": author, "copyright": copyright, "notice_slide": notice_slide,
            "operator_name": operator_name,
            "ccli_number": ccli_number, "default_key": default_key, "notes": notes, "theme_id": theme_id,
            "is_draft": is_draft if is_draft is _UNSET else (1 if is_draft else 0),
        })

    def get_song(self, song_id):
        return self._get("songs", song_id)

    def delete_song(self, song_id):
        """Cascades to sections and arrangements. Fails if a service uses the song."""
        return self._delete("songs", song_id)

    def search_song(self, title=None, author=None):
        """Simple substring match on title/author."""
        return self._search("songs", title=title, author=author)

    def search_songs(self, text):
        """Full-text search across title, author and lyrics, best match first."""
        query = _fts_query(text)
        if not query:
            return []
        return self.conn.execute(
            """SELECT s.* FROM song_fts JOIN songs s ON s.id = song_fts.rowid
               WHERE song_fts MATCH ? ORDER BY rank""",
            (query,),
        ).fetchall()

    def recent_library_items(self, limit=6):
        """The songs and decks used in services most recently: [(item_type, id)], newest first."""
        rows = self.conn.execute(
            """SELECT si.item_type AS kind, COALESCE(si.song_id, si.presentation_id) AS item_id,
                      MAX(sv.service_date || ' ' || printf('%09d', si.id)) AS last_used
                 FROM service_items si JOIN services sv ON sv.id = si.service_id
                WHERE si.item_type IN ('song', 'presentation')
                GROUP BY si.item_type, COALESCE(si.song_id, si.presentation_id)
                ORDER BY last_used DESC LIMIT ?""", (limit,)).fetchall()
        return [(r["kind"], r["item_id"]) for r in rows]

    # -- sections ----------------------------------------------------------

    def add_section(self, song_id, label, lyrics="", section_type="verse", position=None):
        if position is None:
            position = self.conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM song_sections WHERE song_id = ?",
                (song_id,),
            ).fetchone()[0]
        return self._insert("song_sections", {
            "song_id": song_id, "label": label, "section_type": section_type,
            "position": position, "lyrics": lyrics,
        })

    def update_section(self, section_id, label=_UNSET, section_type=_UNSET,
                       lyrics=_UNSET, position=_UNSET):
        return self._update("song_sections", section_id, {
            "label": label, "section_type": section_type,
            "lyrics": lyrics, "position": position,
        })

    def get_sections(self, song_id):
        return self.conn.execute(
            "SELECT * FROM song_sections WHERE song_id = ? ORDER BY position", (song_id,)
        ).fetchall()

    def delete_section(self, section_id):
        return self._delete("song_sections", section_id)

    # -- arrangements ------------------------------------------------------

    def create_arrangement(self, song_id, name, section_ids, is_default=False):
        """`section_ids` is the play order; ids may repeat (e.g. the chorus)."""
        with self.transaction():
            valid = {r["id"] for r in self.conn.execute(
                "SELECT id FROM song_sections WHERE song_id = ?", (song_id,))}
            bad = [s for s in section_ids if s not in valid]
            if bad:
                raise ValueError(f"Sections {bad} do not belong to song {song_id}")
            if is_default:
                self.conn.execute(
                    "UPDATE arrangements SET is_default = 0 WHERE song_id = ?", (song_id,))
            arr_id = self._insert("arrangements", {
                "song_id": song_id, "name": name, "is_default": int(is_default)})
            self.conn.executemany(
                "INSERT INTO arrangement_items (arrangement_id, section_id, position) "
                "VALUES (?, ?, ?)",
                [(arr_id, sid, pos) for pos, sid in enumerate(section_ids)],
            )
        return arr_id

    def get_default_arrangement(self, song_id):
        return self.conn.execute(
            "SELECT * FROM arrangements WHERE song_id = ? AND is_default = 1", (song_id,)
        ).fetchone()

    def get_arrangement_sections(self, arrangement_id):
        """Sections in play order, repeats included. This is what the slide builder consumes."""
        return self.conn.execute(
            """SELECT ai.position AS play_order, sec.id AS section_id, sec.label,
                      sec.section_type, sec.lyrics
               FROM arrangement_items ai
               JOIN song_sections sec ON sec.id = ai.section_id
               WHERE ai.arrangement_id = ? ORDER BY ai.position""",
            (arrangement_id,),
        ).fetchall()

    def create_song_with_sections(self, title, sections, order=None,
                                  arrangement_name="Default", **song_fields):
        """Create a song, its sections and a default arrangement atomically.

        sections: iterable of (label, section_type, lyrics)
        order:    list of labels in play order (defaults to section order)
        Handy for importers.
        """
        sections = list(sections)
        with self.transaction():
            song_id = self.insert_song(title, **song_fields)
            ids = {label: self.add_section(song_id, label, lyrics, stype)
                   for label, stype, lyrics in sections}
            labels = order if order is not None else [s[0] for s in sections]
            missing = [lab for lab in labels if lab not in ids]
            if missing:
                raise ValueError(f"Order references unknown sections: {missing}")
            self.create_arrangement(
                song_id, arrangement_name, [ids[lab] for lab in labels], is_default=True)
        return song_id

    def replace_song(self, song_id, title, sections, order=None, author=_UNSET, is_draft=_UNSET,
                     ccli_number=_UNSET, theme_id=_UNSET, copyright=_UNSET, notice_slide=_UNSET,
                     operator_name=_UNSET):
        """Change a song's title, author and words in one step (used by the song editor).

        `sections` is (label, section_type, lyrics) in display order and `order` the labels in the
        order sung. Its arrangements are replaced by the one order given, and service items that
        named an arrangement fall back to the default one.
        """
        with self.transaction():
            if self.get_song(song_id) is None:
                raise ValueError(f"There is no song number {song_id}")
            sections = list(sections)
            labels = order if order is not None else [s[0] for s in sections]
            if not sections:
                raise ValueError("A song needs at least one part")
            self.update_song(song_id, title=title, author=author, is_draft=is_draft,
                             ccli_number=ccli_number, theme_id=theme_id, copyright=copyright,
                             notice_slide=notice_slide, operator_name=operator_name)
            self.conn.execute("DELETE FROM arrangements WHERE song_id = ?", (song_id,))
            self.conn.execute("DELETE FROM song_sections WHERE song_id = ?", (song_id,))
            ids = {label: self.add_section(song_id, label, lyrics, stype)
                   for label, stype, lyrics in sections}
            missing = [lab for lab in labels if lab not in ids]
            if missing:
                raise ValueError(f"Order references unknown sections: {missing}")
            self.create_arrangement(song_id, "Default", [ids[lab] for lab in labels], is_default=True)

    def song_summaries(self):
        """Every song with how many services use it, in the order the operator sees them (operator name, else title)."""
        return self.conn.execute(
            """SELECT s.id, s.title, s.operator_name, s.author, s.is_draft, s.ccli_number,
                      (SELECT COUNT(DISTINCT service_id) FROM service_items si
                        WHERE si.song_id = s.id) AS uses
               FROM songs s ORDER BY COALESCE(s.operator_name, s.title) COLLATE NOCASE""").fetchall()

    # -- scripture ---------------------------------------------------------

    def add_translation(self, abbreviation, name, language="en", license_notes=None):
        return self._insert("translations", {
            "abbreviation": abbreviation, "name": name,
            "language": language, "license_notes": license_notes})

    def translation_summaries(self):
        return self.conn.execute(
            """SELECT t.id, t.abbreviation, t.name, t.language,
                      (SELECT COUNT(*) FROM verses v WHERE v.translation_id = t.id) AS verse_count,
                      (SELECT COUNT(DISTINCT service_id) FROM service_items si
                        WHERE si.translation_id = t.id) AS uses
               FROM translations t ORDER BY t.abbreviation""").fetchall()

    def delete_translation(self, translation_id):
        """Removes the translation and its verses. Fails if a service uses it."""
        return self._delete("translations", translation_id)

    def _book_id(self, book):
        if isinstance(book, int):
            return book
        row = self.conn.execute(
            "SELECT id FROM bible_books WHERE name = ? COLLATE NOCASE", (book,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown book: {book!r}")
        return row["id"]

    def add_verses(self, translation_id, verses):
        """Bulk import. verses: iterable of (book, chapter, verse, text); book is a name or 1-66."""
        rows = [(translation_id, self._book_id(b), c, v, t) for b, c, v, t in verses]
        with self.transaction():
            self.conn.executemany(
                "INSERT INTO verses (translation_id, book_id, chapter, verse, text) "
                "VALUES (?, ?, ?, ?, ?)", rows)

    def get_passage(self, translation, book, chapter, verse_start, verse_end=None):
        """`translation` is the abbreviation, e.g. 'KJV'."""
        return self.conn.execute(
            """SELECT b.name AS book, v.chapter, v.verse, v.text
               FROM verses v
               JOIN translations t ON t.id = v.translation_id
               JOIN bible_books b ON b.id = v.book_id
               WHERE t.abbreviation = ? AND v.book_id = ? AND v.chapter = ?
                     AND v.verse BETWEEN ? AND ?
               ORDER BY v.verse""",
            (translation, self._book_id(book), chapter,
             verse_start, verse_end if verse_end is not None else verse_start),
        ).fetchall()

    def get_book(self, book):
        """Book row (id, name, testament) from an id or a canonical name; None if unknown."""
        try:
            book_id = self._book_id(book)
        except ValueError:
            return None
        return self._get("bible_books", book_id)

    def get_translation(self, translation):
        """Translation row from an id or an abbreviation like 'KJV'; None if unknown."""
        if isinstance(translation, int):
            return self._get("translations", translation)
        return self.conn.execute(
            "SELECT * FROM translations WHERE abbreviation = ? COLLATE NOCASE",
            (translation,)).fetchone()

    def get_verses(self, translation_id, book_id, chapter, verse_start, verse_end=None):
        """(verse, text) rows in order. Verses a translation omits are simply absent."""
        return self.conn.execute(
            """SELECT verse, text FROM verses
               WHERE translation_id = ? AND book_id = ? AND chapter = ?
                     AND verse BETWEEN ? AND ?
               ORDER BY verse""",
            (translation_id, book_id, chapter, verse_start,
             verse_end if verse_end is not None else verse_start),
        ).fetchall()

    def last_verse(self, translation_id, book_id, chapter):
        """Highest verse number stored for a chapter, or None if there are none."""
        return self.conn.execute(
            "SELECT MAX(verse) FROM verses "
            "WHERE translation_id = ? AND book_id = ? AND chapter = ?",
            (translation_id, book_id, chapter)).fetchone()[0]

    def search_scripture(self, text, translation=None):
        sql = ("SELECT b.name AS book, v.chapter, v.verse, v.text, t.abbreviation "
               "FROM verses v JOIN translations t ON t.id = v.translation_id "
               "JOIN bible_books b ON b.id = v.book_id "
               "WHERE v.text LIKE ? ESCAPE '\\'")
        params = [_like(text)]
        if translation:
            sql += " AND t.abbreviation = ?"
            params.append(translation)
        return self.conn.execute(sql + " ORDER BY v.book_id, v.chapter, v.verse", params).fetchall()

    # -- media assets ------------------------------------------------------

    def insert_media_asset(self, name, path, media_type="image", file_hash=None, duration_ms=None):
        return self._insert("media_assets", {
            "name": name, "path": path, "media_type": media_type, "file_hash": file_hash,
            "duration_ms": duration_ms})

    def media_summaries(self):
        """The Media Library: every picture, video and sound file with how many services and slideshows use each."""
        return self.conn.execute(
            """SELECT m.id, m.name, m.path, m.media_type, m.duration_ms,
                      (SELECT COUNT(DISTINCT service_id) FROM service_items si WHERE si.media_id = m.id) AS uses,
                      (SELECT COUNT(DISTINCT presentation_id) FROM presentation_pages pp
                        WHERE pp.media_id = m.id OR pp.sound_id = m.id) AS decks
                 FROM media_assets m
                ORDER BY m.name COLLATE NOCASE""").fetchall()

    def update_media_asset(self, media_id, name=_UNSET, path=_UNSET,
                           media_type=_UNSET, file_hash=_UNSET):
        return self._update("media_assets", media_id, {
            "name": name, "path": path, "media_type": media_type, "file_hash": file_hash})

    def get_media_asset(self, media_id):
        return self._get("media_assets", media_id)

    def search_media_asset(self, name=None, path=None):
        return self._search("media_assets", name=name, path=path)

    def delete_media_asset(self, media_id):
        return self._delete("media_assets", media_id)

    # -- presentations -----------------------------------------------------
    # A presentation is a deck converted once, at import, into page images.
    # The runtime only ever reads these rows; it never opens the original file.

    def add_presentation(self, name, pages, source_type="pdf", source_path=None,
                         file_hash=None):
        """Create a presentation and its pages atomically.

        pages: ordered iterable; each entry is an image path, or an
               (image_path, notes) pair, or an (image_path, notes, title) triple. Page numbers are assigned 1..N.
        """
        normalized = []
        for page in pages:
            image_path, notes, title, _media = _page_entry(page)
            if not image_path:
                raise ValueError("Every page needs an image path")
            normalized.append((image_path, notes, title))
        if not normalized:
            raise ValueError("A presentation needs at least one page")

        with self.transaction():
            pres_id = self._insert("presentations", {
                "name": name, "source_type": source_type,
                "source_path": source_path, "file_hash": file_hash})
            self.conn.executemany(
                "INSERT INTO presentation_pages "
                "(presentation_id, page_number, image_path, notes, title) VALUES (?, ?, ?, ?, ?)",
                [(pres_id, n, path, notes, title)
                 for n, (path, notes, title) in enumerate(normalized, start=1)],
            )
        return pres_id

    _PRESENTATION_SELECT = (
        "SELECT p.*, (SELECT COUNT(*) FROM presentation_pages pp "
        "WHERE pp.presentation_id = p.id) AS page_count FROM presentations p"
    )

    def get_presentation(self, presentation_id):
        """The presentation row plus a derived `page_count`."""
        return self.conn.execute(
            f"{self._PRESENTATION_SELECT} WHERE p.id = ?", (presentation_id,)
        ).fetchone()

    def search_presentation(self, name=None):
        sql, params = self._PRESENTATION_SELECT, []
        if name:
            sql += " WHERE p.name LIKE ? ESCAPE '\\'"
            params.append(_like(name))
        return self.conn.execute(sql + " ORDER BY p.name COLLATE NOCASE", params).fetchall()

    def find_presentation_by_hash(self, file_hash):
        """Lets an importer notice that a deck was already imported."""
        return self.conn.execute(
            f"{self._PRESENTATION_SELECT} WHERE p.file_hash = ? LIMIT 1", (file_hash,)
        ).fetchone()

    def get_presentation_pages(self, presentation_id):
        return self.conn.execute(
            "SELECT * FROM presentation_pages WHERE presentation_id = ? "
            "ORDER BY page_number", (presentation_id,)
        ).fetchall()

    def update_presentation_page(self, page_id, notes=_UNSET, image_path=_UNSET):
        return self._update("presentation_pages", page_id,
                            {"notes": notes, "image_path": image_path})

    # -- editing a deck's pages (the deck editor) --------------------------------

    def _page_ids(self, presentation_id):
        return [r["id"] for r in self.get_presentation_pages(presentation_id)]

    def _renumber_pages(self, presentation_id, ordered_ids):
        """Give the pages numbers 1..N in this order. Two steps, so the unique number never clashes."""
        with self.transaction():
            self.conn.execute(
                "UPDATE presentation_pages SET page_number = page_number + 1000000 "
                "WHERE presentation_id = ?", (presentation_id,))
            self.conn.executemany(
                "UPDATE presentation_pages SET page_number = ? WHERE id = ? AND presentation_id = ?",
                [(n, page_id, presentation_id) for n, page_id in enumerate(ordered_ids, start=1)])

    def _check_pages(self, presentation_id, page_ids):
        """The ids as a set, after checking each is a page of this deck."""
        have = set(self._page_ids(presentation_id))
        wanted = set(page_ids)
        if not wanted or not wanted <= have:
            raise ValueError("Those pages are not in this slideshow")
        return wanted

    def reorder_presentation_pages(self, presentation_id, ordered_ids):
        """Put the pages in this order. `ordered_ids` must list every page of the deck exactly once."""
        ordered_ids = list(ordered_ids)
        if sorted(ordered_ids) != sorted(self._page_ids(presentation_id)):
            raise ValueError("The new order must list every page of the slideshow once")
        self._renumber_pages(presentation_id, ordered_ids)

    def delete_presentation_pages(self, presentation_id, page_ids):
        """Remove pages and close the gaps. Returns the image paths the removed pages used.

        A deck keeps at least one page that is not skipped."""
        wanted = self._check_pages(presentation_id, page_ids)
        pages = self.get_presentation_pages(presentation_id)
        left = [p for p in pages if p["id"] not in wanted]
        if not left:
            raise ValueError("A slideshow needs at least one page; delete the slideshow instead")
        if all(p["skipped"] for p in left):
            raise ValueError("A slideshow needs at least one page that is not skipped")
        paths = [p["image_path"] for p in pages if p["id"] in wanted]
        with self.transaction():
            self.conn.executemany("DELETE FROM presentation_pages WHERE id = ?",
                                  [(i,) for i in wanted])
            self._renumber_pages(presentation_id, [p["id"] for p in left])
        return paths

    def duplicate_presentation_pages(self, presentation_id, page_ids):
        """Copy pages, each copy going right after its original. Returns the new page ids.

        A copy shares the original's picture file and notes."""
        wanted = self._check_pages(presentation_id, page_ids)
        order, new_ids = [], []
        with self.transaction():
            for page in self.get_presentation_pages(presentation_id):
                order.append(page["id"])
                if page["id"] in wanted:
                    top = self.conn.execute(
                        "SELECT COALESCE(MAX(page_number), 0) + 1 FROM presentation_pages "
                        "WHERE presentation_id = ?", (presentation_id,)).fetchone()[0]
                    cur = self.conn.execute(
                        "INSERT INTO presentation_pages (presentation_id, page_number, image_path, notes, "
                        "skipped, auto_seconds, transition, transition_ms, title, media_id, muted, sound_id, sound_loop) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (presentation_id, top, page["image_path"], page["notes"], page["skipped"],
                         page["auto_seconds"], page["transition"], page["transition_ms"], page["title"],
                         page["media_id"], page["muted"], page["sound_id"], page["sound_loop"]))
                    new_ids.append(cur.lastrowid)
                    order.append(cur.lastrowid)
            self._renumber_pages(presentation_id, order)
        return new_ids

    def set_presentation_pages_skipped(self, presentation_id, page_ids, skipped):
        """Mark pages skipped (passed over when the deck runs) or put them back."""
        wanted = self._check_pages(presentation_id, page_ids)
        if skipped:
            pages = self.get_presentation_pages(presentation_id)
            if all(p["skipped"] or p["id"] in wanted for p in pages):
                raise ValueError("A slideshow needs at least one page that is not skipped")
        with self.transaction():
            self.conn.executemany(
                "UPDATE presentation_pages SET skipped = ? WHERE id = ?",
                [(1 if skipped else 0, i) for i in wanted])

    def set_presentation_pages_timing(self, presentation_id, page_ids, auto_seconds=_UNSET,
                                      transition=_UNSET, transition_ms=_UNSET):
        """Give pages their own delay and/or transition. None means "follow the deck" again.

        Leave an argument out to keep that setting as it is. A transition and its length go together:
        a kind needs a length, and no kind means no length."""
        wanted = self._check_pages(presentation_id, page_ids)
        if transition is not _UNSET or transition_ms is not _UNSET:
            if transition is _UNSET or transition_ms is _UNSET or (transition is None) != (transition_ms is None):
                raise ValueError("A page's transition and its length are set together")
        fields = {k: v for k, v in (("auto_seconds", auto_seconds), ("transition", transition),
                                    ("transition_ms", transition_ms)) if v is not _UNSET}
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction():
            self.conn.executemany(
                f"UPDATE presentation_pages SET {assignments} WHERE id = ?",
                [(*fields.values(), i) for i in wanted])

    def set_presentation_pages_sound(self, presentation_id, page_ids, muted=_UNSET, sound_id=_UNSET, sound_loop=_UNSET):
        """Mute a video page's own sound, and/or give pages a sound file (None takes it away) that plays while they are live."""
        wanted = self._check_pages(presentation_id, page_ids)
        if sound_id is not _UNSET and sound_id is not None:
            asset = self.get_media_asset(sound_id)
            if asset is None or asset["media_type"] != "audio":
                raise ValueError("Choose a sound file from the Media Library")
        fields = {}
        if muted is not _UNSET:
            fields["muted"] = int(bool(muted))
        if sound_id is not _UNSET:
            fields["sound_id"] = sound_id
            if sound_id is None:
                sound_loop = False                  # no sound: nothing to loop
        if sound_loop is not _UNSET:
            fields["sound_loop"] = int(bool(sound_loop))
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        with self.transaction():
            self.conn.executemany(f"UPDATE presentation_pages SET {assignments} WHERE id = ?",
                                  [(*fields.values(), i) for i in wanted])

    def set_presentation_page_notes(self, presentation_id, page_id, notes):
        self._check_pages(presentation_id, [page_id])
        notes = (notes or "").strip() or None
        self._update("presentation_pages", page_id, {"notes": notes})

    def set_presentation_page_title(self, presentation_id, page_id, title):
        """A page's own name; empty text goes back to the plain "Slide N"."""
        self._check_pages(presentation_id, [page_id])
        title = " ".join((title or "").split()) or None
        if title is not None and len(title) > 60:
            raise ValueError("A slide title can be up to 60 characters")
        self._update("presentation_pages", page_id, {"title": title})

    def add_presentation_pages(self, presentation_id, entries, after_page_id=None):
        """Insert pages: `entries` is [(image_path, notes)] or [(image_path, notes, title)]. They go after
        `after_page_id`, or at the end when that is None. Returns the new page ids in order."""
        entries = [_page_entry(entry) for entry in entries]
        if not entries or any(not entry[0] for entry in entries):
            raise ValueError("Every page needs a picture")
        order = self._page_ids(presentation_id)
        if after_page_id is not None and after_page_id not in order:
            raise ValueError("That page is not in this slideshow")
        at = len(order) if after_page_id is None else order.index(after_page_id) + 1
        new_ids = []
        with self.transaction():
            top = self.conn.execute(
                "SELECT COALESCE(MAX(page_number), 0) FROM presentation_pages WHERE presentation_id = ?",
                (presentation_id,)).fetchone()[0]
            for offset, (path, notes, title, media_id) in enumerate(entries, start=1):
                cur = self.conn.execute(
                    "INSERT INTO presentation_pages (presentation_id, page_number, image_path, notes, title, media_id) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (presentation_id, top + offset, path, notes, title, media_id))
                new_ids.append(cur.lastrowid)
            self._renumber_pages(presentation_id, order[:at] + new_ids + order[at:])
        return new_ids

    def replace_presentation_page_image(self, presentation_id, page_id, image_path):
        """Point a page at a new picture, keeping its place, notes and skip flag. Returns the old path."""
        if not image_path:
            raise ValueError("A page needs a picture")
        self._check_pages(presentation_id, [page_id])
        old = self._get("presentation_pages", page_id)["image_path"]
        self._update("presentation_pages", page_id, {"image_path": image_path, "media_id": None, "muted": 0})
        return old

    def presentation_service_titles(self, presentation_id):
        """The services that use this deck: [(service_id, title, date)], newest first."""
        return self.conn.execute(
            "SELECT DISTINCT sv.id, sv.title, sv.service_date FROM service_items si "
            "JOIN services sv ON sv.id = si.service_id WHERE si.presentation_id = ? "
            "ORDER BY sv.service_date DESC, sv.id DESC", (presentation_id,)).fetchall()

    def set_presentation_transition(self, presentation_id, transition, transition_ms):
        """How the deck's slides change on the projector (validated by core/transitions.py)."""
        return self._update("presentations", presentation_id,
                            {"transition": transition, "transition_ms": transition_ms})

    def set_presentation_image_fit(self, presentation_id, image_fit):
        """How the deck's pictures fill the screen: "fit", "fill" or "stretch"."""
        return self._update("presentations", presentation_id, {"image_fit": image_fit})

    def set_presentation_autoplay(self, presentation_id, auto_seconds, end_action):
        """Timed advance for the deck (validated by core/autoplay.py)."""
        return self._update("presentations", presentation_id,
                            {"auto_seconds": auto_seconds, "end_action": end_action})

    def rename_presentation(self, presentation_id, name):
        return self._update("presentations", presentation_id, {"name": name})

    def presentation_summaries(self):
        return self.conn.execute(
            """SELECT p.id, p.name, p.transition, p.transition_ms, p.auto_seconds, p.end_action, p.image_fit,
                      (SELECT COUNT(*) FROM presentation_pages pp
                                      WHERE pp.presentation_id = p.id) AS page_count,
                      (SELECT COUNT(DISTINCT service_id) FROM service_items si
                        WHERE si.presentation_id = p.id) AS uses,
                      EXISTS (SELECT 1 FROM presentation_pages pv JOIN media_assets mv ON mv.id = pv.media_id
                               WHERE pv.presentation_id = p.id AND mv.media_type = 'video') AS has_video
               FROM presentations p ORDER BY p.name COLLATE NOCASE""").fetchall()

    def delete_presentation(self, presentation_id):
        """Cascades to pages. Fails if a service item still uses the presentation."""
        return self._delete("presentations", presentation_id)

    # -- themes ------------------------------------------------------------
    # `settings` is a plain dict stored as JSON (font, size, color, margins,
    # alignment...). Its keys are decided later, when the renderer exists.

    def add_theme(self, name, settings=None, background_media_id=None):
        return self._insert("themes", {
            "name": name, "settings": json.dumps(settings or {}),
            "background_media_id": background_media_id})

    def update_theme(self, theme_id, name=_UNSET, settings=_UNSET,
                     background_media_id=_UNSET):
        if settings is not _UNSET:
            settings = json.dumps(settings or {})
        return self._update("themes", theme_id, {
            "name": name, "settings": settings,
            "background_media_id": background_media_id})

    def get_theme(self, theme_id):
        return self._get("themes", theme_id)

    def get_theme_settings(self, theme_id):
        """The theme's settings as a dict, or None if the theme doesn't exist."""
        row = self._get("themes", theme_id)
        return None if row is None else json.loads(row["settings"])

    def list_themes(self):
        return self.conn.execute(
            "SELECT * FROM themes ORDER BY name COLLATE NOCASE").fetchall()

    def delete_theme(self, theme_id):
        """Services and items using the theme fall back to no theme (NULL)."""
        return self._delete("themes", theme_id)

    def theme_usage(self, theme_id):
        """How many services and service items use the theme."""
        services = self.conn.execute(
            "SELECT COUNT(*) FROM services WHERE theme_id = ? OR overlay_theme_id = ?",
            (theme_id, theme_id)).fetchone()[0]
        items = self.conn.execute(
            "SELECT COUNT(*) FROM service_items WHERE theme_id = ? OR overlay_theme_id = ?",
            (theme_id, theme_id)).fetchone()[0]
        songs = self.conn.execute("SELECT COUNT(*) FROM songs WHERE theme_id = ?", (theme_id,)).fetchone()[0]
        return {"services": services, "items": items, "songs": songs}

    # -- program-wide settings -----------------------------------------------
    def get_setting(self, key, default=None):
        row = self.conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return default if row is None else row[0]

    def set_setting(self, key, value):
        """Store a text value; None removes the setting."""
        if value is None:
            self.conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
        else:
            self.conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
        self.conn.commit()

    # -- services ----------------------------------------------------------

    def create_service(self, title, service_date, theme_id=None, notes=None, overlay_theme_id=None):
        return self._insert("services", {
            "title": title, "service_date": _iso_date(service_date),
            "theme_id": theme_id, "notes": notes, "overlay_theme_id": overlay_theme_id})

    def get_service(self, service_id):
        return self._get("services", service_id)

    def list_services(self, date_from=None, date_to=None):
        """Newest first. Bounds are inclusive and may be dates or 'YYYY-MM-DD'."""
        clauses, params = [], []
        if date_from is not None:
            clauses.append("service_date >= ?")
            params.append(_iso_date(date_from))
        if date_to is not None:
            clauses.append("service_date <= ?")
            params.append(_iso_date(date_to))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.conn.execute(
            f"SELECT * FROM services{where} ORDER BY service_date DESC, id DESC", params
        ).fetchall()

    def update_service(self, service_id, title=_UNSET, service_date=_UNSET,
                       theme_id=_UNSET, notes=_UNSET, overlay_theme_id=_UNSET, verse_split=_UNSET):
        if service_date is not _UNSET:
            service_date = _iso_date(service_date)
        return self._update("services", service_id, {
            "title": title, "service_date": service_date,
            "theme_id": theme_id, "notes": notes, "overlay_theme_id": overlay_theme_id,
            "verse_split": verse_split})

    def delete_service(self, service_id):
        """Also deletes the service's items (cascade); the content they point to stays. Its section of the
        song report stays too (cut loose from the service, so a reused id cannot overwrite it)."""
        with self.transaction():
            self.conn.execute("UPDATE song_report SET service_id = NULL WHERE service_id = ?", (service_id,))
            return self._delete("services", service_id)

    # -- the song report ----------------------------------------------------------
    def report_service_songs(self, service_id):
        """Put the songs now in this service into the report as its own section, replacing the section from an
        earlier Report of the same service. A song that appears twice is listed once. Returns
        (section, number of songs, replaced); raises ValueError if the service has no songs."""
        service = self.get_service(service_id)
        if service is None:
            raise ValueError(f"No service with id {service_id}")
        songs, seen = [], set()
        for item in self.get_service_items(service_id):
            if item["item_type"] != "song" or item["song_id"] in seen:
                continue
            seen.add(item["song_id"])
            songs.append(self.get_song(item["song_id"]))
        if not songs:
            raise ValueError("This service has no songs to report")
        with self.transaction():
            row = self.conn.execute("SELECT section FROM song_report WHERE service_id = ? LIMIT 1",
                                    (service_id,)).fetchone()
            replaced = row is not None
            if replaced:
                section = row[0]
                self.conn.execute("DELETE FROM song_report WHERE section = ?", (section,))
            else:
                section = self.conn.execute("SELECT COALESCE(MAX(section), 0) + 1 FROM song_report").fetchone()[0]
            self.conn.executemany(
                "INSERT INTO song_report (section, service_id, service_title, service_date, position, song_id, "
                "title, author, copyright, ccli_number) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(section, service_id, service["title"], service["service_date"], n, s["id"], s["title"],
                  s["author"], s["copyright"], s["ccli_number"]) for n, s in enumerate(songs, start=1)])
        return section, len(songs), replaced

    def get_song_report(self):
        """Every row of the report: sections newest service first, songs in service order."""
        return self.conn.execute(
            "SELECT * FROM song_report ORDER BY service_date DESC, section DESC, position").fetchall()

    def delete_report_section(self, section):
        with self.transaction():
            return self.conn.execute("DELETE FROM song_report WHERE section = ?", (section,)).rowcount > 0

    def report_section_rows(self, section):
        """The rows of one report section as plain dicts (kept so a removal can be undone)."""
        return [dict(r) for r in self.conn.execute("SELECT * FROM song_report WHERE section = ? ORDER BY position", (section,))]

    def restore_report_rows(self, rows):
        """Put back rows taken from `report_section_rows`."""
        with self.transaction():
            for row in rows:
                names = list(row)
                self.conn.execute(f"INSERT OR REPLACE INTO song_report ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                                  [row[n] for n in names])

    def duplicate_service(self, service_id, service_date, title=None):
        """Copy a service and all its items to a new date ("same as last week")."""
        src = self.get_service(service_id)
        if src is None:
            raise ValueError(f"No service with id {service_id}")
        with self.transaction():
            new_id = self.create_service(
                title or src["title"], service_date, src["theme_id"], src["notes"],
                src["overlay_theme_id"])
            self.conn.execute(
                """INSERT INTO service_items
                       (service_id, position, item_type, song_id, arrangement_id,
                        translation_id, book_id, chapter, verse_start, verse_end,
                        media_id, presentation_id, theme_id, overlay_theme_id, title, body, overrides)
                   SELECT ?, position, item_type, song_id, arrangement_id,
                          translation_id, book_id, chapter, verse_start, verse_end,
                          media_id, presentation_id, theme_id, overlay_theme_id, title, body, overrides
                   FROM service_items WHERE service_id = ? ORDER BY position""",
                (new_id, service_id),
            )
        return new_id

    # -- service items -----------------------------------------------------
    # Positions are always 0..N-1 with no gaps; every method below preserves that.

    def _translation_id(self, translation):
        if isinstance(translation, int):
            return translation
        row = self.conn.execute(
            "SELECT id FROM translations WHERE abbreviation = ? COLLATE NOCASE",
            (translation,)).fetchone()
        if row is None:
            raise ValueError(f"Unknown translation: {translation!r}")
        return row["id"]

    def _check_arrangement(self, song_id, arrangement_id):
        ok = self.conn.execute(
            "SELECT 1 FROM arrangements WHERE id = ? AND song_id = ?",
            (arrangement_id, song_id)).fetchone()
        if ok is None:
            raise ValueError(
                f"Arrangement {arrangement_id} does not belong to song {song_id}")

    def add_service_item(self, service_id, item_type, position=None, *,
                         overrides=None, **refs):
        """Add an item to a service; returns its id.

        item_type: song | scripture | media | presentation | announcement | blank
        position:  0-based slot; omitted (or too large) appends to the end.
        refs:      any of song_id, arrangement_id, translation_id, book_id,
                   chapter, verse_start, verse_end, media_id, presentation_id,
                   theme_id, title, body. For convenience, `book="John"` and
                   `translation="KJV"` may be used instead of book_id/translation_id.
        overrides: dict of per-service adjustments, stored as JSON.
        """
        if item_type not in ITEM_TYPES:
            raise ValueError(f"Unknown item type: {item_type!r}")

        refs = dict(refs)
        if "book" in refs:
            refs["book_id"] = self._book_id(refs.pop("book"))
        if "translation" in refs:
            refs["translation_id"] = self._translation_id(refs.pop("translation"))
        unknown = set(refs) - set(_ITEM_FIELDS)
        if unknown:
            raise TypeError(f"Unknown service item field(s): {sorted(unknown)}")

        missing = [f for f in _REQUIRED_REFS[item_type] if refs.get(f) is None]
        if missing:
            raise ValueError(f"A {item_type} item needs: {', '.join(missing)}")
        start, end = refs.get("verse_start"), refs.get("verse_end")
        if start is not None and end is not None and end < start:
            raise ValueError("verse_end cannot be before verse_start")
        if item_type == "song" and refs.get("arrangement_id") is not None:
            self._check_arrangement(refs["song_id"], refs["arrangement_id"])

        with self.transaction():
            count = self.conn.execute(
                "SELECT COUNT(*) FROM service_items WHERE service_id = ?",
                (service_id,)).fetchone()[0]
            position = count if position is None else max(0, min(position, count))
            self.conn.execute(
                "UPDATE service_items SET position = position + 1 "
                "WHERE service_id = ? AND position >= ?", (service_id, position))
            values = {"service_id": service_id, "position": position,
                      "item_type": item_type, **refs}
            if overrides is not None:
                values["overrides"] = json.dumps(overrides)
            return self._insert("service_items", values)

    def get_service_item(self, item_id):
        return self._get("service_items", item_id)

    def get_service_items(self, service_id):
        """Raw item rows in play order."""
        return self.conn.execute(
            "SELECT * FROM service_items WHERE service_id = ? ORDER BY position",
            (service_id,)).fetchall()

    def get_service_outline(self, service_id):
        """Items in order with a human-readable `display_title`, for the service list."""
        return self.conn.execute(
            """SELECT si.id, si.position, si.item_type, si.theme_id, si.overlay_theme_id,
                      COALESCE(si.title, CASE si.item_type
                          WHEN 'song' THEN COALESCE(s.operator_name, s.title)
                          WHEN 'scripture' THEN b.name || ' ' || si.chapter || ':'
                               || si.verse_start
                               || CASE WHEN si.verse_end IS NOT NULL
                                            AND si.verse_end <> si.verse_start
                                       THEN '-' || si.verse_end ELSE '' END
                               || ' (' || t.abbreviation || ')'
                          WHEN 'media' THEN m.name
                          WHEN 'presentation' THEN p.name
                          WHEN 'announcement' THEN 'Announcement'
                          ELSE 'Blank' END) AS display_title
               FROM service_items si
               LEFT JOIN songs s ON s.id = si.song_id
               LEFT JOIN bible_books b ON b.id = si.book_id
               LEFT JOIN translations t ON t.id = si.translation_id
               LEFT JOIN media_assets m ON m.id = si.media_id
               LEFT JOIN presentations p ON p.id = si.presentation_id
               WHERE si.service_id = ? ORDER BY si.position""",
            (service_id,)).fetchall()

    def update_service_item(self, item_id, title=_UNSET, body=_UNSET, overrides=_UNSET,
                            theme_id=_UNSET, arrangement_id=_UNSET, overlay_theme_id=_UNSET):
        """Change an item's own settings. What it points at (song, passage...) is
        fixed; to point somewhere else, remove the item and add a new one."""
        if overrides is not _UNSET:
            overrides = None if overrides is None else json.dumps(overrides)
        if arrangement_id is not _UNSET and arrangement_id is not None:
            row = self.get_service_item(item_id)
            if row is None or row["song_id"] is None:
                raise ValueError("Only song items have an arrangement")
            self._check_arrangement(row["song_id"], arrangement_id)
        return self._update("service_items", item_id, {
            "title": title, "body": body, "overrides": overrides,
            "theme_id": theme_id, "arrangement_id": arrangement_id,
            "overlay_theme_id": overlay_theme_id})

    def _write_order(self, ordered_ids):
        self.conn.executemany(
            "UPDATE service_items SET position = ? WHERE id = ?",
            [(pos, item_id) for pos, item_id in enumerate(ordered_ids)])

    def remove_service_item(self, item_id):
        """Delete an item and close the gap it leaves."""
        row = self.get_service_item(item_id)
        if row is None:
            return False
        with self.transaction():
            self.conn.execute("DELETE FROM service_items WHERE id = ?", (item_id,))
            self.conn.execute(
                "UPDATE service_items SET position = position - 1 "
                "WHERE service_id = ? AND position > ?",
                (row["service_id"], row["position"]))
        return True

    def restore_service_item(self, row):
        """Put back an item taken from `get_service_item` (as a dict) at its old place; the others move down one."""
        data = {k: v for k, v in row.items() if k != "id"}
        with self.transaction():
            self.conn.execute("UPDATE service_items SET position = position + 1 WHERE service_id = ? AND position >= ?",
                              (data["service_id"], data["position"]))
            names = list(data)
            cur = self.conn.execute(f"INSERT INTO service_items ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                                    [data[n] for n in names])
            return cur.lastrowid

    def reorder_service_items(self, service_id, ordered_ids):
        """Set the full order. `ordered_ids` must be exactly the service's item ids."""
        ordered_ids = list(ordered_ids)
        current = {r["id"] for r in self.conn.execute(
            "SELECT id FROM service_items WHERE service_id = ?", (service_id,))}
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != current:
            raise ValueError("ordered_ids must list each item of the service exactly once")
        with self.transaction():
            self._write_order(ordered_ids)

    def move_service_item(self, item_id, new_position):
        """Move one item to a 0-based position (clamped to the valid range)."""
        row = self.get_service_item(item_id)
        if row is None:
            raise ValueError(f"No service item with id {item_id}")
        ids = [r["id"] for r in self.get_service_items(row["service_id"])]
        ids.remove(item_id)
        ids.insert(max(0, min(new_position, len(ids))), item_id)
        with self.transaction():
            self._write_order(ids)
