# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Import slideshows from a ChurchLight bundle (the .zip that core/export.py writes).

A bundle holds the pictures and a `slideshows.json` with each slideshow's page order, titles, speaker notes,
skipped pages, timing, transitions and picture size, so a slideshow arrives exactly as it left.

The file is treated as untrusted: only the exact picture names the manifest lists are read, every
name must look like `pictures/<n>/page-<n>.<image type>`, sizes are capped (a "zip bomb" is refused),
each picture must really start like a PNG/JPEG/GIF/WebP, and every setting goes through the same checks
as the program's own screens. A slideshow with the same pictures and settings as one already here is
skipped instead of doubled.

    python -m churchlight_presenter.importers.bundle bundle.zip --db church.db --media media
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core.autoplay import check_autoplay
from ..core.export import BUNDLE_FORMAT, BUNDLE_MANIFEST, BUNDLE_VERSION
from ..core.transitions import check_transition
from .common import ImporterError
from .decks import IMAGE_SUFFIXES, _finish

MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PICTURE_BYTES = 60 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 ** 3
MAX_ENTRIES = 20000
MAX_SLIDESHOWS = 500
MAX_PAGES = 3000
MAX_NOTES = 20000
IMAGE_FITS = ("fit", "fill", "stretch")
_PICTURE_NAME = re.compile(r"^pictures/\d{1,4}/page-\d{1,5}\.(png|jpg|jpeg|gif|webp)$")


@dataclass
class BundleReport:
    imported: list = field(default_factory=list)      # (presentation id, name, page count)
    duplicates: int = 0
    warnings: list = field(default_factory=list)


def _looks_like_picture(head, suffix):
    if suffix == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if suffix == ".gif":
        return head[:4] == b"GIF8"
    if suffix == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    return False


