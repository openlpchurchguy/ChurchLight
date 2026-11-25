# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Schema versioning for ChurchLight Presenter.

Rules for working with this file:
  * Never edit a migration once it has shipped. Add a new one at the end.
  * Versions must be 1, 2, 3... with no gaps. The version is stored in
    SQLite's built-in `PRAGMA user_version`.
  * Each migration runs in one transaction (SQLite DDL is transactional), so a
    failure leaves the database exactly as it was.
"""
import sqlite3

# Canonical order, frozen: verses reference these ids (1-66). Do not reorder.
_BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges",
    "Ruth", "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles",
    "2 Chronicles", "Ezra", "Nehemiah", "Esther", "Job", "Psalms", "Proverbs",
    "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah", "Lamentations",
    "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah",
    "Nahum", "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Matthew", "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians",
    "2 Corinthians", "Galatians", "Ephesians", "Philippians", "Colossians",
    "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy", "Titus",
    "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John",
    "3 John", "Jude", "Revelation",
]


def _seed_books_sql():
    rows = ",\n".join(
        f"({i}, '{name}', '{'OT' if i <= 39 else 'NT'}')"
        for i, name in enumerate(_BOOKS, start=1)
    )
    return f"INSERT INTO bible_books (id, name, testament) VALUES\n{rows};"


# ---------------------------------------------------------------------------
# Migration 1: core schema
# ---------------------------------------------------------------------------
_V1 = (
    """
