# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The song usage log: one row each time a song goes live, led by the CCLI number, then the date and time.

    "ccli number","2025-07-27","11:07:50.139472","486. Faith Is the Victory","authors","copyright","songs","display"

All eight fields are quoted, one row per line. The file only ever grows; the church downloads it when it reports.
"""
import csv
import io
import threading
from pathlib import Path

FILE_NAME = "song_usage.csv"


def format_row(when, song):
    """One CSV line (with its newline) for `song` (a row with title, copyright, ccli_number, author) shown at `when`."""
    out = io.StringIO()
    csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n").writerow([
        song["ccli_number"] or "", when.strftime("%Y-%m-%d"), when.strftime("%H:%M:%S.%f"), song["title"] or "",
        song["author"] or "", song["copyright"] or "", "songs", "display"])
    return out.getvalue()


class UsageLog:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, when, song):
        with self._lock:
            with open(self.path, "a", encoding="utf-8", newline="") as f:
                f.write(format_row(when, song))

    def read(self):
        """The whole file as bytes (empty if nothing has been logged yet)."""
        with self._lock:
            try:
                return self.path.read_bytes()
            except FileNotFoundError:
                return b""
