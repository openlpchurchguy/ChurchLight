# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Run with:  python -m churchlight_presenter --db church.db --media media"""
import argparse
import logging
import os
import platform
import sys
import threading
import time
from pathlib import Path

from . import BUILD
from .core.database import DatabaseManager
from . import launcher, logsetup
from .content import ensure_bundled_bible
from .core import backup, usagelog
from .core.themes import ensure_starter_themes
from .core.outputs import default_settings_path, load_outputs
from .ui.console import Console
from .ui import watchdog
from .ui.controller import ControllerError, OperatorController


SHUTDOWN_GRACE_SECONDS = 1.0       # lets the page receive its answer before the server goes away


def _wait_until_stopped(requested=None):
    """Sit quietly while the web server works. Ctrl+C, closing the window or the Settings tab's shut-down
    button (`requested`, an Event) ends it."""
    (requested or threading.Event()).wait()
    if requested is not None and requested.is_set():
        time.sleep(SHUTDOWN_GRACE_SECONDS)


def _exit_when_asked(controller, profile=None, argv=None):
    """With the typed console the main thread is stuck waiting for typing, so the button closes things here."""
    controller.shutdown_requested.wait()
    time.sleep(SHUTDOWN_GRACE_SECONDS)
    _close_within(controller)
    launcher.close_browsers_using(profile)       # the operator window and screen windows this program opened
    logsetup.close_logging()
    os._exit(0)


def _close_within(controller, seconds=5.0):
    """Close everything, but never wait for it forever: a program that has seized up must still be able to shut down."""
    closer = threading.Thread(target=controller.close, name="closing", daemon=True)
    closer.start()
    closer.join(seconds)
    if closer.is_alive():
        logging.getLogger("churchlight_presenter.main").error("Closing did not finish in %s seconds; leaving anyway", seconds)


def _browser_profile_of(argv):
    """The browser profile folder for the --db in these arguments (as main() works it out)."""
    db = "churchlight.db"
    for i, a in enumerate(argv):
        if a == "--db" and i + 1 < len(argv):
            db = argv[i + 1]
        elif a.startswith("--db="):
            db = a[5:]
    return _browser_profile(db)


def _force_exit_later(seconds=15.0):
    """Shutting down must end the program even if something refuses to close: after a while, leave regardless.
    Only when this is the real program (not when the tests call main(), which must not end the test run)."""
    if __name__ != "__main__":
        return

    def run():
        time.sleep(seconds)
        logging.getLogger("churchlight_presenter.main").error("Shutdown took over %s seconds; leaving now", seconds)
        logsetup.close_logging()
        os._exit(0)
    threading.Thread(target=run, name="force-exit", daemon=True).start()


def _restart_now(controller, argv):
    """The program stopped answering: start a fresh copy and leave. Closing is tried, but not waited for, because
    whatever is stuck may be what closing needs."""
    _close_within(controller)
    launcher.close_browsers_using(_browser_profile_of(argv))     # the screens (the new copy opens them again)
    watchdog.spawn_restart(argv)
    logsetup.close_logging()
    os._exit(0)             # 0, so start_churchlight.bat does not stop at "ChurchLight stopped because of a problem"


