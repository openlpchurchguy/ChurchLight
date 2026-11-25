# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Export songs and slideshows, so they can move to another computer (or another program).

Songs leave as OpenLyrics (.xml, which this program and OpenLP read back) or plain text (.txt, for
email and printing; this program reads its own text back too). Several songs travel in one .zip.

Slideshows leave as a "bundle": one .zip holding the pictures and a `slideshows.json` that records
the page order, speaker notes, skipped pages, timing, transitions and picture size. The matching
reader is importers/bundle.py.

Pure Python, no window. The functions that read the database take the connection's lock from the
caller; the slow part (writing pictures into the zip) works from a snapshot and needs no database.
"""
import io
import json
import re
import tempfile
import zipfile
from .themes import is_unchanged_starter, theme_entry
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

BUNDLE_FORMAT = "churchlight-slideshows"
BUNDLE_VERSION = 1
BUNDLE_MANIFEST = "slideshows.json"
THEMES_FORMAT = "churchlight-themes"
THEMES_VERSION = 1
THEMES_MANIFEST = "themes.json"
OPENLYRICS_NS = "http://openlyrics.info/namespace/2009/song"
# Our own attribute on each <verse>: the part's real name ("Tag", "Outro", "Chorus (last time)"), which OpenLyrics has
# no place for. Other programs ignore it; ChurchLight reads it back so a song keeps its part names.
CHURCHLIGHT_NS = "urn:churchlight-presenter:openlyrics-extension"

SONG_FORMATS = {"xml": ("application/xml; charset=utf-8", ".xml"),
                "txt": ("text/plain; charset=utf-8", ".txt")}
MAX_EXPORT_ITEMS = 5000

# section type -> OpenLyrics verse-name letter
_LETTER = {"verse": "v", "chorus": "c", "bridge": "b", "pre_chorus": "p", "intro": "i", "outro": "e"}


class ExportError(Exception):
    """Nothing to export, or something that cannot be exported; the message says why."""


def safe_name(text, fallback="file"):
    """A file name that every operating system accepts."""
    name = re.sub(r"[^\w.\- ()]", "_", str(text or ""), flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .")[:100].strip(" .")
    return name or fallback


def _unique(name, suffix, taken):
    candidate, n = f"{name}{suffix}", 2
    while candidate.lower() in taken:
        candidate, n = f"{name} ({n}){suffix}", n + 1
    taken.add(candidate.lower())
    return candidate


# ---------------------------------------------------------------------------- songs
def _song_parts(db, song_id):
    """(song row, sections in editor order, play order as a list of section rows)."""
    song = db.get_song(song_id)
    if song is None:
        raise ExportError("That song no longer exists")
    sections = list(db.get_sections(song_id))
    by_id = {s["id"]: s for s in sections}
    arrangement = db.get_default_arrangement(song_id)
    order = []
    if arrangement is not None:
        order = [by_id[r["section_id"]] for r in db.get_arrangement_sections(arrangement["id"])
                 if r["section_id"] in by_id]
    return song, sections, order or sections


def _verse_names(sections):
    """{section id: OpenLyrics verse name} such as v1, c1, c2, b1. Unique, the letter says the type, and (as in
    OpenLP, which always writes a number) there is always a number: the part's own when its label ends in one
    ("Verse 2" -> v2), otherwise the next free one for that type."""
    used, names = set(), {}
    for section in sections:
        letter = _LETTER.get(section["section_type"], "o")
        digits = re.search(r"(\d+)\s*$", section["label"] or "")
        candidates = []
        if digits:
            candidates.append(f"{letter}{int(digits.group(1))}")
        candidates.extend(f"{letter}{n}" for n in range(1, 1000))
        name = next(c for c in candidates if c not in used)
        used.add(name)
        names[section["id"]] = name
    return names


def song_to_openlyrics(db, song_id):
    song, sections, order = _song_parts(db, song_id)
    names = _verse_names(sections)
    ET.register_namespace("", OPENLYRICS_NS)
    ET.register_namespace("cl", CHURCHLIGHT_NS)
    q = lambda tag: f"{{{OPENLYRICS_NS}}}{tag}"       # noqa: E731
    root = ET.Element(q("song"), {"version": "0.8", "createdIn": "ChurchLight Presenter",
                                  "modifiedIn": "ChurchLight Presenter"})
    props = ET.SubElement(root, q("properties"))
    ET.SubElement(ET.SubElement(props, q("titles")), q("title")).text = song["title"]
    if song["author"]:
        authors = ET.SubElement(props, q("authors"))
        for author in [a.strip() for a in song["author"].split(",") if a.strip()]:
            ET.SubElement(authors, q("author")).text = author
    if song["copyright"]:
        ET.SubElement(props, q("copyright")).text = song["copyright"]
    if song["ccli_number"]:
        ET.SubElement(props, q("ccliNo")).text = song["ccli_number"]
    if song["default_key"]:
        ET.SubElement(props, q("key")).text = song["default_key"]
    ET.SubElement(props, q("verseOrder")).text = " ".join(names[s["id"]] for s in order)
    if song["notes"]:
        ET.SubElement(ET.SubElement(props, q("comments")), q("comment")).text = song["notes"]
    lyrics = ET.SubElement(root, q("lyrics"))
    for section in sections:
        verse = ET.SubElement(lyrics, q("verse"), {"name": names[section["id"]]})
        if (section["label"] or "").strip():
            verse.set(f"{{{CHURCHLIGHT_NS}}}label", section["label"].strip())
        lines = ET.SubElement(verse, q("lines"))
        pieces = (section["lyrics"] or "").replace("\r\n", "\n").split("\n")
        lines.text = pieces[0]
        last = None
        for piece in pieces[1:]:
            last = ET.SubElement(lines, q("br"))
            last.tail = piece
    ET.indent(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def song_to_text(db, song_id):
    """Title, then every part in the order it is sung (a repeated chorus is written out again), then
    the author, copyright and CCLI lines. Reads back through the plain-text importer."""
    song, _sections, order = _song_parts(db, song_id)
    blocks = [song["title"]]
    for section in order:
        words = (section["lyrics"] or "").replace("\r\n", "\n").strip("\n")
        blocks.append(f"{section['label']}\n{words}")
    info = []
    if song["author"]:
        info.append(f"Author: {song['author']}")
    if song["copyright"]:
        info.append(song["copyright"] if re.match(r"\s*(©|\(c\)|copyright)", song["copyright"], re.I)
                    else f"© {song['copyright']}")
    if song["ccli_number"]:
        info.append(f"CCLI Song # {song['ccli_number']}")
    if info:
        blocks.append("\n".join(info))
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def export_songs(db, song_ids, fmt):
    """(file name, content type, bytes) for one song, or a .zip of several."""
    if fmt not in SONG_FORMATS:
        raise ExportError("Choose XML (OpenLyrics) or text")
    ids = list(dict.fromkeys(int(i) for i in song_ids))[:MAX_EXPORT_ITEMS]
    if not ids:
        raise ExportError("Choose at least one song to export")
    content_type, suffix = SONG_FORMATS[fmt]
    write = song_to_openlyrics if fmt == "xml" else song_to_text
    taken = set()
    files = []
    for song_id in ids:
        song = db.get_song(song_id)
        if song is None:
            continue
        files.append((_unique(safe_name(song["title"], "Song"), suffix, taken), write(db, song_id)))
    if not files:
        raise ExportError("Those songs no longer exist")
    if len(files) == 1:
        return files[0][0], content_type, files[0][1]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return f"ChurchLight songs {date.today().isoformat()}.zip", "application/zip", buffer.getvalue()


# ---------------------------------------------------------------------------- slideshows
def slideshow_snapshot(db, presentation_ids):
    """Everything about the chosen slideshows that lives in the database, as plain data.

    Quick, and the only part that needs the database; `write_bundle` then works from this."""
    ids = list(dict.fromkeys(int(i) for i in presentation_ids))[:MAX_EXPORT_ITEMS]
    shows = []
    for presentation_id in ids:
        row = db.get_presentation(presentation_id)
        if row is None:
            continue
        pages = []
        for page in db.get_presentation_pages(presentation_id):
            clip = db.get_media_asset(page["media_id"]) if page["media_id"] else None
            if clip is not None and clip["media_type"] == "video":
                raise ExportError(f"{row['name']!r} has a video page, and slideshows with video cannot be exported yet. To use it on another "
                                  "computer, add the video files to the Media Library there and put them into the slideshow again "
                                  "(Add from, then Media Library)")
            pages.append({"image": page["image_path"], "notes": page["notes"] or "", "title": page["title"] or "",
                          "skipped": bool(page["skipped"]), "auto_seconds": page["auto_seconds"],
                          "transition": page["transition"], "transition_ms": page["transition_ms"]})
        shows.append({"name": row["name"], "transition": row["transition"], "transition_ms": row["transition_ms"],
                      "auto_seconds": row["auto_seconds"], "end_action": row["end_action"],
                      "image_fit": row["image_fit"], "pages": pages})
    if not shows:
        raise ExportError("Choose at least one slideshow to export")
    return shows


def write_bundle(shows, media_root, target):
    """Write the snapshot and its pictures into the zip file object `target`. Returns the picture count."""
    if media_root is None:
        raise ExportError("Exporting slideshows needs a media folder; start the program with --media")
    root = Path(media_root).resolve()
    manifest = {"format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "created": date.today().isoformat(),
                "slideshows": []}
    count = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for number, show in enumerate(shows, start=1):
            entry = {k: v for k, v in show.items() if k != "pages"}
            entry["pages"] = []
            for page_number, page in enumerate(show["pages"], start=1):
                source = (root / page["image"]).resolve()
                if root not in source.parents or not source.is_file():
                    raise ExportError(f"The picture for page {page_number} of {show['name']!r} "
                                      f"is missing from the media folder")
                inside = f"pictures/{number:03d}/page-{page_number:03d}{source.suffix.lower()}"
                zf.write(source, inside)
                count += 1
                entry["pages"].append(dict(page, image=inside))
            manifest["slideshows"].append(entry)
        zf.writestr(BUNDLE_MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))
    return count


def bundle_file_name(shows):
    if len(shows) == 1:
        return safe_name(shows[0]["name"], "Slideshow") + ".zip"
    return f"ChurchLight slideshows {date.today().isoformat()}.zip"


def write_bundle_tempfile(shows, media_root):
    """Write the bundle to a temporary file (a deck can be hundreds of megabytes); returns its path.
    The caller deletes it when done."""
    handle = tempfile.NamedTemporaryFile(prefix="churchlight-export-", suffix=".zip", delete=False)
    try:
        with handle:
            write_bundle(shows, media_root, handle)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)


# ---------------------------------------------------------------------------- themes
def theme_snapshot(db, theme_ids=None):
    """The chosen themes (all of them when `theme_ids` is None, without the starters that were never
    changed) as plain data, each with the media path of its background picture if it has one."""
    rows = list(db.list_themes())
    if theme_ids is not None:
        wanted = set(int(i) for i in theme_ids)
        rows = [r for r in rows if r["id"] in wanted]
    themes = []
    for row in rows:
        settings = db.get_theme_settings(row["id"]) or {}
        if theme_ids is None and is_unchanged_starter(row["name"], settings):
            continue
        entry = theme_entry(row["name"], settings)
        entry["picture_path"] = entry["style"].pop("background_image", None)
        themes.append(entry)
    if not themes:
        raise ExportError("There are no themes of your own to export" if theme_ids is None
                          else "Choose at least one theme to export")
    return themes


def write_themes_bundle(themes, media_root, target):
    """Write the snapshot, and the pictures it uses, into the zip file object `target`."""
    root = Path(media_root).resolve() if media_root is not None else None
    manifest = {"format": THEMES_FORMAT, "version": THEMES_VERSION, "created": date.today().isoformat(),
                "themes": []}
    stored = {}
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as zf:
        for theme in themes:
            entry = {k: v for k, v in theme.items() if k != "picture_path"}
            path = theme.get("picture_path")
            if path and root is not None:
                source = (root / path).resolve()
                if root in source.parents and source.is_file():
                    if path not in stored:
                        stored[path] = f"pictures/{len(stored) + 1:03d}{source.suffix.lower()}"
                        zf.write(source, stored[path])
                    entry["picture"] = stored[path]
            manifest["themes"].append(entry)
        zf.writestr(THEMES_MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False))


def themes_file_name(themes):
    if len(themes) == 1:
        return safe_name(themes[0]["name"], "Theme") + " (theme).zip"
    return f"ChurchLight themes {date.today().isoformat()}.zip"


def write_themes_tempfile(themes, media_root):
    """Write the themes file to a temporary file; returns its path. The caller deletes it when done."""
    handle = tempfile.NamedTemporaryFile(prefix="churchlight-themes-", suffix=".zip", delete=False)
    try:
        with handle:
            write_themes_bundle(themes, media_root, handle)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise
    return Path(handle.name)