-- Media ---------------------------------------------------------------
CREATE TABLE media_assets (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,            -- relative to the media root folder
    media_type TEXT NOT NULL CHECK (media_type IN ('image', 'video', 'audio')),
    file_hash TEXT,                       -- detect duplicates / moved files
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Songs ---------------------------------------------------------------
CREATE TABLE songs (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    copyright TEXT,
    ccli_number TEXT,
    default_key TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_songs_title ON songs(title COLLATE NOCASE);

-- Each section is stored once: "Verse 1", "Chorus", "Bridge"...
CREATE TABLE song_sections (
    id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    section_type TEXT NOT NULL DEFAULT 'verse' CHECK (section_type IN
        ('verse', 'pre_chorus', 'chorus', 'bridge', 'tag', 'intro', 'outro',
         'instrumental', 'other')),
    position INTEGER NOT NULL,            -- display order in the editor
    lyrics TEXT NOT NULL DEFAULT '',
    UNIQUE (song_id, label)
);
CREATE INDEX idx_sections_song ON song_sections(song_id, position);

-- An arrangement is the play order, e.g. V1, C, V2, C, B, C.
-- The same section may appear many times. Sections must belong to the same
-- song as the arrangement; DatabaseManager enforces that.
CREATE TABLE arrangements (
    id INTEGER PRIMARY KEY,
    song_id INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
    UNIQUE (song_id, name)
);
CREATE UNIQUE INDEX idx_one_default_arrangement
    ON arrangements(song_id) WHERE is_default = 1;

CREATE TABLE arrangement_items (
    id INTEGER PRIMARY KEY,
    arrangement_id INTEGER NOT NULL REFERENCES arrangements(id) ON DELETE CASCADE,
    section_id INTEGER NOT NULL REFERENCES song_sections(id) ON DELETE CASCADE,
    position INTEGER NOT NULL
);
CREATE INDEX idx_arr_items ON arrangement_items(arrangement_id, position);

-- Scripture -----------------------------------------------------------
CREATE TABLE translations (
    id INTEGER PRIMARY KEY,
    abbreviation TEXT NOT NULL UNIQUE,    -- "KJV", "ESV"
    name TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    license_notes TEXT                    -- licensing differs a lot per translation
);

CREATE TABLE bible_books (
    id INTEGER PRIMARY KEY,               -- 1..66, canonical order
    name TEXT NOT NULL UNIQUE,
    testament TEXT NOT NULL CHECK (testament IN ('OT', 'NT'))
);

CREATE TABLE verses (
    id INTEGER PRIMARY KEY,
    translation_id INTEGER NOT NULL REFERENCES translations(id) ON DELETE CASCADE,
    book_id INTEGER NOT NULL REFERENCES bible_books(id),
    chapter INTEGER NOT NULL,
    verse INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (translation_id, book_id, chapter, verse)
);

-- Themes (look and feel, separate from content) -----------------------
CREATE TABLE themes (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    settings TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(settings)),
    background_media_id INTEGER REFERENCES media_assets(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Services ------------------------------------------------------------
CREATE TABLE services (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    service_date TEXT NOT NULL,           -- ISO 8601: YYYY-MM-DD
    theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_services_date ON services(service_date);

-- Items REFERENCE content rather than copying it. RESTRICT keeps a song,
-- translation or media file from being deleted while a service uses it,
-- which also protects usage history for CCLI reporting.
CREATE TABLE service_items (
    id INTEGER PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN
        ('song', 'scripture', 'media', 'announcement', 'blank')),
    song_id INTEGER REFERENCES songs(id) ON DELETE RESTRICT,
    arrangement_id INTEGER REFERENCES arrangements(id) ON DELETE SET NULL,
    translation_id INTEGER REFERENCES translations(id) ON DELETE RESTRICT,
    book_id INTEGER REFERENCES bible_books(id),
    chapter INTEGER,
    verse_start INTEGER,
    verse_end INTEGER,
    media_id INTEGER REFERENCES media_assets(id) ON DELETE RESTRICT,
    title TEXT,
    body TEXT,
    overrides TEXT CHECK (overrides IS NULL OR json_valid(overrides)),
    CHECK (
        (item_type = 'song' AND song_id IS NOT NULL)
        OR (item_type = 'scripture' AND translation_id IS NOT NULL
            AND book_id IS NOT NULL AND chapter IS NOT NULL
            AND verse_start IS NOT NULL)
        OR (item_type = 'media' AND media_id IS NOT NULL)
        OR item_type IN ('announcement', 'blank')
    )
);
CREATE INDEX idx_service_items ON service_items(service_id, position);

-- What was sung when: derived from services, so there is nothing to keep in sync.
CREATE VIEW song_usage AS
SELECT sv.service_date, sv.id AS service_id, s.id AS song_id,
       s.title, s.author, s.copyright, s.ccli_number
FROM service_items si
JOIN services sv ON sv.id = si.service_id
JOIN songs s ON s.id = si.song_id
WHERE si.item_type = 'song';

-- Keep songs.updated_at honest, including when only lyrics change.
CREATE TRIGGER trg_songs_touch AFTER UPDATE ON songs
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE songs SET updated_at = datetime('now') WHERE id = NEW.id;
END;
CREATE TRIGGER trg_sections_touch_ai AFTER INSERT ON song_sections
BEGIN UPDATE songs SET updated_at = datetime('now') WHERE id = NEW.song_id; END;
CREATE TRIGGER trg_sections_touch_au AFTER UPDATE ON song_sections
BEGIN UPDATE songs SET updated_at = datetime('now') WHERE id = NEW.song_id; END;
CREATE TRIGGER trg_sections_touch_ad AFTER DELETE ON song_sections
BEGIN UPDATE songs SET updated_at = datetime('now') WHERE id = OLD.song_id; END;
"""
    + _seed_books_sql()
)


# ---------------------------------------------------------------------------
# Migration 2: full-text song search, kept in sync by triggers
# ---------------------------------------------------------------------------
def _fts_refresh(song_id_expr):
    return f"""
    DELETE FROM song_fts WHERE rowid = {song_id_expr};
    INSERT INTO song_fts (rowid, title, author, lyrics)
    SELECT s.id, s.title, COALESCE(s.author, ''),
           COALESCE((SELECT group_concat(lyrics, char(10)) FROM
                     (SELECT lyrics FROM song_sections
                      WHERE song_id = s.id ORDER BY position)), '')
    FROM songs s WHERE s.id = {song_id_expr};
    """


_V2 = f"""
CREATE VIRTUAL TABLE song_fts USING fts5(
    title, author, lyrics, tokenize = 'porter unicode61 remove_diacritics 2'
);

CREATE TRIGGER trg_fts_song_ai AFTER INSERT ON songs
BEGIN {_fts_refresh('NEW.id')} END;
CREATE TRIGGER trg_fts_song_au AFTER UPDATE OF title, author ON songs
BEGIN {_fts_refresh('NEW.id')} END;
CREATE TRIGGER trg_fts_song_ad AFTER DELETE ON songs
BEGIN DELETE FROM song_fts WHERE rowid = OLD.id; END;

CREATE TRIGGER trg_fts_sec_ai AFTER INSERT ON song_sections
BEGIN {_fts_refresh('NEW.song_id')} END;
CREATE TRIGGER trg_fts_sec_au AFTER UPDATE ON song_sections
BEGIN {_fts_refresh('NEW.song_id')} END;
CREATE TRIGGER trg_fts_sec_ad AFTER DELETE ON song_sections
BEGIN {_fts_refresh('OLD.song_id')} END;

-- Backfill (a no-op on a fresh database).
INSERT INTO song_fts (rowid, title, author, lyrics)
SELECT s.id, s.title, COALESCE(s.author, ''),
       COALESCE((SELECT group_concat(lyrics, char(10)) FROM
                 (SELECT lyrics FROM song_sections
                  WHERE song_id = s.id ORDER BY position)), '')
FROM songs s;
"""


# ---------------------------------------------------------------------------
# Migration 3: presentations, and service_items rebuilt to reference them
# ---------------------------------------------------------------------------
# SQLite cannot alter a CHECK constraint in place, so service_items is rebuilt:
# create the new table, copy every row, drop the old one, rename. The
# song_usage view reads from service_items, so it is dropped first and
# recreated afterwards (renaming a table under a live view fails otherwise).
# Nothing else references service_items, so dropping it is safe with foreign
# keys enabled.
_V3 = """
-- Presentations: a deck converted ONCE at import into page images -----------
CREATE TABLE presentations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'pdf'
        CHECK (source_type IN ('pdf', 'pptx', 'images')),
    source_path TEXT,                     -- original file kept for re-import
    file_hash TEXT,                       -- spot re-imports of the same deck
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_presentations_name ON presentations(name COLLATE NOCASE);
CREATE INDEX idx_presentations_hash ON presentations(file_hash);

-- Page count is derived (COUNT of pages), never stored, so it cannot drift.
CREATE TABLE presentation_pages (
    id INTEGER PRIMARY KEY,
    presentation_id INTEGER NOT NULL
        REFERENCES presentations(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    image_path TEXT NOT NULL,             -- relative to the media root
    notes TEXT,                           -- speaker notes, for stage views
    UNIQUE (presentation_id, page_number)
);

-- Rebuild service_items -----------------------------------------------------
DROP VIEW song_usage;

CREATE TABLE service_items_new (
    id INTEGER PRIMARY KEY,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL CHECK (item_type IN
        ('song', 'scripture', 'media', 'presentation', 'announcement', 'blank')),
    song_id INTEGER REFERENCES songs(id) ON DELETE RESTRICT,
    arrangement_id INTEGER REFERENCES arrangements(id) ON DELETE SET NULL,
    translation_id INTEGER REFERENCES translations(id) ON DELETE RESTRICT,
    book_id INTEGER REFERENCES bible_books(id),
    chapter INTEGER,
    verse_start INTEGER,
    verse_end INTEGER,
    media_id INTEGER REFERENCES media_assets(id) ON DELETE RESTRICT,
    presentation_id INTEGER REFERENCES presentations(id) ON DELETE RESTRICT,
    theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL,
    title TEXT,
    body TEXT,
    overrides TEXT CHECK (overrides IS NULL OR json_valid(overrides)),
    CHECK (
        (item_type = 'song' AND song_id IS NOT NULL)
        OR (item_type = 'scripture' AND translation_id IS NOT NULL
            AND book_id IS NOT NULL AND chapter IS NOT NULL
            AND verse_start IS NOT NULL)
        OR (item_type = 'media' AND media_id IS NOT NULL)
        OR (item_type = 'presentation' AND presentation_id IS NOT NULL)
        OR item_type IN ('announcement', 'blank')
    ),
    CHECK (verse_end IS NULL OR verse_start IS NULL OR verse_end >= verse_start)
);

INSERT INTO service_items_new
    (id, service_id, position, item_type, song_id, arrangement_id,
     translation_id, book_id, chapter, verse_start, verse_end, media_id,
     title, body, overrides)
SELECT id, service_id, position, item_type, song_id, arrangement_id,
       translation_id, book_id, chapter, verse_start, verse_end, media_id,
       title, body, overrides
FROM service_items;

DROP TABLE service_items;
ALTER TABLE service_items_new RENAME TO service_items;

-- position is deliberately not UNIQUE: reordering rewrites positions in one
-- transaction, and a unique index would reject the intermediate states.
CREATE INDEX idx_service_items ON service_items(service_id, position);

CREATE VIEW song_usage AS
SELECT sv.service_date, sv.id AS service_id, s.id AS song_id,
       s.title, s.author, s.copyright, s.ccli_number
FROM service_items si
JOIN services sv ON sv.id = si.service_id
JOIN songs s ON s.id = si.song_id
WHERE si.item_type = 'song';
"""


# Migration 4: how a deck's slides change on the projector (see core/transitions.py).
# Existing decks get "none", so nothing looks different until someone chooses a transition.
_V4 = """
ALTER TABLE presentations ADD COLUMN transition TEXT NOT NULL DEFAULT 'none'
    CHECK (transition IN ('none', 'dissolve', 'circle_out', 'circle_in'));
ALTER TABLE presentations ADD COLUMN transition_ms INTEGER NOT NULL DEFAULT 500
    CHECK (transition_ms BETWEEN 0 AND 2000);
"""


_V5 = """
ALTER TABLE songs ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0 CHECK (is_draft IN (0, 1));
"""


# Migration 6: small program-wide settings (which theme is the default, which is for Bible verses).
_V6 = """
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# Migration 7: timed advance for decks (0 seconds = manual) and what happens after the last slide.
_V7 = """
ALTER TABLE presentations ADD COLUMN auto_seconds INTEGER NOT NULL DEFAULT 0
    CHECK (auto_seconds BETWEEN 0 AND 600);
ALTER TABLE presentations ADD COLUMN end_action TEXT NOT NULL DEFAULT 'stop'
    CHECK (end_action IN ('stop', 'loop', 'blank', 'next_item'));
"""


# Migration 8: a second theme per service and per item, for the overlay (lower third) outputs.
# `theme_id` stays the projector theme; `overlay_theme_id` is the look for overlay outputs.
_V8 = """
ALTER TABLE services ADD COLUMN overlay_theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL;
ALTER TABLE service_items ADD COLUMN overlay_theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL;
"""


# Migration 9: a deck page can be skipped (kept in the deck, passed over when the deck runs).
_V9 = """
ALTER TABLE presentation_pages ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0
    CHECK (skipped IN (0, 1));
"""


# Migration 10: a deck page may have its own delay and transition. NULL means "follow the deck"
# (or the service's own setting for that deck), so existing decks behave exactly as before.
_V10 = """
ALTER TABLE presentation_pages ADD COLUMN auto_seconds INTEGER
    CHECK (auto_seconds IS NULL OR auto_seconds BETWEEN 0 AND 600);
ALTER TABLE presentation_pages ADD COLUMN transition TEXT
    CHECK (transition IS NULL OR transition IN ('none', 'dissolve', 'circle_out', 'circle_in'));
ALTER TABLE presentation_pages ADD COLUMN transition_ms INTEGER
    CHECK (transition_ms IS NULL OR transition_ms BETWEEN 0 AND 2000);
"""


# Migration 11: how a deck's pictures fill the screen: whole picture ("fit"), covering the screen and
# cropping the edges ("fill"), or stretched to the screen's shape ("stretch"). Existing decks stay "fit".
_V11 = """
ALTER TABLE presentations ADD COLUMN image_fit TEXT NOT NULL DEFAULT 'fit'
    CHECK (image_fit IN ('fit', 'fill', 'stretch'));
"""


# Migration 12: a service may choose how its verses are split into slides, whatever the theme says:
# NULL follows the theme, 'verse' is one verse per slide, 'chars' fits as many whole verses as will fit.
_V12 = """
ALTER TABLE services ADD COLUMN verse_split TEXT
    CHECK (verse_split IS NULL OR verse_split IN ('verse', 'chars'));
"""


# Migration 13: a song may carry its own projector theme (patriotic or Christmas songs), which beats the
# service's theme. NULL follows the service; a deleted theme sets it back to NULL.
_V13 = """
ALTER TABLE songs ADD COLUMN theme_id INTEGER REFERENCES themes(id) ON DELETE SET NULL;
"""


# Migration 14: a deck page may have a title ("Offering", "Welcome"). NULL means the plain "Slide N".
_V14 = """
ALTER TABLE presentation_pages ADD COLUMN title TEXT
    CHECK (title IS NULL OR length(title) BETWEEN 1 AND 60);
"""


# Migration 15: more slide transitions. The older columns restrict `transition` to four names with a CHECK
# constraint, and SQLite cannot change a CHECK in place, so the stored table definitions are widened directly
# (the method SQLite's own documentation gives for loosening a constraint). No rows are touched.
_V15_OLD = "IN ('none', 'dissolve', 'circle_out', 'circle_in')"
_V15_KINDS = ("none", "dissolve", "circle_out", "circle_in", "slide_left", "slide_right", "slide_up", "slide_down",
              "split_horizontal", "split_vertical", "split_diagonal_left", "split_diagonal_right",
              "wipe_left", "wipe_right", "wipe_up", "wipe_down", "fade_black", "fade_white")


def _v15(conn):
    new = "IN (" + ", ".join(f"'{kind}'" for kind in _V15_KINDS) + ")"
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    try:
        for table in ("presentations", "presentation_pages"):
            conn.execute("UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND name = ?",
                         (_V15_OLD, new, table))
        conn.execute(f"PRAGMA schema_version = {version + 1}")
    finally:
        conn.execute("PRAGMA writable_schema = OFF")
    left = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE ?",
                        ("%" + _V15_OLD + "%",)).fetchall()
    if left:
        raise sqlite3.OperationalError("Could not widen the slide transition list in " + ", ".join(r[0] for r in left))


# Migration 16: the song report, one running list of the songs used, in a section per service. A section is
# written when the operator presses Report, and is replaced (not doubled) if Report is pressed again for the
# same service. The song's details are copied in, so the list stays right if a song is edited or removed later,
# and `service_id` has no foreign key on purpose: the list outlives its service (it is cleared to NULL if the
# service is deleted, so a reused id can never overwrite it).
_V16 = """
CREATE TABLE song_report (
    id INTEGER PRIMARY KEY,
    section INTEGER NOT NULL,
    service_id INTEGER,
    service_title TEXT NOT NULL,
    service_date TEXT NOT NULL,
    position INTEGER NOT NULL,
    song_id INTEGER,
    title TEXT NOT NULL,
    author TEXT,
    copyright TEXT,
    ccli_number TEXT,
    reported_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_song_report_section ON song_report(section);
CREATE INDEX idx_song_report_service ON song_report(service_id);
"""


# Migration 17: a song may choose where its title and CCLI slide goes. NULL follows the program-wide choice.
_V17 = """
ALTER TABLE songs ADD COLUMN notice_slide TEXT
    CHECK (notice_slide IS NULL OR notice_slide IN ('before', 'after', 'never'));
"""


# Migration 18: a song may have a name only the operator sees ("123 Amazing Grace", a hymnal number...). The title stays the
# song's real name on the screens and in reports. The search index carries the operator name too (in the author column,
# which every search matches), so either name finds the song.
def _fts_refresh_with_operator(song_id_expr):
    return f"""
    DELETE FROM song_fts WHERE rowid = {song_id_expr};
    INSERT INTO song_fts (rowid, title, author, lyrics)
    SELECT s.id, s.title, TRIM(COALESCE(s.author, '') || ' ' || COALESCE(s.operator_name, '')),
           COALESCE((SELECT group_concat(lyrics, char(10)) FROM
                     (SELECT lyrics FROM song_sections
                      WHERE song_id = s.id ORDER BY position)), '')
    FROM songs s WHERE s.id = {song_id_expr};
    """


_V18 = f"""
ALTER TABLE songs ADD COLUMN operator_name TEXT;

DROP TRIGGER trg_fts_song_ai;
DROP TRIGGER trg_fts_song_au;
DROP TRIGGER trg_fts_sec_ai;
DROP TRIGGER trg_fts_sec_au;
DROP TRIGGER trg_fts_sec_ad;

CREATE TRIGGER trg_fts_song_ai AFTER INSERT ON songs
BEGIN {_fts_refresh_with_operator('NEW.id')} END;
CREATE TRIGGER trg_fts_song_au AFTER UPDATE OF title, author, operator_name ON songs
BEGIN {_fts_refresh_with_operator('NEW.id')} END;
CREATE TRIGGER trg_fts_sec_ai AFTER INSERT ON song_sections
BEGIN {_fts_refresh_with_operator('NEW.song_id')} END;
CREATE TRIGGER trg_fts_sec_au AFTER UPDATE ON song_sections
BEGIN {_fts_refresh_with_operator('NEW.song_id')} END;
CREATE TRIGGER trg_fts_sec_ad AFTER DELETE ON song_sections
BEGIN {_fts_refresh_with_operator('OLD.song_id')} END;

DELETE FROM song_fts;
INSERT INTO song_fts (rowid, title, author, lyrics)
SELECT s.id, s.title, TRIM(COALESCE(s.author, '')),
       COALESCE((SELECT group_concat(lyrics, char(10)) FROM
                 (SELECT lyrics FROM song_sections
                  WHERE song_id = s.id ORDER BY position)), '')
FROM songs s;
"""


# (version, description, SQL script or a function taking the connection). Append new migrations at the end.
_V19 = """
ALTER TABLE media_assets ADD COLUMN duration_ms INTEGER;       -- a video or sound clip's length, read by the page when it was added
"""

_V20 = """
ALTER TABLE presentation_pages ADD COLUMN media_id INTEGER REFERENCES media_assets(id);     -- the Media Library file this page shows
ALTER TABLE presentation_pages ADD COLUMN muted INTEGER NOT NULL DEFAULT 0;                 -- a video page without its own sound
ALTER TABLE presentation_pages ADD COLUMN sound_id INTEGER REFERENCES media_assets(id);     -- a sound file that plays while the page is live
ALTER TABLE presentation_pages ADD COLUMN sound_loop INTEGER NOT NULL DEFAULT 0;
"""


MIGRATIONS = [
    (1, "core schema", _V1),
    (2, "song full-text search", _V2),
    (3, "presentations and per-item themes", _V3),
    (4, "slide transitions per deck", _V4),
    (5, "song drafts", _V5),
    (6, "program-wide settings", _V6),
    (7, "timed slide advance for decks", _V7),
    (8, "overlay theme per service and item", _V8),
    (9, "skippable deck pages", _V9),
    (10, "per-page delay and transition", _V10),
    (11, "picture size per deck", _V11),
    (12, "verse layout per service", _V12),
    (13, "theme per song", _V13),
    (14, "slide titles", _V14),
    (15, "more slide transitions", _v15),
    (16, "song report", _V16),
    (17, "title and CCLI slide per song", _V17),
    (18, "operator name per song", _V18),
    (19, "length of video and sound clips", _V19),
    (20, "Media Library files and sound on slideshow pages", _V20),
]


def migrate(conn, migrations=MIGRATIONS):
    """Bring `conn` up to the latest schema. Returns the resulting version.

    Call `PRAGMA foreign_keys = ON` on the connection *before* this; that
    pragma cannot be changed inside a transaction.
    """
    versions = [m[0] for m in migrations]
    if versions != list(range(1, len(versions) + 1)):
        raise ValueError(f"Migration versions must be 1..N with no gaps: {versions}")

    latest = len(migrations)
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > latest:
        raise RuntimeError(
            f"Database schema is version {current}, but this app only knows "
            f"up to {latest}. Update the application."
        )

    for version, _description, script in migrations:
        if version <= current:
            continue
        try:
            if callable(script):
                conn.execute("BEGIN")
                script(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.execute("COMMIT")
            else:
                conn.executescript(
                    f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;"
                )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.rollback()
            raise
    return latest