def _browser_profile(db_path):
    """A folder next to the database for the operator window's own browser profile; None for a throwaway database."""
    if not db_path or str(db_path) == ":memory:":
        return None
    return str(Path(db_path).resolve().parent / "browser-profile")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="churchlight_presenter",
                                     description="ChurchLight Presenter (terminal operator)")
    parser.add_argument("--db", default="churchlight.db", help="database file (made if missing)")
    parser.add_argument("--media", default="media", help="folder holding imported slide images")
    parser.add_argument("--port", type=int, default=8765, help="web server port (0 = any free port)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="address to listen on; keep the default unless you trust the network")
    parser.add_argument("--open", action="store_true",
                        help="open the operator page in its own window (Edge or Chrome)")
    parser.add_argument("--no-console", action="store_true",
                        help="no typed commands: run until stopped (Ctrl+C or close this window)")
    parser.add_argument("--no-server", action="store_true", help="do not start the web server")
    parser.add_argument("--log-file", help="log file (default: churchlight.log beside the database)")
    parser.add_argument("--verbose", action="store_true", help="write extra detail to the log file")
    parser.add_argument("--restore-backup", action="store_true",
                        help="put the newest backup copy back as the database (the old file is kept as .damaged), then exit")
    parser.add_argument("--translation", help="Bible translation abbreviation for typed references")
    args = parser.parse_args(argv)
    restart_argv = list(sys.argv[1:] if argv is None else argv)

    if args.no_server and (args.open or args.no_console):
        parser.error("--open and --no-console need the web server; remove --no-server")
    log_path = logsetup.setup_logging(args.log_file or logsetup.default_log_path(args.db), args.verbose)
    log = logging.getLogger("churchlight_presenter.main")   # (__name__ is "__main__" under python -m)
    log.info("Starting ChurchLight build %s: Python %s on %s; database %s, media %s", BUILD, platform.python_version(),
             platform.platform(), Path(args.db).name, args.media)
    try:
        outputs = load_outputs(default_settings_path())
    except ValueError as err:
        print(err, file=sys.stderr)
        return 2
    if args.restore_backup:
        try:
            used = backup.restore_latest(args.db)
        except backup.DatabaseDamaged as err:
            print(err, file=sys.stderr)
            return 2
        print(f"Restored {args.db} from {used.name}. Start ChurchLight again.")
        return 0
    try:
        backup.check(args.db)
    except backup.DatabaseDamaged as err:
        print(err, file=sys.stderr)
        return 2
    backup.make_backup(args.db)             # before anything opens (or upgrades) the database
    with DatabaseManager(args.db, shared=True) as db:   # the operator page uses it from other threads
        ensure_starter_themes(db)
        if db.get_setting("notice_slide") is None:           # first start: every song gets its title and CCLI slide, before the song
            db.set_setting("notice_slide", "before")
        ensure_bundled_bible(db)            # the KJV, once, only when the church has no Bible yet
        controller = OperatorController(db, outputs)
        controller.set_usage_log(Path(args.db).resolve().parent / usagelog.FILE_NAME)   # beside the database
        controller.set_clicker_dir(Path(args.db).resolve().parent)                      # the clicker helper's script goes here
        if _browser_profile(args.db):
            controller.set_screen_profile_dir(_browser_profile(args.db))                # screens opened on monitors keep settings here
        try:
            if args.translation:
                controller.translation = args.translation
            if not args.no_server:
                server = controller.serve(media_root=args.media, host=args.host, port=args.port)
                print(f"Browser sources are served at {server.url}/ (list of outputs)")
                print(f"Operator page: {server.url}/operator")
                print(f"This is ChurchLight build {BUILD}")
                controller.auto_start_clicker()                  # only if the church ticked "start with ChurchLight"
                port = server.address[1]
                watchdog.Watchdog(lambda: watchdog.probe(port), lambda: _restart_now(controller, restart_argv)).start()
                controller.screen_windows.start_watching()       # a screen window closed by accident opens again
                controller.auto_open_screens()                   # only if the church ticked "open the projector windows at start"
            if log_path:
                print(f"Log file: {log_path}")
            if args.open:
                how = launcher.open_operator(f"{server.url}/operator?app=1", window=controller.operator_window(),
                                            profile=_browser_profile(args.db))
                if how is None:
                    print(f"Could not open a browser by itself. Open this address in Edge or Chrome: {server.url}/operator")
            if args.no_console:
                print("ChurchLight is running. Press Ctrl+C, or close this window, to stop it.")
                try:
                    _wait_until_stopped(controller.shutdown_requested)
                except KeyboardInterrupt:
                    print("Stopping.")
            else:
                threading.Thread(target=_exit_when_asked, args=(controller, _browser_profile(args.db), restart_argv), daemon=True).start()
                Console(controller).cmdloop()
        except (ControllerError, OSError) as err:
            log.error("Could not start: %s", err)
            print(f"Could not start: {err}", file=sys.stderr)
            return 1
        finally:
            _force_exit_later()
            controller.screen_windows.close_all()                # first, so the screens close even if something below hangs
            launcher.close_browsers_using(_browser_profile(args.db))
            _close_within(controller)
            left = [t.name for t in threading.enumerate() if t is not threading.main_thread()]
            log.info("Stopped (threads still alive: %s)", ", ".join(left) or "none")
            logsetup.close_logging()
    return 0


if __name__ == "__main__":
    sys.exit(main())