def _text(value, what, limit, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ImporterError(f"{what} must be text")
    value = value.strip() if required else value
    if required and not value:
        raise ImporterError(f"{what} is empty")
    if len(value) > limit:
        raise ImporterError(f"{what} is too long")
    return value


def _clean_show(raw, number):
    """Check one slideshow entry from the manifest; returns it with every value known good."""
    if not isinstance(raw, dict):
        raise ImporterError(f"Slideshow {number} is not an object")
    name = _text(raw.get("name"), f"The name of slideshow {number}", 200)
    try:
        transition, transition_ms = check_transition(raw.get("transition", "none"), raw.get("transition_ms", 500))
        auto_seconds, end_action = check_autoplay(raw.get("auto_seconds", 0), raw.get("end_action", "stop"))
    except ValueError as err:
        raise ImporterError(f"{name!r}: {err}") from None
    fit = raw.get("image_fit", "fit")
    if fit not in IMAGE_FITS:
        raise ImporterError(f"{name!r}: the picture size must be one of {', '.join(IMAGE_FITS)}")
    pages_raw = raw.get("pages")
    if not isinstance(pages_raw, list) or not 1 <= len(pages_raw) <= MAX_PAGES:
        raise ImporterError(f"{name!r} needs between 1 and {MAX_PAGES} pages")
    pages = []
    for index, page in enumerate(pages_raw, start=1):
        label = f"{name!r} page {index}"
        if not isinstance(page, dict):
            raise ImporterError(f"{label} is not an object")
        image = page.get("image")
        if not isinstance(image, str) or not _PICTURE_NAME.match(image):
            raise ImporterError(f"{label} names a picture the bundle should not contain")
        own_transition = own_ms = None
        if page.get("transition") is not None or page.get("transition_ms") is not None:
            try:
                own_transition, own_ms = check_transition(page.get("transition"), page.get("transition_ms"))
            except ValueError as err:
                raise ImporterError(f"{label}: {err}") from None
        own_seconds = page.get("auto_seconds")
        if own_seconds is not None:
            try:
                check_autoplay(own_seconds, "stop")
            except ValueError as err:
                raise ImporterError(f"{label}: {err}") from None
        skipped = page.get("skipped", False)
        if not isinstance(skipped, bool):
            raise ImporterError(f"{label}: 'skipped' must be true or false")
        pages.append({"image": image, "notes": _text(page.get("notes"), f"The notes of {label}", MAX_NOTES, False),
                      "title": " ".join(_text(page.get("title"), f"The title of {label}", 60, False).split()),
                      "skipped": skipped, "auto_seconds": own_seconds,
                      "transition": own_transition, "transition_ms": own_ms})
    if all(p["skipped"] for p in pages):
        raise ImporterError(f"{name!r} has every page skipped")
    return {"name": name, "transition": transition, "transition_ms": transition_ms,
            "auto_seconds": auto_seconds, "end_action": end_action, "image_fit": fit, "pages": pages}


def read_manifest(zf):
    """The checked list of slideshows in an open bundle."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ImporterError("The bundle holds far too many files")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise ImporterError("The bundle is unreasonably large")
    try:
        info = zf.getinfo(BUNDLE_MANIFEST)
    except KeyError:
        raise ImporterError("This zip is not a ChurchLight slideshow bundle (it has no slideshows.json)") from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ImporterError("The bundle's slideshows.json is unreasonably large")
    try:
        manifest = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ImporterError("The bundle's slideshows.json could not be read") from None
    if not isinstance(manifest, dict) or manifest.get("format") != BUNDLE_FORMAT:
        raise ImporterError("This zip is not a ChurchLight slideshow bundle")
    version = manifest.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version > BUNDLE_VERSION:
        raise ImporterError("This bundle was made by a newer version of ChurchLight; update this program first")
    shows = manifest.get("slideshows")
    if not isinstance(shows, list) or not 1 <= len(shows) <= MAX_SLIDESHOWS:
        raise ImporterError("The bundle holds no slideshows")
    return [_clean_show(show, n) for n, show in enumerate(shows, start=1)]


def _read_picture(zf, name):
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise ImporterError(f"The bundle is missing {name}") from None
    if info.file_size > MAX_PICTURE_BYTES:
        raise ImporterError(f"{name} is unreasonably large for a slide picture")
    with zf.open(info) as f:
        data = f.read(MAX_PICTURE_BYTES + 1)
    if len(data) > MAX_PICTURE_BYTES:
        raise ImporterError(f"{name} is unreasonably large for a slide picture")
    suffix = "." + name.rsplit(".", 1)[1]
    if suffix not in IMAGE_SUFFIXES or not _looks_like_picture(data[:16], suffix):
        raise ImporterError(f"{name} is not a picture of the kind it claims to be")
    return data


def _settings_key(show):
    return json.dumps({k: v for k, v in show.items() if k != "pages"} |
                      {"pages": [{k: v for k, v in p.items() if k != "image"} for p in show["pages"]]},
                      sort_keys=True)


def import_bundle(db, path, media_root):
    """Add every slideshow in the bundle at `path`. Returns a BundleReport. All or nothing per slideshow."""
    if media_root is None:
        raise ImporterError("Importing slideshows needs a media folder; start the program with --media")
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ImporterError(f"{path.name} is not a zip file")
    media_root = Path(media_root)
    media_root.mkdir(parents=True, exist_ok=True)
    report = BundleReport()
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise ImporterError(f"{path.name} could not be opened as a zip file") from None
    with zf:
        shows = read_manifest(zf)
        for show in shows:
            digest = hashlib.sha256(_settings_key(show).encode())
            with tempfile.TemporaryDirectory(dir=media_root, prefix=".import-") as work:
                staged = []
                for number, page in enumerate(show["pages"], start=1):
                    data = _read_picture(zf, page["image"])
                    digest.update(hashlib.sha256(data).digest())
                    target = Path(work) / f"page-{number:05d}{Path(page['image']).suffix.lower()}"
                    target.write_bytes(data)
                    staged.append(target)
                file_hash = digest.hexdigest()
                if db.find_presentation_by_hash(file_hash) is not None:
                    report.duplicates += 1
                    continue
                notes = [p["notes"] or None for p in show["pages"]]
                result = _finish(db, media_root, staged, notes, show["name"], "images",
                                 f"bundle:{path.name}", file_hash, [])
            try:
                _apply_settings(db, result.presentation_id, show)
            except BaseException:
                _discard(db, media_root, result.presentation_id)
                raise
            report.imported.append((result.presentation_id, show["name"], result.page_count))
    return report


def _apply_settings(db, presentation_id, show):
    db.set_presentation_transition(presentation_id, show["transition"], show["transition_ms"])
    db.set_presentation_autoplay(presentation_id, show["auto_seconds"], show["end_action"])
    db.set_presentation_image_fit(presentation_id, show["image_fit"])
    rows = db.get_presentation_pages(presentation_id)
    for row, page in zip(rows, show["pages"]):
        if page["title"]:
            db.set_presentation_page_title(presentation_id, row["id"], page["title"])
    skipped = [row["id"] for row, page in zip(rows, show["pages"]) if page["skipped"]]
    if skipped:
        db.set_presentation_pages_skipped(presentation_id, skipped, True)
    groups = {}
    for row, page in zip(rows, show["pages"]):
        key = (page["auto_seconds"], page["transition"], page["transition_ms"])
        if key != (None, None, None):
            groups.setdefault(key, []).append(row["id"])
    for (seconds, transition, ms), ids in groups.items():
        db.set_presentation_pages_timing(presentation_id, ids, auto_seconds=seconds,
                                         transition=transition, transition_ms=ms)


def _discard(db, media_root, presentation_id):
    pages = db.get_presentation_pages(presentation_id)
    folders = {Path(p["image_path"]).parent for p in pages}
    db.delete_presentation(presentation_id)
    for folder in folders:
        shutil.rmtree(Path(media_root) / folder, ignore_errors=True)


# ---------------------------------------------------------------------------
def main(argv=None, out=None):
    from ..core.database import DatabaseManager
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Import slideshows from a ChurchLight bundle (.zip).")
    parser.add_argument("bundle")
    parser.add_argument("--db", default="churchlight.db")
    parser.add_argument("--media", default="media")
    args = parser.parse_args(argv)
    db = DatabaseManager(args.db)
    try:
        report = import_bundle(db, args.bundle, args.media)
    except ImporterError as err:
        print(f"Could not import: {err}", file=out)
        return 1
    finally:
        db.close()
    for _id, name, pages in report.imported:
        print(f"Added {name!r} ({pages} pages)", file=out)
    if report.duplicates:
        print(f"{report.duplicates} already here; skipped", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
