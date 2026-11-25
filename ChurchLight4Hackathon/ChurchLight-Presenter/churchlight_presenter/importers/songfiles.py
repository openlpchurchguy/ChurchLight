# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Song files that are not OpenLyrics: plain text (including CCLI SongSelect's lyrics download),
OpenSong and ChordPro.

Each reader turns one file into the same `ParsedSong` the OpenLyrics reader makes, so saving,
duplicate skipping and the import report are shared. Chords are dropped everywhere.

`parse_song_file(path)` looks at the file name and the first bytes and picks the reader:

    .txt                     plain lyrics, headings as `Verse 1` or `[Verse 1]` (SongSelect style)
    .cho .chordpro .crd .chopro   ChordPro
    .xml or no extension     OpenLyrics, or OpenSong when its <lyrics> holds plain text

A song with no recognizable headings imports as Verse 1, Verse 2 ... one per blank-line block, and can
be renamed in the editor afterwards.
"""
import re
from pathlib import Path

from .common import ImporterError, parse_xml, squash
from .songs import ParsedSong, _label_for, parse_openlyrics

SONG_SUFFIXES = (".xml", ".txt", ".cho", ".chordpro", ".crd", ".chopro", "")
CHORDPRO_SUFFIXES = {".cho", ".chordpro", ".crd", ".chopro"}

_HEADING_WORDS = (r"verse|chorus|refrain|bridge|pre[- ]?chorus|intro|outro|ending|tag|interlude|"
                  r"instrumental|coda|turnaround|vamp|v\d|c\d|b\d|p\d")
_PLAIN_HEADING = re.compile(rf"^\s*\[?\s*((?:{_HEADING_WORDS})\s*\d*[a-z]?)\s*\]?\s*:?\s*$", re.IGNORECASE)
_CCLI_LINE = re.compile(r"^\s*CCLI\s*Song\s*(?:#|No\.?|Number)?\s*:?\s*(\d+)", re.IGNORECASE)
_LICENSE_LINE = re.compile(r"^\s*(CCLI\s*License|For use solely with|Used by permission|All rights reserved)", re.IGNORECASE)
_AUTHOR_LINE = re.compile(r"^\s*(?:authors?|words and music|words|music|written by)\s*:\s*(\S.*)$", re.IGNORECASE)
_COPYRIGHT_LINE = re.compile(r"^\s*(©|\(c\)|copyright)", re.IGNORECASE)


def decode_text(raw):
    """File bytes to text: UTF-8 (with or without a BOM), else Windows-1252, which never fails."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _clean_block(lines):
    lines = [line.rstrip() for line in lines]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _add_section(parsed, used, by_text, label, lyrics):
    """Add a part. A heading repeated with the same words only repeats it in the order; the same
    heading with different words gets "(2)" so every part name stays unique."""
    label = squash(label)
    key = (label.lower(), squash(lyrics).lower())
    if key in by_text:
        parsed.order.append(by_text[key])
        return
    if re.fullmatch(r"[vcbpie]\d*", label.replace(" ", ""), re.IGNORECASE):
        name, section_type = _label_for(label.replace(" ", ""), used)      # "V1" -> "Verse 1"
    else:
        name, n = label, 2
        while name.lower() in used:
            name, n = f"{label} ({n})", n + 1
        used.add(name.lower())
        section_type = _type_of(label)
    parsed.sections.append((name, section_type, lyrics))
    parsed.order.append(name)
    by_text[key] = name


def _type_of(label):
    from ..core.songtext import section_type_for
    return section_type_for(label)


def _finish(parsed):
    if not parsed.sections:
        raise ImporterError("The song has no lyrics")
    return parsed


