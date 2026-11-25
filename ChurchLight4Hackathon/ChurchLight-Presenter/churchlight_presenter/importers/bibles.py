# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Import Bible text from Zefania XML or a simple CSV/TSV file.

The program ships no Bible text. Translations differ a lot in licensing, so the
person importing is responsible for having the right to use the file; the
`license_notes` you give is stored with the translation as a reminder.

Formats
    Zefania XML   <XMLBIBLE><BIBLEBOOK bnumber="43"><CHAPTER cnumber="3"><VERS vnumber="16">...
    CSV / TSV     book, chapter, verse, text   (an optional header row is fine; the
                  book may be a name, a common abbreviation such as "Jn", or 1-66)

Partial Bibles (a New Testament only, say) import fine. The whole import is one
transaction: a problem in verse 20,000 leaves nothing half-imported.

    python -m churchlight_presenter.importers.bibles file.xml --abbr DEMO --name "Demo" --db church.db
"""
import argparse
import csv
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..core.scripture_ref import ReferenceParseError, parse_reference
from .common import ImporterError, parse_xml, squash

MAX_FILE_BYTES = 100 * 1024 * 1024


@dataclass
class BibleImportReport:
    translation_id: int
    abbreviation: str
    verse_count: int
    book_count: int
    warnings: list = field(default_factory=list)


def _book_id(label):
    """A book id from a number 1-66, a name, or an abbreviation."""
    label = str(label).strip()
    if label.isdigit():
        number = int(label)
        if 1 <= number <= 66:
            return number
        raise ImporterError(f"Book number {number} is not between 1 and 66")
    try:
        # parse_reference understands every alias the program knows; chapter 1 always exists
        return parse_reference(f"{label} 1").book_id
    except ReferenceParseError as err:
        raise ImporterError(f"Unknown book {label!r}") from err


def _int(text, what, where):
    try:
        number = int(str(text).strip())
    except ValueError:
        raise ImporterError(f"{where}: {what} {text!r} is not a number") from None
    if number < 1:
        raise ImporterError(f"{where}: {what} must be 1 or more")
    return number


# ---------------------------------------------------------------------------
# Readers: each returns (verses, metadata, warnings); verses are (book_id, chapter, verse, text)
# ---------------------------------------------------------------------------
def _verse_text(node):
    """A <VERS>'s words, leaving out footnotes (<NOTE>) and keeping the rest of the markup's text."""
    pieces = [node.text or ""]

    def walk(parent):
        for child in parent:
            if child.tag.upper() != "NOTE":
                pieces.append(child.text or "")
                walk(child)
            pieces.append(child.tail or "")

    walk(node)
    return squash("".join(pieces))


def read_zefania(data, what="the Bible file"):
    root = parse_xml(data, what)
    if root.tag.upper() != "XMLBIBLE":
        raise ImporterError(f"{what} is not Zefania XML (its top element is <{root.tag}>)")
    meta = {"name": root.get("biblename")}
    info = root.find("INFORMATION")
    if info is not None:
        meta["name"] = meta["name"] or squash(info.findtext("title") or "") or None
        meta["language"] = squash(info.findtext("language") or "") or None
        meta["abbreviation"] = squash(info.findtext("identifier") or "") or None
        meta["rights"] = squash(info.findtext("rights") or "") or None
    verses, warnings = [], []
    for book in root.iter("BIBLEBOOK"):
        label = book.get("bnumber") or book.get("bname")
        if not label:
            raise ImporterError("A book in the file has neither a number nor a name")
        book_id = _book_id(label)
        for chapter in book.findall("CHAPTER"):
            c = _int(chapter.get("cnumber"), "chapter", f"Book {label}")
            for vers in chapter.findall("VERS"):
                v = _int(vers.get("vnumber"), "verse", f"Book {label} chapter {c}")
                text = _verse_text(vers)
                if text:
                    verses.append((book_id, c, v, text))
    return verses, meta, warnings


def read_csv(data, what="the Bible file"):
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    first_line = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first_line else ("|" if first_line.count("|") >= 3 else ",")
    verses, warnings = [], []
    for n, row in enumerate(csv.reader(io.StringIO(text), delimiter=delimiter), start=1):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) < 4:
            raise ImporterError(f"{what}, line {n}: expected book, chapter, verse, text")
        book, chapter, verse = row[0], row[1], row[2]
        body = delimiter.join(row[3:])       # a text that contained the delimiter, unquoted
        if n == 1 and not chapter.strip().isdigit():
            continue                         # header row
        where = f"line {n}"
        verses.append((_book_id(book), _int(chapter, "chapter", where),
                       _int(verse, "verse", where), squash(body)))
    return verses, {}, warnings


# ---------------------------------------------------------------------------
def import_bible(db, path, abbreviation=None, name=None, language=None, license_notes=None):
    """Import a Zefania XML (.xml) or CSV/TSV (.csv, .tsv, .txt) file as a new translation."""
    path = Path(path)
    if not path.is_file():
        raise ImporterError(f"{path} was not found")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ImporterError("The file is unreasonably large for a Bible")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".xml":
        verses, meta, warnings = read_zefania(data, path.name)
    elif suffix in (".csv", ".tsv", ".txt"):
        verses, meta, warnings = read_csv(data, path.name)
    else:
        raise ImporterError("Bible files must be .xml (Zefania) or .csv/.tsv")
    if not verses:
        raise ImporterError("The file contains no verses")

    abbreviation = (abbreviation or meta.get("abbreviation") or path.stem).strip().upper()
    name = (name or meta.get("name") or abbreviation).strip()
    language = language or meta.get("language") or "en"
    license_notes = license_notes or meta.get("rights")
    if db.get_translation(abbreviation) is not None:
        raise ImporterError(f"A translation named {abbreviation} is already installed "
                            "(choose another abbreviation, or remove the old one first)")

    seen, unique = set(), []
    for book_id, chapter, verse, text in verses:
        key = (book_id, chapter, verse)
        if key in seen:
            warnings.append(f"Verse {book_id}:{chapter}:{verse} appears twice; the first was kept")
            continue
        seen.add(key)
        unique.append((book_id, chapter, verse, text))

    with db.transaction():
        translation_id = db.add_translation(abbreviation, name, language, license_notes)
        db.add_verses(translation_id, unique)
    return BibleImportReport(translation_id, abbreviation, len(unique),
                             len({v[0] for v in unique}), warnings)


def main(argv=None, out=None):
    from ..core.database import DatabaseManager
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Import Bible text into ChurchLight Presenter")
    parser.add_argument("source", help="Zefania .xml, or .csv/.tsv (book, chapter, verse, text)")
    parser.add_argument("--db", required=True, help="database file")
    parser.add_argument("--abbr", help="short name such as KJV (default: from the file)")
    parser.add_argument("--name", help="full name (default: from the file)")
    parser.add_argument("--language", help="language code, default en")
    parser.add_argument("--license", dest="license_notes",
                        help="licensing note stored with the translation")
    args = parser.parse_args(argv)
    try:
        with DatabaseManager(args.db) as db:
            report = import_bible(db, args.source, args.abbr, args.name, args.language,
                                  args.license_notes)
    except (ImporterError, ValueError) as err:
        print(f"Import failed: {err}", file=out)
        return 1
    print(f"Imported {report.abbreviation}: {report.verse_count} verses in "
          f"{report.book_count} books", file=out)
    for warning in report.warnings[:20]:
        print(f"  note: {warning}", file=out)
    if len(report.warnings) > 20:
        print(f"  ...and {len(report.warnings) - 20} more notes", file=out)
    print("  Remember: make sure you have the right to use this translation.", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
