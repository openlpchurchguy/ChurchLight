# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Content that ships with the program, installed once on a first run.

Right now that is the King James Bible (`bibles/kjv.csv`, public domain in the United States; its
source and notice are in `NOTICES.txt`). It is installed only when the database holds no Bible at all,
it is made the default Bible, and it is never put back: once the decision is recorded (installed, or
skipped because the church already had a Bible), deleting the KJV later keeps it deleted.
"""
import hashlib
import logging
from pathlib import Path

from ..importers.bibles import ImporterError, import_bible

log = logging.getLogger("churchlight.content")

FOLDER = Path(__file__).resolve().parent
KJV_FILE = FOLDER / "bibles" / "kjv.csv"
KJV_DIGEST = FOLDER / "bibles" / "kjv.sha256"
BIBLE_FLAG = "bundled_bible"
BIBLE_VERSION = "kjv-1"
KJV_NOTE = ("King James Version (1769 text), from eBible.org (eng-kjv2006). Public domain in the United States; "
            "in the United Kingdom the Crown holds a printing right. See NOTICES.txt.")


def kjv_is_intact(csv_path=None, digest_path=None):
    """True when the bundled file matches the checksum written when it was built."""
    csv_path, digest_path = Path(csv_path or KJV_FILE), Path(digest_path or KJV_DIGEST)
    try:
        wanted = digest_path.read_text(encoding="utf-8").split()[0]
        return hashlib.sha256(csv_path.read_bytes()).hexdigest() == wanted
    except (OSError, IndexError):
        return False


def ensure_bundled_bible(db, csv_path=None, digest_path=None):
    """Install the KJV once, on a database with no Bible. Returns the import report, or None.

    Never raises: a missing or damaged file is logged and the program starts without it (the
    decision is not recorded then, so a repaired package installs it next time)."""
    if db.get_setting(BIBLE_FLAG) == BIBLE_VERSION:
        return None
    if db.conn.execute("SELECT 1 FROM translations LIMIT 1").fetchone() is not None:
        db.set_setting(BIBLE_FLAG, BIBLE_VERSION)           # the church already has a Bible: leave things alone
        return None
    csv_path = Path(csv_path or KJV_FILE)
    if not kjv_is_intact(csv_path, digest_path):
        log.error("The bundled King James file is missing or damaged; no Bible was installed")
        return None
    try:
        report = import_bible(db, csv_path, abbreviation="KJV", name="King James Version",
                              language="en", license_notes=KJV_NOTE)
    except ImporterError as err:
        log.error("Could not install the bundled King James Bible: %s", err)
        return None
    db.set_setting("default_translation", "KJV")
    db.set_setting(BIBLE_FLAG, BIBLE_VERSION)
    log.info("Installed the bundled King James Bible (%d verses)", report.verse_count)
    return report
