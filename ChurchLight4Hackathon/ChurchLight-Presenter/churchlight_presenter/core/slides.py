# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Slide builder: turns items (songs, passages, decks...) into a flat list of Slides.

Pure Python and deterministic: the same database contents and settings always
produce the same slides, so everything here can be unit-tested without a window.
Every output (projector, stage, livestream overlays) is fed from these slides.
"""
from dataclasses import dataclass, field, fields, replace

from .autoplay import timing_for
from .items import ItemRef, load_service
from .songnotice import notice_lines, notice_position, notice_settings
from .songtext import skipped_parts
from .scripture_ref import format_reference
from .fontmetrics import Ruler
from .themes import (ASPECTS, overlay_char_capacity, overlay_fit_shape, projector_char_capacity,
                     projector_line_room_em, projector_max_lines)


@dataclass(frozen=True)
class Slide:
    """One screen of content. Immutable, so a slide can't change after it is built.

    kind          how to draw it: lyrics | scripture | text | image | video | audio | blank
    item_type     where it came from: song | scripture | presentation | media |
                  announcement | blank (stage layouts are chosen by this)
    label         song section ("Chorus"), translation ("KJV"), or "Slide 4"
    reference     song title, passage ("John 3:16-17"), or presentation title
    segments      scripture only: ((verse_number, text), ...), so a stage can draw
                  verse numbers without parsing `text`
    verse_numbers every verse number on the slide
    continued     True if the slide carries on from the previous one (a split
                  verse or a long section)
    title_first   scripture only: draw `reference` as a title above the text and put each
                  verse number in front of its text as "16) ..." (the verse tray uses this)
    transition    how the projector changes to this slide: a key of core/transitions.TRANSITIONS
                  (a deck's setting; see core/transitions.py); transition_ms is its length
    notes         speaker notes; for stage views ONLY, never for public screens
    """
    kind: str
    item_type: str
    source_item_id: int | None
    text: str = ""
    segments: tuple = ()
    label: str | None = None
    reference: str | None = None
    verse_numbers: tuple = ()
    continued: bool = False
    image_path: str | None = None
    notes: str | None = None
    theme_id: int | None = None
    title_first: bool = False
    transition: str = "none"
    transition_ms: int = 0
    image_fit: str = "fit"          # how a picture fills the screen: "fit" (whole picture), "fill" (crops) or "stretch"
    overlay_theme_id: int | None = None  # the look for overlay (lower third) outputs
    bible_theme_id: int | None = None   # the global Bible theme, used by projector-style outputs only
    auto_seconds: int = 0           # timed advance for deck slides (0 = manual); see core/autoplay.py
    end_action: str = "stop"
    part: int | None = None         # songs only: the position in the song's order (a left-out part keeps its number)
    duration_ms: int = 0            # a video or sound clip's length (kind "video" or "audio"; image_path is its media file)
    muted: bool = False             # a video page played without its own sound
    sound_path: str | None = None   # a sound file from the Media Library that plays while this slide is live
    sound_name: str = ""
    sound_duration_ms: int = 0
    sound_loop: bool = False


NOTICE_LABEL = "Title and CCLI"
NOTICE_PART = -1                    # the part number of a song's title and CCLI slide (it is not one of the song's parts)


@dataclass(frozen=True)
class BuildSettings:
    """Splitting rules. The defaults are starting points to tune with real themes."""
    lines_per_slide: int = 4
    scripture_split: str = "chars"    # "chars" packs verses together; "verse" = one verse per slide
    scripture_max_chars: int = 200
    chars_per_line: int = 0           # songs: a lyric line longer than this counts as more than one line; 0 = count lines as written
    song_count: str = "lines"         # songs: "lines" counts the lines as written; "screen" counts the lines the screen draws
    line_ruler: object = field(default=None, compare=False)   # set by the builder for "screen": a (Ruler, room in em) pair
    verse_fit: object = field(default=None, compare=False)    # set by the builder for a real run: a _VerseFit (what a slide holds)

    def __post_init__(self):
        for name in ("lines_per_slide", "scripture_max_chars"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a whole number of at least 1, got {value!r}")
        value = self.chars_per_line
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"chars_per_line must be a whole number of 0 or more, got {value!r}")
        if self.song_count not in ("lines", "screen", "characters"):          # "characters" is the older name of "screen"
            raise ValueError(f"song_count must be 'lines' or 'screen', got {self.song_count!r}")
        if self.scripture_split not in ("chars", "verse"):
            raise ValueError(
                f"scripture_split must be 'chars' or 'verse', got {self.scripture_split!r}")


_SETTING_KEYS = frozenset(f.name for f in fields(BuildSettings))


def _merge_settings(base, *layers):
    """Later layers win. Keys that aren't build settings (font, color...) are ignored."""
    changes = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if key in _SETTING_KEYS:
                changes[key] = value
    return replace(base, **changes) if changes else base


# -- text splitting ----------------------------------------------------------

def _paragraphs(text):
    """Lines grouped by blank lines; a blank line forces a slide break."""
    paragraphs, current = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line:
            current.append(line)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return paragraphs


def _balanced_chunks(lines, per_slide):
    """Split lines into the fewest slides, as evenly as possible (5 lines at 4
    per slide becomes 3 + 2, not 4 + 1)."""
    if not lines:
        return []
    count = -(-len(lines) // per_slide)
    size, extra = divmod(len(lines), count)
    chunks, start = [], 0
    for i in range(count):
        end = start + size + (1 if i < extra else 0)
        chunks.append(lines[start:end])
        start = end
    return chunks


class _Rows:
    """How many screen lines a lyric line takes: measured with the theme's font when the builder gave a ruler
    (the real letter widths and word wrap), else estimated from a limit of `chars` characters per line."""

    def __init__(self, settings):
        self.ruler, self.room, self.upper = settings.line_ruler or (None, 0, False)
        self.chars = settings.chars_per_line

    def __bool__(self):
        return bool(self.ruler) or bool(self.chars)

    def cost(self, line):
        if self.ruler:
            return self.ruler.lines(line.upper() if self.upper else line, self.room)
        return max(1, -(-len(line) // self.chars))

    def split(self, line, rows):
        """Break a line at word breaks into pieces of at most `rows` screen lines each (a single longer word stays whole)."""
        pieces, current = [], ""
        for word in line.split():
            trial = f"{current} {word}" if current else word
            if current and self.cost(trial) > rows:
                pieces.append(current)
                current = word
            else:
                current = trial
        if current:
            pieces.append(current)
        return pieces or [line]


def _even_cuts(costs, per):
    """Cut a run of lines into the fewest slides of at most `per` screen lines, as even as possible.
    Returns the slide sizes in lines (pieces), or None when a single piece is already too big."""
    n = len(costs)
    if any(c > per for c in costs):
        return None
    prefix = [0]
    for c in costs:
        prefix.append(prefix[-1] + c)
    for count in range(-(-prefix[-1] // per), n + 1):
        INF = float("inf")
        best = [[INF] * (n + 1) for _ in range(count + 1)]      # best[k][i]: k slides cover the first i pieces
        back = [[0] * (n + 1) for _ in range(count + 1)]
        best[0][0] = 0
        for k in range(1, count + 1):
            for i in range(k, n + 1):
                for j in range(k - 1, i):
                    fill = prefix[i] - prefix[j]
                    if fill > per or best[k - 1][j] == INF:
                        continue
                    score = best[k - 1][j] + fill * fill
                    if score <= best[k][i]:          # on a tie the earlier slide is the fuller one
                        best[k][i], back[k][i] = score, j
        if best[count][n] < INF:
            sizes, i = [], n
            for k in range(count, 0, -1):
                j = back[k][i]
                sizes.append(i - j)
                i = j
            return sizes[::-1]
    return None


def _song_chunks(lines, settings):
    """Split a part's lines into slides, counting a long line as the several lines it will take on screen.

    With `song_count` "lines" every line counts as one (the plain even split). Otherwise a long lyric line counts as
    the lines the screen draws for it, a slide holds at most `lines_per_slide` of those, a line that is longer than a
    whole slide is split at word breaks, and the slides are kept as even as possible."""
    per = settings.lines_per_slide
    rows = _Rows(settings) if settings.song_count in ("screen", "characters") else None
    if not rows:
        return _balanced_chunks(lines, per)
    pieces = []
    for line in lines:
        if rows.cost(line) > per:
            pieces.extend(rows.split(line, per))
        else:
            pieces.append(line)
    costs = [rows.cost(piece) for piece in pieces]
    sizes = _even_cuts([min(per, c) for c in costs], per)
    if sizes is None:
        return _greedy_costs(pieces, [min(per, c) for c in costs], per)
    chunks, start = [], 0
    for size in sizes:
        chunks.append(pieces[start:start + size])
        start += size
    return [chunk for chunk in chunks if chunk]


def _greedy_costs(pieces, costs, per):
    chunks, current, used = [], [], 0
    for piece, cost in zip(pieces, costs):
        if current and used + cost > per:
            chunks.append(current)
            current, used = [], 0
        current.append(piece)
        used += cost
    if current:
        chunks.append(current)
    return chunks


def _greedy(words, limit):
    parts, current, length = [], [], 0
    for word in words:
        needed = len(word) + (1 if current else 0)
        if current and length + needed > limit:
            parts.append(" ".join(current))
            current, length, needed = [], 0, len(word)
        current.append(word)
        length += needed
    if current:
        parts.append(" ".join(current))
    return parts


class _VerseFit:
    """Whether a piece of verse text fits on ONE slide of every screen that shows verses, measured with each
    screen's own font, line width and line count (the projector's, and the lower third's at its smallest size)."""

    def __init__(self, screens):
        self.screens = screens          # [(Ruler, room in em, most lines, uppercase)]

    def fits(self, text):
        for ruler, room, most, upper in self.screens:
            if ruler.lines(text.upper() if upper else text, room) > most:
                return False
        return True


_PAUSE = ",;:.?!)\u2014\u201d\u2019\""


def _fit_split(text, fits):
    """Split a verse at word boundaries into the fewest parts that each fit, as even as possible.

    Equal parts (by letters) keep a slide from ending on one lonely word, and a cut after a comma, semicolon or
    other natural pause is preferred to a cut in the middle of a phrase. A single word too big for any slide
    stays whole."""
    text = " ".join(text.split())
    if fits(text):
        return [text]
    words = text.split()
    n = len(words)
    prefix = [0]
    for word in words:
        prefix.append(prefix[-1] + len(word) + 1)
    reach = []                              # reach[j]: the furthest end i such that words[j:i] fit
    for j in range(n):
        i = j + 1
        while i < n and fits(" ".join(words[j:i + 1])):
            i += 1
        reach.append(i)
    total = prefix[-1]
    penalty = (0.12 * total) ** 2
    INF = float("inf")
    for count in range(2, n + 1):
        best = [[INF] * (n + 1) for _ in range(count + 1)]
        back = [[0] * (n + 1) for _ in range(count + 1)]
        best[0][0] = 0.0
        for k in range(1, count + 1):
            for j in range(k - 1, n):
                if best[k - 1][j] == INF:
                    continue
                for i in range(j + 1, reach[j] + 1):
                    size = prefix[i] - prefix[j]
                    cut = 0.0 if i == n or words[i - 1][-1] in _PAUSE else penalty
                    score = best[k - 1][j] + size * size + cut
                    if score < best[k][i]:
                        best[k][i], back[k][i] = score, j
        if best[count][n] < INF:
            cuts, i = [], n
            for k in range(count, 0, -1):
                j = back[k][i]
                cuts.append((j, i))
                i = j
            return [" ".join(words[j:i]) for j, i in reversed(cuts)]
    return [text]


def _split_text(text, limit, fits=None):
    """Split at word boundaries into near-equal parts of at most `limit` characters
    (a single word longer than the limit stays whole). With `fits`, the parts are the fewest that each
    fit the screens, as measured."""
    if fits is not None:
        return _fit_split(text, fits)
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text]
    words = text.split()
    count = -(-len(text) // limit)
    parts = _greedy(words, -(-len(text) // count))
    if len(parts) > count:  # word lengths didn't divide evenly; fall back
        parts = _greedy(words, limit)
    return parts


def _pack_verses(verses, max_chars, pack, fits=None):
    """Group verses into slides: [(segments, continued), ...].

    A verse longer than max_chars (or, with `fits`, one that does not fit a slide) is split across slides (the
    later parts are marked continued) and never shares a slide. With pack=False every verse gets its own slide.
    """
    def too_big(text):
        return not fits(text) if fits is not None else len(text) > max_chars

    def together(current, number, text):
        """The words of a slide holding `current` (and one more verse): numbers show only when there are several."""
        parts = list(current) + [(number, text)]
        if len(parts) == 1:
            return text
        return " ".join(f"{n} {t}" for n, t in parts)

    groups, current, length = [], [], 0
    for verse in verses:
        number, text = verse["verse"], " ".join(verse["text"].split())
        if too_big(text):
            if current:
                groups.append((tuple(current), False))
                current, length = [], 0
            for i, part in enumerate(_split_text(text, max_chars, fits)):
                groups.append((((number, part),), i > 0))
            continue
        needed = len(text) + (1 if current else 0)
        if fits is not None:
            overflow = bool(current) and too_big(together(current, number, text))
        else:
            overflow = bool(current) and length + needed > max_chars
        if current and (not pack or overflow):
            groups.append((tuple(current), False))
            current, length, needed = [], 0, len(text)
        current.append((number, text))
        length += needed
    if current:
        groups.append((tuple(current), False))
    return groups


# -- the builder -------------------------------------------------------------

class SlideBuilder:
    def __init__(self, db, defaults=None):
        self.db = db
        self.defaults = defaults or BuildSettings()

    def build_service(self, service_id):
        """All of a service's slides, in order. Each slide's `source_item_id` is
        the service item it came from.

        An item that can't be built (a verse the translation lacks, a
        song with no sections) becomes one blank placeholder slide labeled
        "Problem: ...", so one bad item never stops the
        rest of the service from loading. `build_item` still raises, so the
        library can report the problem at once when something is sent live.
        """
        service = self.db.get_service(service_id)
        if service is None:
            raise ValueError(f"No service with id {service_id}")
        slides = []
        for ref in load_service(self.db, service_id):
            try:
                slides.extend(self.build_item(ref, service["theme_id"], service["overlay_theme_id"],
                                              verse_split=service["verse_split"]))
            except ValueError as err:
                theme_id = ref.theme_id if ref.theme_id is not None else service["theme_id"]
                slides.append(Slide(
                    kind="blank", item_type=ref.item_type, source_item_id=ref.item_id,
                    label=f"Problem: {err}", theme_id=theme_id))
        return slides

    # Set by the controller: the theme Bible verses use when neither the item nor an output picks one.
    bible_theme_id = None
    # Set by the controller for a real run: "fit as many verses as will fit" then measures what fits on the
    # projector (its theme's text size and its screen shape) instead of using a fixed character limit.
    use_capacity = False
    projector_aspect = ASPECTS["16:9"]

    def build_item(self, ref, default_theme_id=None, default_overlay_theme_id=None, verse_split=None,
                   verse_over_theme_id=None):
        """Slides for one ItemRef, saved or not (so the library can send items live)."""
        theme_id = ref.theme_id if ref.theme_id is not None else self._song_theme(ref, default_theme_id)
        # The Bible theme styles verses on projector-style outputs only (see build_payload), and its
        # slide-splitting settings apply to the verses, since every output shares the same slides.
        bible_id = (self.bible_theme_id if ref.theme_id is None and ref.item_type == "scripture" else None)
        settings_theme = bible_id if bible_id is not None else theme_id
        theme_settings = self.db.get_theme_settings(settings_theme) if settings_theme is not None else None
        # A service's own choice of verse layout beats its theme's; an item's own override beats both.
        service_layer = {"scripture_split": verse_split} if verse_split in ("verse", "chars") else None
        settings = _merge_settings(self.defaults, theme_settings, service_layer, ref.overrides)
        handlers = {
            "song": self._song, "scripture": self._scripture,
            "presentation": self._presentation, "media": self._media,
            "announcement": self._announcement, "blank": self._blank,
        }
        overlay_id = ref.overlay_theme_id if ref.overlay_theme_id is not None else default_overlay_theme_id
        if ref.item_type == "scripture":
            settings = self._fit_verses(settings, theme_settings, overlay_id, verse_over_theme_id)
        elif ref.item_type == "song":
            settings = self._fit_song_lines(settings, theme_settings)
        slides = handlers[ref.item_type](ref, settings, theme_id)
        return [replace(slide, overlay_theme_id=overlay_id, bible_theme_id=bible_id) for slide in slides]

    def _song_theme(self, ref, default_theme_id):
        """The theme a song with no item-level theme uses: its own (patriotic, Christmas...), else the default."""
        if ref.item_type == "song" and ref.song_id is not None:
            song = self.db.get_song(ref.song_id)
            if song is not None and song["theme_id"] is not None:
                return song["theme_id"]
        return default_theme_id

    def _fit_verses(self, settings, theme_settings, overlay_id, verse_over_id=None):
        """Settings that split verses by what the screens really hold (a real run), else by the character limits.

        Every output shares the same slides, so a verse must fit the projector AND the lower third; each is
        measured with its own theme's font, so neither is guessed too small and a slide is never cut earlier than
        it has to be."""
        if not self.use_capacity:
            fitted = self._fit_overlay(self._fit_projector(settings, theme_settings), overlay_id)
            return self._fit_overlay(fitted, verse_over_id)
        screens = []
        style = ((theme_settings or {}).get("style") if theme_settings else None) or {}
        ruler = Ruler(style.get("font_family"))
        screens.append((ruler, projector_line_room_em(style, self.projector_aspect) * (0.99 if ruler.exact else 0.97),
                        projector_max_lines(style, self.projector_aspect), bool(style.get("uppercase"))))
        for theme_id in (overlay_id, verse_over_id):    # the lower third, and the projector's verse-over box
            if theme_id is None:
                continue
            overlay_style = ((self.db.get_theme_settings(theme_id) or {}).get("style")) or {}
            shape = overlay_fit_shape(overlay_style)
            if shape is not None:
                ruler = Ruler(overlay_style.get("font_family"))
                screens.append((ruler, shape[0] * (0.99 if ruler.exact else 0.97), shape[1],
                                bool(overlay_style.get("uppercase"))))
        return replace(settings, verse_fit=_VerseFit(screens).fits)

    def _fit_projector(self, settings, theme_settings):
        """In "fit as many as will fit" mode, the character limit comes from what the projector can hold."""
        if not self.use_capacity or settings.scripture_split != "chars":
            return settings
        style = (theme_settings or {}).get("style") if theme_settings else None
        return replace(settings, scripture_max_chars=projector_char_capacity(style, self.projector_aspect))

    def _fit_song_lines(self, settings, theme_settings):
        """In "lines on the screen" mode, how wide a drawn line is comes from the projector theme's text size."""
        if not self.use_capacity or settings.song_count not in ("screen", "characters"):
            return settings
        style = ((theme_settings or {}).get("style") if theme_settings else None) or {}
        ruler = Ruler(style.get("font_family"))
        room = projector_line_room_em(style, self.projector_aspect) * (0.99 if ruler.exact else 0.97)   # never over the limit
        return replace(settings, line_ruler=(ruler, room, bool(style.get("uppercase"))))

    def _fit_overlay(self, settings, overlay_id):
        """Settings whose verse length also suits the lower third's height cap, when it has one.

        Every output shares the same slides, so when the overlay can hold fewer words than the
        projector, the overlay decides where a verse breaks.
        """
        if overlay_id is None:
            return settings
        theme = self.db.get_theme_settings(overlay_id)
        capacity = overlay_char_capacity((theme or {}).get("style"))
        if capacity is None or capacity >= settings.scripture_max_chars:
            return settings
        return replace(settings, scripture_max_chars=capacity)

    # songs
    def _song(self, ref, settings, theme_id):
        song = self.db.get_song(ref.song_id)
        if song is None:
            raise ValueError(f"No song with id {ref.song_id}")
        arrangement_id = ref.arrangement_id
        if arrangement_id is None:
            default = self.db.get_default_arrangement(ref.song_id)
            arrangement_id = default["id"] if default else None
        if arrangement_id is not None:
            sections = self.db.get_arrangement_sections(arrangement_id)
        else:  # no arrangement yet: play the sections in their written order
            sections = self.db.get_sections(ref.song_id)

        if not sections:
            raise ValueError(f"Song {song['title']!r} has no sections")
        skip = skipped_parts(ref.overrides, len(sections))      # parts this service leaves out
        sections = [(i, s) for i, s in enumerate(sections) if i not in skip]

        title = ref.title or song["title"]
        slides = []
        for part, section in sections:
            chunks = [chunk for paragraph in _paragraphs(section["lyrics"])
                      for chunk in _song_chunks(paragraph, settings)]
            if not chunks:  # instrumental/intro: keep its place in the song structure
                slides.append(Slide(
                    kind="blank", item_type="song", source_item_id=ref.item_id,
                    label=section["label"], reference=title, theme_id=theme_id, part=part))
                continue
            for i, chunk in enumerate(chunks):
                slides.append(Slide(
                    kind="lyrics", item_type="song", source_item_id=ref.item_id,
                    text="\n".join(chunk), label=section["label"], reference=title,
                    continued=i > 0, theme_id=theme_id, part=part))
        return self._with_notice(slides, ref, song, title, theme_id)

    def _with_notice(self, slides, ref, song, title, theme_id):
        """Put the song's title and CCLI slide before or after its slides, when it is switched on."""
        notice = notice_settings(self.db)
        where = notice_position(notice, song["notice_slide"], ref.overrides)
        if where is None:
            return slides
        lines = notice_lines(notice, title, song["author"], song["copyright"], song["ccli_number"])
        slide = Slide(kind="notice", item_type="song", source_item_id=ref.item_id, text="\n".join(lines),
                      label=NOTICE_LABEL, reference=title, theme_id=theme_id, part=NOTICE_PART)
        return [slide] + slides if where == "before" else slides + [slide]

    # scripture
    def _scripture(self, ref, settings, theme_id):
        book = self.db.get_book(ref.book_id)
        translation = self.db.get_translation(ref.translation_id)
        if book is None or translation is None:
            raise ValueError("Scripture item refers to an unknown book or translation")
        end = ref.verse_end if ref.verse_end is not None else ref.verse_start
        verses = self.db.get_verses(
            ref.translation_id, ref.book_id, ref.chapter, ref.verse_start, end)
        if not verses:
            shown = format_reference(book["name"], ref.chapter, ref.verse_start, ref.verse_end)
            raise ValueError(f"{shown} was not found in {translation['abbreviation']}")

        # An item can ask for one verse per slide (the verse tray's cue does), whatever the packing setting says.
        one_per_slide = bool((ref.overrides or {}).get("one_per_slide"))
        groups = _pack_verses(
            verses, settings.scripture_max_chars, settings.scripture_split == "chars" and not one_per_slide,
            settings.verse_fit)
        # Every verse slide reads the same way: the full reference ("John 3:16") above the text, and no
        # leading verse number (small numbers appear inside the text only when a slide holds several verses).
        title_first = True
        slides = []
        for segments, continued in groups:
            numbers = tuple(number for number, _ in segments)
            last = numbers[-1] if numbers[-1] != numbers[0] else None
            reference = format_reference(book["name"], ref.chapter, numbers[0], last)
            slides.append(Slide(
                kind="scripture", item_type="scripture", source_item_id=ref.item_id,
                text=" ".join(text for _, text in segments), segments=segments,
                label=translation["abbreviation"], reference=reference,
                verse_numbers=numbers, continued=continued, theme_id=theme_id,
                title_first=title_first))
        return slides

    def build_passage(self, translation_id, book_id, verses, chapter, default_theme_id=None,
                      bible_theme_id=None, default_overlay_theme_id=None, verse_split=None,
                      verse_over_theme_id=None):
        """Slides for chosen verses of one chapter, drawn as a title ("John 3") over numbered lines.

        `verses` are (number, text) pairs, in the order to show them (they need not be consecutive:
        16 and 18 can share a slide). The title is always the book and chapter, never the verse
        numbers, so it stays the same as the operator steps through the chapter. Verses are packed
        onto slides by the same rules as any passage, and a long verse is split into parts.
        """
        book = self.db.get_book(book_id)
        translation = self.db.get_translation(translation_id)
        if book is None or translation is None:
            raise ValueError("Scripture refers to an unknown book or translation")
        if not verses:
            raise ValueError("Choose at least one verse to show")
        chosen = bible_theme_id if bible_theme_id is not None else default_theme_id
        theme_settings = self.db.get_theme_settings(chosen) if chosen is not None else None
        service_layer = {"scripture_split": verse_split} if verse_split in ("verse", "chars") else None
        settings = _merge_settings(self.defaults, theme_settings, service_layer)
        settings = self._fit_verses(settings, theme_settings, default_overlay_theme_id, verse_over_theme_id)
        groups = _pack_verses([{"verse": n, "text": t} for n, t in verses],
                              settings.scripture_max_chars, settings.scripture_split == "chars", settings.verse_fit)
        def reference_of(segments):
            numbers = [number for number, _ in segments]
            return format_reference(book["name"], chapter, numbers[0], numbers[-1] if numbers[-1] != numbers[0] else None)

        return [
            Slide(kind="scripture", item_type="scripture", source_item_id=None,
                  text=" ".join(text for _, text in segments), segments=segments,
                  label=translation["abbreviation"], reference=reference_of(segments),
                  verse_numbers=tuple(number for number, _ in segments), continued=continued,
                  theme_id=default_theme_id, bible_theme_id=bible_theme_id, title_first=True,
                  overlay_theme_id=default_overlay_theme_id)
            for segments, continued in groups
        ]

    # presentations (converted to page images at import)
    def _presentation(self, ref, settings, theme_id):
        presentation = self.db.get_presentation(ref.presentation_id)
        if presentation is None:
            raise ValueError(f"No presentation with id {ref.presentation_id}")
        name = ref.title or presentation["name"]
        deck_kind, deck_ms = presentation["transition"], presentation["transition_ms"]
        seconds, end = timing_for(presentation["auto_seconds"], presentation["end_action"], ref.overrides)
        slides = []
        for page in self.db.get_presentation_pages(ref.presentation_id):
            if page["skipped"]:
                continue
            # A page's own setting wins over the service's and the deck's.
            kind, ms = (page["transition"], page["transition_ms"]) if page["transition"] is not None \
                else (deck_kind, deck_ms)
            if kind == "none" or ms <= 0:
                kind, ms = "none", 0
            own = page["auto_seconds"]
            clip = self.db.get_media_asset(page["media_id"]) if page["media_id"] else None
            is_video = clip is not None and clip["media_type"] == "video"
            sound = self.db.get_media_asset(page["sound_id"]) if page["sound_id"] else None
            slides.append(
                Slide(kind="video" if is_video else "image", item_type="presentation", source_item_id=ref.item_id,
                      label=page["title"] or (clip["name"] if clip is not None else f"Slide {page['page_number']}"),
                      reference=name, image_path=page["image_path"], notes=page["notes"], theme_id=theme_id,
                      transition=kind, transition_ms=ms, image_fit=presentation["image_fit"],
                      auto_seconds=seconds if own is None else own, end_action=end,
                      duration_ms=int(clip["duration_ms"] or 0) if is_video else 0,
                      muted=bool(page["muted"]) and is_video,
                      sound_path=sound["path"] if sound is not None else None,
                      sound_name=sound["name"] if sound is not None else "",
                      sound_duration_ms=int(sound["duration_ms"] or 0) if sound is not None else 0,
                      sound_loop=bool(page["sound_loop"]) and sound is not None))
        return slides

    # single media files
    def _media(self, ref, settings, theme_id):
        asset = self.db.get_media_asset(ref.media_id)
        if asset is None:
            raise ValueError(f"No media asset with id {ref.media_id}")
        label = ref.title or asset["name"]
        if asset["media_type"] in ("video", "audio"):      # played by the projector page; see core/live.py
            return [Slide(kind=asset["media_type"], item_type="media", source_item_id=ref.item_id, label=label,
                          image_path=asset["path"], theme_id=theme_id,
                          duration_ms=int(asset["duration_ms"] or 0))]
        return [Slide(kind="image", item_type="media", source_item_id=ref.item_id,
                      label=label, image_path=asset["path"], theme_id=theme_id)]

    # announcements and blanks
    def _announcement(self, ref, settings, theme_id):
        chunks = [chunk for paragraph in _paragraphs(ref.body)
                  for chunk in _balanced_chunks(paragraph, settings.lines_per_slide)]
        if not chunks:
            chunks = [[ref.title]] if ref.title else [[]]
        return [
            Slide(kind="text", item_type="announcement", source_item_id=ref.item_id,
                  text="\n".join(chunk), label=ref.title or "Announcement",
                  continued=i > 0, theme_id=theme_id)
            for i, chunk in enumerate(chunks)
        ]

    def _blank(self, ref, settings, theme_id):
        return [Slide(kind="blank", item_type="blank", source_item_id=ref.item_id,
                      label=ref.title or "Blank", theme_id=theme_id)]
