# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Starts ChurchLight again by itself if it stops answering, and does the same when asked (the Restart button).

A thread asks the program's own server for its state every few seconds. The answer needs the same lock the whole
program uses, so a program that is stuck does not answer. After several silent checks in a row it is restarted:
the new copy opens the screens again, and the operator page picks up where it was."""
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

log = logging.getLogger("churchlight_presenter.watchdog")

RESTART_ENV = "CHURCHLIGHT_RESTARTS"
MAX_RESTARTS = 5            # in a row, so a program that cannot start is not restarted forever
PAUSE_BEFORE_START = 3      # seconds the new copy waits, so the old one has let go of the port


def probe(port, timeout=5.0):
    """True when the program answers its state request in time."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=timeout) as reply:
            reply.read()
            return reply.status == 200
    except Exception:  # noqa: BLE001 - any failure is "no answer"
        return False


def thread_report():
    """Where every thread of the program is right now, in plain text. Written to the log when the program is stuck, so
    the reason can be read afterwards (which thread waits for what)."""
    names = {t.ident: t.name for t in threading.enumerate()}
    parts = []
    for ident, frame in sys._current_frames().items():
        parts.append(f"--- thread {names.get(ident, ident)}\n" + "".join(traceback.format_stack(frame)))
    return "\n".join(parts)


def restart_args(argv, keep_open=False):
    """The arguments to start again with. Without `keep_open` there is no --open: after a freeze the operator window is
    still there and reconnects by itself, so a second one is not wanted."""
    return list(argv) if keep_open else [a for a in argv if a != "--open"]


def restarts_so_far(environ=None):
    try:
        return int((environ if environ is not None else os.environ).get(RESTART_ENV, "0"))
    except ValueError:
        return 0


def spawn_restart(argv, popen=subprocess.Popen, environ=None, executable=None, keep_open=False):
    """Start the new copy (it waits a moment for this one to let go of the port). Returns False when it has been
    restarted too many times already."""
    count = restarts_so_far(environ)
    if count >= MAX_RESTARTS:
        log.error("Not restarting again: it has already been restarted %s times in a row", count)
        return False
    env = dict(os.environ if environ is None else environ)
    env[RESTART_ENV] = str(count + 1)
    code = ("import runpy, sys, time; time.sleep(%d); sys.argv = ['churchlight_presenter'] + sys.argv[1:]; "
            "runpy.run_module('churchlight_presenter', run_name='__main__')" % PAUSE_BEFORE_START)
    command = [executable or sys.executable, "-c", code] + restart_args(argv, keep_open)
    popen(command, env=env, stdin=subprocess.DEVNULL, close_fds=True)
    log.warning("Restarting ChurchLight (restart %s)", count + 1)
    return True


class Watchdog:
    def __init__(self, check, on_stuck, every=3.0, failures=5):
        self.check = check                    # () -> True when the program answers
        self.on_stuck = on_stuck              # called once when it has not answered `failures` times in a row
        self.every = every
        self.failures = failures
        self.missed = 0
        self.was_up = False                   # never act before the program has answered once
        self._stop = threading.Event()

    def step(self):
        """One check. Returns True when the program was judged stuck (and on_stuck was called)."""
        if self.check():
            self.was_up = True
            self.missed = 0
            return False
        if not self.was_up:
            return False
        self.missed += 1
        if self.missed == 3 or self.missed >= self.failures:          # while it is still stuck: what is every thread doing?
            log.error("ChurchLight is not answering (%s checks missed). Threads:\n%s", self.missed, thread_report())
        if self.missed >= self.failures:
            self.missed = 0
            log.error("ChurchLight did not answer %s checks in a row", self.failures)
            self.on_stuck()
            return True
        return False

    def start(self):
        def run():
            while not self._stop.wait(self.every):
                try:
                    self.step()
                except Exception:  # noqa: BLE001
                    log.debug("Watchdog check failed", exc_info=True)
        threading.Thread(target=run, name="watchdog", daemon=True).start()

    def stop(self):
        self._stop.set()
