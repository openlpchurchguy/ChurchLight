# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Pieces shared by the importers."""
import re
import struct
import zlib
from xml.etree import ElementTree as ET


class ImporterError(Exception):
    """Something about the file stopped the import; the message says what to do."""


def parse_xml(data, what="the file"):
    """Parse XML bytes or text safely and return the root element with namespaces removed.

    Entity declarations are refused (they are how "billion laughs" files work and
    no real song or Bible file needs them). Namespace prefixes are stripped so
    callers can match plain tag names whatever version of a format they meet.
    """
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if b"<!ENTITY" in raw.upper():
        raise ImporterError(f"{what} contains unsafe XML; refusing to read it")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as err:
        raise ImporterError(f"{what} is not valid XML ({err})") from err
    for node in root.iter():
        if isinstance(node.tag, str) and "}" in node.tag:
            node.tag = node.tag.rsplit("}", 1)[1]
    return root


def squash(text):
    """Collapse every run of whitespace to one space and trim the ends."""
    return re.sub(r"\s+", " ", text or "").strip()


def encode_png(width, height, rows):
    """A PNG file's bytes. `rows` is a list of `height` byte strings, each `width * 3` RGB bytes."""
    if len(rows) != height or any(len(r) != width * 3 for r in rows):
        raise ValueError("rows must be `height` strings of `width * 3` RGB bytes")

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
