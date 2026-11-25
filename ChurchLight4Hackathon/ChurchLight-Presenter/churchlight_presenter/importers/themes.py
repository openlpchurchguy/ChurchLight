# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Import themes from a ChurchLight themes file (the .zip that core/export.py writes).

A themes file holds `themes.json` (each theme's name, kind, look and slide settings) and the
background pictures the themes use. It is treated as untrusted: only the exact picture names the
manifest lists are read, they must really be pictures, sizes are capped, and every setting goes
through the same checks as the theme screens. A setting this version does not know (the file came from
a newer ChurchLight) is skipped and reported; the rest of the theme still arrives. A theme with the same
name and look as one already here is skipped; the same name with a different look arrives as "Name (2)".
Nothing becomes a default or is attached to a service.

    python -m churchlight_presenter.importers.themes themes.zip --db church.db --media media
"""
import argparse
import hashlib
import json
import re
import secrets
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core.export import THEMES_FORMAT, THEMES_MANIFEST, THEMES_VERSION
from ..core.themes import (BUILD_KEYS, KINDS, STYLE_KEYS, style_for_kind, theme_entry, validate_build,
                           validate_style)
from .bundle import MAX_ENTRIES, MAX_MANIFEST_BYTES, MAX_PICTURE_BYTES, _looks_like_picture
from .common import ImporterError

MAX_THEMES = 500
MAX_TOTAL_BYTES = 512 * 1024 ** 2
MAX_NAME = 80
_PICTURE_NAME = re.compile(r"^pictures/\d{1,4}\.(png|jpg|jpeg|gif|webp)$")


@dataclass
class ThemesReport:
    added: list = field(default_factory=list)          # names as they now appear in the program
    duplicates: int = 0
    warnings: list = field(default_factory=list)


def read_manifest(zf):
    """The list of theme entries in an open themes file (each still to be cleaned)."""
    infos = zf.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ImporterError("The file holds far too many files")
    if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
        raise ImporterError("The file is unreasonably large")
    try:
        info = zf.getinfo(THEMES_MANIFEST)
    except KeyError:
        raise ImporterError("This zip is not a ChurchLight themes file (it has no themes.json)") from None
    if info.file_size > MAX_MANIFEST_BYTES:
        raise ImporterError("The themes.json in this file is unreasonably large")
    try:
        manifest = json.loads(zf.read(info).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ImporterError("The themes.json in this file could not be read") from None
    if not isinstance(manifest, dict) or manifest.get("format") != THEMES_FORMAT:
        raise ImporterError("This zip is not a ChurchLight themes file")
    version = manifest.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version > THEMES_VERSION:
        raise ImporterError("This file was made by a newer version of ChurchLight; update this program first")
    themes = manifest.get("themes")
    if not isinstance(themes, list) or not 1 <= len(themes) <= MAX_THEMES:
        raise ImporterError("The file holds no themes")
    return themes


def _clean(raw, number, warnings):
    """(name, kind, style, build, picture name or None) with every value known good, or None to skip the theme."""
    if not isinstance(raw, dict):
        warnings.append(f"Theme {number} was skipped: it is not in a form this program understands")
        return None
    name = raw.get("name")
    name = re.sub(r"\s+", " ", name).strip() if isinstance(name, str) else ""
    if not name:
        warnings.append(f"Theme {number} was skipped: it has no name")
        return None
    name = name[:MAX_NAME].strip()
    kind = raw.get("kind", "projector")
    if kind not in KINDS:
        warnings.append(f"{name}: skipped, it is a kind of theme this version does not have")
        return None
    style, dropped = {}, []
    source = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    for key, value in source.items():
        if key not in STYLE_KEYS:
            dropped.append(key)
            continue
        try:
            style.update(validate_style({key: value}))
        except ValueError:
            dropped.append(key)
    try:
        validate_style(style)                   # the margins must also work together
    except ValueError:
        for key in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
            if style.pop(key, None) is not None:
                dropped.append(key)
    build = {}
    source = raw.get("build") if isinstance(raw.get("build"), dict) else {}
    for key, value in source.items():
        try:
            build.update(validate_build({key: value}))
        except ValueError:
            dropped.append(key)
    if dropped:
        warnings.append(f"{name}: left out settings this version does not use ({', '.join(sorted(set(dropped)))})")
    picture = raw.get("picture")
    if picture is not None and (not isinstance(picture, str) or not _PICTURE_NAME.match(picture)):
        warnings.append(f"{name}: its background picture was left out")
        picture = None
    if kind != "projector":
        picture = None
    return name, kind, style, build, picture


def _read_picture(zf, name):
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_PICTURE_BYTES:
        return None
    with zf.open(info) as f:
        data = f.read(MAX_PICTURE_BYTES + 1)
    suffix = "." + name.rsplit(".", 1)[1]
    if len(data) > MAX_PICTURE_BYTES or not _looks_like_picture(data[:16], suffix):
        return None
    return data


def _same_picture(media_root, stored_path, data):
    if not stored_path or media_root is None or data is None:
        return stored_path is None and data is None
    try:
        return hashlib.sha256((Path(media_root) / stored_path).read_bytes()).digest() == hashlib.sha256(data).digest()
    except OSError:
        return False


def import_themes(db, path, media_root):
    """Add the themes in the file at `path`. Returns a ThemesReport."""
    path = Path(path)
    if not zipfile.is_zipfile(path):
        raise ImporterError(f"{path.name} is not a zip file")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise ImporterError(f"{path.name} could not be opened as a zip file") from None
    report = ThemesReport()
    pictures = {}                                    # picture name in the file -> media path it was stored at
    with zf:
        raw_themes = read_manifest(zf)
        existing = {}
        for row in db.list_themes():
            existing.setdefault(row["name"].lower(), []).append((row["name"], db.get_theme_settings(row["id"]) or {}))
        taken = {n for n in existing}
        for number, raw in enumerate(raw_themes, start=1):
            cleaned = _clean(raw, number, report.warnings)
            if cleaned is None:
                continue
            name, kind, style, build, picture = cleaned
            data = _read_picture(zf, picture) if picture else None
            if picture and data is None:
                report.warnings.append(f"{name}: its background picture could not be used and was left out")
            # The same theme may already be here under its own name or as an earlier "Name (2)" import of it.
            candidates = [entry for key, entries in existing.items() for entry in entries
                          if key == name.lower() or re.fullmatch(re.escape(name.lower()) + r" \(\d+\)", key)]
            if any(_is_same(entry, kind, style, build, data, media_root) for entry in candidates):
                report.duplicates += 1
                continue
            final, n = name, 2
            while final.lower() in taken:
                suffix = f" ({n})"
                final, n = name[:MAX_NAME - len(suffix)].rstrip() + suffix, n + 1
            written = None
            if data is not None:
                if media_root is None:
                    report.warnings.append(f"{name}: its background picture was left out (no media folder)")
                elif picture in pictures:
                    style["background_image"] = pictures[picture]
                else:
                    folder = Path(media_root) / "backgrounds"
                    folder.mkdir(parents=True, exist_ok=True)
                    stored = f"backgrounds/{secrets.token_hex(8)}.{picture.rsplit('.', 1)[1]}"
                    (Path(media_root) / stored).write_bytes(data)
                    pictures[picture] = written = stored
                    style["background_image"] = stored
            settings = {"kind": kind, "style": style_for_kind(kind, style)}
            settings.update(build)
            try:
                db.add_theme(final, settings)
            except BaseException:
                if written:
                    (Path(media_root) / written).unlink(missing_ok=True)
                raise
            taken.add(final.lower())
            existing.setdefault(final.lower(), []).append((final, settings))
            report.added.append(final)
    return report


def _is_same(entry, kind, style, build, data, media_root):
    """Does the theme already here (name, settings) match the incoming one, picture included?"""
    name, settings = entry
    have = theme_entry(name, settings)
    stored = have["style"].pop("background_image", None)
    want = theme_entry(name, {"kind": kind, "style": style_for_kind(kind, style), **build})
    want["style"].pop("background_image", None)
    return (have["kind"], have["style"], have["build"]) == (want["kind"], want["style"], want["build"]) \
        and _same_picture(media_root, stored, data)


def main(argv=None, out=None):
    from ..core.database import DatabaseManager
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Import themes from a ChurchLight themes file (.zip).")
    parser.add_argument("file")
    parser.add_argument("--db", default="churchlight.db")
    parser.add_argument("--media", default="media")
    args = parser.parse_args(argv)
    db = DatabaseManager(args.db)
    try:
        report = import_themes(db, args.file, args.media)
    except ImporterError as err:
        print(f"Could not import: {err}", file=out)
        return 1
    finally:
        db.close()
    for name in report.added:
        print(f"Added {name!r}", file=out)
    if report.duplicates:
        print(f"{report.duplicates} already here; skipped", file=out)
    for note in report.warnings:
        print(f"Note: {note}", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