# ---------------------------------------------------------------------------- plain text
def parse_plain_text(text, fallback_title=""):
    """SongSelect-style or typed lyrics. Headings may be `Verse 1` or `[Verse 1]`."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    parsed = ParsedSong(title="")
    blocks, current = [], []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    # trailing information block(s): CCLI number, author, copyright, license boilerplate
    while blocks and any(_CCLI_LINE.match(l) or _LICENSE_LINE.match(l) or _COPYRIGHT_LINE.match(l)
                         or _AUTHOR_LINE.match(l) for l in blocks[-1]):
        info = blocks.pop()
        authors = []
        for line in info:
            if (m := _CCLI_LINE.match(line)):
                parsed.ccli_number = m.group(1)
            elif (m := _AUTHOR_LINE.match(line)):
                authors.append(squash(m.group(1)))
            elif _COPYRIGHT_LINE.match(line):
                parsed.copyright = (parsed.copyright + " " if parsed.copyright else "") + squash(line)
            elif not _LICENSE_LINE.match(line) and "ccli.com" not in line.lower():
                authors.append(squash(line))
        if authors:
            parsed.author = ", ".join(authors)

    # an optional first block that is a lone line and not a heading is the title
    heading_blocks = [b for b in blocks if _PLAIN_HEADING.match(b[0])]
    if blocks and len(blocks[0]) == 1 and not _PLAIN_HEADING.match(blocks[0][0]) and heading_blocks:
        parsed.title = squash(blocks.pop(0)[0])
        if parsed.title.lower().startswith("title:"):
            parsed.title = squash(parsed.title[6:])

    used, by_text = set(), {}
    if heading_blocks:
        label = None
        for block in blocks:
            if _PLAIN_HEADING.match(block[0]):
                label = _PLAIN_HEADING.match(block[0]).group(1).strip()
                words = _clean_block(block[1:])
                if not words:           # a heading alone: the words follow in the next block
                    continue
                _add_section(parsed, used, by_text, label, words)
                label = None
            elif label is not None:
                _add_section(parsed, used, by_text, label, _clean_block(block))
                label = None
            else:
                _add_section(parsed, used, by_text, f"Verse {len(parsed.sections) + 1}", _clean_block(block))
                parsed.warnings.append("Some words had no heading and were called a verse")
    else:
        for i, block in enumerate(blocks, 1):
            _add_section(parsed, used, by_text, f"Verse {i}", _clean_block(block))
    parsed.title = parsed.title or fallback_title
    return _finish(parsed)


# ---------------------------------------------------------------------------- ChordPro
_DIRECTIVE = re.compile(r"^\s*\{\s*([a-z_]+)\s*(?::\s*(.*?))?\s*\}\s*$", re.IGNORECASE)
_CHORD = re.compile(r"\[[^\]]*\]")
_START = {"start_of_verse": "Verse", "sov": "Verse", "start_of_chorus": "Chorus", "soc": "Chorus",
          "start_of_bridge": "Bridge", "sob": "Bridge", "start_of_tab": None, "sot": None}
_END = {"end_of_verse", "eov", "end_of_chorus", "eoc", "end_of_bridge", "eob", "end_of_tab", "eot"}
_META = {"title": "title", "t": "title", "subtitle": "author", "st": "author", "artist": "author",
         "composer": "author", "lyricist": "author", "author": "author", "copyright": "copyright",
         "ccli": "ccli", "ccli_song": "ccli", "key": "key", "comment_intro": None}


def parse_chordpro(text, fallback_title=""):
    parsed = ParsedSong(title="")
    authors = []
    open_label, open_lines, loose = None, [], []
    used, by_text = set(), {}
    last_chorus = None
    counts = {}

    def flush_loose():
        nonlocal loose
        block = []
        for line in loose + [""]:
            if line.strip():
                block.append(line)
            elif block:
                counts["Verse"] = counts.get("Verse", 0) + 1
                _add_section(parsed, used, by_text, f"Verse {len(parsed.sections) + 1}", _clean_block(block))
                block = []
        loose = []

    def close_section():
        nonlocal open_label, open_lines, last_chorus
        if open_label is not None and _clean_block(open_lines):
            label = open_label
            _add_section(parsed, used, by_text, label, _clean_block(open_lines))
            if label.lower().startswith("chorus"):
                last_chorus = parsed.sections[-1][0] if parsed.sections else None
        open_label, open_lines = None, []

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.lstrip().startswith("#"):
            continue
        m = _DIRECTIVE.match(raw)
        if m:
            name, value = m.group(1).lower(), (m.group(2) or "").strip()
            if name in _META:
                kind = _META[name]
                if kind == "title" and value:
                    parsed.title = value
                elif kind == "author" and value:
                    authors.append(value)
                elif kind == "copyright" and value:
                    parsed.copyright = value
                elif kind == "ccli" and value:
                    parsed.ccli_number = re.sub(r"\D", "", value) or None
                elif kind == "key" and value:
                    parsed.default_key = value
            elif name in _START:
                flush_loose()
                close_section()
                base = _START[name]
                if base is None:
                    open_label = "\x00skip"
                else:
                    counts[base] = counts.get(base, 0) + 1
                    open_label = value or (base if base == "Chorus" and counts[base] == 1 else f"{base} {counts[base]}")
            elif name in _END:
                if open_label == "\x00skip":
                    open_label, open_lines = None, []
                else:
                    close_section()
            elif name in ("comment", "c", "comment_italic", "ci", "comment_box", "cb") and value:
                if open_label is None:
                    flush_loose()
                    open_label = value        # "{comment: Verse 1}" as a heading
                else:
                    open_lines.append(value)
            elif name == "chorus":
                flush_loose()
                close_section()
                if last_chorus:
                    parsed.order.append(last_chorus)
            continue
        line = _CHORD.sub("", raw)
        if open_label == "\x00skip":
            continue
        if open_label is not None:
            if not line.strip() and _clean_block(open_lines) and not _is_heading_comment(open_label):
                open_lines.append("")
            elif not line.strip() and _is_heading_comment(open_label):
                if _clean_block(open_lines):
                    close_section()
            else:
                open_lines.append(line)
        else:
            loose.append(line)
    flush_loose()
    close_section()
    if authors:
        parsed.author = ", ".join(dict.fromkeys(authors))
    parsed.title = parsed.title or fallback_title
    return _finish(parsed)


def _is_heading_comment(label):
    return bool(_PLAIN_HEADING.match(label))


# ---------------------------------------------------------------------------- OpenSong
_OS_HEADING = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def parse_opensong(root, what="the song file"):
    parsed = ParsedSong(title="")

    def text_of(tag):
        node = root.find(tag)
        return squash("".join(node.itertext())) if node is not None else ""

    parsed.title = text_of("title")
    parsed.author = text_of("author") or None
    parsed.copyright = text_of("copyright") or None
    parsed.ccli_number = text_of("ccli") or None
    parsed.default_key = text_of("key") or None
    parsed.notes = text_of("user1") or None
    lyrics_node = root.find("lyrics")
    raw = "".join(lyrics_node.itertext()) if lyrics_node is not None else ""

    used, by_name = set(), {}
    name, lines = None, []

    def close():
        nonlocal name, lines
        if name is not None:
            words = _clean_block(lines)
            if words:
                label, section_type = _label_for(name, used)
                parsed.sections.append((label, section_type, words))
                by_name.setdefault(name.lower(), label)
        name, lines = None, []

    for line in raw.replace("\r\n", "\n").split("\n"):
        heading = _OS_HEADING.match(line)
        if heading:
            close()
            name = heading.group(1).strip()
        elif line.startswith((".", ";")):
            continue                       # chord line or comment
        elif name is None:
            if line.strip():
                name = "V"
                lines.append(line)
        elif line.strip() == "---":
            lines.append("")
        else:
            piece = line[1:] if line.startswith(" ") else line
            lines.extend(piece.replace("|", "\n").split("\n"))
    close()
    for token in text_of("presentation").split():
        label = by_name.get(token.lower())
        if label is None:
            parsed.warnings.append(f"The presentation order names {token!r}, which has no lyrics; skipped")
        else:
            parsed.order.append(label)
    if not parsed.order:
        parsed.order = [label for label, _, _ in parsed.sections]
    return _finish(parsed)


# ---------------------------------------------------------------------------- choosing a reader
def parse_song_file(path):
    """Read one song file of any supported kind into a ParsedSong."""
    path = Path(path)
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    head = raw[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    if suffix in CHORDPRO_SUFFIXES:
        return parse_chordpro(decode_text(raw), path.stem)
    if suffix == ".txt":
        return parse_plain_text(decode_text(raw), path.stem)
    if head.startswith(b"<"):
        root = parse_xml(raw, path.name)
        if root.tag != "song":
            raise ImporterError(f"{path.name} is not a song file (its top element is <{root.tag}>)")
        lyrics = root.find("lyrics")
        if lyrics is not None and lyrics.find("verse") is None:
            return parse_opensong(root, path.name)
        return parse_openlyrics(raw, path.name)
    if b"{title" in raw[:4096].lower() or b"{t:" in raw[:4096].lower():
        return parse_chordpro(decode_text(raw), path.stem)
    return parse_plain_text(decode_text(raw), path.stem)
