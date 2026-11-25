# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""A small log file, so a problem on a church PC can be looked into afterwards.

The log records what happened (a service was opened, an item went live, an import failed, an
error with its traceback), not what was shown: no lyrics, verse text, announcement text or
speaker notes are written. It rotates (about 1 MB, three old copies), so it never fills the disk.

Failing to open the log file (a read-only folder, say) is reported once and never stops the program.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

PACKAGE = "churchlight_presenter"
MAX_BYTES = 1_000_000
BACKUPS = 3
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_handler = None


def default_log_path(db_path):
    """`churchlight.log` in the folder that holds the database."""
    return Path(db_path).resolve().parent / "churchlight.log"


def setup_logging(path, verbose=False, warn=None):
    """Start logging to `path`; returns the path used, or None if the file could not be opened.

    Calling it again replaces the earlier setup. `warn(text)` receives the reason on failure
    (default: print to stderr).
    """
    global _handler
    close_logging()
    logger = logging.getLogger(PACKAGE)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False              # keep stray messages off the operator's console
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                      encoding="utf-8", delay=False)
    except OSError as err:
        (warn or (lambda text: print(text, file=sys.stderr)))(
            f"Could not open the log file {path}: {err}. Carrying on without a log.")
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    _handler = handler
    return path


def current_log_path():
    return Path(_handler.baseFilename) if _handler is not None else None


def close_logging():
    """Stop logging to the file and release it (needed before deleting it on Windows)."""
    global _handler
    if _handler is not None:
        logging.getLogger(PACKAGE).removeHandler(_handler)
        _handler.close()
    _handler = None
