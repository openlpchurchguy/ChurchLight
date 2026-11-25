# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""A small local web server that hands each output's payload to a web page.

XSplit, OBS and any browser can load `/view/<name>` as a browser source. The
page then asks `/out/<name>` for what to show. Everything comes from the same
`build_payload` the native windows use, so the rule that speaker notes reach
stage layouts only is enforced in one place, not here.

Addresses
    /                    list of outputs (and the source-code link)
    /view/<name>         the page for one output (transparent for public layouts)
    /out/<name>          that output's payload as JSON
    /out/<name>?peek=1          (the operator's preview frames) the live slide even while the screens are blanked
    /out/<name>?since=N&wait=S
                         "tell me when the version is no longer N": answers at
                         once if it already differs, otherwise holds the request
                         up to S seconds (at most 30), then answers 304 meaning
                         "nothing new". Plain polling is the same call without wait.
    /static/<file>       the page's own CSS and JS
    /media/<path>        slide images, from the media folder only
    /source              redirect to the source code (AGPL section 13)

With an operator API (see ui/operator_api.py) there are also
    /operator            the operator's control page
    /api/export?kind=songs|slideshows|themes|usage&ids=1,2|all=1&format=xml|txt   a file to save (songs, a slideshow bundle, themes)
    /api/state?since=N&wait=S   the operator's view of everything (long-poll like /out)
    /api/library?q=TEXT  songs and decks matching TEXT
    /api/suggest?q=TEXT  a few names to offer while TEXT is typed (what was used lately if empty)
    /api/manage          the whole library with how much each thing is used
    POST /api/do         {"action": ..., "args": {...}}: do something
    POST /api/upload?batch=ID&kind=K&name=FILE   one file's bytes, for an import
The /api routes only answer requests addressed to this computer (a Host check, which also stops
"DNS rebinding" from a web page), and POST needs a JSON body and an X-ChurchLight header, which a
web page on another site cannot send without the browser first asking us (we never say yes).

The server listens on this computer only (127.0.0.1) unless told otherwise.
Anyone who can reach a stage output can read the speaker notes, so only widen
the address on a network you trust.
"""
import hashlib
import html
import json
import logging
import re
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..core.outputs import build_payload

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = {"output.html", "output.css", "output.js", "themefonts.css",
                "operator.html", "operator.css", "operator.js", "look.js", "themes.js", "wizard.js"}
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}
MAX_BODY_BYTES = 64 * 1024
API_HEADER = "X-ChurchLight"
MAX_WAIT = 30.0
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
# Video and sound files, sent in pieces (see _stream_file): a clip is never read into memory whole.
MEDIA_TYPES = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mp3": "audio/mpeg", ".wav": "audio/wav"}
STREAM_CHUNK = 64 * 1024
# The page only loads things from this server, whatever the slide text says.
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; media-src 'self'; font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'")
# The operator page may also show this server's own output pages in small preview frames, and keeps one hidden sound frame
# on the other loopback name (localhost / 127.0.0.1), so that frame has connections of its own (see hearAddress in operator.js).
# It also reads the length of a video or sound file from the computer before uploading it (a blob: address).
OPERATOR_CSP = PAGE_CSP.replace("media-src 'self'", "media-src 'self' blob:") + "; frame-src 'self' http://localhost:* http://127.0.0.1:*"


class OutputServer:
    """Serves a LiveSession's outputs over HTTP.

    `port=0` picks a free port (see `.url`). `media_root` is the folder that
    slide `image_path` values are relative to; without it no images are served.
    `source_url` is where the program's source can be found (AGPL section 13).
    `style_for(theme_id)` supplies theme looks for the public outputs (see core/themes.py).
    """

    def __init__(self, session, outputs, host="127.0.0.1", port=0, media_root=None,
                 source_url=None, style_for=None, operator=None, confidence_theme=None, home=None, hook=None,
                 verse_over_theme=None, timer_style=None, confidence_words=None, sound_output=None):
        self.session = session
        self.operator = operator
        self.loopback_only = host in ("127.0.0.1", "localhost", "::1")
        self.style_for = style_for
        self.confidence_theme = confidence_theme      # callable: the confidence monitor's theme id
        self.confidence_words = confidence_words      # callable: does Hide words also hide the confidence monitor's words
        self.timer_style = timer_style                # callable: the countdown and alert look (timerstyle.py)
        self.verse_over_theme = verse_over_theme      # callable: the overlay theme for a verse over a slideshow
        self.sound_output = sound_output              # callable: the name of the one screen that plays sound
        self.home = home                              # callable: the home screen picture shown while blanked, or None
        self.hook = hook                              # callable(token) -> True when trigger URLs are on and token is the secret
        self._hook_misses = []                        # when wrong secrets were tried lately (a slow-down, not a lock-out)
        self.media_root = Path(media_root).resolve() if media_root else None
        self.source_url = source_url
        self.build = self._build_id()                 # changes whenever the output page's own files change
        self._outputs = {}
        self.set_outputs(outputs)
        self._changed = threading.Condition()
        self._ticks = 0                   # counts changes, so a waiter can tell that one happened while it was not holding _changed
        self._closing = False
        self._thread = None
        self._unsubscribe = session.subscribe(self._on_change)
        try:
            self._httpd = _Httpd((host, port), _make_handler(self))
        except BaseException:
            self._unsubscribe()
            raise
        if host not in ("127.0.0.1", "localhost", "::1"):
            log.warning("Output server listening on %s: speaker notes on stage "
                        "outputs are readable by anyone who can reach it", host)

    # -- configuration -----------------------------------------------------
    def set_outputs(self, outputs):
        """Replace the set of outputs; takes effect on the next request."""
        outputs = list(outputs)
        by_name = {o.name: o for o in outputs}
        if len(by_name) != len(outputs):
            raise ValueError("Output names must be unique")
        self._outputs = by_name

    @property
    def outputs(self):
        return list(self._outputs.values())

    @property
    def address(self):
        return self._httpd.server_address[:2]

    @property
    def url(self):
        host, port = self.address
        return f"http://{'[' + host + ']' if ':' in host else host}:{port}"

    # -- running -------------------------------------------------------------
    def start(self):
        """Serve in a background thread and return at once."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever,
                                            name="output-server", daemon=True)
            self._thread.start()
        return self

    def serve_forever(self):
        self._httpd.serve_forever()

    def close(self):
        """Stop serving, release waiting requests and the port. Safe to repeat."""
        with self._changed:
            self._closing = True
            self._changed.notify_all()
        self._unsubscribe()
        if self._thread is not None:
            self._httpd.shutdown()
            self._thread.join(timeout=5)
            self._thread = None
        self._httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- used by the request handler ----------------------------------------
    def _on_change(self, state):
        with self._changed:
            self._ticks += 1
            self._changed.notify_all()

    def wait_for_change(self, since, wait):
        """Block until the session version differs from `since` (or `wait` seconds pass).

        The session's lock is never asked for while `_changed` is held: a thread telling listeners of a change can be
        holding the session's lock while it waits for `_changed`, so holding `_changed` and waiting for the session
        would be a deadlock (the whole program stopped answering because of exactly that)."""
        deadline = time.monotonic() + wait
        while True:
            with self._changed:
                ticks = self._ticks
                if self._closing:
                    return
            if self.session.version != since:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            with self._changed:
                if self._ticks == ticks and not self._closing:      # nothing happened since the check: sleep until something does
                    self._changed.wait(remaining)

    @staticmethod
    def _build_id():
        digest = hashlib.sha1()
        for name in ("output.html", "output.js", "output.css"):
            try:
                digest.update((STATIC_DIR / name).read_bytes())
            except OSError:
                pass
        return digest.hexdigest()[:12]

    def payload_for(self, output, peek=False):
        payload = build_payload(self.session.state(), output, self.style_for,
                                self.confidence_theme() if self.confidence_theme else None,
                                self.home() if self.home else None, peek=peek,
                                verse_over_theme_id=self.verse_over_theme() if self.verse_over_theme else None,
                                timer_style=self.timer_style() if self.timer_style else None,
                                confidence_hides_words=self.confidence_words() if self.confidence_words else True,
                                sound_output=self.sound_output() if self.sound_output else None)
        payload["build"] = self.build      # a page left open across an update sees it change and reloads itself
        return payload


