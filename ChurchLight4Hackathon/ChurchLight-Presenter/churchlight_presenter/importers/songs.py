# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Import songs from OpenLyrics files (the open format OpenLP, OpenSong exports,
SongBeamer converters and others write).

Each song becomes sections stored once ("Verse 1", "Chorus") and a default
arrangement that follows the file's verse order, repeats included. Chords are
dropped, line breaks are kept, and a verse written in two languages imports in
the first language only (with a warning).

A folder imports every .xml file in it. A song already in the library with the
same title and the same words is skipped rather than doubled.

    python -m churchlight_presenter.importers.songs songs_folder --db church.db
"""
import argparse
import time
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .common import ImporterError, parse_xml, squash

MAX_FILE_BYTES = 5 * 1024 * 1024

# OpenLyrics verse-name prefix -> (section type, label)
_TYPES = {"v": ("verse", "Verse"), "c": ("chorus", "Chorus"), "b": ("bridge", "Bridge"),
          "p": ("pre_chorus", "Pre-Chorus"), "i": ("intro", "Intro"), "e": ("outro", "Ending"),
          "o": ("other", "Other")}
_NAME_RE = re.compile(r"^([a-z])(\d*)(.*)$", re.IGNORECASE)
# The part's real name, written by ChurchLight's own exporter (core/export.py CHURCHLIGHT_NS)
CHURCHLIGHT_LABEL = "{urn:churchlight-presenter:openlyrics-extension}label"
# Some programs write the whole word ("verse1", "Chorus 2", "pre-chorus", "ending") instead of OpenLyrics' one letter.
_PART_RE = re.compile(r"^([a-z])(\d+)([a-z]+)$", re.IGNORECASE)      # v1a, c2b: one part of a split verse
_WORD_RE = re.compile(r"^(verse|chorus|refrain|bridge|pre[-_ ]?chorus|prechorus|intro|ending|outro|other|tag)[\s_-]*(.*)$",
                      re.IGNORECASE)
_WORDS = {"verse": "v", "chorus": "c", "refrain": "c", "bridge": "b", "prechorus": "p", "pre-chorus": "p", "pre_chorus": "p",
          "pre chorus": "p", "intro": "i", "ending": "e", "outro": "e", "other": "o", "tag": "o"}
_BREAK = "\x00"
_LICENCE_SENTENCE = re.compile(r"for use solely with|terms of use|all rights reserved|ccli\.com", re.IGNORECASE)


@dataclass
class ParsedSong:
    title: str
    author: str | None = None
    copyright: str | None = None
    ccli_number: str | None = None
    default_key: str | None = None
    notes: str | None = None
    sections: list = field(default_factory=list)   # (label, section_type, lyrics)
    order: list = field(default_factory=list)      # labels in play order
    warnings: list = field(default_factory=list)


@dataclass
class SongImportReport:
    imported: list = field(default_factory=list)      # song ids
    duplicates: int = 0
    failed: list = field(default_factory=list)        # (file name, message)
    warnings: list = field(default_factory=list)      # (file name, message)


# ---------------------------------------------------------------------------
def _lines_text(lines):
    """The text of a <lines> element: <br/> becomes a line break, chords are dropped,
    other markup keeps its words, and source-file indentation is ignored."""
    pieces = [lines.text or ""]

    def walk(node):
        for child in node:
            if child.tag == "br":
                pieces.append(_BREAK)
            elif child.tag != "chord":
                pieces.append(child.text or "")
                walk(child)
            pieces.append(child.tail or "")

    walk(lines)
    return "\n".join(squash(part) for part in "".join(pieces).split(_BREAK))


def _label_for(name, used):
    """('v1' -> 'Verse 1', 'c' -> 'Chorus', 'c2' -> 'Chorus 2'), unique within the song."""
    name = name.strip()
    word = _WORD_RE.match(name)
    if word:                                                  # "verse1" -> "v1"
        name = _WORDS[word.group(1).lower().replace("  ", " ")] + word.group(2).strip()
    match = _NAME_RE.match(name)
    if match and match.group(1).lower() in _TYPES:
        section_type, base = _TYPES[match.group(1).lower()]
        suffix = (match.group(2) + match.group(3)).strip()
        label = f"{base} {suffix}" if suffix else base
    else:
        section_type, label = "other", name or "Other"
    unique, n = label, 2
    while unique.lower() in used:
        unique, n = f"{label} ({n})", n + 1
    used.add(unique.lower())
    return unique, section_type


def parse_openlyrics(source, what="the song file"):
    """Read one OpenLyrics document (bytes or text) into a ParsedSong."""
    root = parse_xml(source, what)
    if root.tag != "song":
        raise ImporterError(f"{what} is not an OpenLyrics song (its top element is <{root.tag}>)")
    parsed = ParsedSong(title="")
    props = root.find("properties")
    if props is None:
        props = root

    def first(path):
        node = props.find(path)
        return squash("".join(node.itertext())) or None if node is not None else None

    titles = [squash("".join(t.itertext())) for t in props.findall("titles/title")]
    parsed.title = next((t for t in titles if t), "")
    authors = [squash("".join(a.itertext())) for a in props.findall("authors/author")]
    # SongSelect's licence sentence is sometimes filed under "authors"; it is not a person
    authors = [a for a in authors if a and not _LICENCE_SENTENCE.search(a)]
    parsed.author = ", ".join(authors) or None
    parsed.copyright = first("copyright")
    parsed.ccli_number = (first("ccliNo") or "").lstrip("#").strip() or None
    parsed.default_key = first("key")
    comments = [squash("".join(c.itertext())) for c in props.findall("comments/comment")]
    parsed.notes = "\n".join(c for c in comments if c) or None

    # OpenLP splits a long verse into parts named v1a, v1b (c1a...) and then writes the order as plain "v1": the
    # whole verse. So parts of one verse are joined into one section (a blank line between them keeps each part a
    # slide of its own), unless the order itself names single parts, when they stay separate.
    order_names = (first("verseOrder") or "").split()
    merge_parts = not any(_PART_RE.match(name) for name in order_names)
    language, groups = None, {}
    for verse in root.findall("lyrics/verse"):
        name = verse.get("name", "").strip()
        lang = verse.get("lang")
        if language is None:
            language = lang
        if lang and language and lang != language:
            parsed.warnings.append(f"Skipped {name!r} in language {lang!r}; kept {language!r}")
            continue
        text = "\n".join(t for t in (_lines_text(l) for l in verse.findall("lines")) if t)
        if not text:
            continue
        part = _PART_RE.match(name)
        base = (part.group(1) + part.group(2)) if part and merge_parts else name
        key = base.lower() if part and merge_parts else f"{len(groups)}:{base.lower()}"      # a repeated plain name is its own part
        group = groups.setdefault(key, {"name": base, "texts": [], "parts": [], "own": ""})
        group["texts"].append(text)
        group["parts"].append(name.lower())
        group["own"] = group["own"] or squash(verse.get(CHURCHLIGHT_LABEL) or "")

    used, by_name, split_parts = set(), {}, {}
    for key, group in groups.items():
        label, section_type = _label_for(group["name"], used)
        if group["own"]:                                      # ChurchLight's own export: keep the part's real name
            used.discard(label.lower())
            label, n = group["own"], 2
            while label.lower() in used:
                label, n = f"{group['own']} ({n})", n + 1
            used.add(label.lower())
        parsed.sections.append((label, section_type, "\n\n".join(group["texts"])))
        by_name.setdefault(group["name"].lower(), label)
        for part_name in group["parts"]:
            by_name.setdefault(part_name, label)
        whole = _PART_RE.match(group["name"])
        if whole:                                             # kept apart: "v1" in the order means v1a, v1b...
            split_parts.setdefault((whole.group(1) + whole.group(2)).lower(), []).append(label)

    for name in order_names:
        label = by_name.get(name.lower())
        if label is not None:
            parsed.order.append(label)
        elif name.lower() in split_parts:
            parsed.order.extend(split_parts[name.lower()])
        else:
            parsed.warnings.append(f"The verse order names {name!r}, which has no lyrics; skipped")
    if not parsed.order:
        parsed.order = [label for label, _, _ in parsed.sections]
    if not parsed.sections:
        raise ImporterError("The song has no lyrics")
    return parsed


# ---------------------------------------------------------------------------
def _words(text):
    return squash(text).lower()


def _already_in_library(db, parsed):
    mine = _words(" ".join(lyrics for _, _, lyrics in parsed.sections))
    for row in db.search_song(title=parsed.title):
        if row["title"].lower() != parsed.title.lower():
            continue
        theirs = _words(" ".join(s["lyrics"] for s in db.get_sections(row["id"])))
        if theirs == mine:
            return True
    return False


def save_song(db, parsed, force=False):
    """Store a ParsedSong; returns the new song id, or None if it was already there."""
    if not force and _already_in_library(db, parsed):
        return None
    return db.create_song_with_sections(
        parsed.title, parsed.sections, order=parsed.order, author=parsed.author,
        copyright=parsed.copyright, ccli_number=parsed.ccli_number,
        default_key=parsed.default_key, notes=parsed.notes)


def import_openlyrics(db, path, *, force=False, pause=0.0):
    """Import one .xml file, or every .xml file under a folder. Returns a SongImportReport.

    One bad file never stops the others; it is listed in `failed` with the reason.
    `pause` (seconds) is a short rest after each file, so a big batch does not use a whole processor core for its whole length
    (the program's import does this while a video may be playing on the same computer).
    """
    path = Path(path)
    if path.is_dir():
        from .songfiles import SONG_SUFFIXES
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SONG_SUFFIXES)
        if not files:
            raise ImporterError(f"{path} has no song files")
    elif path.is_file():
        files = [path]
    else:
        raise ImporterError(f"{path} was not found")

    report = SongImportReport()
    for file in files:
        try:
            if file.stat().st_size > MAX_FILE_BYTES:
                raise ImporterError("the file is unreasonably large for a song")
            from .songfiles import parse_song_file
            parsed = parse_song_file(file)
            if not parsed.title:
                parsed.title = file.stem
                parsed.warnings.append("The song has no title; the file name was used")
            song_id = save_song(db, parsed, force)
        except (ImporterError, OSError, ValueError) as err:
            report.failed.append((file.name, str(err)))
            continue
        if song_id is None:
            report.duplicates += 1
        else:
            report.imported.append(song_id)
        report.warnings.extend((file.name, w) for w in parsed.warnings)
        if pause:
            time.sleep(pause)
    return report


def main(argv=None, out=None):
    from ..core.database import DatabaseManager
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Import OpenLyrics songs into ChurchLight Presenter")
    parser.add_argument("source", help="an OpenLyrics .xml file, or a folder of them")
    parser.add_argument("--db", required=True, help="database file")
    parser.add_argument("--force", action="store_true", help="import songs even if already there")
    args = parser.parse_args(argv)
    try:
        with DatabaseManager(args.db) as db:
            report = import_openlyrics(db, args.source, force=args.force)
    except ImporterError as err:
        print(f"Import failed: {err}", file=out)
        return 1
    print(f"Imported {len(report.imported)} songs, skipped {report.duplicates} already there, "
          f"{len(report.failed)} failed", file=out)
    for name, message in report.failed:
        print(f"  FAILED {name}: {message}", file=out)
    for name, message in report.warnings:
        print(f"  note {name}: {message}", file=out)
    return 0 if report.imported or not report.failed else 1


if __name__ == "__main__":
    sys.exit(main())
