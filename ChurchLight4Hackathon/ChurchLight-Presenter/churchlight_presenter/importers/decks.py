# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Import slide decks once, so nothing else is needed at service time.

A deck becomes a folder of page images, recorded as a presentation in the database.
From then on the program only ever shows images.

    PDF   -> page images (rendered with pypdfium2, a plain `pip install`; poppler's
             `pdftoppm` is used instead when pypdfium2 is not installed)
    folder of images -> copied in as pages, in natural filename order

Images are stored under `<media_root>/decks/<id>/page-001.png`, and the
database keeps the path *relative to media_root*, which is also what the output
server serves. Re-importing the same file is detected by its hash.

Command line, for trying it by hand:

    python -m churchlight_presenter.importers.decks deck.pdf --db church.db --media media
"""
import argparse
import hashlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .common import ImporterError
from . import pdfrender

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class DeckImportError(ImporterError):
    """Something about the file or the machine stopped the import; the message says what to do."""


@dataclass(frozen=True)
class ImportResult:
    presentation_id: int
    page_count: int
    notes_count: int
    duplicate: bool = False          # True: this exact file was already imported, nothing was added
    warnings: tuple = ()


def _natural_key(path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", Path(path).name)]


def render_pdf_with_poppler(pdf, out_dir, dpi=150):
    """Render each PDF page to a PNG with `pdftoppm`; returns the files in page order."""
    exe = shutil.which("pdftoppm")
    if exe is None:
        raise DeckImportError(
            "Importing PDFs needs poppler's `pdftoppm`. On Ubuntu: sudo apt install "
            "poppler-utils. On Windows: install poppler and add its bin folder to PATH.")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([exe, "-png", "-r", str(int(dpi)), str(pdf), str(out_dir / "page")],
                       check=True, capture_output=True, timeout=900)
    except subprocess.CalledProcessError as err:
        raise DeckImportError("The PDF could not be read: "
                           + err.stderr.decode("utf-8", "replace").strip()[:300]) from err
    except subprocess.TimeoutExpired as err:
        raise DeckImportError("Rendering the PDF took too long and was stopped") from err
    pages = sorted(out_dir.glob("page-*.png"), key=_natural_key)
    if not pages:
        raise DeckImportError("The PDF has no pages")
    return pages


def _sha256(paths):
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()


def _already_imported(db, file_hash):
    row = db.find_presentation_by_hash(file_hash)
    if row is None:
        return None
    with_notes = sum(1 for page in db.get_presentation_pages(row["id"]) if page["notes"])
    return ImportResult(row["id"], row["page_count"], with_notes, duplicate=True,
                        warnings=(f"{row['name']!r} was already imported; nothing was added",))


def _finish(db, media_root, page_files, notes, name, source_type, source_path, file_hash,
            warnings):
    """Move rendered pages into place and record them; undo the move if recording fails."""
    media_root = Path(media_root)
    rel_dir = Path("decks") / file_hash[:12]
    final = media_root / rel_dir
    if final.exists():
        # Pictures kept by another deck (a copy or split of a deleted deck) must not be wiped.
        in_use = db.conn.execute("SELECT 1 FROM presentation_pages WHERE image_path LIKE ? LIMIT 1",
                                 (rel_dir.as_posix() + "/%",)).fetchone()
        if in_use:
            rel_dir = Path("decks") / f"{file_hash[:12]}-{secrets.token_hex(3)}"
            final = media_root / rel_dir
        else:
            shutil.rmtree(final)                 # left over from an import that never finished
    final.mkdir(parents=True)
    try:
        entries = []
        for number, page in enumerate(page_files, start=1):
            target = final / f"page-{number:03d}{Path(page).suffix.lower()}"
            shutil.move(str(page), target)
            entries.append(((rel_dir / target.name).as_posix(),
                            notes[number - 1] if notes else None))
        pres_id = db.add_presentation(name, entries, source_type=source_type,
                                      source_path=str(source_path), file_hash=file_hash)
    except BaseException:
        shutil.rmtree(final, ignore_errors=True)
        raise
    return ImportResult(pres_id, len(entries), sum(1 for _, n in entries if n),
                        warnings=tuple(warnings))


def import_deck(db, source, media_root, *, name=None, dpi=150, renderer=None, force=False):
    """Import a .pdf as a presentation. Returns an ImportResult.

    `force` imports again even if this exact file was imported before.
    `renderer` can be replaced (tests do, and so could another platform's setup).
    By default pypdfium2 renders, or poppler if that is all there is.
    """
    source = Path(source)
    if not source.is_file():
        raise DeckImportError(f"{source} was not found")
    if source.suffix.lower() != ".pdf":
        raise DeckImportError("Slideshows must be .pdf files, or slide pictures")

    file_hash = _sha256([source])
    if not force:
        existing = _already_imported(db, file_hash)
        if existing:
            return existing

    if renderer is None:
        try:
            renderer = pdfrender.default_renderer(render_pdf_with_poppler)
        except ImporterError as err:
            raise DeckImportError(str(err)) from err
    media_root = Path(media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=media_root, prefix=".import-") as work:
        work = Path(work)
        try:
            pages = renderer(source, work / "pages", dpi)
        except DeckImportError:
            raise
        except ImporterError as err:
            raise DeckImportError(str(err)) from err
        if not pages:
            raise DeckImportError("The slideshow has no pages")
        return _finish(db, media_root, pages, None, name or source.stem,
                       "pdf", source, file_hash, [])


def import_image_folder(db, folder, media_root, *, name=None, force=False):
    """Import a folder of images (already exported slides, say) as a presentation."""
    folder = Path(folder)
    if not folder.is_dir():
        raise DeckImportError(f"{folder} is not a folder")
    images = sorted((p for p in folder.iterdir()
                     if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES), key=_natural_key)
    if not images:
        raise DeckImportError(f"{folder} has no images ({', '.join(sorted(IMAGE_SUFFIXES))})")
    digest = hashlib.sha256()
    digest.update(_sha256(images).encode())
    digest.update("\n".join(p.name for p in images).encode())
    file_hash = digest.hexdigest()
    if not force:
        existing = _already_imported(db, file_hash)
        if existing:
            return existing
    media_root = Path(media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=media_root, prefix=".import-") as work:
        staged = []
        for image in images:                      # copy, so the originals stay where they are
            target = Path(work) / image.name
            shutil.copy2(image, target)
            staged.append(target)
        return _finish(db, media_root, staged, None, name or folder.name,
                       "images", folder, file_hash, [])


# ---------------------------------------------------------------------------
def main(argv=None, out=None):
    from ..core.database import DatabaseManager
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Import a slideshow into ChurchLight Presenter")
    parser.add_argument("source", help=".pdf, or a folder of images")
    parser.add_argument("--db", required=True, help="database file")
    parser.add_argument("--media", required=True, help="media folder (images are stored here)")
    parser.add_argument("--name", help="name in the library (default: the file name)")
    parser.add_argument("--dpi", type=int, default=150)
    parser.add_argument("--force", action="store_true", help="import even if already imported")
    args = parser.parse_args(argv)
    try:
        with DatabaseManager(args.db) as db:
            if Path(args.source).is_dir():
                result = import_image_folder(db, args.source, args.media, name=args.name,
                                             force=args.force)
            else:
                result = import_deck(db, args.source, args.media, name=args.name,
                                     dpi=args.dpi, force=args.force)
    except DeckImportError as err:
        print(f"Import failed: {err}", file=out)
        return 1
    print(f"{'Already there' if result.duplicate else 'Imported'}: presentation "
          f"{result.presentation_id}, {result.page_count} pages, {result.notes_count} with notes",
          file=out)
    for warning in result.warnings:
        print(f"  note: {warning}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