class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows, SO_REUSEADDR lets a second program share a busy port instead of reporting
    # the clash (two copies would silently split the browser sources between them).
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address):
        # A browser source being closed mid-request is normal, not an error.
        log.debug("request from %s ended early", client_address, exc_info=True)


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ChurchLight"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            text = re.sub(r"/hook/[^/\s]+", "/hook/***", format % args)      # the trigger secret never goes in the log
            log.debug("%s - %s", self.address_string(), text)

        # -- plumbing ---------------------------------------------------------
        def _send(self, status, body=b"", content_type="text/plain; charset=utf-8",
                  headers=()):
            self.send_response(status)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for key, value in headers:
                self.send_header(key, value)
            if status != 304:
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if status != 304 and self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status, data):
            self._send(status, json.dumps(data).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _error(self, status, message):
            self._json(status, {"error": message})

        # -- routing ----------------------------------------------------------
        def do_GET(self):
            parts = urlsplit(self.path)
            segments = [unquote(s) for s in parts.path.split("/")[1:]]
            query = parse_qs(parts.query)
            head = segments[0] if segments else ""
            try:
                if parts.path == "/":
                    self._index()
                elif head == "view" and len(segments) == 2:
                    self._view(segments[1])
                elif head == "out" and len(segments) == 2:
                    self._out(segments[1], query)
                elif head == "static" and len(segments) == 2:
                    self._static(segments[1])
                elif head == "static" and len(segments) == 3 and segments[1] == "fonts":
                    self._font(segments[2])
                elif head == "media" and len(segments) >= 2:
                    self._media(segments[1:])
                elif head == "hook" and len(segments) == 3:
                    self._hook(segments[1], segments[2])
                elif parts.path == "/source":
                    self._source()
                elif parts.path == "/operator":
                    self._operator_page()
                elif parts.path == "/api/state":
                    self._api_state(query)
                elif parts.path == "/api/library":
                    self._api_library(query)
                elif parts.path == "/api/suggest":
                    self._api_suggest(query)
                elif parts.path == "/api/export":
                    self._api_export(query)
                elif parts.path == "/api/manage":
                    if self._api_allowed():
                        self._api_reply(app.operator.manage)
                else:
                    self._error(404, "Not found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_HEAD(self):
            if urlsplit(self.path).path.startswith("/hook/"):
                return self._send(405, headers=[("Allow", "GET, POST")])      # a link preview must never press a button
            self.do_GET()

        def do_POST(self):
            try:
                segments = [unquote(s) for s in urlsplit(self.path).path.split("/")[1:]]
                if segments[:1] == ["hook"] and len(segments) == 3:
                    self._hook(segments[1], segments[2])
                elif urlsplit(self.path).path == "/api/do":
                    self._api_do()
                elif urlsplit(self.path).path == "/api/upload":
                    self._api_upload(parse_qs(urlsplit(self.path).query))
                else:
                    self.send_error(501, "Unsupported method ('POST')")
            except (BrokenPipeError, ConnectionResetError):
                pass

        # -- routes -----------------------------------------------------------
        def _index(self):
            rows = "".join(
                f'<li><a href="/view/{html.escape(o.name)}">{html.escape(o.name)}</a> '
                f"&mdash; {html.escape(o.layout)}</li>" for o in app.outputs)
            source = ('<p><a href="/source">Source code</a></p>' if app.source_url else "")
            page = ("<!doctype html><meta charset=utf-8><title>ChurchLight Presenter</title>"
                    f"<h1>ChurchLight Presenter</h1><p>Outputs:</p><ul>{rows}</ul>{source}")
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8",
                       [("Content-Security-Policy", "default-src 'none'; style-src 'none'")])

        def _view(self, name):
            if name not in app._outputs:
                return self._error(404, f"No output named {name!r}")
            self._static("output.html")

        def _out(self, name, query):
            output = app._outputs.get(name)
            if output is None:
                return self._error(404, f"No output named {name!r}")
            since = None
            try:
                if "since" in query:
                    since = int(query["since"][0])
                wait = float(query["wait"][0]) if "wait" in query else 0.0
            except ValueError:
                return self._error(400, "since must be a whole number and wait a number")
            if not 0 <= wait < float("inf"):
                return self._error(400, "wait must be zero or more")
            if since is not None and wait > 0:
                app.wait_for_change(since, min(wait, MAX_WAIT))
            payload = app.payload_for(output, peek="peek" in query)
            if since is not None and payload["version"] == since:
                return self._send(304)
            self._json(200, payload)

        def _static(self, filename):
            if filename not in STATIC_FILES:
                return self._error(404, "Not found")
            try:
                body = (STATIC_DIR / filename).read_bytes()
            except OSError:
                return self._error(404, "Not found")
            content_type = {"html": "text/html", "css": "text/css",
                            "js": "text/javascript"}[filename.rsplit(".", 1)[1]]
            policy = OPERATOR_CSP if filename == "operator.html" else PAGE_CSP
            self._send(200, body, content_type + "; charset=utf-8",
                       [("Content-Security-Policy", policy)])

        def _font(self, filename):
            """The operator page's fonts, from static/fonts. Only plain .ttf/.woff2 names are served."""
            if not re.fullmatch(r"[A-Za-z0-9_\-]+\.(ttf|woff2)", filename):
                return self._error(404, "Not found")
            try:
                body = (STATIC_DIR / "fonts" / filename).read_bytes()
            except OSError:
                return self._error(404, "Not found")
            content_type = "font/woff2" if filename.endswith(".woff2") else "font/ttf"
            self._send(200, body, content_type, [("Cache-Control", "max-age=86400")])

        def _media(self, segments):
            root = app.media_root
            if root is None:
                return self._error(404, "Not found")
            suffix = Path(segments[-1]).suffix.lower()
            content_type = IMAGE_TYPES.get(suffix) or MEDIA_TYPES.get(suffix)
            if content_type is None or any(s in ("", ".", "..") or s.startswith(".") or "\\" in s
                                           or "\0" in s for s in segments):
                return self._error(404, "Not found")
            try:
                path = root.joinpath(*segments).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    return self._error(404, "Not found")
                if suffix in MEDIA_TYPES:
                    return self._stream_file(path, content_type)
                body = path.read_bytes()
            except (OSError, ValueError):
                return self._error(404, "Not found")
            self._send(200, body, content_type)

        def _stream_file(self, path, content_type):
            """Send a video or sound file a piece at a time, honouring one `Range: bytes=a-b` request (a browser asks for
            these to start playing and to jump about). Memory use is one small buffer, whatever the file's size. The
            program's data is never touched here, so playing a clip cannot hold up the operator's buttons."""
            size = path.stat().st_size
            first, last, partial = 0, size - 1, False
            header = self.headers.get("Range")
            if header:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
                start, end = (match.group(1), match.group(2)) if match else ("", "")
                if not match or (start == "" and end == "") or size == 0:
                    return self._range_error(size)
                if start == "":                              # the last N bytes
                    first, last = max(0, size - int(end)), size - 1
                else:
                    first = int(start)
                    last = min(int(end), size - 1) if end != "" else size - 1
                if first > last or first >= size:
                    return self._range_error(size)
                partial = True
            length = last - first + 1 if size else 0
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if partial:
                self.send_header("Content-Range", f"bytes {first}-{last}/{size}")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if self.command == "HEAD" or not length:
                return
            try:
                with open(path, "rb") as handle:
                    handle.seek(first)
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(STREAM_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (ConnectionError, OSError):               # the page moved on (a seek, a new slide): normal
                self.close_connection = True

        def _range_error(self, size):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def _hook(self, token, action):
            """A trigger URL: /hook/<secret>/<button>. Wrong secret, switched off, or no such button all
            look the same (404), so nothing can be learned by guessing."""
            if not self._api_allowed():
                return
            now = time.monotonic()
            misses = app._hook_misses
            misses[:] = [t for t in misses if now - t < 60]
            if len(misses) >= 10:
                return self._error(429, "Too many wrong tries; wait a minute")
            if app.hook is None or not app.hook(token):
                misses.append(now)
                return self._error(404, "Not found")
            from ..ui.operator_api import ApiError
            try:
                app.operator.trigger(action)
            except ApiError as err:
                return self._error(err.status, str(err))
            except Exception:  # noqa: BLE001
                log.exception("A trigger URL failed")
                return self._error(500, "Something went wrong; details are in the log file")
            self._send(200, f"ok: {action}\n".encode("utf-8"))

        # -- operator routes ----------------------------------------------------
        def _api_allowed(self):
            """Only answer requests that were really addressed to this computer's own server."""
            if app.operator is None:
                self._error(404, "Not found")
                return False
            host = self.headers.get("Host", "")
            name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if app.loopback_only and name not in LOOPBACK_NAMES:
                self._error(403, "This address is not allowed")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and urlsplit(origin).netloc != host:
                self._error(403, "Requests from other sites are not allowed")
                return False
            return True

        def _operator_page(self):
            if app.operator is None:
                return self._error(404, "Not found")
            if app.loopback_only and not self._host_is_loopback():
                return self._error(403, "This address is not allowed")
            self._static("operator.html")

        def _host_is_loopback(self):
            host = self.headers.get("Host", "")
            name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            return name in LOOPBACK_NAMES

        def _api_state(self, query):
            if not self._api_allowed():
                return
            try:
                since = int(query["since"][0]) if "since" in query else None
                wait = float(query["wait"][0]) if "wait" in query else 0.0
            except ValueError:
                return self._error(400, "since must be a whole number and wait a number")
            if not 0 <= wait < float("inf"):
                return self._error(400, "wait must be zero or more")
            if since is not None and wait > 0:
                app.operator.wait_for_change(since, min(wait, MAX_WAIT))
            if since is not None and app.operator.version == since:
                return self._send(304)
            self._api_reply(app.operator.state)

        def _api_library(self, query):
            if not self._api_allowed():
                return
            text = query.get("q", [""])[0][:200]
            kind = query.get("kind", [""])[0]            # "song" or "presentation" narrows the list; anything else is all
            self._api_reply(lambda: {"entries": app.operator.library(text, kind)})

        def _api_suggest(self, query):
            if not self._api_allowed():
                return
            text = query.get("q", [""])[0][:200]
            kind = query.get("kind", [""])[0]
            self._api_reply(lambda: {"entries": app.operator.suggest(text, kind)})

        def _api_export(self, query):
            """A file for the browser to save: songs as .xml/.txt (a .zip for several), slideshows as a bundle."""
            if not self._api_allowed():
                return
            from ..ui.operator_api import ApiError
            kind = query.get("kind", [""])[0]
            everything = query.get("all", [""])[0] == "1"
            try:
                ids = [int(i) for i in query.get("ids", [""])[0].split(",") if i.strip()][:5000]
            except ValueError:
                return self._error(400, "ids must be whole numbers separated by commas")
            path = None
            try:
                if kind == "songs":
                    name, content_type, body = app.operator.export_songs(ids, query.get("format", ["xml"])[0], everything)
                elif kind == "usage":
                    name, content_type, body = app.operator.export_usage()
                elif kind == "slideshows":
                    name, content_type, path = app.operator.export_slideshows(ids, everything, query.get("skip_video", [""])[0] == "1")
                elif kind == "themes":
                    name, content_type, path = app.operator.export_themes(ids, everything)
                else:
                    return self._error(400, "Export songs, slideshows or themes")
                disposition = ("attachment; filename=\"" + name.encode("ascii", "replace").decode().replace('"', "_")
                               + "\"; filename*=UTF-8''" + quote(name))
                if path is None:
                    return self._send(200, body, content_type, [("Content-Disposition", disposition)])
                self.send_response(200)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Disposition", disposition)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(path.stat().st_size))
                self.end_headers()
                with open(path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 1 << 20)
            except ApiError as err:
                self._error(err.status, str(err))
            except Exception:  # noqa: BLE001
                log.exception("An export failed")
                self._error(500, "Something went wrong; details are in the log file")
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)

        def _api_do(self):
            if not self._api_allowed():
                return
            if self.headers.get(API_HEADER) is None:
                return self._error(403, f"Missing the {API_HEADER} header")
            if not (self.headers.get("Content-Type") or "").lower().startswith("application/json"):
                return self._error(415, "Send JSON")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._error(411, "Content-Length is required")
            if not 0 <= length <= MAX_BODY_BYTES:
                return self._error(413, "That request is too large")
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._error(400, "That was not valid JSON")
            if not isinstance(body, dict) or not isinstance(body.get("action"), str):
                return self._error(400, "Expected {\"action\": ..., \"args\": {...}}")
            self._api_reply(lambda: app.operator.call(body["action"], body.get("args")))

        def _api_upload(self, query):
            if not self._api_allowed():
                return
            if self.headers.get(API_HEADER) is None:
                return self._error(403, f"Missing the {API_HEADER} header")
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/octet-stream":
                return self._error(415, "Send the file as application/octet-stream")
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._error(411, "Content-Length is required")
            fields = {k: v[0] for k, v in query.items() if v}
            if not all(k in fields for k in ("batch", "kind", "name")):
                return self._error(400, "Expected batch, kind and name")
            from ..ui.operator_api import ApiError
            try:
                saved = app.operator.save_upload(fields["batch"], fields["name"], self.rfile, length,
                                                 fields["kind"])
            except ApiError as err:
                # The unread body would be taken for the next request on this connection.
                self._discard_body(length)
                return self._error(err.status, str(err))
            except Exception:  # noqa: BLE001
                self._discard_body(length)
                log.exception("An upload failed")
                return self._error(500, "Something went wrong; details are in the log file")
            self._json(200, {"ok": True, "saved": saved})

        def _discard_body(self, length):
            """Read a little of an unwanted upload so the browser can see our answer, then hang up."""
            self.close_connection = True
            try:
                self.rfile.read(min(max(length, 0), 4 * 1024 * 1024))
            except OSError:
                pass

        def _api_reply(self, make):
            from ..ui.operator_api import ApiError
            try:
                data = make()
            except ApiError as err:
                return self._error(err.status, str(err))
            except Exception:  # noqa: BLE001 - never leave the operator's page hanging
                log.exception("The operator API failed")
                return self._error(500, "Something went wrong; details are in the log file")
            self._json(200, data)

        def _source(self):
            if not app.source_url:
                return self._error(404, "Not found")
            self._send(302, b"", headers=[("Location", app.source_url)])

    return Handler
