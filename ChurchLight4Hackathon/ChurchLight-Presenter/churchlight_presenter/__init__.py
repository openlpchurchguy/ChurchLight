# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""ChurchLight Presenter."""
import logging

BUILD = "zt271"       # the test-build number: shown in Settings and written to the log, so a church can tell which copy is running

# A library should not print log messages by itself; logsetup.py attaches the real log file.
logging.getLogger(__name__).addHandler(logging.NullHandler())
