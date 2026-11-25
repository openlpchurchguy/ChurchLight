# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""How wide text is drawn, from the bundled theme fonts' own letter widths.

Songs can be divided by the lines the screen really draws ("Lines on the screen"). For that the builder has to know when
a lyric line wraps, which depends on the theme's font. The bundled fonts (server/static/fonts) are read directly (their
glyph advance widths, no outside library); a font that is not bundled (Georgia, Arial, the computer's own) falls back to
the widths of Helvetica, which is close to most sans-serif and serif text at lyric sizes.
Everything is measured in em (1 em = the font size), so the answer scales with the theme's size."""
import re
import struct
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "server" / "static" / "fonts"
CSS = FONT_DIR.parent / "themefonts.css"

# Helvetica's advance widths per 1000 em for the printable ASCII range: the fallback for fonts that are not bundled.
_HELVETICA = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667, "'": 191, "(": 333, ")": 333, "*": 389,
    "+": 584, ",": 278, "-": 333, ".": 278, "/": 278, ":": 278, ";": 278, "<": 584, "=": 584, ">": 584, "?": 556,
    "@": 1015, "[": 278, "\\": 278, "]": 278, "^": 469, "_": 556, "`": 333, "{": 334, "|": 260, "}": 334, "~": 584,
    "A": 667, "B": 667, "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278, "J": 500, "K": 667,
    "L": 556, "M": 833, "N": 722, "O": 778, "P": 667, "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667,
    "W": 944, "X": 667, "Y": 667, "Z": 611,
    "a": 556, "b": 556, "c": 500, "d": 556, "e": 556, "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500,
    "l": 222, "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333, "s": 500, "t": 278, "u": 556, "v": 500,
    "w": 722, "x": 500, "y": 500, "z": 500,
    "’": 222, "‘": 222, "“": 333, "”": 333, "—": 1000, "–": 556,
}
for _digit in "0123456789":
    _HELVETICA[_digit] = 556
_FALLBACK = 1.05             # Georgia, Arial and other system fonts run a little wider than Helvetica: err on the cautious side
_FALLBACK_BOLD = 1.12


class _Font:
    """The advance widths of one TrueType file."""

    def __init__(self, path):
        data = Path(path).read_bytes()
        tables = {}
        count = struct.unpack(">H", data[4:6])[0]
        for i in range(count):
            tag, _, offset, length = struct.unpack(">4sIII", data[12 + 16 * i: 28 + 16 * i])
            tables[tag.decode("latin-1")] = (offset, length)
        self.units = struct.unpack(">H", data[tables["head"][0] + 18: tables["head"][0] + 20])[0]
        hhea = tables["hhea"][0]
        self.metrics = struct.unpack(">H", data[hhea + 34: hhea + 36])[0]
        hmtx = tables["hmtx"][0]
        self.advances = [struct.unpack(">H", data[hmtx + 4 * i: hmtx + 4 * i + 2])[0] for i in range(self.metrics)]
        self.cmap = self._read_cmap(data, tables["cmap"][0])

    @staticmethod
    def _read_cmap(data, base):
        best, result = -1, {}
        tables = struct.unpack(">H", data[base + 2: base + 4])[0]
        for i in range(tables):
            platform, encoding, offset = struct.unpack(">HHI", data[base + 4 + 8 * i: base + 12 + 8 * i])
            rank = {(3, 10): 3, (0, 4): 3, (3, 1): 2, (0, 3): 2, (0, 6): 1}.get((platform, encoding), 0)
            if rank <= best:
                continue
            table = base + offset
            form = struct.unpack(">H", data[table: table + 2])[0]
            mapping = {}
            if form == 4:
                segs = struct.unpack(">H", data[table + 6: table + 8])[0] // 2
                ends = struct.unpack(">%dH" % segs, data[table + 14: table + 14 + 2 * segs])
                starts = struct.unpack(">%dH" % segs, data[table + 16 + 2 * segs: table + 16 + 4 * segs])
                deltas = struct.unpack(">%dh" % segs, data[table + 16 + 4 * segs: table + 16 + 6 * segs])
                range_at = table + 16 + 6 * segs
                ranges = struct.unpack(">%dH" % segs, data[range_at: range_at + 2 * segs])
                for s in range(segs):
                    for code in range(starts[s], ends[s] + 1):
                        if code == 0xFFFF:
                            continue
                        if ranges[s] == 0:
                            glyph = (code + deltas[s]) & 0xFFFF
                        else:
                            at = range_at + 2 * s + ranges[s] + 2 * (code - starts[s])
                            glyph = struct.unpack(">H", data[at: at + 2])[0]
                            if glyph:
                                glyph = (glyph + deltas[s]) & 0xFFFF
                        if glyph:
                            mapping[code] = glyph
            elif form == 12:
                groups = struct.unpack(">I", data[table + 12: table + 16])[0]
                for g in range(groups):
                    start, end, glyph = struct.unpack(">III", data[table + 16 + 12 * g: table + 28 + 12 * g])
                    for code in range(start, min(end, start + 70000) + 1):
                        mapping[code] = glyph + code - start
            else:
                continue
            best, result = rank, mapping
        return result

    def width(self, char):
        glyph = self.cmap.get(ord(char))
        if glyph is None:
            return None
        advance = self.advances[min(glyph, self.metrics - 1)]
        return advance / self.units


@lru_cache(maxsize=None)
def _bundled():
    """{(family lower-case, bold): file} from the font-face rules the themes already use."""
    found = {}
    try:
        css = CSS.read_text(encoding="utf-8")
    except OSError:
        return found
    for rule in re.finditer(r'font-family:\s*"([^"]+)";\s*font-weight:\s*(\d+);.*?url\("/static/fonts/([^"]+)"\)', css, re.S):
        found[(rule.group(1).lower(), int(rule.group(2)) >= 600)] = rule.group(3)
    return found


@lru_cache(maxsize=None)
def _load(filename):
    try:
        return _Font(FONT_DIR / filename)
    except (OSError, struct.error, KeyError, IndexError):
        return None


def _first_families(stack):
    return [p.strip().strip("'\"").lower() for p in (stack or "").split(",") if p.strip()]


class Ruler:
    """Measures text in em for one theme font: `width(text)` and `lines(text, room_em)` (how many lines it wraps to)."""

    def __init__(self, font_family=None, bold=False):
        self.font = None
        self.fallback_scale = _FALLBACK_BOLD if bold else _FALLBACK
        bundled = _bundled()
        for family in _first_families(font_family):
            name = bundled.get((family, bold)) or bundled.get((family, False))
            if name:
                self.font = _load(name)
                if self.font:
                    break
        self.exact = self.font is not None          # the widths come from the font file itself
        self._cache = {}

    def char(self, c):
        if c in self._cache:
            return self._cache[c]
        width = self.font.width(c) if self.font else None
        if width is None:
            width = _HELVETICA.get(c, 556) / 1000 * (1.0 if self.font else self.fallback_scale)
        self._cache[c] = width
        return width

    def width(self, text):
        return sum(self.char(c) for c in text)

    def lines(self, text, room_em):
        """How many lines `text` takes when drawn `room_em` wide, wrapping at spaces the way the browser does."""
        words = text.split()
        if not words:
            return 1
        space, used, lines = self.char(" "), 0.0, 1
        for word in words:
            size = self.width(word)
            if used == 0:
                used = size
            elif used + space + size <= room_em:
                used += space + size
            else:
                lines, used = lines + 1, size
            while used > room_em and room_em > 0:          # a word wider than the line breaks across lines
                lines += 1
                used -= room_em
        return lines
