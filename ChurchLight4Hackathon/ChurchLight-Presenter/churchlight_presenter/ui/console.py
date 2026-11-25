# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""A terminal operator: run a whole service from a prompt.

It is the same controller a window will use, so it is a real way to try the
program (with XSplit pointed at the printed addresses) before any window
exists, and a quick way to check behavior by hand.
"""
import cmd
import logging
import shlex

from .. import logsetup
from ..core.scripture_ref import parse_reference
from .controller import ControllerError

log = logging.getLogger(__name__)


def _first_line(text, width=48):
    text = (text or "").replace("\n", " / ")
    return text if len(text) <= width else text[:width - 1] + "…"


class Console(cmd.Cmd):
    intro = "ChurchLight Presenter console. Type 'help' for commands, 'quit' to leave."
    prompt = "church> "

    def __init__(self, controller, stdin=None, stdout=None):
        super().__init__(stdin=stdin, stdout=stdout)
        self.use_rawinput = stdin is None
        self.c = controller
        self.results = []

    # -- helpers ---------------------------------------------------------------
    def say(self, text=""):
        print(text, file=self.stdout)

    def onecmd(self, line):
        try:
            with self.c.lock:                 # the operator web page may be changing things too
                return super().onecmd(line)
        except ControllerError as err:
            self.say(f"! {err}")
        except (ValueError, IndexError) as err:
            self.say(f"! {err}")
        except Exception:  # noqa: BLE001 - a bug must not end the service; it goes in the log
            log.exception("Unexpected error in command %r", line.split(None, 1)[0] if line.strip() else "")
            where = logsetup.current_log_path()
            self.say("! Something unexpected went wrong with that command. Check the screens, then carry on."
                     + (f" Details are in {where}" if where else ""))
        return False

    def emptyline(self):
        return False

    def default(self, line):
        """Input that is not a command. A typed Bible reference is previewed (never shown live)."""
        text = line.strip()
        try:
            parse_reference(text)
        except ValueError:
            first = text.split()[0] if text.split() else text
            return self.say(f"! I don't know the command {first!r}. Type help to see the commands. "
                            "(To show a Bible verse: verse jn 3:16)")
        for i, s in enumerate(self.c.preview_verse(text), 1):
            self.say(f"  {i:>2}. {self._describe(s)}")
        self.say("  That is only a preview. To show it: verse " + text
                 + "     To add it to the service: addverse " + text)

    def _number(self, arg, items, what, kind="search result"):
        """Pick item N (counting from 1) from a list, with messages a newcomer can act on."""
        hint = ("search first, e.g.: find chorus" if kind == "search result"
                else "see them with: service")
        try:
            n = int(arg)
        except ValueError:
            raise ControllerError(f"Give the number of a {kind}, like: {what} 1   ({hint})") from None
        if not items:
            raise ControllerError(f"There are no {kind}s yet ({hint})")
        if not 1 <= n <= len(items):
            raise ControllerError(f"There is no {kind} {n}; choose 1 to {len(items)}")
        return items[n - 1]

    def _describe(self, slide):
        if slide is None:
            return "-"
        body = slide.reference + ": " if slide.kind == "scripture" and slide.reference else ""
        what = slide.text or slide.image_path or slide.label
        flag = " (continued)" if slide.continued else ""
        return f"{slide.label or slide.item_type}{flag} | {body}{_first_line(what)}"

    # -- library and service ---------------------------------------------------
    def do_find(self, arg):
        """find [text]   Search songs and slideshows (everything when empty)."""
        self.results = self.c.search_library(arg)
        if not self.results:
            return self.say("Nothing found.")
        for n, e in enumerate(self.results, 1):
            self.say(f"{n:>3}. [{e.kind}] {e.title}" + (f"  ({e.detail})" if e.detail else ""))

    def do_look(self, arg):
        """look N   Preview the slides of search result N without going live."""
        for i, s in enumerate(self.c.preview(self._number(arg, self.results, "look")), 1):
            self.say(f"  {i:>2}. {self._describe(s)}")

    def do_services(self, arg):
        """services   List saved services."""
        for s in self.c.services():
            self.say(f"{s['id']:>3}. {s['title']}  {s['service_date']}")

    def do_new(self, arg):
        """new TITLE   Start a new service dated today."""
        if not arg.strip():
            raise ControllerError("Give the service a title: new Sunday Morning")
        self.say(f"Started service {self.c.new_service(arg.strip())}.")

    def do_open(self, arg):
        """open ID   Open a saved service."""
        self.c.open_service(int(arg))
        self.do_service("")

    def do_service(self, arg):
        """service   Show the open service."""
        rows = self.c.service_rows()
        if not rows:
            return self.say("The service is empty." if self.c.service_id else
                            "No service open. Use: new TITLE")
        for n, r in enumerate(rows, 1):
            self.say(f"{'>' if r.is_live else ' '}{n:>3}. {r.title}  [{r.item_type}]")

    def do_add(self, arg):
        """add N   Add search result N to the service.  (add verse jn 3:16 adds a Bible verse.)"""
        word, _, rest = arg.strip().partition(" ")
        if word.lower() == "verse":
            return self.do_addverse(rest)
        e = self._number(arg, self.results, "add")
        self.c.add_entry(e)
        self.say(f"Added {e.title}.")

    def do_addverse(self, arg):
        """addverse REFERENCE   Add a typed Bible reference to the service (addverse jn 3:16)."""
        self.c.add_verse(arg)
        self.say(f"Added {arg.strip()}.")

    def do_announce(self, arg):
        """announce TITLE | TEXT   Add an announcement slide."""
        title, _, body = arg.partition("|")
        self.c.add_announcement(title.strip() or None, body.strip())
        self.say("Added announcement.")

    def do_remove(self, arg):
        """remove N   Remove service row N."""
        row = self._number(arg, self.c.service_rows(), "remove", "service row")
        self.c.remove_item(row.item_id)
        self.say(f"Removed {row.title}.")

    def do_move(self, arg):
        """move N TO   Move service row N to row number TO."""
        parts = shlex.split(arg)
        if len(parts) != 2:
            raise ControllerError("Use: move 3 1")
        rows = self.c.service_rows()
        row = self._number(parts[0], rows, "move", "service row")
        target = self._number(parts[1], rows, "move", "service row")
        self.c.move_item(row.item_id, target.position)

    # -- going live ------------------------------------------------------------
    def do_go(self, arg):
        """go N   Go live on service row N."""
        self.c.go_live_service_item(self._number(arg, self.c.service_rows(), "go", "service row").item_id)
        self.do_status("")

    def do_live(self, arg):
        """live N   Send search result N straight live, outside the service."""
        self.c.go_live_entry(self._number(arg, self.results, "live"))
        self.do_status("")

    def do_next(self, arg):
        """next   The next slide (also: n)."""
        self.say("" if self.c.next() else "That is the last slide.")
        self.do_status("")
    do_n = do_next

    def do_prev(self, arg):
        """prev   The previous slide (also: p)."""
        self.say("" if self.c.previous() else "That is the first slide.")
        self.do_status("")
    do_p = do_prev

    def do_slide(self, arg):
        """slide N   Jump to slide N of whatever is loaded."""
        self.c.go_to_slide(int(arg) - 1)
        self.do_status("")

    def do_verse(self, arg):
        """verse REFERENCE   Show a verse over what is live (verse jn 3:16)."""
        slides = self.c.show_verse(arg)
        self.say(f"Showing {slides[0].reference}" + (f" in {len(slides)} slides" if len(slides) > 1 else ""))
        self.do_status("")

    def do_vn(self, arg):
        """vn   Next piece of the verse."""
        self.say("" if self.c.verse_next() else "That is the last piece.")
        self.do_status("")

    def do_vp(self, arg):
        """vp   Previous piece of the verse."""
        self.say("" if self.c.verse_previous() else "That is the first piece.")
        self.do_status("")

    def do_hide(self, arg):
        """hide   Hide the verse; what is under it stays."""
        self.c.hide_verse()
        self.do_status("")

    def do_again(self, arg):
        """again   Bring the hidden verse back."""
        self.c.reshow_verse()
        self.do_status("")

    def do_blank(self, arg):
        """blank   Toggle blanking the public screens."""
        self.c.toggle_blank()
        self.do_status("")

    def do_words(self, arg):
        """words   Toggle hiding the words (the theme background stays)."""
        self.c.toggle_text_hidden()
        self.do_status("")

    def do_clear(self, arg):
        """clear   Take everything off."""
        self.c.clear()
        self.do_status("")

    # -- themes ----------------------------------------------------------------
    @staticmethod
    def _value(text):
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        try:
            return int(text)
        except ValueError:
            try:
                return float(text)
            except ValueError:
                return text

    def do_themes(self, arg):
        """themes   List themes and their style settings."""
        rows = self.c.themes()
        if not rows:
            return self.say("No themes yet. Make one: theme Warm color=#ffeecc font_size=8")
        for row in rows:
            style = self.c.styles.get(row["id"]) or {}
            shown = " ".join(f"{k}={v}" for k, v in style.items()) or "(default look)"
            self.say(f"{row['id']:>3}. {row['name']}  {shown}")

    def do_theme(self, arg):
        """theme NAME key=value ...   Create or restyle a theme (font_size=8 color=#ffffff align=left)."""
        parts = shlex.split(arg)
        if not parts:
            raise ControllerError('Use: theme NAME color=#ffffff font_size=8 font_family="Georgia, serif"')
        name, style = parts[0], {}
        for item in parts[1:]:
            key, sep, value = item.partition("=")
            if not sep:
                raise ControllerError(f"{item!r} should look like key=value")
            style[key] = self._value(value)
        existing = next((r["id"] for r in self.c.themes() if r["name"].lower() == name.lower()), None)
        theme_id = self.c.save_theme(name, style, theme_id=existing)
        self.say(f"{'Updated' if existing else 'Made'} theme {theme_id}.")

    def do_usetheme(self, arg):
        """usetheme OUTPUT THEME|none   Give an output its own theme (none = follow each slide)."""
        parts = shlex.split(arg)
        if len(parts) != 2:
            raise ControllerError("Use: usetheme projector 1")
        self.c.set_output_theme(parts[0], None if parts[1].lower() == "none" else int(parts[1]))

    # -- looking ---------------------------------------------------------------
    def do_status(self, arg):
        """status   What is live right now."""
        st = self.c.session.state()
        where = f" [{st.base_index + 1}/{st.base_count}]" if st.base_index is not None else ""
        self.say(f"  LIVE{where}: {self._describe(st.base)}" + ("   (SCREENS BLANKED)" if st.blank else ""))
        if st.overlay_loaded:
            shown = self._describe(st.overlay) if st.overlay else "hidden"
            self.say(f"  VERSE [{st.overlay_index + 1}/{st.overlay_count}]: {shown}")
        self.say(f"  NEXT: {self._describe(st.next_base)}")

    def do_outputs(self, arg):
        """outputs   List the outputs and their web addresses."""
        from ..core.outputs import LAYOUTS
        server = self.c._server
        any_public = False
        for o in self.c.outputs:
            where = f"{server.url}/view/{o.name}" if server else (
                f"native, screen {o.screen}" if o.kind == "native" else "(server not running)")
            if server and LAYOUTS[o.layout].audience == "public":
                any_public = True
            self.say(f"  {o.name:<14} {o.layout:<12} {where}")
        if any_public:
            self.say("  Tip: in an ordinary browser tab, add ?preview to a public address to see it on a "
                     "dark background (XSplit/OBS show it transparent).")

    def do_quit(self, arg):
        """quit   Leave."""
        return True
    do_exit = do_quit
    do_EOF = do_quit
