# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Adding songs, Bibles and decks from files chosen in the operator page.

The page uploads the chosen files into a "batch" folder, then asks for an import of that batch.
Imports run one at a time on a background thread, so a long PDF render never freezes the page or
the live controls. The thread uses its **own database connection** for the importers (the slow part,
rendering pages, happens before they touch the database, and each import writes in one short
transaction), and tells the controller when something changed so every open page refreshes. A
throwaway in-memory database (the tests, the self-test) has no second connection, so there the
import takes the controller's lock instead.

Uploaded files wait in `<media>/.uploads/<batch>/` and are deleted when the import ends.
"""
import logging
import queue
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..core.mediacheck import check_video

from ..core.database import DatabaseManager
from ..importers.bibles import import_bible
from ..importers.bundle import import_bundle
from ..importers.common import ImporterError
from ..importers.decks import IMAGE_SUFFIXES, _natural_key, import_deck, import_image_folder
from ..importers.songs import import_openlyrics
from .controller import ControllerError

log = logging.getLogger(__name__)

KINDS = ("songs", "bible", "deck", "background", "slidepics", "themes", "media")
ALLOWED = {
    "songs": {".xml", ".txt", ".cho", ".chordpro", ".crd", ".chopro", ""},   # "" = OpenSong files have no extension
    "bible": {".xml", ".csv", ".tsv", ".txt"},
    "deck": {".pdf", ".zip"} | IMAGE_SUFFIXES,       # .zip: a ChurchLight slideshow bundle
    "background": IMAGE_SUFFIXES,
    "slidepics": IMAGE_SUFFIXES,
    "themes": {".zip"},                                       # a ChurchLight themes file
    "media": {".mp4", ".m4v", ".mp3", ".wav"} | IMAGE_SUFFIXES,       # a video clip, a sound file or a picture (the Media Library)
}
MAX_FILE_BYTES = {"songs": 5 * 1024 ** 2, "bible": 100 * 1024 ** 2, "deck": 400 * 1024 ** 2,
                  "background": 25 * 1024 ** 2, "slidepics": 25 * 1024 ** 2, "themes": 200 * 1024 ** 2,
                  "media": 8 * 1024 ** 3}          # 8 GB: a long video such as a youth camp trip
MAX_FILES = 500
MAX_BATCH_BYTES = 8 * 1024 ** 3
SONG_PAUSE = 0.004          # seconds of rest after each imported song: a big batch then shares the processor with a playing video
KEEP_FREE_BYTES = 512 * 1024 ** 2          # an upload must leave this much of the disk free, so the computer itself keeps working
BATCH_PATTERN = re.compile(r"[0-9a-f]{16}")
KEEP_JOBS = 20
STALE_BATCH_SECONDS = 24 * 3600
NOTES_LIMIT = 25


H265_ENTRIES = (b"hvc1", b"hev1", b"dvh1", b"dvhe")       # how an MP4 names an H.265 (HEVC) video track


def mp4_uses_h265(path, limit=64 * 1024 ** 2):
    """True when the MP4's movie header (the "moov" box, wherever it sits in the file) names an H.265 video track.
    Anything that cannot be read as an MP4 answers False: this only ever warns about a file known to be a problem."""
    try:
        size_of_file = Path(path).stat().st_size
        with open(path, "rb") as handle:
            position = 0
            while position + 8 <= size_of_file:
                handle.seek(position)
                header = handle.read(8)
                size, kind = int.from_bytes(header[:4], "big"), header[4:8]
                if size == 1:
                    size = int.from_bytes(handle.read(8), "big")
                elif size == 0:
                    size = size_of_file - position
                if size < 8:
                    return False
                if kind == b"moov":
                    handle.seek(position + 8)
                    body = handle.read(min(size - 8, limit))
                    return any(entry in body for entry in H265_ENTRIES)
                position += size
    except (OSError, ValueError):
        return False
    return False


@dataclass
class ImportJob:
    id: int
    kind: str
    label: str
    status: str = "waiting"            # waiting | running | done | failed
    message: str = ""
    notes: list = field(default_factory=list)
    started: float = field(default_factory=time.time)
    replace_for: int | None = None     # a deck import made to replace slides of this deck (see Replace from a PDF)
    deck_id: int | None = None         # the deck such an import made, once it is done

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "label": self.label, "status": self.status,
                "message": self.message, "notes": list(self.notes),
                "replace_for": self.replace_for, "deck_id": self.deck_id}


def safe_filename(name):
    """The last part of a path, with anything odd replaced; never empty, never starts with a dot."""
    name = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()]", "_", name, flags=re.UNICODE).strip(" .")
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > 120:                    # shorten the name, never the ending that says what kind of file it is
        stem, dot, suffix = name.rpartition(".")
        if dot and 0 < len(suffix) <= 10:
            name = stem[:max(1, 119 - len(suffix))].rstrip(" .") + "." + suffix
        else:
            name = name[:120].rstrip(" .")
    return name or "file"


class ImportManager:
    def __init__(self, controller, upload_root=None):
        self.c = controller
        self.media_warnings = []                  # what the last video checked said may be a problem (the file was still accepted)
        root = Path(upload_root) if upload_root else (
            Path(controller.media_root) / ".uploads" if controller.media_root else None)
        self.upload_root = root
        self._jobs = []
        self._next_id = 1
        self._lock = threading.Lock()
        self._queue = queue.Queue()
        self._worker = None
        self._closing = False
        if root is not None:
            self._purge_stale(root)

    # -- uploads -----------------------------------------------------------------
    def _need_root(self):
        if self.upload_root is None:
            raise ControllerError("Importing needs a media folder; start the program with --media")
        return self.upload_root

    @staticmethod
    def _purge_stale(root):
        if not root.is_dir():
            return
        cutoff = time.time() - STALE_BATCH_SECONDS
        for folder in root.iterdir():
            try:
                if folder.is_dir() and folder.stat().st_mtime < cutoff:
                    shutil.rmtree(folder, ignore_errors=True)
            except OSError:
                pass

    def new_batch(self):
        root = self._need_root()
        batch = secrets.token_hex(8)
        (root / batch).mkdir(parents=True)
        return batch

    def _batch_dir(self, batch):
        root = self._need_root()
        if not isinstance(batch, str) or not BATCH_PATTERN.fullmatch(batch):
            raise ControllerError("That upload is not recognized; try choosing the files again")
        folder = root / batch
        if not folder.is_dir():
            raise ControllerError("That upload has expired; try choosing the files again")
        return folder

    def save_upload(self, batch, filename, stream, length, kind):
        """Write `length` bytes from `stream` into the batch; returns the saved file name."""
        if kind not in KINDS:
            raise ControllerError("Unknown kind of import")
        folder = self._batch_dir(batch)
        name = safe_filename(filename)
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED[kind]:
            raise ControllerError(f"{name} is not a kind of file this import accepts "
                                  f"({', '.join(sorted(ALLOWED[kind]))})")
        if length <= 0:
            raise ControllerError(f"{name} is empty")
        if length > MAX_FILE_BYTES[kind]:
            raise ControllerError(f"{name} is too large for this import "
                                  f"(over {MAX_FILE_BYTES[kind] // 1024 ** 2} MB)")
        existing = [p for p in folder.iterdir() if p.is_file()]
        if len(existing) >= MAX_FILES:
            raise ControllerError(f"That is too many files at once (more than {MAX_FILES})")
        if sum(p.stat().st_size for p in existing) + length > MAX_BATCH_BYTES:
            raise ControllerError("That is too much data at once; import in smaller groups")
        self._check_room(name, length, folder)
        target = folder / name
        counter = 1
        while target.exists():
            counter += 1
            target = folder / f"{Path(name).stem} ({counter}){suffix}"
        remaining = length
        try:
            with open(target, "wb") as out:
                while remaining:
                    chunk = stream.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ControllerError(f"The upload of {name} stopped early; please try again")
                    out.write(chunk)
                    remaining -= len(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target.name

    @staticmethod
    def _check_room(name, length, folder):
        """Refuse a file the disk cannot hold, in plain words, before any of it is written."""
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return
        if length + KEEP_FREE_BYTES > free:
            def gb(n):
                return f"{n / 1024 ** 3:.1f} GB"
            raise ControllerError(f"{name} is {gb(length)} but this computer's disk has only {gb(free)} free "
                                  f"(and {gb(KEEP_FREE_BYTES)} is kept spare). Free up some space, or put ChurchLight's media "
                                  "folder on a bigger drive, and try again.")

    # -- jobs ----------------------------------------------------------------------
    def jobs(self):
        with self._lock:
            return [j.as_dict() for j in reversed(self._jobs)]

    def replacement(self, job_id, deck_id):
        """The deck a finished "replace slides" import made, after checking the job is for this deck."""
        with self._lock:
            job = next((j for j in self._jobs if j.id == job_id), None)
            if (job is None or job.replace_for != deck_id or job.status != "done" or job.deck_id is None):
                raise ControllerError("That replacement is not ready, or it is for a different slideshow")
            return job.deck_id

    def busy(self):
        with self._lock:
            return any(j.status in ("waiting", "running") for j in self._jobs)

    _MAGIC = {".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8",), ".jpeg": (b"\xff\xd8",),
              ".gif": (b"GIF8",), ".webp": (b"RIFF",)}

    def take_background(self, batch):
        """Move the one uploaded picture into <media>/backgrounds/ and return its media path."""
        folder = self._batch_dir(batch)
        files = [p for p in folder.iterdir() if p.is_file()]
        if len(files) != 1:
            raise ControllerError("Choose exactly one picture")
        source = files[0]
        suffix = source.suffix.lower()
        with open(source, "rb") as handle:
            head = handle.read(12)
        if suffix not in self._MAGIC or not head.startswith(self._MAGIC[suffix]) \
                or (suffix == ".webp" and head[8:12] != b"WEBP"):
            raise ControllerError(f"{source.name} does not look like a {suffix[1:].upper()} picture")
        target_dir = Path(self.c.media_root) / "backgrounds"
        target_dir.mkdir(parents=True, exist_ok=True)
        name = secrets.token_hex(8) + suffix
        shutil.move(str(source), str(target_dir / name))
        shutil.rmtree(folder, ignore_errors=True)
        return "backgrounds/" + name

    @staticmethod
    def _media_type(suffix, head):
        """"video", "audio" or "image" when the first bytes fit the file's ending, else None."""
        if suffix in ImportManager._MAGIC:
            ok = head.startswith(ImportManager._MAGIC[suffix]) and (suffix != ".webp" or head[8:12] == b"WEBP")
            return "image" if ok else None
        if suffix in (".mp4", ".m4v"):
            return "video" if head[4:8] == b"ftyp" else None
        if suffix == ".mp3":
            return "audio" if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0) else None
        if suffix == ".wav":
            return "audio" if head.startswith(b"RIFF") and head[8:12] == b"WAVE" else None
        return None

    def take_media(self, batch, seconds=None):
        """Move the one uploaded picture, video or sound file into <media>/media/ and return (media path, name, kind)."""
        folder = self._batch_dir(batch)
        files = [p for p in folder.iterdir() if p.is_file()]
        if len(files) != 1:
            raise ControllerError("Choose exactly one video or sound file")
        source = files[0]
        suffix = source.suffix.lower()
        with open(source, "rb") as handle:
            head = handle.read(12)
        kind = self._media_type(suffix, head)
        if kind is None:
            raise ControllerError(f"{source.name} cannot be used. Use a picture (PNG, JPG, GIF or WEBP), an MP4 video (H.264) "
                                  "or an MP3 or WAV sound file. A video in another format can be re-saved as MP4 with a free converter such as HandBrake.")
        self.media_warnings = []
        if kind == "video":
            problem, warnings = check_video(source, seconds)
            if problem:
                raise ControllerError(f"{source.name}: {problem}")
            self.media_warnings = [f"{source.name}: {w}" for w in warnings]
        if kind == "image" and source.stat().st_size > MAX_FILE_BYTES["background"]:
            raise ControllerError(f"{source.name} is too large (over {MAX_FILE_BYTES['background'] // 1024 ** 2} MB)")
        target_dir = Path(self.c.media_root) / "media"
        target_dir.mkdir(parents=True, exist_ok=True)
        name = secrets.token_hex(8) + suffix
        shutil.move(str(source), str(target_dir / name))
        shutil.rmtree(folder, ignore_errors=True)
        return "media/" + name, source.stem, kind

    def take_themes_file(self, batch):
        """The one uploaded themes file as (path, folder). The caller deletes the folder when done."""
        folder = self._batch_dir(batch)
        files = [p for p in folder.iterdir() if p.is_file()]
        if len(files) != 1:
            shutil.rmtree(folder, ignore_errors=True)
            raise ControllerError("Choose exactly one themes file")
        return files[0], folder

    def take_pictures(self, batch, subdir, exactly_one=False):
        """Move the uploaded slide pictures into <media>/<subdir>/ and return their media paths, in
        the order of their file names (1, 2, 10 sort naturally). Nothing is moved unless all are pictures."""
        folder = self._batch_dir(batch)
        files = sorted((p for p in folder.iterdir() if p.is_file()), key=_natural_key)
        if not files:
            raise ControllerError("No pictures were uploaded")
        if exactly_one and len(files) != 1:
            raise ControllerError("Choose exactly one picture")
        for source in files:
            suffix = source.suffix.lower()
            with open(source, "rb") as handle:
                head = handle.read(12)
            if suffix not in self._MAGIC or not head.startswith(self._MAGIC[suffix]) \
                    or (suffix == ".webp" and head[8:12] != b"WEBP"):
                raise ControllerError(f"{source.name} does not look like a {suffix[1:].upper() or 'picture'} picture")
        target_dir = Path(self.c.media_root) / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        made = []
        for source in files:
            name = secrets.token_hex(8) + source.suffix.lower()
            shutil.move(str(source), str(target_dir / name))
            made.append(f"{subdir}/{name}")
        shutil.rmtree(folder, ignore_errors=True)
        return made

    def start(self, kind, batch, abbreviation=None, name=None, replace_for=None):
        if kind not in KINDS or kind in ("background", "slidepics", "themes", "media"):
            raise ControllerError("Unknown kind of import")
        if replace_for is not None and kind != "deck":
            raise ControllerError("Only a slideshow can be replaced from a file")
        folder = self._batch_dir(batch)
        files = sorted(p for p in folder.iterdir() if p.is_file())
        if not files:
            raise ControllerError("No files were uploaded")
        plan = self._plan(kind, files)
        if replace_for is not None and plan["mode"] == "bundle":
            raise ControllerError("Choose a PDF or slide pictures to replace slides from")
        label = {"songs": f"{len(files)} song file{'s' if len(files) != 1 else ''}",
                 "bible": files[0].name, "deck": name or plan.get("label", files[0].name)}[kind]
        with self._lock:
            job = ImportJob(self._next_id, kind, label, replace_for=replace_for)
            self._next_id += 1
            self._jobs.append(job)
            del self._jobs[:-KEEP_JOBS]
        self._queue.put((job, folder, plan, abbreviation, name))
        self._ensure_worker()
        self.c.library_changed()
        return job.id

    @staticmethod
    def _plan(kind, files):
        suffixes = [p.suffix.lower() for p in files]
        if kind == "bible":
            if len(files) != 1:
                raise ControllerError("Choose one Bible file at a time")
            return {}
        if kind == "songs":
            return {}
        bundles = [p for p in files if p.suffix.lower() == ".zip"]
        if bundles:
            if len(files) != 1:
                raise ControllerError("Choose one ChurchLight bundle (.zip) on its own")
            return {"mode": "bundle", "bundle": bundles[0], "label": bundles[0].stem}
        pdfs = [p for p in files if p.suffix.lower() == ".pdf"]
        images = [p for p in files if p.suffix.lower() in IMAGE_SUFFIXES]
        if images and pdfs:
            raise ControllerError("Choose either slide pictures or one PDF file, not both")
        if images:
            # A readable name: the picture's own name when there is one, else "Slide pictures <date>".
            # (The upload folder's name is random, so it is never used.)
            label = Path(images[0].name).stem if len(images) == 1 else f"Slide pictures {date.today().isoformat()}"
            return {"mode": "images", "label": label}
        if len(pdfs) == 1:
            return {"mode": "pdf", "pdf": pdfs[0], "label": pdfs[0].stem}
        raise ControllerError("Choose one PDF file, or a set of slide pictures")

    def _ensure_worker(self):
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._run, name="importer", daemon=True)
                self._worker.start()

    def close(self):
        self._closing = True
        self._queue.put(None)

    def _run(self):
        while not self._closing:
            item = self._queue.get()
            if item is None:
                return
            job, folder, plan, abbreviation, name = item
            job.status = "running"
            self.c.library_changed()
            try:
                self._do(job, folder, plan, abbreviation, name)
            except (ImporterError, ControllerError, ValueError, OSError) as err:
                job.status, job.message = "failed", str(err)
                log.warning("Import %s failed: %s", job.id, err)
            except Exception as err:  # noqa: BLE001 - one bad file must never kill the importer
                job.status, job.message = "failed", "Something unexpected went wrong; details are in the log file"
                log.exception("Import %s crashed", job.id)
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            self.c.library_changed()

    def _database(self):
        """(database, closer, lock) for the importer to use."""
        path = self.c.db.path
        if path == ":memory:" or path == "":
            return self.c.db, (lambda: None), self.c.lock
        db = DatabaseManager(path)
        return db, db.close, threading.Lock()

    def _do(self, job, folder, plan, abbreviation, name):
        db, close, lock = self._database()
        try:
            if job.kind == "deck":
                self._import_deck(job, db, folder, plan, name)
            else:
                with lock:
                    self._import_text_kind(job, db, folder, abbreviation, name)
        finally:
            close()

    def _import_text_kind(self, job, db, folder, abbreviation, name):
        files = sorted(p for p in folder.iterdir() if p.is_file())
        if job.kind == "songs":
            report = import_openlyrics(db, folder, pause=SONG_PAUSE)
            job.message = (f"Added {len(report.imported)} song{'s' if len(report.imported) != 1 else ''}"
                           + (f", {report.duplicates} already there" if report.duplicates else "")
                           + (f", {len(report.failed)} could not be read" if report.failed else ""))
            job.notes = ([f"{n}: {m}" for n, m in report.failed]
                         + [f"{n}: {m}" for n, m in report.warnings])[:NOTES_LIMIT]
            job.status = "failed" if report.failed and not report.imported and not report.duplicates else "done"
        else:
            report = import_bible(db, files[0], abbreviation=abbreviation or None, name=name or None)
            job.message = (f"Installed {report.abbreviation}: {report.verse_count} verses "
                           f"in {report.book_count} books")
            job.notes = list(report.warnings)[:NOTES_LIMIT]
            job.status = "done"

    def _import_deck(self, job, db, folder, plan, name):
        if self.c.media_root is None:
            raise ControllerError("Importing needs a media folder; start the program with --media")
        # The slow work (rendering) happens here without the controller's lock when the import has
        # its own connection; with a shared in-memory database the whole thing takes the lock.
        with (self.c.lock if db is self.c.db else _NoLock()):
            if plan["mode"] == "bundle":
                report = import_bundle(db, plan["bundle"], self.c.media_root)
                added = [f"{name} ({pages} pages)" for _id, name, pages in report.imported]
                job.message = ((f"Added {len(added)} slideshow{'s' if len(added) != 1 else ''}" if added else "Nothing new was added")
                               + (f"; {report.duplicates} already in the library" if report.duplicates else ""))
                job.notes = added[:NOTES_LIMIT]
                job.status = "done"
                return
            again = job.replace_for is not None           # a replacement always makes its own copy of the pages
            if plan["mode"] == "images":
                result = import_image_folder(db, folder, self.c.media_root, name=name or plan["label"], force=again)
            else:
                result = import_deck(db, plan["pdf"], self.c.media_root, name=name or None, force=again)
        job.deck_id = result.presentation_id
        if result.duplicate:
            job.message = "That slideshow is already in the library; nothing was added"
        else:
            job.message = (f"Added a slideshow of {result.page_count} page{'s' if result.page_count != 1 else ''}"
                           + (f", {result.notes_count} with speaker notes" if result.notes_count else ""))
        job.notes = list(result.warnings if not result.duplicate else ())[:NOTES_LIMIT]
        job.status = "done"


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
