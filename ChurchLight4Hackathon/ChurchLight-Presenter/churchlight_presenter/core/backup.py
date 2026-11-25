# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Automatic copies of the church's database, so one bad day never loses the songs and services.

A copy is made when ChurchLight starts, before the database is opened (and so before any upgrade touches
it), into a `backups` folder beside the database. The newest few are kept. A copy is a whole, working database:
to go back, close ChurchLight and use `--restore-backup`, or copy one over `churchlight.db` by hand.

A backup holds everything the database does, including the secret in the Trigger URLs. Keep the folder on
the same computer; do not share it.
"""
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("churchlight_presenter.backup")

FOLDER = "backups"
KEEP = 10                  # how many copies are kept
MIN_GAP_SECONDS = 600      # starting twice in a few minutes does not push out the older copies


class DatabaseDamaged(Exception):
    """The database file cannot be read; `str()` says what to do in plain words."""


def backup_folder(db_path):
    return Path(db_path).resolve().parent / FOLDER


def list_backups(db_path):
    """The copies, newest first."""
    folder = backup_folder(db_path)
    stem = Path(db_path).stem
    return sorted(folder.glob(f"{stem}-*.db"), key=lambda p: p.name, reverse=True) if folder.is_dir() else []


def _version(path):
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


def check(db_path):
    """Raise DatabaseDamaged when the file is not a healthy database. A missing or empty file is fine (a new start)."""
    path = Path(db_path)
    if not path.is_file() or path.stat().st_size == 0:
        return
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as err:
        result = str(err)
    if result != "ok":
        log.error("Database check failed: %s", result)
        newest = list_backups(db_path)
        hint = (f"The newest copy is {newest[0].name} in the {FOLDER} folder. Start ChurchLight with --restore-backup "
                "to bring it back." if newest else "There is no backup copy to go back to.")
        raise DatabaseDamaged(f"The church database ({path.name}) is damaged and cannot be opened. {hint}")


def make_backup(db_path, keep=KEEP, now=None, force=False):
    """Copy the database into the backups folder and drop the oldest copies. Returns the new file, or None when
    there is nothing to copy or a copy was made a few minutes ago (`force` copies anyway, before something risky). Never raises: a failed backup must not stop a service."""
    try:
        path = Path(db_path)
        if str(db_path) == ":memory:" or not path.is_file() or path.stat().st_size == 0:
            return None
        existing = list_backups(db_path)
        stamp = time.time() if now is None else now
        if not force and existing and stamp - existing[0].stat().st_mtime < MIN_GAP_SECONDS:
            return None
        folder = backup_folder(db_path)
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{path.stem}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(stamp))}-v{_version(path)}.db"
        target = folder / name
        source = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        try:
            copy = sqlite3.connect(target)
            try:
                source.backup(copy)           # a consistent copy even if something has the database open
            finally:
                copy.close()
        finally:
            source.close()
        for old in list_backups(db_path)[keep:]:
            old.unlink(missing_ok=True)
        log.info("Database backed up as %s", name)
        return target
    except (OSError, sqlite3.Error) as err:
        log.warning("Could not back up the database: %s", err)
        return None


def restore_latest(db_path):
    """Put the newest healthy copy back as the database. The damaged file is kept beside it as `<name>.damaged`.
    Returns the copy used."""
    path = Path(db_path)
    for candidate in list_backups(db_path):
        try:
            check(candidate)
        except DatabaseDamaged:
            continue
        if path.exists():
            path.replace(path.with_name(path.name + ".damaged"))
        for extra in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
            extra.unlink(missing_ok=True)
        source = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
        try:
            target = sqlite3.connect(path)
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        log.info("Database restored from %s", candidate.name)
        return candidate
    raise DatabaseDamaged("There is no healthy backup copy to restore from.")
