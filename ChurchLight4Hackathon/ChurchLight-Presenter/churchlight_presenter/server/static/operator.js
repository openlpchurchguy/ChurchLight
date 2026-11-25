"use strict";
/* The operator page: library, service order and live controls.
   All text from the server goes in with textContent, never as HTML. The functions at the top
   are pure (no page access) so they can be tested under node; the rest talks to the page. */

const SCHEMA = 1;

/* ---------------- pure logic ---------------- */
function mediaUrl(path) {
  return "/media/" + String(path).split("/").map(encodeURIComponent).join("/");
}

function slideSummary(view) {
  if (!view) return null;
  const segments = view.segments || [];
  let lines;
  if (segments.length > 1 || (view.title_first && segments.length)) {
    lines = segments.map(([number, text]) => ({ number: number, text: text }));
  } else {
    const text = String(view.text || "");
    lines = text === "" ? [] : text.split("\n").map((line) => ({ number: null, text: line }));
  }
  return {
    label: view.label || "",
    reference: view.reference || "",
    continued: !!view.continued,
    image: view.image_path && view.kind !== "video" && view.kind !== "audio" ? mediaUrl(view.image_path) : null,
    clip: view.kind === "video" || view.kind === "audio" ? view.kind : null,
    lines: lines,
    notes: view.notes || "",
  };
}

function liveSummary(state) {
  const live = state.live;
  const hasOverlay = !!live.overlay;
  return {
    position: live.index === null ? "Nothing live" : "Slide " + (live.index + 1) + " of " + live.count,
    mode: state.mode === "item" ? "single item (service untouched)" : state.mode === "service" ? "service" : "",
    blank: !!live.blank,
    textHidden: !!live.text_hidden,
    lowerHidden: !!live.lower_hidden,
    base: slideSummary(live.base),
    next: slideSummary(live.next),
    nextTitle: live.index === null ? "First slide" : "Next slide",
    overlay: slideSummary(live.overlay),
    overlayPosition: live.overlay_loaded && live.overlay_count > 1
      ? "Verse part " + (live.overlay_index + 1) + " of " + live.overlay_count : "",
    verseLoaded: !!live.overlay_loaded,
    verseShown: hasOverlay,
    canNext: live.count > 0 && (live.index === null || live.index + 1 < live.count),
    canPrevious: live.index !== null && live.index > 0,
    notes: live.base && live.base.notes ? live.base.notes : "",
  };
}

/* The keyboard shortcuts. Each action has the keys that trigger it (the operator can change them on the
   Settings tab). `where` says which pages it works on: "both" or "service" only (those that need the
   Service tab's boxes). Keys are KeyboardEvent.key values; single letters are kept in lower case. */
const KEY_ACTIONS = [
  { id: "next", label: "Next slide", keys: [" ", "ArrowDown", "PageDown"], result: { action: "next" }, where: "both" },
  { id: "previous", label: "Previous slide", keys: ["ArrowUp", "PageUp"], result: { action: "previous" }, where: "both" },
  { id: "next_item", label: "Next service item", keys: ["ArrowRight"], result: { action: "next_item" }, where: "both" },
  { id: "previous_item", label: "Previous service item", keys: ["ArrowLeft"], result: { action: "previous_item" }, where: "both" },
  { id: "blank", label: "Blank screens", keys: ["b"], result: { action: "blank" }, where: "both" },
  { id: "hide_text", label: "Hide the words (keep the background)", keys: ["w"], result: { action: "text_hide" }, where: "both" },
  { id: "hide_lower", label: "Hide the lower thirds (the projector stays)", keys: ["l"], result: { action: "lower_hide" }, where: "both" },
  { id: "hide_verse", label: "Hide the verse", keys: ["h"], result: { action: "hide_verse" }, where: "both" },
  { id: "verse_next", label: "Next piece of the verse", keys: ["]"], result: { action: "verse_next" }, where: "both" },
  { id: "verse_previous", label: "Previous piece of the verse", keys: ["["], result: { action: "verse_previous" }, where: "both" },
  { id: "tray_next", label: "Next tray verse", keys: ["."], result: { action: "tray_step", args: { delta: 1 } }, where: "both" },
  { id: "tray_previous", label: "Previous tray verse", keys: [","], result: { action: "tray_step", args: { delta: -1 } }, where: "both" },
  { id: "tray_box", label: "Go to the tray box", keys: ["t"], result: { focus: "tray" }, where: "service" },
  { id: "tray_jump", label: "Go to the verse number box", keys: ["g"], result: { focus: "jump" }, where: "service" },
  { id: "search", label: "Search the library", keys: ["/"], result: { focus: "search" }, where: "service" },
  { id: "fold_items", label: "Collapse the service items, or open the live one", keys: ["c"], result: { fold: "items" }, where: "service" },
  { id: "take_cue", label: "Show the ready verse", keys: ["x"], result: { action: "cue_take" }, where: "both", needsCue: true },
];
const RESERVED_KEYS = ["Escape", "Tab", "Enter", "Shift", "Control", "Alt", "Meta", "CapsLock", "Dead", "Unidentified"];

function defaultKeys() {
  const keys = {};
  for (const item of KEY_ACTIONS) keys[item.id] = item.keys.slice();
  return keys;
}

function normalizeKey(key) {
  return typeof key === "string" && key.length === 1 && key !== " " ? key.toLowerCase() : key;
}

/* What a key press means. `ctx`: typing (in a text box), onButton (a button has focus), verseShown.
   `keys` maps each action id to its keys (the defaults when left out). Escape always hides a showing verse. */
function keyAction(key, ctx, keys) {
  if (ctx.typing) return key === "Escape" ? { blur: true } : null;
  if (key === "Escape") return ctx.verseShown ? { action: "hide_verse" } : ctx.cueReady ? { action: "cue_clear" } : null;
  if (key === "Enter") return ctx.cueReady && !ctx.onButton ? { action: "cue_take" } : null;      // Enter takes the ready verse
  const wanted = normalizeKey(key);
  const mine = keys || defaultKeys();
  for (const item of KEY_ACTIONS) {
    if (!(mine[item.id] || []).includes(wanted)) continue;
    if (item.needsCue && !ctx.cueReady) return null;
    if (wanted === " " && ctx.onButton) return null;          // Space on a focused button presses that button
    if (ctx.view === "broadcast" && item.where === "service") return null;
    return item.result;
  }
  return null;
}

/* The saved key choices: the defaults with whatever valid choices were stored laid over them. */
function parseKeyChoices(text) {
  const keys = defaultKeys();
  let saved;
  try { saved = JSON.parse(text); } catch (err) { return keys; }
  if (!saved || saved.v !== 1 || !saved.keys || typeof saved.keys !== "object") return keys;
  const taken = new Set();
  const chosen = {};
  for (const item of KEY_ACTIONS) {
    const list = saved.keys[item.id];
    if (!Array.isArray(list) || !list.length || list.length > 4) continue;
    const clean = list.filter((k) => typeof k === "string" && k.length > 0 && k.length <= 16 && !RESERVED_KEYS.includes(k));
    if (clean.length === list.length && !clean.some((k) => taken.has(k))) {
      chosen[item.id] = clean.map(normalizeKey);
      for (const k of chosen[item.id]) taken.add(k);
    }
  }
  for (const id of Object.keys(chosen)) keys[id] = chosen[id];
  // a key may belong to one action only: when a saved choice takes a key, the default owner loses it
  for (const item of KEY_ACTIONS) {
    if (chosen[item.id]) continue;
    keys[item.id] = keys[item.id].filter((k) => !taken.has(k));
    if (!keys[item.id].length) keys[item.id] = item.keys.filter((k) => !taken.has(k));
  }
  return keys;
}

/* Give `key` to one action as its only key. Returns { keys, moved } (moved: the label of the action
   that lost it, if any) or { error }. An action never ends up with no keys: the one that lost its
   only key is told so, and the change is refused. */
function assignKey(keys, id, key) {
  const wanted = normalizeKey(key);
  if (!KEY_ACTIONS.some((item) => item.id === id)) return { error: "Unknown action" };
  if (typeof wanted !== "string" || !wanted || RESERVED_KEYS.includes(wanted)) {
    return { error: "That key cannot be used (Esc, Tab, Enter and modifier keys are kept)" };
  }
  const next = {};
  let moved = null;
  for (const item of KEY_ACTIONS) {
    next[item.id] = (keys[item.id] || []).slice();
    if (item.id !== id && next[item.id].includes(wanted)) {
      next[item.id] = next[item.id].filter((k) => k !== wanted);
      moved = item.label;
      if (!next[item.id].length) return { error: "That key is the only key for “" + item.label + "”. Give it another key first." };
    }
  }
  next[id] = [wanted];
  return { keys: next, moved: moved };
}

const KEY_NAMES = { " ": "Space", ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", PageUp: "Page Up", PageDown: "Page Down" };
function keyLabel(key) {
  if (KEY_NAMES[key]) return KEY_NAMES[key];
  return key.length === 1 ? key.toUpperCase() : key;
}

/* The name of an action's first key ("B", "Space", "↓"), or "" when it has none: what a button shows as its hint.
   `keys` is the operator's own choice (the Settings tab), so a button follows a changed key. */
function keyHint(keys, id) {
  const mine = (keys || defaultKeys())[id] || [];
  return mine.length ? keyLabel(mine[0]) : "";
}

/* "Hide words" + "W" -> "Hide words (W)"; no key, no brackets. */
function withHint(text, hint) {
  return hint ? text + " (" + hint + ")" : text;
}

/* The one-line list of every key under the Live panel, from the keys in use (pure). */
function keysLine(keys) {
  const parts = KEY_ACTIONS.filter((item) => (keys[item.id] || []).length)
    .map((item) => keys[item.id].map(keyLabel).join(" or ") + " " + item.label.charAt(0).toLowerCase() + item.label.slice(1));
  return "Keys: " + parts.join(" · ") + " · Esc hides a showing verse or clears the ready one · Enter takes the ready verse. You can change them on the Settings tab.";
}

/* The position to give `move` so item `index` swaps with its neighbor; null at the ends. */
function moveTarget(items, index, delta) {
  const other = items[index + delta];
  return other ? other.position : null;
}

function describeServiceSelect(state) {
  const options = state.services.map((s) => ({ value: String(s.id), text: s.title + (s.date ? " (" + s.date + ")" : "") }));
  if (!state.service) options.unshift({ value: "", text: options.length ? "Choose a service…" : "No services yet" });
  return { options: options, selected: state.service ? String(state.service.id) : "" };
}

/* ---- library management (pure) ---- */
const MB = 1024 * 1024;
const IMPORT_LIMITS = {
  songs: { exts: [".xml", ".txt", ".cho", ".chordpro", ".crd", ".chopro", ""], max: 5 * MB },
  bible: { exts: [".xml", ".csv", ".tsv", ".txt"], max: 100 * MB },
  deck: { exts: [".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".webp"], max: 400 * MB },
  background: { exts: [".png", ".jpg", ".jpeg", ".gif", ".webp"], max: 25 * MB },
  slidepics: { exts: [".png", ".jpg", ".jpeg", ".gif", ".webp"], max: 25 * MB },
  themes: { exts: [".zip"], max: 200 * MB },
  media: { exts: [".mp4", ".m4v", ".mp3", ".wav", ".png", ".jpg", ".jpeg", ".gif", ".webp"], max: 8192 * MB },
};

function extensionOf(name) {
  const dot = String(name).lastIndexOf(".");
  return dot < 0 ? "" : String(name).slice(dot).toLowerCase();
}

/* Split chosen files into those worth uploading and plain-language problems (the server checks again). */
function sizeText(bytes) {
  return bytes >= 1024 * MB ? Math.round(bytes / (1024 * MB)) + " GB" : Math.round(bytes / MB) + " MB";
}

function checkFiles(kind, files) {
  const limit = IMPORT_LIMITS[kind];
  const good = [];
  const problems = [];
  for (const file of files) {
    if (!limit.exts.includes(extensionOf(file.name))) problems.push(file.name + " is not one of: " + limit.exts.map((e) => e || "(no extension)").join(" "));
    else if (!file.size) problems.push(file.name + " is empty");
    else if (file.size > limit.max) problems.push(file.name + " is too large (over " + sizeText(limit.max) + ")");
    else good.push(file);
  }
  return { good: good, problems: problems };
}

function filterSongs(songs, text) {
  const needle = String(text || "").trim().toLowerCase();
  if (!needle) return songs;
  return songs.filter((song) => (song.title + " " + (song.operator_name || "") + " " + song.author + " " + (song.ccli || "")).toLowerCase().includes(needle));
}

function filterDecks(decks, text) {
  const needle = String(text || "").trim().toLowerCase();
  if (!needle) return decks;
  return decks.filter((deck) => String(deck.name || "").toLowerCase().includes(needle));
}

function usesText(uses) {
  return uses ? "used in " + uses + " service" + (uses === 1 ? "" : "s") : "";
}

function jobStatusText(status) {
  return { waiting: "Waiting", running: "Importing…", done: "Done", failed: "Problem" }[status] || status;
}

/* ---- slide transitions (pure) ---- */
const TRANSITION_CHOICES = [
  { value: "none", text: "None" },
  { value: "dissolve", text: "Dissolve" },
  { value: "circle_out", text: "Circle wipe, out (grows from the center)" },
  { value: "circle_in", text: "Circle wipe, in (closes to the center)" },
  { value: "slide_left", text: "Slide left (new slide comes in from the right)" },
  { value: "slide_right", text: "Slide right (new slide comes in from the left)" },
  { value: "slide_up", text: "Slide up (new slide comes in from the bottom)" },
  { value: "slide_down", text: "Slide down (new slide comes in from the top)" },
  { value: "split_horizontal", text: "Split, horizontal (opens up and down from the middle)" },
  { value: "split_vertical", text: "Split, vertical (opens left and right from the middle)" },
  { value: "split_diagonal_left", text: "Split, diagonal left (opens from a \\ line)" },
  { value: "split_diagonal_right", text: "Split, diagonal right (opens from a / line)" },
  { value: "wipe_left", text: "Wipe left (sweeps from the right edge)" },
  { value: "wipe_right", text: "Wipe right (sweeps from the left edge)" },
  { value: "wipe_up", text: "Wipe up (sweeps from the bottom edge)" },
  { value: "wipe_down", text: "Wipe down (sweeps from the top edge)" },
  { value: "fade_black", text: "Fade to black" },
  { value: "fade_white", text: "Fade to white" },
];
const MAX_TRANSITION_MS = 2000;

/* "0.5" or "1,2" seconds -> milliseconds in whole tenths between 0 and 2 seconds; null if it is not that. */
function secondsToMs(text) {
  const clean = String(text === undefined || text === null ? "" : text).trim().replace(",", ".");
  if (!/^\d+(\.\d+)?$/.test(clean)) return null;
  const tenths = Math.round(Number(clean) * 10);
  if (!(tenths >= 0) || tenths * 100 > MAX_TRANSITION_MS) return null;
  return tenths * 100;
}

function msToSeconds(ms) {
  return (Number(ms) / 1000).toFixed(1);
}

function transitionText(kind, ms) {
  const choice = TRANSITION_CHOICES.find((c) => c.value === kind);
  if (!choice || kind === "none" || !(ms > 0)) return "No transition";
  return choice.text.split(" (")[0] + ", " + msToSeconds(ms) + " s";
}

/* ---- timed advance (pure) ---- */
const END_CHOICES = [
  { value: "stop", text: "Stay on the last slide" },
  { value: "loop", text: "Loop back to the first slide" },
  { value: "blank", text: "Blank the screens" },
  { value: "next_item", text: "Move on to the next service item" },
];
const MAX_AUTO_SECONDS = 600;

/* "8" -> 8; "" or junk or out of range -> null. 0 means manual. */
function parseAutoSeconds(text) {
  const clean = String(text === undefined || text === null ? "" : text).trim();
  if (!/^\d+$/.test(clean)) return null;
  const n = Number(clean);
  return n <= MAX_AUTO_SECONDS ? n : null;
}

function autoplayText(seconds, endAction) {
  if (!(seconds > 0)) return "Manual advance";
  const end = END_CHOICES.find((c) => c.value === endAction);
  return "Every " + seconds + " s, " + (end ? end.text.charAt(0).toLowerCase() + end.text.slice(1) : "stop");
}

/* The Run page's line about timed advance, and what the pause button says. */
function timerText(timer) {
  if (!timer || !timer.available) return { text: "", button: "Pause timer", enabled: false };
  if (timer.paused) return { text: "Timer paused", button: "Resume timer", enabled: true };
  const note = autoplayText(timer.seconds, timer.end_action);
  return { text: timer.running ? "Timer running: " + note : "Timer finished: " + note, button: "Pause timer", enabled: true };
}

/* The line above the service list: which service, and the look it uses. */
function serviceSummary(state) {
  if (!state || !state.service) return "No service open. Press Change to open or start one.";
  const name = (id) => {
    const theme = id === null || id === undefined ? null : (state.themes || []).find((t) => t.id === id);
    return theme ? theme.name : "plain";
  };
  return state.service.title + (state.service.date ? " — " + state.service.date : "")
    + " · Projector: " + name(state.service.theme_id) + " · Overlay: " + name(state.service.overlay_theme_id);
}

/* The item after the live one, for the "Next" mark. */
function nextItemIndex(items) {
  const at = (items || []).findIndex((item) => item.live);
  return at >= 0 && at + 1 < items.length ? at + 1 : -1;
}

function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

/* Where "Save and add to service" puts a new song: right after the item that is live, else at the end. */
function addPosition(items) {
  const live = (items || []).find((item) => item.live);
  return live ? live.position + 1 : undefined;
}

/* The unsaved song kept in this browser, or null when there is none or it is damaged. */
function parseSongBuffer(raw) {
  try {
    const data = JSON.parse(raw);
    if (data && typeof data.title === "string" && typeof data.author === "string" && typeof data.text === "string") {
      return { id: Number.isInteger(data.id) ? data.id : null, title: data.title, author: data.author, text: data.text,
               order: typeof data.order === "string" ? data.order : "", ccli: typeof data.ccli === "string" ? data.ccli : "",
               copyright: typeof data.copyright === "string" ? data.copyright : "", notice: typeof data.notice === "string" ? data.notice : "",
               theme: typeof data.theme === "string" ? data.theme : "",
               base: typeof data.base === "string" ? data.base : "" };
    }
  } catch (err) { /* nothing stored, or damaged */ }
  return null;
}

/* The names in [square brackets] on their own lines, once each, in the order they are written. */
function partNames(text) {
  const names = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    const match = /^\s*\[([^\[\]]{1,60})\]\s*$/.exec(line);
    if (match && !names.includes(match[1].trim())) names.push(match[1].trim());
  }
  return names;
}

/* Add a part name to the end of the order box text: "" + "Chorus" -> "Chorus". */
function appendToOrder(order, name) {
  const clean = String(order || "").replace(/[,\s]+$/, "");
  return clean ? clean + ", " + name : name;
}

/* "3 of 8 parts" for a song in the service, or "" when the whole song is sung. */
/* A song's parts for the options list (pure): each slide marked live, done (already shown) or next. */
function outlineView(outline, liveIndex, isLive) {
  const parts = outline || [];
  const following = isLive && liveIndex !== null && liveIndex !== undefined
    ? Math.min(...parts.flatMap((p) => p.slides.map((s) => s.index)).filter((i) => i > liveIndex)) : Infinity;
  return parts.map((part) => ({
    index: part.index, label: part.label, skipped: !!part.skipped, firstLine: part.first_line || "", notice: !!part.notice,
    slides: part.slides.map((slide) => ({
      index: slide.index, line: slide.line, continued: !!slide.continued,
      state: !isLive || liveIndex === null || liveIndex === undefined ? ""
        : slide.index === liveIndex ? "live" : slide.index < liveIndex ? "done" : slide.index === following ? "next" : "",
    })),
  }));
}

/* A slideshow's slides for the options list: each with its state (live / done / next / none) once the item is live. */
function deckSlideView(slides, liveIndex, isLive) {
  const list = slides || [];
  const known = isLive && liveIndex !== null && liveIndex !== undefined;
  const following = known ? Math.min(...list.map((s) => s.index).filter((i) => i > liveIndex)) : Infinity;
  return list.map((slide) => ({
    index: slide.index, label: slide.label,
    state: !known ? "" : slide.index === liveIndex ? "live" : slide.index < liveIndex ? "done" : slide.index === following ? "next" : "",
  }));
}

function partsSummary(parts) {
  if (!parts || !(parts.total > 0) || parts.used >= parts.total) return "";
  return parts.used + " of " + parts.total + " parts";
}

/* ---- the Library page's containers (pure) ----
   Slide decks, Songs and Bibles stay where they are; each folds to one line naming what is inside.
   Bibles starts folded because it rarely changes after the first install. */
const LIBRARY_FOLDS = ["decks", "media", "songs", "drafts", "bibles"];

function parseLibraryFolds(raw) {
  try {
    const data = JSON.parse(raw);
    if (Array.isArray(data)) return LIBRARY_FOLDS.filter((name) => data.includes(name));
  } catch (err) { /* nothing stored, or damaged */ }
  return ["bibles"];
}

/* The Settings page's sections can be folded; the ones folded are kept in this browser. */
const SETTINGS_FOLDS = ["windows", "screens", "themes", "pdf", "notice", "triggers", "clicker", "keys", "anykeys", "advanced"];

/* What each Settings section is for: a short line always shown under its title, and a longer sentence behind the "?" button. */
const SETTINGS_HELP = {
  windows: ["Choose which monitor each screen opens on.",
    "ChurchLight opens its own full-screen windows on your projector, stage and confidence monitors every time it starts. Close them from here when you need your monitors back."],
  screens: ["The home picture, sound, screen shape and the addresses for OBS or XSplit.",
    "How the room screens behave: the picture shown when nothing is live, whether Hide words also hides the confidence monitor's words, where video sound plays, each screen's shape, and the web addresses of the screens."],
  notice: ["The title and CCLI slide that can start or end a song.",
    "A song can start or end with a slide showing its title, writers, copyright, CCLI song number and your church's license number. Choose where it goes, or turn it off."],
  themes: ["Send your themes to another computer, or bring them in.",
    "Export your own themes to a file and import them on another ChurchLight computer. The file holds each theme's look and pictures, not where it is used."],
  advanced: ["Remote buttons, a clicker, keys and other once-in-a-while options.",
    "Options most churches set once, or never. Open this when you need a Stream Deck, a clicker, or different keys."],
  pdf: ["Checks that PDF slideshows can be turned into pages.",
    "ChurchLight turns a PDF into slide pictures with a renderer that comes with it. If this says the renderer is not set up, PDFs cannot be added until it is."],
  triggers: ["Links a Stream Deck or another program opens to press a button.",
    "Each link presses one ChurchLight button when it is opened. They work on this computer only, and each one includes a private code, so do not share them."],
  clicker: ["Use a presentation clicker's buttons to run the service.",
    "The helper catches the clicker's keys (such as Page Down) and sends them to ChurchLight as button links. Windows only."],
  keys: ["The keys that move the service along.",
    "These keys work while this window is in front and you are not typing in a box. Change any of them here."],
  anykeys: ["Keys that work even when another program is in front.",
    "For a clicker or keyboard when another program, such as XSplit, is in front. Windows only. Off until you turn it on, and while it is on those keys are taken from other programs."],
};

/* Under each Settings title: its one-line caption and a "?" that shows the longer sentence (without opening the section). */
function initSettingsHelp() {
  for (const section of document.querySelectorAll("details[data-settings-fold]")) {
    const help = SETTINGS_HELP[section.dataset.settingsFold];
    const summary = section.querySelector(":scope > summary");
    const title = summary && summary.querySelector("h3");
    if (!help || !title || summary.querySelector(".fold-head")) continue;
    const head = el("div", "fold-head");
    summary.insertBefore(head, title);
    head.appendChild(title);
    head.appendChild(el("span", "fold-line", help[0]));
    const why = el("span", "fold-why", help[1]);
    why.id = "fold-why-" + section.dataset.settingsFold;
    why.hidden = true;
    head.appendChild(why);
    const ask = el("button", "fold-help", "?");
    ask.type = "button";
    ask.setAttribute("aria-label", "What is " + title.textContent + " for?");
    ask.setAttribute("aria-expanded", "false");
    ask.setAttribute("aria-controls", why.id);
    ask.addEventListener("click", (event) => {
      event.preventDefault();                                   // not the section's own open/close
      event.stopPropagation();
      why.hidden = !why.hidden;
      ask.setAttribute("aria-expanded", String(!why.hidden));
    });
    summary.insertBefore(ask, head.nextSibling);
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const why of document.querySelectorAll(".fold-why:not([hidden])")) {
      why.hidden = true;
      const ask = document.querySelector('.fold-help[aria-controls="' + why.id + '"]');
      if (ask) ask.setAttribute("aria-expanded", "false");
    }
  });
}

/* The one Fold all / Open all button above a long page of folds: if any section is open it folds them all,
   and only when every section is folded does it open them all. `opens` is one true/false per section. */
function foldAllPlan(opens) {
  const anyOpen = (opens || []).some(Boolean);
  return { label: anyOpen ? "Fold all" : "Open all", openAll: !anyOpen };
}
function parseSettingsFolds(raw) {
  try {
    const data = JSON.parse(raw);
    if (Array.isArray(data)) return SETTINGS_FOLDS.filter((name) => data.includes(name));
  } catch (err) { /* nothing stored, or damaged */ }
  return [];
}

function librarySummary(name, manage) {
  const data = manage || {};
  if (name === "media") return (data.media || []).length ? plural(data.media.length, "file") : "Nothing added yet. Choose a video, a picture or a sound file above.";
  if (name === "sources") {
    const src = data.media_sources || {};
    const count = (src.decks || []).length + (src.themes || []).length + (src.home ? 1 : 0);
    return count ? plural(count, "place") : "None";
  }
  if (name === "decks") return (data.decks || []).length ? plural(data.decks.length, "slideshow") : "No slideshows yet";
  if (name === "songs") {
    const count = (data.songs || []).filter((s) => !s.draft).length;
    return count ? plural(count, "song") : "No songs yet";
  }
  if (name === "drafts") {
    const count = (data.songs || []).filter((s) => s.draft).length;
    return count ? plural(count, "draft") : "No drafts";
  }
  if (name === "bibles") {
    const names = (data.translations || []).map((t) => t.abbreviation);
    return names.length ? names.join(", ") : "None installed";
  }
  return "";
}

/* The Blank button's words: with a home screen picture it goes to that picture and back. */
/* A button that is switched on shows it by its color (and aria-pressed), so no banner is needed. */
function setToggle(button, on) {
  button.classList.toggle("toggle-on", !!on);
  button.setAttribute("aria-pressed", String(!!on));
}

function blankText(blank, home, withKey) {
  const homeOn = !!(home && home.image && home.on);
  const words = homeOn ? (blank ? "Back to the service" : "Home screen") : (blank ? "Show screens again" : "Blank screens");
  const key = typeof withKey === "string" ? withKey : withKey ? "B" : "";      // the key's name, or true for the default
  return words + (key ? " (" + key + ")" : "");
}

/* ---- the Enter key in "Go to verse" (pure) ----
   With nothing ticked, nothing happens. Shift+Enter cues and takes at once. Enter takes when these same ticks are
   already ready; otherwise it cues them (adds them to the service and makes them ready, screens untouched).
   `cuedKey` is the ticks the last cue was made from (or null); `ready` says whether a verse is ready now. */
function jumpEnter(cuedKey, checked, ready, shift) {
  if (!checked || !checked.length) return "none";
  if (shift) return "cue_take";
  return ready && cuedKey === checked.join(",") ? "take" : "cue";
}

/* ---- the library search box (pure) ----
   It starts as just the bar. Typing lists matches; "Show all" lists everything; Clear goes back to the bar. */
function libraryMode(text, showAll) {
  if (String(text || "").trim()) return "search";
  return showAll ? "all" : "idle";
}

/* ---- jumping to a typed verse number in the tray (pure) ----
   "16", "16-18", "16, 18" or "3 5-7". Returns the verses of the tray that were asked for and the ones it lacks.
   A half-typed piece ("16-") is ignored until it is finished. Nothing here touches the screen or the service. */
function parseVerseJump(text, numbers) {
  const have = new Set(numbers || []);
  const picked = new Set();
  const missing = [];
  for (const piece of String(text === undefined || text === null ? "" : text).replace(/[–—]/g, "-").split(/[\s,;]+/)) {
    const single = /^(\d{1,3})$/.exec(piece);
    const range = /^(\d{1,3})-(\d{1,3})$/.exec(piece);
    if (single) {
      const n = Number(single[1]);
      if (have.has(n)) picked.add(n); else missing.push(String(n));
    } else if (range) {
      const a = Number(range[1]);
      const b = Number(range[2]);
      const found = (numbers || []).filter((n) => n >= Math.min(a, b) && n <= Math.max(a, b));
      found.forEach((n) => picked.add(n));
      if (!found.length) missing.push(piece);
    }
  }
  return { picked: Array.from(picked).sort((x, y) => x - y), missing: missing };
}

/* ---- the verse tray (pure) ---- */
function trayView(tray, checked) {
  if (!tray) return null;
  const ticked = new Set(checked || []);
  const live = new Set(tray.live || []);
  return {
    title: tray.reference + " (" + tray.translation + ")",
    count: tray.verses.length,
    key: tray.reference + "|" + tray.translation + "|" + tray.verses.length,
    rows: tray.verses.map(([number, text]) => ({ number: number, text: text, live: live.has(number), checked: ticked.has(number) })),
  };
}

function toggleChecked(checked, number) {
  return checked.includes(number) ? checked.filter((n) => n !== number) : checked.concat([number]).sort((a, b) => a - b);
}

/* Shift-click: tick every verse from the last one ticked to this one (in the tray's order). */
function checkRun(checked, numbers, anchor, number) {
  const a = numbers.indexOf(anchor);
  const b = numbers.indexOf(number);
  if (a < 0 || b < 0) return toggleChecked(checked, number);
  const run = numbers.slice(Math.min(a, b), Math.max(a, b) + 1);
  return Array.from(new Set(checked.concat(run))).sort((x, y) => x - y);
}

/* ---- screen shapes and previews (pure) ---- */
const PREVIEW_BASE_WIDTH = 1920;  // output pages lay themselves out at this width, then are scaled down

/* The shape an output's real screen has. Previews are drawn in it, so a 4:3 projector shows its bands. */
const SCREEN_SHAPES = ["16:9", "4:3", "16:10"];
const SHAPE_LAYOUTS = ["projector", "confidence"];          // the only screens whose shape can be set
function hasScreenShape(layout) { return SHAPE_LAYOUTS.includes(layout); }

function shapeParts(shape) {
  const found = /^(\d+):(\d+)$/.exec(String(shape));
  return found && SCREEN_SHAPES.includes(found[0]) ? [Number(found[1]), Number(found[2])] : [16, 9];
}

/* Height, in the page's own pixels, of a page laid out PREVIEW_BASE_WIDTH wide in this shape. */
function frameHeight(shape) {
  const parts = shapeParts(shape);
  return Math.round(PREVIEW_BASE_WIDTH * parts[1] / parts[0]);
}

function applyShape(entry, shape) {
  const parts = shapeParts(shape);
  entry.view.style.aspectRatio = parts[0] + " / " + parts[1];
  entry.frame.style.height = frameHeight(shape) + "px";
}

function previewScale(width) {
  const w = Number(width);
  return w > 0 && isFinite(w) ? w / PREVIEW_BASE_WIDTH : 0;
}

/* ---------------- the page ---------------- */
const $ =(id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

let state = null;
let libraryEntries = [];
let libraryHadService = false;
let messageTimer = null;

/* The Service board fills exactly what is left under the header, so the page itself never scrolls (each box
   scrolls inside only when it needs to). The header can grow (a wrapped tab row, a message, a banner), so measure. */
function fitBoard() {
  const board = $("view-run");
  if (!board || board.hidden) return;
  board.style.setProperty("--board-top", Math.ceil(board.getBoundingClientRect().top + window.scrollY) + "px");
}

/* Messages. A problem or warning (showMessage) STAYS until it is cleared with its x button or a newer message replaces it, and a
   success after it does not wipe it. An ordinary notice (showNotice: "Added...", "Saved...") fades by itself. showMessage("")
   clears only a notice. Inside the setup guide the same message is shown in the guide, above its buttons. */
let messageSticky = false;
function hideMessageBox() {
  for (const id of ["message", "wizard-message"]) { const box = $(id); if (box) box.hidden = true; }
  messageSticky = false;
  clearTimeout(messageTimer);
  fitBoard();
}
function drawMessage(text, sticky, undo) {
  const inGuide = typeof wizardOpen !== "undefined" && wizardOpen;      // the setup guide covers the page: say it inside the guide
  const box = $(inGuide ? "wizard-message" : "message");
  $(inGuide ? "wizard-message-text" : "message-text").textContent = text;
  box.hidden = false;
  messageSticky = sticky;
  const undoButton = $("message-undo");
  undoButton.hidden = !undo || inGuide;
  undoAction = undo && !inGuide ? undo : null;
  fitBoard();
  clearTimeout(messageTimer);
  if (!sticky) messageTimer = setTimeout(hideMessageBox, inGuide ? 12000 : (undo ? 10000 : 8000));
}
let undoAction = null;
function showMessage(text) {
  if (!text) { if (!messageSticky) hideMessageBox(); return; }
  drawMessage(text, true);
}
function showNotice(text, undo, force) {                 // `undo`: a function that puts the thing back; shows an Undo button for ~10 s
  if (!text) { if (!messageSticky) hideMessageBox(); return; }
  if (messageSticky && !undo && !force) return;           // an ordinary notice never covers a problem that is still showing
  drawMessage(text, false, undo);
}
function wireMessageClear() {
  for (const id of ["message-clear", "wizard-message-clear"]) $(id).addEventListener("click", hideMessageBox);
  $("message-undo").addEventListener("click", async () => { const run = undoAction; hideMessageBox(); if (run) await run(); });
}

async function call(action, args) {
  try {
    const res = await fetch("/api/do", {
      method: "POST", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-ChurchLight": "operator" },
      body: JSON.stringify({ action: action, args: args || {} }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return { ok: false, error: data.error || "That did not work (" + res.status + ")" };
    return { ok: true, data: data };
  } catch (err) {
    return { ok: false, error: "Cannot reach the program. Is it still running?" };
  }
}

/* In the program's own window (opened with ?app=1) report where the window is, so the next start opens it
   in the same place. A page opened as an ordinary browser tab does not, so it cannot move the saved place. */
let lastWindowPlace = "";
let placeBaseline = null;          // where the window was once the saved place had been applied
function wantedWindowPlace() {
  const found = /[?&]place=(-?\d+),(-?\d+),(\d+),(\d+)/.exec(location.search);
  return found ? found.slice(1, 5).map(Number) : null;
}

/* The program starts the window with its saved place in the address; move there even when the browser
   ignored the position flags (it was already running). */
let hadSavedPlace = false;
function restoreWindowPlace() {
  const want = wantedWindowPlace();
  if (!want || !/[?&]app=1\b/.test(location.search)) return;
  hadSavedPlace = true;
  try {          // a later reload of this page must not pull the window back to the old place
    history.replaceState(null, "", location.pathname + location.search.replace(/([?&])place=[^&]*&?/, "$1").replace(/[?&]$/, ""));
  } catch (err) { /* keep the address as it is */ }
  try {
    window.resizeTo(want[2], want[3]);
    window.moveTo(want[0], want[1]);
  } catch (err) { /* the browser refused: it keeps its own place */ }
}

function reportWindowPlace() {
  if (!/[?&]app=1\b/.test(location.search)) return;
  const place = [window.screenX, window.screenY, window.outerWidth, window.outerHeight];
  if (place.some((v) => !Number.isFinite(v)) || place[0] <= -30000 || place[2] < 400 || place[3] < 300) return;   // minimized
  const text = place.join(",");
  if (text === lastWindowPlace) return;
  lastWindowPlace = text;
  // With a saved place, the first look is only the starting point: if the browser did not honour the saved
  // place, reporting it would overwrite the good one. Only a change the operator makes is reported.
  if (hadSavedPlace && placeBaseline === null) { placeBaseline = text; return; }
  try {
    fetch("/api/do", {
      method: "POST", keepalive: true, cache: "no-store",
      headers: { "Content-Type": "application/json", "X-ChurchLight": "operator" },
      body: JSON.stringify({ action: "window_place", args: { x: place[0], y: place[1], width: place[2], height: place[3] } }),
    }).catch(() => {});
  } catch (err) { /* only a convenience */ }
}

/* ---------------- Alerts Settings: how the countdown, the scrolling bar and the alert look ---------------- */
/* The style the output pages are sent is built on the server (core/timerstyle.py). timerPreviewStyles does the same
   sums for the preview, so a change shows before the server has answered. A node test keeps them in step. */
function timerPreviewStyles(v) {
  return {
    projector: { mode: v.projector_mode, font: v.font, color: v.color, ended_color: v.ended_color, size: v.size, label_size: v.label_size,
      backdrop: v.backdrop, box: { x: v.clock_x, y: v.clock_y, w: v.clock_w, h: v.clock_h },
      bar: { x: v.bar_x, y: v.bar_y, w: v.bar_w, h: v.bar_h, bg: v.bar_bg, color: v.bar_color, size: v.bar_size, speed: v.bar_speed,
        radius: v.bar_radius, every: v.bar_every, text: v.bar_text, zero_text: v.bar_zero_text } },
    speaker: { font: v.speaker_font, color: v.speaker_color, ended_color: v.speaker_ended_color, size: v.speaker_size,
      label_size: Math.floor(Math.max(2, v.speaker_size * 0.35) * 10 + 0.5) / 10, position: v.speaker_position, backdrop: v.speaker_backdrop },
    alert: { font: v.font, bg: v.alert_bg, color: v.alert_color, size: v.alert_size, position: v.alert_position },
  };
}

/* "#rrggbb" plus an opacity from 0 to 100 -> "#rrggbbaa"; and back. */
function colorWithOpacity(hex, percent) {
  const base = String(hex).slice(0, 7);
  const alpha = Math.max(0, Math.min(255, Math.round(Number(percent) * 2.55)));
  return alpha === 255 ? base : base + (alpha < 16 ? "0" : "") + alpha.toString(16);
}
function splitOpacity(value) {
  const text = String(value || "#000000");
  const hex = /^#[0-9a-fA-F]{6}/.test(text) ? text.slice(0, 7) : text.length === 4 ? "#" + text[1] + text[1] + text[2] + text[2] + text[3] + text[3] : "#000000";
  const alpha = /^#[0-9a-fA-F]{8}$/.test(text) ? parseInt(text.slice(7), 16) : 255;
  return { hex: hex.toLowerCase(), percent: Math.round(alpha / 2.55) };
}

/* Drag a box: `handle` is "move" or the edge / corner letters (n, s, e, w, ne ...); dx and dy are the mouse movement in
   percent of the screen. The result stays on the screen and never gets smaller than 5 x 3 percent. */
const RECT_MIN_W = 5, RECT_MIN_H = 3;
function dragRect(rect, handle, dx, dy) {
  const clamp = (n, low, high) => Math.min(high, Math.max(low, n));
  let { x, y, w, h } = rect;
  if (handle === "move") {
    x = clamp(x + dx, 0, 100 - w);
    y = clamp(y + dy, 0, 100 - h);
  } else {
    if (handle.includes("w")) { const nx = clamp(x + dx, 0, x + w - RECT_MIN_W); w = x + w - nx; x = nx; }
    if (handle.includes("e")) w = clamp(w + dx, RECT_MIN_W, 100 - x);
    if (handle.includes("n")) { const ny = clamp(y + dy, 0, y + h - RECT_MIN_H); h = y + h - ny; y = ny; }
    if (handle.includes("s")) h = clamp(h + dy, RECT_MIN_H, 100 - y);
  }
  const round = (n) => Math.round(n * 10) / 10;
  return { x: round(x), y: round(y), w: round(w), h: round(h) };
}

const POSITION_NAMES = [["top-left", "Top left"], ["top-right", "Top right"], ["bottom-left", "Bottom left"], ["bottom-right", "Bottom right"]];
const TIMER_GROUPS = [
  { holder: "clock", title: "Big clock", rows: [
    { key: "font", label: "Font", kind: "font" },
    { key: "color", label: "Text color", kind: "color" },
    { key: "ended_color", label: "Color when it reaches zero", kind: "color" },
    { key: "size", label: "Clock text size", kind: "range", min: 10, max: 95, unit: "% of the box height" },
    { key: "label_size", label: "Words above it: size", kind: "range", min: 4, max: 40, unit: "% of the box height" },
    { key: "backdrop", label: "Box behind it", kind: "colorOpacity" } ] },
  { holder: "bar", title: "Scrolling bar", rows: [
    { key: "bar_text", label: "What it says", kind: "text", max: 120, help: "{minutes} becomes 10 minutes, 9 minutes ... 1 minute. Leave empty to say nothing" },
    { key: "bar_zero_text", label: "When it reaches zero", kind: "text", max: 120, help: "Leave empty to say nothing" },
    { key: "bar_every", label: "How often", kind: "choice", number: true, options: [["1", "Every minute"], ["2", "Every 2 minutes"], ["5", "Every 5 minutes"], ["10", "Every 10 minutes"]] },
    { key: "bar_speed", label: "Scroll speed", kind: "range", min: 4, max: 60, unit: "seconds to cross the screen" },
    { key: "font", label: "Font", kind: "font" },
    { key: "bar_color", label: "Text color", kind: "color" },
    { key: "bar_size", label: "Text size", kind: "range", min: 20, max: 95, unit: "% of the bar height" },
    { key: "bar_bg", label: "Bar color", kind: "colorOpacity" },
    { key: "bar_radius", label: "Rounded corners", kind: "range", min: 0, max: 50, unit: "% of the bar height" } ] },
  { holder: "speaker", title: "Timer on the stage and confidence screens", rows: [
    { key: "speaker_font", label: "Font", kind: "font" },
    { key: "speaker_color", label: "Text color", kind: "color" },
    { key: "speaker_ended_color", label: "Color when it reaches zero", kind: "color" },
    { key: "speaker_size", label: "Size", kind: "range", min: 3, max: 20, unit: "% of the height" },
    { key: "speaker_position", label: "Corner", kind: "choice", options: POSITION_NAMES },
    { key: "speaker_backdrop", label: "Box color", kind: "color" } ] },
  { holder: "speaker", title: "Alert bar (stage and confidence screens only)", rows: [
    { key: "alert_bg", label: "Bar color", kind: "color" },
    { key: "alert_color", label: "Text color", kind: "color" },
    { key: "alert_size", label: "Text size", kind: "range", min: 3, max: 14, unit: "% of the height" },
    { key: "alert_position", label: "Where it sits", kind: "choice", options: [["bottom", "Bottom"], ["top", "Top"]] } ] },
];

let tsControls = [];                 // { key, set(values), input }
let tsDraft = null;                  // the values the preview shows right now (saved ones plus any being dragged)
let tsEdit = "clock";                // which of the projector's two looks is being edited
const tsReady = { projector: false, speaker: false };

function tsValues() { return (state && state.timer_style && state.timer_style.values) || null; }
function tsSave(changes) { return act("timer_style_set", { style: changes }); }
function tsRectKeys() { return tsEdit === "bar" ? ["bar_x", "bar_y", "bar_w", "bar_h"] : ["clock_x", "clock_y", "clock_w", "clock_h"]; }
function tsRect(values) { const [x, y, w, h] = tsRectKeys().map((k) => values[k]); return { x: x, y: y, w: w, h: h }; }

function buildTimerStyleControls() {
  for (const id of ["clock", "bar", "speaker"]) $("ts-controls-" + id).replaceChildren();
  tsControls = [];
  for (const group of TIMER_GROUPS) {
    const holder = $("ts-controls-" + group.holder);
    holder.appendChild(el("h5", "", group.title));
    for (const row of group.rows) {
      const wrap = el("label", "theme-control");
      wrap.appendChild(el("span", "theme-label", row.label));
      let input, read, set;
      const save = () => tsSave({ [row.key]: read() });
      if (row.kind === "font") {
        input = el("select");
        for (const [value, text] of THEME_FONTS) input.appendChild(new Option(text, value));
        read = () => input.value;
        set = (v) => { if (!THEME_FONTS.some(([f]) => f === v)) input.appendChild(new Option(v, v)); input.value = v; };
        wrap.appendChild(input);
        input.addEventListener("change", save);
      } else if (row.kind === "choice") {
        input = el("select");
        for (const [value, text] of row.options) input.appendChild(new Option(text, value));
        read = () => (row.number ? Number(input.value) : input.value);
        set = (v) => { input.value = String(v); };
        wrap.appendChild(input);
        input.addEventListener("change", save);
      } else if (row.kind === "text") {
        const box = el("span", "ts-pair");
        input = el("input"); input.type = "text"; input.maxLength = row.max;
        read = () => input.value;
        set = (v) => { input.value = v; };
        box.appendChild(input);
        wrap.appendChild(box);
        input.addEventListener("input", () => tsPreview({ [row.key]: read() }));
        input.addEventListener("change", save);
        if (row.help) wrap.title = row.help;
      } else if (row.kind === "color") {
        input = el("input"); input.type = "color";
        read = () => input.value;
        set = (v) => { input.value = splitOpacity(v).hex; };
        wrap.appendChild(input);
        input.addEventListener("input", () => tsPreview({ [row.key]: read() }));
        input.addEventListener("change", save);
      } else if (row.kind === "range") {
        const pair = el("span", "ts-pair");
        input = el("input"); input.type = "range"; input.min = row.min; input.max = row.max; input.step = 1;
        const out = el("span", "muted");
        read = () => Number(input.value);
        set = (v) => { input.value = v; out.textContent = v + " " + row.unit; };
        pair.append(input, out);
        wrap.appendChild(pair);
        input.addEventListener("input", () => { out.textContent = input.value + " " + row.unit; tsPreview({ [row.key]: read() }); });
        input.addEventListener("change", save);
      } else {                                           // a color plus how see-through it is
        const pair = el("span", "ts-pair");
        input = el("input"); input.type = "color";
        const range = el("input"); range.type = "range"; range.min = 0; range.max = 100; range.step = 5;
        const out = el("span", "muted");
        read = () => colorWithOpacity(input.value, range.value);
        set = (v) => { const parts = splitOpacity(v); input.value = parts.hex; range.value = parts.percent; out.textContent = parts.percent + "% solid"; };
        pair.append(input, range, out);
        wrap.appendChild(pair);
        for (const control of [input, range]) {
          control.addEventListener("input", () => { out.textContent = range.value + "% solid"; tsPreview({ [row.key]: read() }); });
          control.addEventListener("change", save);
        }
      }
      holder.appendChild(wrap);
      tsControls.push({ key: row.key, input: input, set: set });
    }
  }
}

/* Show values being changed in the previews, before they are saved. */
function tsPreview(changes) {
  const base = tsValues();
  if (!base) return;
  tsDraft = Object.assign({}, tsDraft || base, changes);
  postTimerPreviews();
  drawRectEditor();
}

function drawTimerStyle() {
  const values = tsValues();
  if (!values) return;
  if (!tsControls.length) buildTimerStyleControls();
  for (const control of tsControls) {
    if (document.activeElement === control.input) continue;          // do not fight a control being used
    control.set(values[control.key]);
  }
  tsDraft = null;
  const live = values.projector_mode === "bar" ? "scrolling bar" : "big clock";
  $("ts-live-mode").textContent = "The projector shows the " + live + ".";
  $("ts-use").disabled = values.projector_mode === tsEdit;
  postTimerPreviews();
  drawRectEditor();
}

function timerPreviewPayload(which) {
  const values = tsDraft || tsValues();
  if (!values) return null;
  const styles = timerPreviewStyles(values);
  const nowSeconds = Date.now() / 1000;
  const common = { schema: 1, version: 1, output: "preview", theme_id: null, blank: false, base: null, overlay: null };
  if (which === "projector") {
    const style = Object.assign({}, styles.projector, { mode: tsEdit });
    style.bar = Object.assign({}, style.bar, { demo: true });          // the preview keeps scrolling so the look can be judged
    return Object.assign(common, { layout: "projector", audience: "public",
      countdown: { end: nowSeconds + 9 * 60 + 59, now: nowSeconds, label: "Service begins in", linger: 5, style: style } });
  }
  return Object.assign(common, { layout: "confidence", audience: "confidence",
    countdown: { end: nowSeconds + 4 * 60 + 12, now: nowSeconds, label: "Sermon", linger: 5, style: styles.speaker },
    message: "Wrap up in 2 minutes", message_style: styles.alert });
}

function postTimerPreviews() {
  for (const which of ["projector", "speaker"]) {
    const frame = $("ts-frame-" + which);
    const payload = timerPreviewPayload(which);
    if (!tsReady[which] || !payload || !frame.contentWindow) continue;
    frame.contentWindow.postMessage(payload, location.origin);
  }
}

function rescaleTimerFrames() {
  for (const which of ["projector", "speaker"]) {
    const frame = $("ts-frame-" + which);
    const scale = previewScale(frame.parentElement.clientWidth);
    if (scale) frame.style.transform = "scale(" + scale + ")";
  }
}

/* The draggable box on the projector preview. */
function drawRectEditor() {
  const box = $("ts-rect");
  const values = tsDraft || tsValues();
  if (!values) return;
  const rect = tsRect(values);
  box.hidden = false;
  box._rect = rect;
  box.style.left = rect.x + "%";
  box.style.top = rect.y + "%";
  box.style.width = rect.w + "%";
  box.style.height = rect.h + "%";
  box.firstChild.textContent = tsEdit === "bar" ? "Scrolling bar" : "Big clock";
}

function wireRectEditor() {
  const box = $("ts-rect");
  const view = $("ts-proj-view");
  box.appendChild(el("span", ""));
  for (const handle of ["nw", "n", "ne", "e", "se", "s", "sw", "w"]) {
    const knob = el("div", "h h-" + handle);
    knob.dataset.handle = handle;
    box.appendChild(knob);
  }
  let drag = null;
  box.addEventListener("pointerdown", (event) => {
    const handle = event.target.dataset.handle || "move";
    const area = view.getBoundingClientRect();
    drag = { handle: handle, x: event.clientX, y: event.clientY, rect: Object.assign({}, box._rect), w: area.width, h: area.height };
    box.setPointerCapture(event.pointerId);
    event.preventDefault();
  });
  const place = (event) => {
    const next = dragRect(drag.rect, drag.handle, (event.clientX - drag.x) / drag.w * 100, (event.clientY - drag.y) / drag.h * 100);
    const keys = tsRectKeys();
    return { next: next, changes: { [keys[0]]: next.x, [keys[1]]: next.y, [keys[2]]: next.w, [keys[3]]: next.h } };
  };
  box.addEventListener("pointermove", (event) => { if (drag) tsPreview(place(event).changes); });
  const finish = async (event) => {
    if (!drag) return;
    const changes = place(event).changes;
    drag = null;
    await tsSave(changes);
  };
  box.addEventListener("pointerup", finish);
  box.addEventListener("pointercancel", () => { drag = null; drawTimerStyle(); });
}

/* Big clock or scrolling bar: which one the editor shows (the projector keeps showing the saved choice). */
function setTimerEdit(which) {
  tsEdit = which === "bar" ? "bar" : "clock";
  $("ts-controls-clock").hidden = tsEdit !== "clock";
  $("ts-controls-bar").hidden = tsEdit !== "bar";
  for (const radio of document.querySelectorAll("input[name=ts-edit]")) radio.checked = radio.value === tsEdit;
  tsDraft = null;
  const values = tsValues();
  if (values) $("ts-use").disabled = values.projector_mode === tsEdit;
  postTimerPreviews();
  drawRectEditor();
}

/* The pages are only loaded once Alerts Settings is opened. */
function openTimerStyle() {
  const outputs = (state && state.outputs) || [];
  for (const which of ["projector", "speaker"]) {
    const frame = $("ts-frame-" + which);
    if (!frame.getAttribute("src") && outputs.length) frame.src = outputs[0].path + "?sandbox&preview";
  }
  drawTimerStyle();
  rescaleTimerFrames();
}

function wireTimerStyle() {
  $("ts-reset").addEventListener("click", () => act("timer_style_reset"));
  $("ts-copy-stage").addEventListener("click", () => {
    const v = tsValues();
    if (v) tsSave({ speaker_font: v.font, speaker_color: v.color, speaker_ended_color: v.ended_color });
  });
  wireRectEditor();
  for (const radio of document.querySelectorAll("input[name=ts-edit]")) radio.addEventListener("change", () => setTimerEdit(radio.value));
  $("ts-use").addEventListener("click", () => tsSave({ projector_mode: tsEdit }));
  $("cd-mode").addEventListener("change", () => tsSave({ projector_mode: $("cd-mode").value }));
  $("cd-look").addEventListener("click", () => {
    const values = tsValues();
    showView("broadcast");
    if (values) setTimerEdit(values.projector_mode);
    $("ts-proj").open = true;
    $("ts-proj").scrollIntoView({ block: "start" });
  });
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin) return;
    for (const which of ["projector", "speaker"]) {
      const frame = $("ts-frame-" + which);
      if (event.source === frame.contentWindow && event.data && event.data.sandbox === "ready") { tsReady[which] = true; postTimerPreviews(); }
    }
  });
  window.addEventListener("resize", rescaleTimerFrames);
  for (const id of ["ts-proj", "ts-speak"]) $(id).addEventListener("toggle", rescaleTimerFrames);
}

/* ---------------- Clicker helper: an AutoHotkey script that turns clicker keys into Trigger URLs ---------------- */
const CLICKER_KEY = "churchlight.clicker";
const CLICKER_DEFAULTS = [{ key: "PgDn", action: "next" }, { key: "PgUp", action: "previous" }, { key: ".", action: "blank" }];
const AHK_CODES = {
  PageDown: "PgDn", PageUp: "PgUp", ArrowRight: "Right", ArrowLeft: "Left", ArrowUp: "Up", ArrowDown: "Down",
  Home: "Home", End: "End", Space: "Space", Enter: "Enter", NumpadEnter: "Enter", Escape: "Esc", Tab: "Tab", Backspace: "Backspace",
  Period: ".", Comma: ",", Slash: "/", Minus: "-", Equal: "=", BracketLeft: "[", BracketRight: "]",
};

/* The AutoHotkey name for a key press (event.code), or "" when the helper cannot use that key. */
function ahkKeyName(code) {
  if (Object.prototype.hasOwnProperty.call(AHK_CODES, code)) return AHK_CODES[code];
  let m = /^Key([A-Z])$/.exec(code);
  if (m) return m[1].toLowerCase();
  m = /^Digit([0-9])$/.exec(code);
  if (m) return m[1];
  m = /^F([1-9]|1[0-2])$/.exec(code);
  if (m) return "F" + m[1];
  return "";
}

/* The rows worth keeping: a usable key, a known action, each key once. */
function cleanClickerRows(rows, actions) {
  const known = new Set((actions || []).map((a) => a.name));
  const seen = new Set();
  const out = [];
  for (const row of Array.isArray(rows) ? rows : []) {
    if (!row || typeof row.key !== "string" || typeof row.action !== "string") continue;
    if (!row.key || !known.has(row.action) || seen.has(row.key.toLowerCase())) continue;
    seen.add(row.key.toLowerCase());
    out.push({ key: row.key, action: row.action });
  }
  return out;
}

/* The text of the .ahk file. Each key calls the Trigger URL; if ChurchLight cannot be reached the key is sent on
   as a normal key, so the clicker is never left dead. */
function buildClickerScript(origin, token, rows) {
  const lines = [
    "; ChurchLight clicker helper - made by ChurchLight Presenter.",
    "; Needs AutoHotkey v2 (free, autohotkey.com). Double-click this file to start it.",
    "; It contains your private remote button link: keep it on this computer. After making a new",
    "; private code in ChurchLight (Settings, Advanced, Remote button links), download this script again.",
    "; Ctrl+Alt+F12 turns the clicker on and off.",
    "#Requires AutoHotkey v2.0",
    "#SingleInstance Force",
    "",
    'BaseUrl := "' + origin + "/hook/" + token + '/"',
    "Warned := false",
    "",
    "Press(action, fallback) {",
    "    global Warned",
    "    try {",
    '        req := ComObject("WinHttp.WinHttpRequest.5.1")',
    "        req.SetTimeouts(1000, 1000, 1000, 1000)",
    '        req.Open("GET", BaseUrl . action, false)',
    "        req.Send()",
    "        if (req.Status = 200 or req.Status = 409)",
    "            return",
    "    }",
    "    ; ChurchLight did not answer, or the secret is out of date: pass the key on as a normal key.",
    '    Send "{" . fallback . "}"',
    "    if (!Warned) {",
    "        Warned := true",
    '        TrayTip "ChurchLight did not answer. Is it running? If you made a new private code, download this script again.", "ChurchLight clicker"',
    "    }",
    "}",
    "",
  ];
  for (const row of rows) lines.push("$" + row.key + '::Press("' + row.action + '", "' + row.key + '")');
  lines.push("", "#SuspendExempt", "^!F12:: {", "    Suspend(-1)",
    '    TrayTip A_IsSuspended ? "Clicker is off." : "Clicker is on.", "ChurchLight clicker"', "}", "#SuspendExempt False", "",
    'TrayTip "The clicker is ready. Ctrl+Alt+F12 turns it on and off.", "ChurchLight clicker"', "");
  return lines.join("\r\n");
}

let clickerRows = CLICKER_DEFAULTS.map((r) => Object.assign({}, r));
let clickerSeeded = false;
let clickerNote = "";                        // a message from the last Start / Stop, shown until the next redraw after it
let clickerListening = null;                 // index of the row waiting for a clicker press
try {
  const saved = JSON.parse(localStorage.getItem(CLICKER_KEY) || "null");
  if (Array.isArray(saved)) clickerRows = saved.filter((r) => r && typeof r.key === "string" && typeof r.action === "string");
} catch (err) { /* the standard buttons */ }

function saveClickerRows() {
  try { localStorage.setItem(CLICKER_KEY, JSON.stringify(clickerRows)); } catch (err) { /* only a convenience */ }
}

function drawClicker() {
  const hook = state && state.hook;
  if (!hook) return;
  const list = $("clicker-list");
  if (!clickerSeeded) {
    clickerSeeded = true;
    let local = null;
    try { local = localStorage.getItem(CLICKER_KEY); } catch (err) { /* none */ }
    if (!local && state.clicker && state.clicker.rows.length) clickerRows = state.clicker.rows.map((r) => Object.assign({}, r));
  }
  list.replaceChildren();
  clickerRows.forEach((row, index) => {
    const item = el("li", "row");
    item.appendChild(el("span", "clicker-key", row.key || "(none)"));
    const listen = el("button", "", clickerListening === index ? "Now press the button…" : "Press the button");
    listen.type = "button";
    if (clickerListening === index) listen.classList.add("listening");
    listen.addEventListener("click", () => { clickerListening = clickerListening === index ? null : index; drawClicker(); });
    item.appendChild(listen);
    const select = el("select");
    select.setAttribute("aria-label", "What the button " + (row.key || "") + " does");
    for (const action of hook.actions) {
      const option = el("option", "", action.label);
      option.value = action.name;
      select.appendChild(option);
    }
    select.value = row.action;
    select.addEventListener("change", () => { row.action = select.value; saveClickerRows(); });
    item.appendChild(select);
    const remove = el("button", "", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => { clickerRows.splice(index, 1); clickerListening = null; saveClickerRows(); drawClicker(); });
    item.appendChild(remove);
    list.appendChild(item);
  });
  const usable = cleanClickerRows(clickerRows, hook.actions);
  $("clicker-download").disabled = !hook.on || !usable.length;
  const helper = state.clicker || { available: false, running: false, auto: false, rows: [] };
  $("clicker-start").hidden = !helper.available;
  $("clicker-stop").hidden = !helper.available || !helper.running;
  $("clicker-auto-line").hidden = !helper.available;
  $("clicker-start").textContent = helper.running ? "Restart the helper with these buttons" : "Start the clicker helper";
  $("clicker-start").disabled = !hook.on || !usable.length;
  if (document.activeElement !== $("clicker-auto")) $("clicker-auto").checked = !!helper.auto;
  const toolbarButton = $("btn-clicker");
  toolbarButton.hidden = !helper.available;
  toolbarButton.textContent = helper.running ? "Clicker: on" : "Clicker: off";
  toolbarButton.title = helper.running ? "The clicker helper is running. Press to stop it." : "Start the clicker helper. Set up its buttons in Settings.";
  setToggle(toolbarButton, helper.running);
  const status = !hook.on ? "Turn Remote button links on (above) before starting the helper."
    : !usable.length ? "Add at least one button with a key."
    : helper.available ? (helper.running ? "The clicker helper is running." : "The clicker helper is not running.")
    : "The built-in helper runs on Windows only. You can still download the script for a computer that has AutoHotkey v2.";
  if (!clickerNote) $("clicker-status").textContent = status;
}

function wireClicker() {
  $("clicker-add").addEventListener("click", () => {
    const hook = state && state.hook;
    if (!hook || !hook.actions.length) return;
    clickerRows.push({ key: "", action: hook.actions[0].name });
    clickerListening = clickerRows.length - 1;
    saveClickerRows();
    drawClicker();
  });
  document.addEventListener("keydown", (event) => {
    if (clickerListening === null) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const name = ahkKeyName(event.code);
    if (event.code === "Escape" && !event.shiftKey) { clickerListening = null; drawClicker(); return; }
    if (!name) { $("clicker-status").textContent = "The helper cannot use that key. Try another button."; return; }
    const taken = clickerRows.some((r, i) => i !== clickerListening && r.key.toLowerCase() === name.toLowerCase());
    if (taken) { $("clicker-status").textContent = name + " is already used by another button."; return; }
    clickerRows[clickerListening].key = name;
    clickerListening = null;
    saveClickerRows();
    drawClicker();
  }, true);
  $("clicker-start").addEventListener("click", async () => {
    const hook = state && state.hook;
    if (!hook) return;
    const rows = cleanClickerRows(clickerRows, hook.actions);
    if (await act("clicker_start", { rows }) === null) return;
    $("clicker-status").textContent = "The clicker helper is running. Ctrl+Alt+F12 turns it on and off.";
    clickerNote = "x";
    setTimeout(() => { clickerNote = ""; }, 4000);
  });
  $("clicker-stop").addEventListener("click", async () => { await act("clicker_stop"); });
  $("btn-clicker").addEventListener("click", async () => {               // the toolbar button: start or stop
    const helper = state && state.clicker;
    if (helper && helper.running) { await act("clicker_stop"); return; }
    const hook = state && state.hook;
    if (!hook) return;
    await act("clicker_start", { rows: cleanClickerRows(clickerRows, hook.actions) });
  });
  $("clicker-auto").addEventListener("change", () => act("clicker_auto_set", { on: $("clicker-auto").checked }));
  $("clicker-download").addEventListener("click", () => {
    const hook = state && state.hook;
    if (!hook || !hook.on) return;
    const rows = cleanClickerRows(clickerRows, hook.actions);
    if (!rows.length) return;
    const blob = new Blob([buildClickerScript(location.origin, hook.token, rows)], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "ChurchLight-clicker.ahk";
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    $("clicker-status").textContent = "Downloaded ChurchLight-clicker.ahk. Double-click it to start the clicker.";
  });
}

/* ---- the song report ---- */
let reportData = null;
async function loadReport() {
  const result = await call("report_list");
  if (!result.ok) { showMessage(result.error); return; }
  reportData = result.data.report;
  drawReport();
}
function drawReport() {
  const box = $("report-sections");
  box.replaceChildren();
  const sections = reportData ? reportData.sections : [];
  $("report-empty").hidden = sections.length > 0;
  $("report-copy-all").disabled = !sections.length;
  for (const sec of sections) {
    const block = el("div", "report-section");
    block.appendChild(el("h4", "", sec.title + " — " + sec.date));
    const list = el("ol");
    for (const song of sec.songs) {
      list.appendChild(el("li", "", song.title + (song.author || song.ccli ? " (" + [song.author, song.ccli ? "CCLI " + song.ccli : ""].filter(Boolean).join(", ") + ")" : "")));
    }
    block.appendChild(list);
    const buttons = el("div", "buttons");
    const copy = el("button", "", "Copy this section");
    copy.type = "button";
    copy.addEventListener("click", () => copyReportText(sec.text));
    const remove = el("button", "", "Remove");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      if (await act("report_remove", { section: sec.section }) === null) return;
      await loadReport();
      showNotice("Removed the report for " + sec.title + ".", async () => { if (await act("undo_report_remove") !== null) await loadReport(); });
    });
    buttons.append(copy, remove);
    block.appendChild(buttons);
    box.appendChild(block);
  }
}
async function copyReportText(text) {
  const hint = $("report-copy-hint");
  try {
    await navigator.clipboard.writeText(text);
    hint.textContent = "Copied";
  } catch (err) {
    hint.textContent = "Your browser would not copy it. Select the text above and copy it by hand.";
  }
  setTimeout(() => { hint.textContent = ""; }, 3000);
}
function wireReport() {
  confirmClick($("report-btn"), async () => {                 // press, then "Sure?" in the same place
    const data = await act("report_service");
    if (data === null) return;
    const r = data.reported;
    showNotice((r.replaced ? "Updated" : "Added") + " the report for " + r.title + " (" + r.date + "): " + r.songs + (r.songs === 1 ? " song." : " songs."));
    if (!$("report-panel").hidden) await loadReport();
  });
  $("report-list-btn").addEventListener("click", async () => {
    const open = $("report-panel").hidden;
    $("report-panel").hidden = !open;
    $("report-list-btn").setAttribute("aria-expanded", String(open));
    if (open) await loadReport();
  });
  $("usage-download").addEventListener("click", () => { downloadFile("/api/export?kind=usage"); });
  $("report-copy-all").addEventListener("click", () => copyReportText(reportData ? reportData.text : ""));
}

/* ---- countdown and speaker alert ---- */
let countdownInfo = null;
function countdownLeft(info, nowMs) {
  return info && info.end ? Math.ceil((info.end * 1000 - (nowMs + info.offset)) / 1000) : null;
}
function clockText(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = seconds % 60;
  const two = (n) => (n < 10 ? "0" : "") + n;
  return h > 0 ? h + ":" + two(m) + ":" + two(s) : m + ":" + two(s);
}
function tickCountdownStatus() {
  const left = countdownLeft(countdownInfo, Date.now());
  const status = $("cd-status");
  if (left === null) status.textContent = "No countdown is running.";
  else if (left <= 0) status.textContent = "The countdown has finished. Press Stop countdown to clear it.";
  else status.textContent = "Counting down: " + clockText(left) + " left.";
  $("cd-stop").disabled = left === null;
  const chip = $("chip-countdown");
  chip.hidden = left === null;
  if (left !== null) chip.textContent = left <= 0 ? "Countdown finished" : "Countdown " + clockText(left);
}
function drawCountdown() {
  const c = state && state.countdown;
  countdownInfo = c && c.end ? { end: c.end, offset: c.now * 1000 - Date.now() } : null;
  tickCountdownStatus();
  const message = c && c.message ? c.message : "";
  $("alert-status").textContent = message ? "Showing on the speaker screens: " + message : "No alert is showing.";
  $("alert-clear").disabled = !message;
  $("chip-alert").hidden = !message;
  $("chip-alert").textContent = message ? "Alert: " + message : "";
  const look = tsValues();
  if (look && document.activeElement !== $("cd-mode")) $("cd-mode").value = look.projector_mode;
  if (look && document.activeElement !== $("cd-bar-text")) $("cd-bar-text").value = look.bar_text || "";
  if (look && document.activeElement !== $("cd-zero-text")) $("cd-zero-text").value = look.bar_zero_text || "";
  syncCountdownInputs();
}

/* Big clock: the words go above the clock. Scrolling bar: the words are the bar's message, with {minutes} in it. */
function syncCountdownInputs() {
  const bar = $("cd-mode").value === "bar";
  $("cd-label-line").hidden = bar;
  $("cd-bar-line").hidden = !bar;
  $("cd-zero-line").hidden = !bar;
}

function wireCountdown() {
  $("cd-mode").addEventListener("change", syncCountdownInputs);
  $("cd-bar-text").addEventListener("change", () => tsSave({ bar_text: $("cd-bar-text").value }));
  $("cd-zero-text").addEventListener("change", () => tsSave({ bar_zero_text: $("cd-zero-text").value }));
  const label = () => $("cd-label").value.trim();
  $("cd-start-at").addEventListener("click", () => {
    if (!$("cd-at").value) { showMessage("Pick the time to count down to."); return; }
    act("countdown_start", { at: $("cd-at").value, label: label() });
  });
  $("cd-start-min").addEventListener("click", () => {
    const minutes = Number($("cd-minutes").value);
    if (!(minutes > 0)) { showMessage("Enter the minutes."); return; }
    act("countdown_start", { minutes, label: label() });
  });
  $("cd-stop").addEventListener("click", () => act("countdown_stop"));
  const send = () => act("alert_set", { text: $("alert-text").value });
  $("alert-send").addEventListener("click", send);
  $("alert-text").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); send(); } });
  $("alert-clear").addEventListener("click", async () => { $("alert-text").value = ""; await act("alert_set", { text: "" }); });
  setInterval(tickCountdownStatus, 1000);
}

async function act(action, args) {
  const result = await call(action, args);
  if (!result.ok) { showMessage(result.error); return null; }
  showMessage("");
  if (SAVED_ACTIONS.has(action)) flashSaved();
  return result.data;
}

/* "Saved ✓" in the connection pill for about two seconds after an edit is saved. Only these edits count (things a person
   changes and expects to keep); live actions (next slide, blank, play) never show it. A connection problem overrides it. */
const SAVED_ACTIONS = new Set(["song_save", "church_ccli", "notice_set", "item_notice", "deck_pages_reorder", "deck_pages_delete",
  "deck_pages_duplicate", "deck_pages_skip", "deck_pages_add", "deck_page_replace", "deck_pages_timing", "deck_page_notes",
  "deck_page_title", "deck_image_fit", "deck_transition", "deck_autoplay", "item_autoplay", "theme_save", "theme_duplicate",
  "default_theme", "bible_theme", "confidence_theme", "verse_over_theme", "service_theme", "item_theme", "timer_style_set",
  "timer_style_reset", "confidence_words_set", "hook_set", "clicker_auto_set", "obs_auto_set", "screens_set", "screen_assign",
  "screen_shape_set", "window_place", "media_rename", "service_verse_split", "set_translation"]);
let savedTimer = null;
let connectionDown = false;
function setConnection(text, bad) {
  connectionDown = bad;
  clearTimeout(savedTimer);
  $("connection").textContent = text;
  $("connection").className = bad ? "conn bad" : "conn";
}
function flashSaved() {
  if (connectionDown) return;
  $("connection").textContent = "Saved ✓";
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => { if (!connectionDown) $("connection").textContent = "Connected"; }, 2000);
}

function drawSlide(into, summary, emptyText) {
  into.replaceChildren();
  if (!summary) { into.textContent = emptyText; return; }
  if (summary.label) into.appendChild(el("span", "label", summary.label));
  if (summary.clip) into.appendChild(el("div", "clip-tag", summary.clip === "video" ? "Video clip (it plays on the projector)" : "Sound (it plays on the projector)"));
  if (summary.image) {
    const img = el("img");
    img.alt = summary.label || "Slide";
    img.src = summary.image;
    into.appendChild(img);
  }
  for (const line of summary.lines) {
    const row = el("div");
    if (line.number !== null) row.appendChild(el("span", "num", String(line.number)));
    row.appendChild(document.createTextNode(line.text));
    into.appendChild(row);
  }
  if (summary.reference) into.appendChild(el("span", "ref", summary.reference + (summary.continued ? " (continued)" : "")));
}

const optionsChoice = new Map();    // item id -> true/false when the operator opened or closed its options (kept across redraws)

function hasOverrides(item) {
  return item.theme_id !== null && item.theme_id !== undefined || item.overlay_theme_id !== null && item.overlay_theme_id !== undefined;
}

function itemOptionsOpen(item) {
  return optionsChoice.has(item.item_id) ? optionsChoice.get(item.item_id) : !!item.live;
}

/* The toolbar button has two jobs. While any item's drop-down is open it closes them all, the live item's too.
   Once everything is closed it opens only the live item's. Items without a choice go back to their default (closed,
   and a live one opens by itself), so the next item still opens as the service moves on. */
function foldItemsPlan(items, isOpen) {
  const anyOpen = items.some(isOpen);
  const live = items.find((item) => item.live) || null;
  return { collapse: anyOpen, label: anyOpen ? "Collapse items" : "Open live item", disabled: !items.length || (!anyOpen && !live), live: live };
}

function drawFoldItems() {
  const plan = foldItemsPlan((state && state.items) || [], itemOptionsOpen);
  const button = $("btn-fold-items");
  button.textContent = withHint(plan.label, keyHint(keyChoices, "fold_items"));
  button.disabled = plan.disabled;
}

function foldItems() {
  const items = (state && state.items) || [];
  const plan = foldItemsPlan(items, itemOptionsOpen);
  optionsChoice.clear();
  if (plan.collapse) { for (const item of items) if (item.live) optionsChoice.set(item.item_id, false); }
  else if (plan.live) optionsChoice.set(plan.live.item_id, true);
  drawItems(items);
}

function drawItems(items) {
  const list = $("items");
  // a redraw rebuilds every row: remember where the service pane and each song's part list were scrolled to
  const pane = list.closest(".col.service");
  const paneTop = pane ? pane.scrollTop : 0;
  const innerTops = new Map();
  for (const box of list.querySelectorAll(".outline-list")) {
    const row = box.closest(".item-row");
    if (row && row.dataset.item) innerTops.set(row.dataset.item, box.scrollTop);
  }
  try { drawItemRows(items, list); } finally {
    if (pane) pane.scrollTop = paneTop;
    for (const box of list.querySelectorAll(".outline-list")) {
      const row = box.closest(".item-row");
      if (row && innerTops.has(row.dataset.item)) box.scrollTop = innerTops.get(row.dataset.item);
    }
  }
}

function drawItemRows(items, list) {
  list.replaceChildren();
  drawFoldItems();
  if (!items.length) {
    const message = state.service ? "Nothing in this service yet. Search songs and slideshows above." : "";
    list.appendChild(el("li", "muted", message));
    return;
  }
  const nextAt = nextItemIndex(items);
  items.forEach((item, index) => {
    const row = el("li", "row item-row" + (item.live ? " live" : "") + (index === nextAt ? " next" : ""));
    row.dataset.item = String(item.item_id);
    const main = el("div", "item-main");
    const go = el("button", "primary item-go", "Go");        // first on the row, where the eye starts
    go.type = "button";
    go.setAttribute("aria-label", "Go live with " + item.title);
    go.addEventListener("click", () => {
      if (item.live) {                       // already live: only open its drop-down, nothing else changes
        optionsChoice.set(item.item_id, true);
        drawItems(state.items);
        return;
      }
      act("go_item", { item_id: item.item_id });
    });
    main.appendChild(go);
    const title = el("span", "title");
    title.appendChild(el("span", "badge", item.type));
    title.appendChild(document.createTextNode(item.title));
    if (item.live) title.appendChild(el("span", "tag", "LIVE"));
    if (item.live && item.last_slide) title.appendChild(el("span", "tag last-tag", "LAST SLIDE"));
    if (index === nextAt) title.appendChild(el("span", "tag next-tag", "NEXT"));
    main.appendChild(title);
    if (item.type === "scripture" && item.slides > 1) main.appendChild(el("span", "item-slides muted", item.slides + " slides"));
    if (item.type === "scripture" && item.note) {
      const warn = el("span", "item-warning");
      warn.appendChild(el("span", "warn-mark", "⚠"));
      warn.appendChild(document.createTextNode(" " + item.note));
      main.appendChild(warn);
    }
    const actions = el("span", "item-actions");        // the buttons stay together; the whole group drops under a long title
    main.appendChild(actions);
    for (const [label, delta, name] of [["↑", -1, "up"], ["↓", 1, "down"]]) {
      const button = el("button", "", label);
      button.type = "button";
      button.setAttribute("aria-label", "Move " + item.title + " " + name);
      const target = moveTarget(items, index, delta);
      button.disabled = target === null;
      button.addEventListener("click", () => act("move", { item_id: item.item_id, position: target }));
      actions.appendChild(button);
    }
    const options = el("div", "item-options");
    const isOpen = itemOptionsOpen(item);      // the live item opens by itself
    options.hidden = !isOpen;
    const more = el("button", "more", hasOverrides(item) ? "⋯•" : "⋯");
    more.type = "button";
    more.title = "Options for this item: themes, timing, parts, slides";
    more.setAttribute("aria-label", "Options for " + item.title);
    more.setAttribute("aria-expanded", String(isOpen));
    more.addEventListener("click", () => {
      optionsChoice.set(item.item_id, options.hidden);
      options.hidden = !options.hidden;
      more.setAttribute("aria-expanded", String(!options.hidden));
      drawFoldItems();
    });
    actions.appendChild(more);
    const remove = el("button", "danger", "✕");
    remove.type = "button";
    remove.setAttribute("aria-label", "Remove " + item.title);
    remove.addEventListener("click", async () => {
      optionsChoice.delete(item.item_id);
      if (await act("remove", { item_id: item.item_id }) === null) return;
      showNotice("Removed “" + item.title + "” from the service.", () => act("undo_remove"));
    });
    actions.appendChild(remove);
    row.appendChild(main);

    if (item.type !== "presentation") {
      const picks = [["projector", item.type === "scripture" ? "Projector theme (Bible theme if blank)" : item.type === "song" ? "Projector theme (the song's own, else the service's, if blank)" : "Projector theme (service's if blank)", item.theme_id],
                     ["overlay", "Overlay theme (service's if blank)", item.overlay_theme_id]];
      for (const [kind, none, chosen] of picks) {
        const label = el("label", "theme-control");
        label.appendChild(el("span", "theme-label", kind === "overlay" ? "Overlay theme" : "Projector theme"));
        const look = el("select", "item-theme");
        fillThemeSelect(look, none, chosen, kind);
        look.setAttribute("aria-label", (kind === "overlay" ? "Overlay theme for " : "Projector theme for ") + item.title);
        look.addEventListener("change", () => act("item_theme", Object.assign({ item_id: item.item_id, kind: kind }, look.value ? { id: Number(look.value) } : {})));
        label.appendChild(look);
        options.appendChild(label);
      }
    }
    if (item.type === "scripture" && item.slide_list && item.slide_list.length > 1) {
      const slideBox = el("ol", "item-slide-list slide-bars");
      const liveNow = state && state.live ? state.live.index : null;
      item.slide_list.forEach((line, i) => {
        const here = item.live && liveNow !== null && (item.slide_numbers || [])[i] === liveNow;
        const entry = el("li", "outline-slide" + (here ? " slide-live" : ""));
        const number = (item.slide_numbers || [])[i];
        if (number !== undefined) {                                  // the whole line jumps straight to this slide
          entry.appendChild(slideBar(line, here ? "live" : "", "Show " + line, () => act("go_slide", { index: number })));
        } else entry.appendChild(el("span", "slide-text", line));
        slideBox.appendChild(entry);
      });
      options.appendChild(slideBox);
    }
    if (item.type === "presentation" && item.deck_slides && item.deck_slides.length > 1) options.appendChild(deckSlideList(item));
    if (item.timing) options.appendChild(itemTimingControls(item));
    if (item.outline && (item.parts && item.parts.total > 1 || item.outline.some((p) => p.slides.length > 1 || p.notice))) options.appendChild(songOutlineControls(item));
    if (!options.children.length) options.appendChild(el("p", "muted", "No options for this item."));
    row.appendChild(options);
    list.appendChild(row);
  });
}

/* One slide in a drop-down list: the whole row is a button ("Click a line to show it"). A small mark at the left
   says where things are: ▶ for the line on the screen, ✓ for ones already shown, → for the next, a faded ▶ for the rest. */
function slideBar(text, state, label, onShow) {
  const bar = el("button", "slide-bar");
  bar.type = "button";
  bar.title = text + "  (click to show this slide now)";
  bar.setAttribute("aria-label", label);
  if (state === "live") bar.setAttribute("aria-current", "true");
  bar.appendChild(el("span", "slide-mark", state === "done" ? "✓" : state === "next" ? "→" : "▶"));
  bar.appendChild(el("span", "slide-text", text));
  bar.addEventListener("click", onShow);
  return bar;
}

/* A slideshow in the service: every slide by its title ("Offering") or "Slide 7", each with a Go button. No pictures are
   loaded, so a long slideshow costs nothing. The live slide is marked, the ones before it faded, the next one outlined. */
function deckSlideList(item) {
  const box = el("div", "item-parts");
  box.appendChild(el("span", "muted", "Click a slide to show it:"));
  const live = state && state.live ? state.live.index : null;
  const view = deckSlideView(item.deck_slides, live, item.live);
  const list = el("ol", "outline-list deck-slide-list slide-bars");
  let liveRow = null;
  for (const slide of view) {
    const line = el("li", "outline-slide" + (slide.state ? " slide-" + slide.state : ""));
    line.appendChild(slideBar(slide.label, slide.state, "Go to " + slide.label, () => act("go_slide", { index: slide.index })));
    list.appendChild(line);
    if (slide.state === "live") liveRow = line;
  }
  box.appendChild(list);
  if (liveRow && outlineFollowed.get(item.item_id) !== live) {
    outlineFollowed.set(item.item_id, live);
    requestAnimationFrame(() => { list.scrollTop = Math.max(0, liveRow.offsetTop - list.clientHeight / 2); });
  }
  return box;
}

/* A song's title and CCLI slide in its list: a tick to show it in this service, and its one slide as a bar. */
function noticeRow(item, part) {
  const row = el("li", "outline-part notice-part" + (part.skipped ? " left-out" : ""));
  const label = el("label", "");
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = !part.skipped;
  check.setAttribute("aria-label", "Show the title and CCLI slide in this service");
  check.addEventListener("change", () => {
    if (runMode === "run") { check.checked = !check.checked; return; }          // parts are only left out or put back in Plan mode
    act("item_notice", { item_id: item.item_id, off: !check.checked });
  });
  label.appendChild(check);
  if (part.skipped) label.appendChild(el("span", "skip-mark", "✕"));
  label.appendChild(document.createTextNode(" " + part.label));
  if (part.skipped) label.appendChild(el("span", "muted", "  left out of this service"));
  row.appendChild(label);
  if (part.slides.length) {
    const slides = el("ol", "outline-slides slide-bars");
    for (const slide of part.slides) {
      const line = el("li", "outline-slide" + (slide.state ? " slide-" + slide.state : ""));
      line.appendChild(slideBar(slide.line || part.label, slide.state, "Go to the title and CCLI slide", () => act("go_slide", { index: slide.index })));
      slides.appendChild(line);
    }
    row.appendChild(slides);
  }
  return row;
}

/* A song in the service: its parts in order (tick one to sing it), each with its slides, and where the service is. */
const outlineFollowed = new Map();     // service item id -> "live slide number" the list last scrolled to

function songOutlineControls(item) {
  const box = el("div", "item-parts");
  const noticeOff = (item.outline || []).some((p) => p.notice && p.skipped);
  const summary = [partsSummary(item.parts), noticeOff ? "title and CCLI slide left out" : ""].filter(Boolean).join(", ");
  if (summary) {                                // the line only says something when parts are left out
    box.appendChild(el("span", "muted", "Parts and where we are:"));
    box.appendChild(el("span", "tag", summary));
  }
  const reset = el("button", "reset-parts", "Sing the whole song");
  reset.type = "button";
  reset.hidden = !summary;                       // only there when something is left out (and only in Run mode, by the style)
  reset.addEventListener("click", async () => {
    if (partsSummary(item.parts)) await act("item_parts", { item_id: item.item_id, skip: [] });
    if (noticeOff) await act("item_notice", { item_id: item.item_id, off: false });
  });
  box.appendChild(reset);
  const live = state && state.live ? state.live.index : null;
  const view = outlineView(item.outline, live, item.live);
  const list = el("ul", "parts-list outline-list");
  const boxes = [];
  let liveRow = null;
  for (const part of view) {
    if (part.notice) { list.appendChild(noticeRow(item, part)); if (part.slides.some((s) => s.state === "live")) liveRow = list.lastChild.querySelector(".slide-live"); continue; }
    const row = el("li", part.skipped ? "outline-part left-out" : "outline-part");
    const label = el("label", "");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = !part.skipped;
    check.setAttribute("aria-label", "Sing " + part.label);
    boxes.push(check);
    label.appendChild(check);
    if (part.skipped) label.appendChild(el("span", "skip-mark", "✕"));          // in Run mode this stands where the tick was
    label.appendChild(document.createTextNode(" " + part.label));
    if (part.skipped) label.appendChild(el("span", "muted", "  left out"));
    else if (part.firstLine && !part.slides.length) label.appendChild(el("span", "muted", "  " + part.firstLine));
    row.appendChild(label);
    if (part.slides.length) {
      const slides = el("ol", "outline-slides slide-bars");
      for (const slide of part.slides) {
        const line = el("li", "outline-slide" + (slide.state ? " slide-" + slide.state : ""));
        line.appendChild(slideBar(slide.line || part.label, slide.state, "Go to " + part.label + ": " + (slide.line || "this slide"),
          () => act("go_slide", { index: slide.index })));
        slides.appendChild(line);
        if (slide.state === "live") liveRow = line;
      }
      row.appendChild(slides);
    }
    list.appendChild(row);
  }
  const apply = async () => {
    if (runMode === "run") { drawItems(state.items); return; }                   // parts are only left out or put back in Plan mode
    const skip = boxes.map((b, i) => (b.checked ? -1 : i)).filter((i) => i >= 0);
    if (skip.length === boxes.length) { showMessage("Keep at least one part of the song."); drawItems(state.items); return; }
    await act("item_parts", { item_id: item.item_id, skip: skip });
  };
  for (const check of boxes) check.addEventListener("change", apply);
  box.appendChild(list);
  // Keep the live slide in view inside the list, once per change (not on every redraw, so scrolling by hand sticks).
  if (liveRow && outlineFollowed.get(item.item_id) !== live) {
    outlineFollowed.set(item.item_id, live);
    requestAnimationFrame(() => { list.scrollTop = Math.max(0, liveRow.offsetTop - list.clientHeight / 2); });
  }
  return box;
}

/* A deck in the service: follow the deck's timing, or use its own for this service. */
function itemTimingControls(item) {
  const box = el("div", "item-timing");
  const mode = document.createElement("select");
  mode.setAttribute("aria-label", "Timing for " + item.title);
  mode.appendChild(new Option("Timing: " + autoplayText(item.timing.custom ? 0 : item.timing.seconds, item.timing.end_action).replace("Manual advance", "manual") + " (slideshow setting)", "deck"));
  mode.appendChild(new Option("Timing: set for this service", "custom"));
  mode.value = item.timing.custom ? "custom" : "deck";
  box.appendChild(mode);
  if (item.timing.custom) {
    const seconds = document.createElement("input");
    seconds.type = "number"; seconds.min = "0"; seconds.max = String(MAX_AUTO_SECONDS); seconds.step = "1";
    seconds.value = item.timing.seconds;
    seconds.setAttribute("aria-label", "Seconds per slide (0 for manual)");
    const end = document.createElement("select");
    end.setAttribute("aria-label", "At the end");
    for (const choice of END_CHOICES) end.appendChild(new Option(choice.text, choice.value));
    end.value = item.timing.end_action;
    const save = () => {
      const n = parseAutoSeconds(seconds.value);
      if (n === null) { showMessage("Type a whole number of seconds from 0 to " + MAX_AUTO_SECONDS + " (0 means manual)."); seconds.value = item.timing.seconds; return; }
      act("item_autoplay", { item_id: item.item_id, seconds: n, end_action: end.value });
    };
    seconds.addEventListener("change", save);
    end.addEventListener("change", save);
    box.append(seconds, el("span", "muted", "s, then"), end);
  }
  mode.addEventListener("change", () => {
    if (mode.value === "deck") act("item_autoplay", { item_id: item.item_id });
    else act("item_autoplay", { item_id: item.item_id, seconds: item.timing.seconds, end_action: item.timing.end_action });
  });
  return box;
}

function drawServiceTheme() {
  const select = $("service-theme");
  if (!state || !state.service) return;
  if (document.activeElement !== select) fillThemeSelect(select, "No theme (plain)", state.service.theme_id, "projector");
  const split = $("service-verse-split");
  if (document.activeElement !== split) split.value = state.service.verse_split || "";
  const overlay = $("service-overlay-theme");
  if (document.activeElement !== overlay) fillThemeSelect(overlay, "No theme (plain)", state.service.overlay_theme_id, "overlay");
}

let previewFor = "";                 // "kind:id" of the result whose slides are open under it

function drawLibrary() {
  const list = $("results");
  $("preview-home").appendChild($("preview"));      // keep the panel out of the list while it is rebuilt
  list.replaceChildren();
  const hasService = !!(state && state.service);
  libraryHadService = hasService;
  if (libraryMode($("search").value, showAll) === "idle") {
    list.appendChild(el("li", "muted", "Type a song or slideshow name, or press Show all."));
    return;
  }
  if (!libraryEntries.length) { list.appendChild(el("li", "muted", "Nothing found")); return; }
  for (const entry of libraryEntries) {
    const row = el("li", "row");
    const title = el("span", "title");
    title.appendChild(el("span", "badge", entryBadge(entry)));
    title.appendChild(document.createTextNode(entry.title));
    if (entry.detail) title.appendChild(el("span", "detail", entry.detail));
    row.appendChild(title);
    const add = el("button", "", "Add");
    add.type = "button";
    add.disabled = !hasService;
    add.title = hasService ? "Add to the service" : "Start or open a service first";
    add.setAttribute("aria-label", "Add " + entry.title + " to the service");
    add.addEventListener("click", () => act("add", { kind: entry.kind, id: entry.id }));
    const live = el("button", "", "Live");
    live.type = "button";
    live.title = "Show this on its own, without changing the service";
    live.setAttribute("aria-label", "Show " + entry.title + " live on its own");
    live.addEventListener("click", () => act("go_entry", { kind: entry.kind, id: entry.id }));
    const look = el("button", "", "Look");
    look.type = "button";
    look.title = "See its slides without showing them";
    look.setAttribute("aria-label", "Preview " + entry.title);
    look.addEventListener("click", () => showPreview(entry));
    row.append(look, add, live);
    list.appendChild(row);
    if (previewFor === entry.kind + ":" + entry.id && !$("preview").hidden) list.appendChild($("preview"));   // right under its own song
  }
}

async function showPreview(entry) {
  const data = await act("preview", { kind: entry.kind, id: entry.id });
  if (!data) return;
  previewFor = entry.kind + ":" + entry.id;
  $("preview-title").textContent = entry.title;
  const list = $("preview-slides");
  list.replaceChildren();
  for (const view of data.slides) {
    const summary = slideSummary(view);
    const item = el("li");
    drawSlide(item, summary, "");
    list.appendChild(item);
  }
  $("preview").hidden = false;
  drawLibrary();
  updateSearchButtons();
}

const RESULTS_KEY = "churchlight.resultsunder";
let resultsUnder = true;            // false: only the dropdown under the search box (the backup way to search)
try { resultsUnder = localStorage.getItem(RESULTS_KEY) !== "off"; } catch (err) { /* no storage: results show */ }

function applyResultsSwitch() {
  $("results-switch").checked = resultsUnder;
  $("results").hidden = !resultsUnder;
  if (!resultsUnder) $("preview").hidden = true;
  updateSearchButtons();
  if (resultsUnder) loadLibrary();
}

let searchTimer = null;
let searchCounter = 0;
let showAll = false;
let searchKind = "";                       // "", "song", "presentation", "image", "video" or "audio": narrows the search box's results and names
const SEARCH_KINDS = ["", "song", "presentation", "image", "video", "audio"];
const MEDIA_WORDS = { image: "picture", video: "video", audio: "sound" };
function entryBadge(entry) {
  return entry.kind === "presentation" ? "slideshow" : entry.kind === "media" ? (MEDIA_WORDS[entry.media_type] || "media") : "song";
}
const KIND_KEY = "churchlight.searchkind";
function kindParam() { return searchKind ? "&kind=" + searchKind : ""; }

/* The Clear button is useful whenever the search is not just the bare bar (text, a full list, or a Look panel). */
function updateSearchButtons() {
  const mode = libraryMode($("search").value, showAll);
  $("search-clear").hidden = mode === "idle" && $("preview").hidden;
  $("search-all").hidden = mode !== "idle" || !resultsUnder;
}

async function loadLibrary() {
  const mine = ++searchCounter;              // answers can arrive out of order; only the newest counts
  const text = $("search").value;
  updateSearchButtons();
  if (libraryMode(text, showAll) === "idle") {      // just the bar, as at start-up
    libraryEntries = [];
    drawLibrary();
    return;
  }
  try {
    const res = await fetch("/api/library?q=" + encodeURIComponent(text) + kindParam(), { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const entries = (await res.json()).entries;
    if (mine !== searchCounter) return;
    libraryEntries = entries;
    drawLibrary();
  } catch (err) {
    showMessage("Cannot reach the program. Is it still running?");
  }
}

/* Back to just the search bar: no text, no list, no Look panel. */
function clearSearch(keepFocus) {
  clearTimeout(searchTimer);
  $("search").value = "";
  showAll = false;
  $("preview").hidden = true;
  previewFor = "";
  loadLibrary();
  if (keepFocus) $("search").focus();
}

function drawSelect(select, options, selected) {
  select.replaceChildren();
  for (const option of options) {
    const node = el("option", "", option.text);
    node.value = option.value;
    select.appendChild(node);
  }
  select.value = selected;
}

function render(next) {
  state = next;
  setTimeout(fitBoard, 0);
  const summary = liveSummary(state);

  const chooser = describeServiceSelect(state);
  drawSelect($("service-select"), chooser.options, chooser.selected);
  $("service-title").textContent = serviceSummary(state);
  drawItems(state.items);
  drawServiceTheme();
  drawThemesTab();
  applyLanding();
  if (libraryHadService !== !!state.service) drawLibrary();

  const liveItem = (state.items || []).find((item) => item.live);
  $("live-title").textContent = liveItem ? liveItem.title : "";
  $("position").textContent = summary.position;
  $("mode").textContent = summary.mode;
  drawLiveOutputs();
  $("live-text").textContent = summary.notes ? "Notes: " + summary.notes : (summary.base ? "" : "Nothing is live. Press Go on a service item, or Next.");
  $("btn-next").disabled = !summary.canNext;
  $("btn-previous").disabled = !summary.canPrevious;
  const timer = timerText(state.timer);
  $("timer-line").hidden = !timer.text;
  $("timer-text").textContent = timer.text;
  $("btn-timer").textContent = timer.button;
  $("btn-timer").disabled = !timer.enabled;
  drawClips();
  $("btn-blank").textContent = blankText(summary.blank, state.home, keyHint(keyChoices, "blank"));
  drawHome();
  if (typeof wizardStateChanged === "function") wizardStateChanged();
  $("build-note").textContent = state.build_label ? "This copy of ChurchLight is build " + state.build_label + "." : "";
  drawPdf();
  drawHook();
  if (typeof wizardMaybeOpen === "function") wizardMaybeOpen();
  drawCountdown();
  if (currentView === "broadcast") drawTimerStyle();
  setToggle($("btn-blank"), summary.blank);                     // a colored button says it is on; no banner
  setToggle($("btn-words"), summary.textHidden);
  setToggle($("btn-lower"), summary.lowerHidden);
  $("btn-lower").textContent = withHint(summary.lowerHidden ? "Show lower third" : "Hide lower third", keyHint(keyChoices, "hide_lower"));
  $("btn-words").textContent = withHint(summary.textHidden ? "Show words" : "Hide words", keyHint(keyChoices, "hide_text"));
  drawKeyHints();
  $("btn-clear-verse").hidden = !summary.verseShown;           // only while a verse is over a slide

  const cue = state.cue;
  $("tray-ready").hidden = !cue;
  if (cue) {
    $("tray-ready").textContent = "Ready: " + cue.title + ". Press Enter to show it live.";
  } else cuedKey = null;
  const verseOk = !!state.verse_over_ok;
  for (const id of ["tray-prev", "tray-next"]) { $(id).disabled = !verseOk; $(id).hidden = !verseOk; }
  drawJobs(state);
  drawNotice(state);
  if (LIBRARY_VIEWS.includes(currentView)) loadManage();
  if (state.translation !== shownDefaultBible) { shownDefaultBible = state.translation; drawBibleList(); }   // the Default marker follows the setting
  if (currentView === "deckedit") refreshDeckEditor(false);
  drawScreenSettings();
  drawTray();
}

/* ---------------- the Service tab's tools panel: Bible, Announcements, Timer ---------------- */
const RUN_PANES = ["bible", "announce"];
const RUN_PANE_KEY = "churchlight.runpane";

/* Which pane to show: the saved name if it is one we know, else the Bible tray. */
function runPaneName(raw) { return raw === "timer" ? "announce" : RUN_PANES.includes(raw) ? raw : "bible"; }   // "timer" was its own tab once

/* The tools panel's height (a percent of the window) and whether it is folded; and Plan or Run for the service list. */
const DOCK_KEY = "churchlight.dock";
const RUNMODE_KEY = "churchlight.runmode";
const DOCK_MIN = 12, DOCK_MAX = 60, DOCK_DEFAULT = 24;
function clampDock(vh) { const n = Number(vh); return Number.isFinite(n) ? Math.min(DOCK_MAX, Math.max(DOCK_MIN, Math.round(n))) : DOCK_DEFAULT; }
function parseDock(raw) {
  let data = null;
  try { data = JSON.parse(raw); } catch (err) { /* the defaults */ }
  if (!data || typeof data !== "object") return { vh: DOCK_DEFAULT, folded: false };
  return { vh: clampDock(data.vh === undefined ? DOCK_DEFAULT : data.vh), folded: data.folded === true };
}
function parseRunMode(raw) { return raw === "run" ? "run" : "plan"; }

let dock = { vh: DOCK_DEFAULT, folded: false };
function drawDock() {
  const board = document.querySelector(".board");
  board.style.setProperty("--run-bottom", dock.folded ? "3.2rem" : dock.vh + "vh");
  $("run-bottom").classList.toggle("folded", dock.folded);
  $("rb-collapse").setAttribute("aria-expanded", String(!dock.folded));
  $("rb-collapse").textContent = dock.folded ? "Open ▴" : "Fold ▾";
}
function setDock(changes) {
  dock = { ...dock, ...changes };
  drawDock();
  try { localStorage.setItem(DOCK_KEY, JSON.stringify(dock)); } catch (err) { /* only a convenience */ }
}
function openDock() { if (dock.folded) setDock({ folded: false }); }

let runMode = "plan";
function setRunMode(mode, remember) {
  runMode = parseRunMode(mode);
  document.querySelector(".col.service").classList.toggle("run-mode", runMode === "run");
  $("mode-plan").setAttribute("aria-pressed", String(runMode === "plan"));
  $("mode-run").setAttribute("aria-pressed", String(runMode === "run"));
  if (remember) { try { localStorage.setItem(RUNMODE_KEY, runMode); } catch (err) { /* only a convenience */ } }
}

function initDock() {
  try { dock = parseDock(localStorage.getItem(DOCK_KEY)); } catch (err) { /* the defaults */ }
  drawDock();
  $("rb-collapse").addEventListener("click", () => setDock({ folded: !dock.folded }));
  const handle = $("rb-resize");
  const fromPointer = (event) => clampDock(((window.innerHeight - event.clientY) / window.innerHeight) * 100);
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    setDock({ folded: false, vh: fromPointer(event) });
    const move = (e) => setDock({ vh: fromPointer(e) });
    const done = () => { handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", done); handle.removeEventListener("pointercancel", done); };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", done);
    handle.addEventListener("pointercancel", done);
  });
  handle.addEventListener("keydown", (event) => {
    const step = event.key === "ArrowUp" ? 2 : event.key === "ArrowDown" ? -2 : 0;
    if (!step) return;
    event.preventDefault();
    setDock({ folded: false, vh: clampDock(dock.vh + step) });
  });
  let mode = null;
  try { mode = localStorage.getItem(RUNMODE_KEY); } catch (err) { /* the default */ }
  setRunMode(mode, false);
  $("mode-plan").addEventListener("click", () => setRunMode("plan", true));
  $("mode-run").addEventListener("click", () => setRunMode("run", true));
}

function showRunPane(name, remember) {
  name = runPaneName(name);
  if (remember) openDock();
  for (const pane of RUN_PANES) {
    $("rb-" + pane).hidden = pane !== name;
    const tab = $("rb-tab-" + pane);
    tab.setAttribute("aria-selected", String(pane === name));
    tab.tabIndex = pane === name ? 0 : -1;
  }
  if (remember) { try { localStorage.setItem(RUN_PANE_KEY, name); } catch (err) { /* only a convenience */ } }
}

function initRunPanel() {
  let saved = null;
  try { saved = localStorage.getItem(RUN_PANE_KEY); } catch (err) { /* no storage */ }
  showRunPane(saved, false);
  for (const tab of document.querySelectorAll(".rb-tab")) {
    tab.addEventListener("click", () => showRunPane(tab.dataset.pane, true));
    tab.addEventListener("keydown", (event) => {            // arrow keys move between the tabs, as on any tab strip
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const next = RUN_PANES[(RUN_PANES.indexOf(tab.dataset.pane) + step + RUN_PANES.length) % RUN_PANES.length];
      showRunPane(next, true);
      $("rb-tab-" + next).focus();
    });
  }
}

/* ---------------- the verse tray ---------------- */
let pendingJumpFocus = false;       // after loading a passage, put the cursor in "Go to verse"
let pendingJumpTimer = null;

/* After Enter on the book-and-chapter line, move the cursor to "Go to verse". The page may already have drawn
   the new passage (the update can arrive before the reply to Enter), or not yet: so focus now if the tray is
   showing, and otherwise as soon as it is drawn (but never later than a couple of seconds from now). */
function focusVerseLine() {
  clearTimeout(pendingJumpTimer);
  if (state && state.tray && !$("tray-body").hidden) {
    pendingJumpFocus = false;
    $("tray-jump").focus();
    return;
  }
  pendingJumpFocus = true;
  pendingJumpTimer = setTimeout(() => { pendingJumpFocus = false; }, 2500);
}
let trayChecked = [];
let trayKey = "";
let trayAnchor = null;
const trayRows = new Map();          // verse number -> { row, box }

function drawTray() {
  const view = trayView(state.tray, trayChecked);
  $("tray-empty").hidden = !!view;
  $("tray-body").hidden = !view;
  $("tray-clear").disabled = !view;
  if (!view) { trayKey = ""; trayChecked = []; trayRows.clear(); $("tray-list").replaceChildren(); return; }
  $("tray-title").textContent = view.title + " — " + view.count + " verse" + (view.count === 1 ? "" : "s");
  if (view.key !== trayKey) {                        // a different passage: rebuild the list once
    trayKey = view.key;
    trayChecked = [];
    clearJump();
    trayAnchor = null;
    trayRows.clear();
    const list = $("tray-list");
    list.replaceChildren();
    const numbers = view.rows.map((r) => r.number);
    for (const item of view.rows) {
      const row = el("li", "row tray-row");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.setAttribute("aria-label", "Select verse " + item.number);
      box.addEventListener("click", (event) => {
        const wasOn = trayChecked.includes(item.number);
        trayChecked = event.shiftKey && trayAnchor !== null && !wasOn
          ? checkRun(trayChecked, numbers, trayAnchor, item.number) : toggleChecked(trayChecked, item.number);
        trayAnchor = item.number;
        paintTray();
      });
      const text = el("button", "verse-text");
      text.type = "button";
      text.title = "Show this verse";
      text.appendChild(el("span", "num lead", item.number + ")"));
      text.appendChild(document.createTextNode(" " + item.text));
      text.addEventListener("click", () => act("tray_show", { verses: [item.number] }));
      const add = el("button", "row-add", "+");
      add.type = "button";
      add.title = "Add verse " + item.number + " to the service (nothing changes on screen)";
      add.setAttribute("aria-label", "Add verse " + item.number + " to the service");
      add.addEventListener("click", () => addToService([item.number]));
      row.appendChild(box);
      row.appendChild(text);
      row.appendChild(add);
      list.appendChild(row);
      trayRows.set(item.number, { row: row, box: box });
    }
  }
  paintTray();
  if (pendingJumpFocus) { pendingJumpFocus = false; clearTimeout(pendingJumpTimer); $("tray-jump").focus(); }
}

/* Update ticks and the "on screen" highlight without rebuilding the list (so it keeps its scroll). */
function paintTray() {
  const view = trayView(state.tray, trayChecked);
  if (!view) return;
  for (const item of view.rows) {
    const entry = trayRows.get(item.number);
    if (!entry) continue;
    entry.row.classList.toggle("live", item.live);
    entry.box.checked = item.checked;
  }
  const over = !!(state && state.verse_over_ok);
  $("tray-show-checked").hidden = !over;                          // a verse can only go over a slideshow slide
  $("tray-show-checked").disabled = !trayChecked.length;
  $("tray-add-checked").disabled = !trayChecked.length;
  $("tray-uncheck").disabled = !trayChecked.length;
  $("tray-show-checked").textContent = trayChecked.length > 1 ? "Show over slideshow (" + trayChecked.length + ")" : "Show over slideshow";
  $("tray-add-checked").textContent = trayChecked.length > 1 ? "Add to service (" + trayChecked.length + ")" : "Add to service";
  $("tray-add-all").disabled = !view.count;
}

async function addToService(numbers) {
  const result = await act("tray_add", { verses: numbers });
  if (result) showMessage("");
  return result;
}

/* Enter in the tray's "Go to verse" box with nothing typed, once a chapter is loaded: the first Enter adds the whole passage to
   the service, the next shows its first verse (as if "1" had been typed in Go to verse). After that the verse buttons or
   the clicker's forward trigger carry on. A chapter that was loaded with Ctrl+Enter is already in the service, so its
   first Enter shows verse 1. */
let trayEnter = { key: null, added: false, shown: false, pendingAdded: false };
function trayEnterStep(entered, tray) {
  if (!tray || !tray.verses.length) return "none";
  if (!entered.added) return "add";
  if (!entered.shown) return "show";
  return "done";
}
/* One step at a time: a quick second Enter waits for the first to finish instead of repeating it. */
let trayEnterQueue = Promise.resolve();
function trayEnterEmpty() {
  trayEnterQueue = trayEnterQueue.then(trayEnterRun, trayEnterRun);
  return trayEnterQueue;
}
async function trayEnterRun() {
  const tray = state && state.tray;
  if (!tray) return;
  const key = tray.reference + "|" + tray.translation;
  if (trayEnter.key !== key) trayEnter = { key: key, added: trayEnter.pendingAdded, shown: false, pendingAdded: false };
  const step = trayEnterStep(trayEnter, tray);
  if (step === "add") {
    if (await addToService(tray.verses.map((v) => v[0]))) trayEnter.added = true;
  } else if (step === "show") {
    if (await act("tray_show", { verses: [tray.verses[0][0]] })) { trayEnter.shown = true; backToPassage(); }
  } else if (step === "done") {
    showMessage("Verse " + tray.verses[0][0] + " is already showing. Use the verse buttons or the clicker for the next verse.");
  }
}

/* Typing in "Go to verse": tick the verses and scroll the tray to the first one. The screen and the service are
   not touched; Enter shows what is ticked. */
function jumpInTray() {
  if (!state || !state.tray) return;
  const found = parseVerseJump($("tray-jump").value, state.tray.verses.map((v) => v[0]));
  trayChecked = found.picked;
  trayAnchor = found.picked.length ? found.picked[0] : null;
  $("tray-jump-note").textContent = found.missing.length ? "Not in this passage: " + found.missing.join(", ") : "";
  paintTray();
  const entry = found.picked.length ? trayRows.get(found.picked[0]) : null;
  if (entry) $("tray-list").scrollTop = Math.max(0, entry.row.offsetTop - 8);   // scrolls the list only, never the page
}

function clearJump() {
  $("tray-jump").value = "";
  $("tray-jump-note").textContent = "";
}

/* ---- the verse tray: ready for the next passage ----
   "Go to verse" only takes numbers, so a letter typed there means a new passage is being typed: the text moves to the
   passage box (pure test below). */
function jumpHandover(text) {
  return /\p{L}/u.test(String(text || ""));
}

/* Show the top of the Bible pane (the passage box and its buttons) without moving the cursor. */
function showTrayTop() {
  const dock = $("run-bottom");
  if (dock) dock.scrollTop = 0;
}

/* A verse is finished (on screen or in the service): the cursor goes back to the passage box with its text selected,
   "Go to verse" is emptied, and the pane shows its top. */
function backToPassage() {
  clearJump();
  showTrayTop();
  const box = $("tray-text");
  box.focus();
  box.select();
}

function handOverToPassage() {
  const text = $("tray-jump").value;
  if (!jumpHandover(text)) return false;
  const box = $("tray-text");
  box.value = text;
  clearJump();
  trayChecked = [];
  trayAnchor = null;
  paintTray();
  showTrayTop();
  box.focus();
  box.setSelectionRange(box.value.length, box.value.length);          // keep typing after what moved over
  return true;
}

/* Cue and take. The ticks the last cue came from are remembered, so Enter again on the same ticks takes it. */
let cuedKey = null;

async function cueTicked() {
  if (!trayChecked.length) return false;
  const key = trayChecked.join(",");
  // One verse means "from here to the end of the chapter"; a typed range or list stays exactly as typed.
  const done = await act("tray_cue", { verses: trayChecked.slice(), to_end: trayChecked.length === 1 });
  if (done) { cuedKey = key; showTrayTop(); }          // the cursor stays: Enter again takes it
  return !!done;
}

async function takeReady() {
  const done = await act("cue_take");
  if (done !== null) { cuedKey = null; trayChecked = []; trayAnchor = null; clearJump(); paintTray(); backToPassage(); }
  return done !== null;
}

/* ---------------- screens and previews ---------------- */
/* The operator's own preview shows the live slide even while the public screens are blanked (labelled as such). */
function peekPath(path) {
  return path + (String(path).includes("?") ? "&" : "?") + "peek=1";
}

/* ---------------- global shortcuts (Settings tab) ---------------- */
const CODE_KEYS = { Comma: ",", Period: ".", Slash: "/", Semicolon: ";", Quote: "'", BracketLeft: "[", BracketRight: "]",
  Minus: "-", Equal: "=", Backquote: "`", Backslash: "\\", Space: "Space", ArrowLeft: "Left", ArrowRight: "Right",
  ArrowUp: "Up", ArrowDown: "Down", PageUp: "PageUp", PageDown: "PageDown", Home: "Home", End: "End",
  Insert: "Insert", Delete: "Delete" };

/* A key press as a combo name such as "Ctrl+Alt+Right"; null while only modifiers are down or for a key
   that cannot be used. (The program checks the combo again.) */
function comboFromEvent(event) {
  const code = event.code || "";
  let key = null;
  if (/^Key[A-Z]$/.test(code)) key = code.slice(3);
  else if (/^Digit[0-9]$/.test(code)) key = code.slice(5);
  else if (/^F([1-9]|1[0-9]|2[0-4])$/.test(code)) key = code;
  else if (CODE_KEYS[code]) key = CODE_KEYS[code];
  if (!key) return null;
  const mods = [];
  if (event.ctrlKey) mods.push("Ctrl");
  if (event.altKey) mods.push("Alt");
  if (event.shiftKey) mods.push("Shift");
  return mods.concat([key]).join("+");
}

function globalStatusText(row, armed) {
  if (row.status === "none") return "no key";
  if (row.status === "ready") return armed ? "…" : "ready";
  if (row.status === "active") return "ON";
  return row.status;
}

let capturingCombo = null;
function drawGlobalKeys() {
  const gk = state && state.global_keys;
  if (!gk) return;
  $("globalkeys-banner").hidden = !gk.armed;
  const toggle = $("gk-toggle");
  toggle.textContent = gk.armed ? "Turn OFF global shortcuts" : "Turn on global shortcuts";
  toggle.classList.toggle("primary", !gk.armed);
  toggle.classList.toggle("danger", gk.armed);
  toggle.disabled = !gk.available || (!gk.armed && !gk.actions.some((a) => a.combo));
  toggle.title = !gk.available ? "Global shortcuts work on Windows only" : toggle.disabled ? "Choose at least one key first" : "";
  if (!gk.available) $("gk-note").textContent = "Global shortcuts are not available here (Windows only).";
  const body = $("gk-body");
  body.replaceChildren();
  for (const row of gk.actions) {
    const tr = el("tr");
    tr.appendChild(el("td", "", row.label));
    tr.appendChild(el("td", "keys-now", row.combo || "(none)"));
    tr.appendChild(el("td", "", globalStatusText(row, gk.armed)));
    const cell = el("td", "keys-act");
    const set = button(capturingCombo === row.id ? "Press keys…" : "Set", () => listenForCombo(row.id),
      capturingCombo === row.id ? "listening" : "", "Choose the global key for " + row.label);
    set.disabled = !gk.available;
    cell.appendChild(set);
    if (row.combo) cell.appendChild(button("Clear", () => act("global_keys_set", { action: row.id, combo: null }), "", "Remove the global key for " + row.label));
    tr.appendChild(cell);
    body.appendChild(tr);
  }
}

function stopListeningCombo(message) {
  capturingCombo = null;
  document.removeEventListener("keydown", onCapturedCombo, true);
  $("gk-note").textContent = message || "";
  drawGlobalKeys();
}

async function onCapturedCombo(event) {
  if (!capturingCombo) return;
  if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return;
  event.preventDefault();
  event.stopImmediatePropagation();
  if (event.key === "Escape" && !event.ctrlKey && !event.altKey && !event.shiftKey) { stopListeningCombo("Cancelled"); return; }
  if (event.metaKey) { $("gk-note").textContent = "The Windows key cannot be used. Try again or press Esc."; return; }
  const combo = comboFromEvent(event);
  if (!combo) { $("gk-note").textContent = "That key cannot be used. Try again or press Esc."; return; }
  const ok = await act("global_keys_set", { action: capturingCombo, combo: combo });
  if (!ok) { $("gk-note").textContent = "That key was refused (see the message above). Press another, or Esc to cancel."; return; }   // keep listening
  stopListeningCombo("Saved: " + combo);
}

function listenForCombo(id) {
  if (capturingCombo) stopListeningCombo("");
  if (capturingKey) stopListening("");
  capturingCombo = id;
  $("gk-note").textContent = "Press the keys you want, for example Ctrl+Alt+→ (Esc cancels).";
  document.addEventListener("keydown", onCapturedCombo, true);
  drawGlobalKeys();
}

/* ---------------- keyboard shortcuts (Settings tab) ---------------- */
const KEYS_KEY = "churchlight.keys";
let keyChoices = defaultKeys();
let capturingKey = null;
try { keyChoices = parseKeyChoices(localStorage.getItem(KEYS_KEY)); } catch (err) { /* no storage: the default keys */ }

function saveKeyChoices() {
  try {
    if (JSON.stringify(keyChoices) === JSON.stringify(defaultKeys())) localStorage.removeItem(KEYS_KEY);
    else localStorage.setItem(KEYS_KEY, JSON.stringify({ v: 1, keys: keyChoices }));
  } catch (err) { /* only a convenience */ }
}

/* Everything on the pages that names a key (button titles, placeholders, banners, the key list) follows the keys
   chosen on the Settings tab. Called on every redraw and whenever a key changes. */
function drawKeyHints() {
  const hint = (id) => keyHint(keyChoices, id);
  const tip = (key, text) => (key ? text + " ( " + key + " )" : text);
  drawFoldItems();
  $("btn-fold-items").title = tip(hint("fold_items"), "Close the drop-down lists of every service item, or open just the live one");
  $("btn-lower").title = tip(hint("hide_lower"), "Take the lower thirds off the air without blanking the projector");
  $("btn-clear-verse").title = tip(hint("hide_verse"), "Take only the verse off the screen. The slide under it stays");
  $("tray-text").placeholder = "John 3   or   John 3:1-21" + (hint("tray_box") ? "   ( " + hint("tray_box") + " )" : "") + "   Ctrl+Enter adds it to the service";
  const jumpKey = hint("tray_jump") ? "   ( " + hint("tray_jump") + " )" : "";
  $("tray-jump").placeholder = state && state.tray && state.tray.verses.length
    ? "Enter adds " + state.tray.title + " to the service, Enter again shows verse " + state.tray.verses[0][0] + "   (or type 16, 16-18, 16, 18)" + jumpKey
    : "16   or   16-18   or   16, 18" + jumpKey;
  $("btn-words").title = tip(hint("hide_text"), "Hide the words on the public screens and the confidence monitor (the background stays)");
}

function drawKeyTable() {
  drawKeyHints();
  if (state) { $("btn-blank").textContent = blankText(liveSummary(state).blank, state.home, keyHint(keyChoices, "blank")); }
  const body = $("keys-body");
  body.replaceChildren();
  for (const item of KEY_ACTIONS) {
    const row = el("tr");
    row.appendChild(el("td", "", item.label));
    row.appendChild(el("td", "keys-now", keyChoices[item.id].length ? keyChoices[item.id].map(keyLabel).join(" or ") : "(no key)"));
    const cell = el("td", "keys-act");
    const change = button(capturingKey === item.id ? "Press a key…" : "Change", () => listenForKey(item.id), capturingKey === item.id ? "listening" : "",
      "Change the key for " + item.label);
    cell.appendChild(change);
    row.appendChild(cell);
    body.appendChild(row);
  }
}

function stopListening(message) {
  capturingKey = null;
  document.removeEventListener("keydown", onCapturedKey, true);
  $("keys-note").textContent = message || "";
  drawKeyTable();
}

function onCapturedKey(event) {
  if (!capturingKey) return;
  if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return;      // wait for the real key
  event.preventDefault();
  event.stopImmediatePropagation();
  if (event.key === "Escape") { stopListening("Cancelled"); return; }
  if (event.ctrlKey || event.metaKey || event.altKey) { $("keys-note").textContent = "Use a single key, without Ctrl, Alt or the Windows key. Try again or press Esc."; return; }
  const result = assignKey(keyChoices, capturingKey, event.key);
  if (result.error) { $("keys-note").textContent = result.error.replace(/\.$/, "") + ". Try again or press Esc."; return; }
  keyChoices = result.keys;
  saveKeyChoices();
  stopListening(result.moved ? keyLabel(normalizeKey(event.key)) + " now belongs here; it was “" + result.moved + "” before." : "Saved");
}

function listenForKey(id) {
  if (capturingKey) stopListening("");
  if (capturingCombo) stopListeningCombo("");
  capturingKey = id;
  $("keys-note").textContent = "Press the key you want (Esc cancels).";
  document.addEventListener("keydown", onCapturedKey, true);
  drawKeyTable();
}

/* The screen shape choices on the Settings tab (projector and confidence monitor only). */
let shownShapes = "";
function drawShapes() {
  if (!state) return;
  const rows = state.outputs.filter((o) => hasScreenShape(o.layout));
  const key = JSON.stringify(rows.map((o) => [o.name, o.shape]));
  const list = $("shape-list");
  if (key === shownShapes || list.contains(document.activeElement)) return;      // never close a dropdown being used
  shownShapes = key;
  list.replaceChildren();
  for (const output of rows) {
    const row = el("li", "row");
    row.appendChild(el("span", "grow", output.name));
    const select = el("select", "shape-pick");
    select.setAttribute("aria-label", "Screen shape of " + output.name);
    for (const option of SCREEN_SHAPES) select.appendChild(el("option", "", option));
    select.value = SCREEN_SHAPES.includes(output.shape) ? output.shape : SCREEN_SHAPES[0];
    select.addEventListener("change", () => act("screen_shape_set", { name: output.name, shape: select.value }));
    row.appendChild(select);
    list.appendChild(row);
  }
}

/* The screen addresses on the Settings tab: this computer's, and other computers' when the program is open to them. */
function addressRow(label, url) {
  const row = el("li", "row hook-row");
  row.appendChild(el("span", "title", label));
  const field = document.createElement("input");
  field.type = "text";
  field.readOnly = true;
  field.value = url;
  field.setAttribute("aria-label", "Address of " + label);
  field.addEventListener("focus", () => field.select());
  row.appendChild(field);
  const copy = el("button", "", "Copy");
  copy.type = "button";
  copy.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(field.value); copy.textContent = "Copied"; }
    catch (err) { field.focus(); field.select(); copy.textContent = "Press Ctrl+C"; }
    setTimeout(() => { copy.textContent = "Copy"; }, 1500);
  });
  row.appendChild(copy);
  return row;
}
let shownAddresses = "";
function drawAddresses() {
  if (!state) return;
  const server = state.server || { port: null, lan: [] };
  const key = JSON.stringify([state.outputs.map((o) => [o.name, o.path]), server, location.origin]);
  if (key === shownAddresses) return;
  shownAddresses = key;
  const list = $("address-list");
  list.replaceChildren();
  for (const output of state.outputs) list.appendChild(addressRow(output.name, location.origin + output.path));
  const lan = $("address-lan-list");
  lan.replaceChildren();
  for (const ip of server.lan) {
    const host = ip.includes(":") ? "[" + ip + "]" : ip;
    for (const output of state.outputs) {
      lan.appendChild(addressRow(output.name, "http://" + host + ":" + server.port + output.path));
    }
  }
  $("address-lan").hidden = server.lan.length === 0;
}

/* The Home screen settings (Settings tab). */
let shownHome = "";
function drawHome() {
  if (state) $("conf-hide-words").checked = state.confidence_hides_words !== false;
  const home = (state && state.home) || { image: null, fit: "fit", on: true, idle: true };
  const key = JSON.stringify(home);
  if (key === shownHome) return;
  shownHome = key;
  const thumb = $("home-thumb");
  thumb.hidden = !home.image;
  if (home.image) thumb.src = mediaUrl(home.image);
  $("home-remove").hidden = !home.image;
  $("home-choose").textContent = home.image ? "Change picture…" : "Choose picture…";
  $("home-on").checked = home.on;
  $("home-idle").checked = home.idle !== false;
  $("home-on").disabled = $("home-idle").disabled = $("home-fit").disabled = !home.image;
  $("home-fit").value = home.fit;
  const when = [home.on ? "the screens are blanked" : "", home.idle !== false ? "nothing is live" : ""].filter(Boolean);
  $("home-note").textContent = home.image
    ? (when.length ? "The projector shows this when " + when.join(" or ") + "." : "Switched off: the projector never shows it.")
    : "No picture chosen: the projector shows nothing at those times.";
}

/* The PDF import section (Settings tab): can this computer turn a PDF into pages? */
function pdfLine(pdf) {
  if (!pdf) return "Checking…";
  return (pdf.ready ? "✓ " : "⚠ ") + pdf.message;
}

function drawPdf() {
  const pdf = state && state.pdf;
  const line = $("pdf-status");
  line.textContent = pdfLine(pdf);
  line.classList.toggle("pdf-missing", !!pdf && !pdf.ready);
}

/* The Trigger URLs section (Settings tab): one copyable address per safe button. */
let shownHook = "";
function hookUrl(name) {
  return location.origin + "/hook/" + state.hook.token + "/" + name;
}

function drawHook() {
  const hook = state && state.hook;
  if (!hook) return;
  const key = JSON.stringify(hook) + JSON.stringify(state.clicker || null) + location.origin;
  if (key === shownHook) return;
  shownHook = key;
  drawClicker();
  $("hook-on").checked = hook.on;
  $("hook-note").textContent = hook.on ? "" : "Switched off: none of these addresses do anything.";
  const list = $("hook-list");
  list.replaceChildren();
  list.hidden = !hook.on;
  for (const action of hook.actions) {
    const row = el("li", "row hook-row");
    row.appendChild(el("span", "title", action.label));
    const field = document.createElement("input");
    field.type = "text";
    field.readOnly = true;
    field.value = hookUrl(action.name);
    field.setAttribute("aria-label", "Address for: " + action.label);
    field.addEventListener("focus", () => field.select());
    row.appendChild(field);
    const copy = el("button", "", "Copy");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(field.value); copy.textContent = "Copied"; }
      catch (err) { field.focus(); field.select(); copy.textContent = "Press Ctrl+C"; }
      setTimeout(() => { copy.textContent = "Copy"; }, 1500);
    });
    row.appendChild(copy);
    list.appendChild(row);
  }
}

let hookNewArmed = null;
function hookNewClick() {
  const button = $("hook-new");
  if (hookNewArmed === null) {           // a second click confirms: the old addresses stop working
    button.textContent = "Click again: your old remote button links and clicker scripts will stop working";
    hookNewArmed = setTimeout(() => { hookNewArmed = null; button.textContent = "Make a new private code"; }, 5000);
    return;
  }
  clearTimeout(hookNewArmed);
  hookNewArmed = null;
  button.textContent = "Make a new private code";
  act("hook_new_token");
}

async function chooseHome(file) {
  if (!file) return;
  $("home-note").textContent = "Uploading…";
  const made = await act("import_batch");
  if (!made || !(await uploadOne(made.batch, "background", file))) { shownHome = ""; drawHome(); return; }
  const stored = await act("background_import", { batch: made.batch });
  if (stored) await act("home_set", { image: stored.path, on: true });
  shownHome = "";
  drawHome();
}

/* Settings: the title and CCLI slide every song gets. */
function drawNotice(current) {
  const notice = current && current.notice;
  if (!notice || !$("notice-position")) return;
  if (!$("notice-box").contains(document.activeElement)) {
    $("notice-position").value = notice.position;
    $("notice-writers").value = notice.writers;
    $("notice-permission").value = notice.permission;
    $("notice-songnum").checked = !!notice.song_number;
  }
  $("notice-note").textContent = notice.church_ccli ? ""
    : "Your church's CCLI license number is not set, so it is left off these slides. Add it in the Library, under Songs.";
}

function wireNotice() {
  $("notice-save").addEventListener("click", async () => {
    const done = await act("notice_set", { position: $("notice-position").value, writers: $("notice-writers").value,
      permission: $("notice-permission").value, song_number: $("notice-songnum").checked });
    if (done !== null) showNotice("Saved the title and CCLI slide settings.");
  });
}

function drawScreenSettings() {
  if (!state) return;
  drawGlobalKeys();
  drawShapes();
  drawAddresses();
  syncLivePreview();
}

/* ---- the live panel's own previews: two slots, each with its own output picked from a dropdown ---- */
const LIVE_OUTPUT_KEY = "churchlight.liveoutput";
const NO_PREVIEW = "none";
/* Slot 1 starts on the first output, slot 2 on the second (a church with one output sees one preview).
   choice: "" = that default; NO_PREVIEW = nothing in this slot. */
const LIVE_SLOTS = [
  { select: "live-output", holder: "live-frame", key: LIVE_OUTPUT_KEY, fallback: 0, choice: "", shown: null },
  { select: "live-output-2", holder: "live-frame-2", key: LIVE_OUTPUT_KEY + "2", fallback: 1, choice: "", shown: null },
];
for (const slot of LIVE_SLOTS) {
  try { slot.choice = localStorage.getItem(slot.key) || ""; } catch (err) { /* the default */ }
}

/* The output a live slot shows: the stored choice if it still exists, else the slot's default (by position). */
function chosenLiveOutput(outputs, choice, fallback) {
  if (choice === NO_PREVIEW) return null;
  const list = outputs || [];
  return list.find((o) => o.name === choice) || list[fallback || 0] || null;
}

function drawLiveOutputs() {
  for (const slot of LIVE_SLOTS) {
    const select = $(slot.select);
    if (!state || document.activeElement === select) continue;
    const chosen = chosenLiveOutput(state.outputs, slot.choice, slot.fallback);
    const options = state.outputs.map((o) => ({ value: o.name, text: o.name })).concat([{ value: NO_PREVIEW, text: "None" }]);
    const key = JSON.stringify(options) + "|" + (chosen ? chosen.name : NO_PREVIEW);
    if (select.dataset.key === key) continue;
    select.dataset.key = key;
    select.replaceChildren();
    for (const option of options) {
      const node = el("option", "", option.text);
      node.value = option.value;
      select.appendChild(node);
    }
    select.value = chosen ? chosen.name : NO_PREVIEW;
  }
}

function syncLivePreview() {
  for (const slot of LIVE_SLOTS) {
    const holder = $(slot.holder);
    const chosen = document.hidden || !state || currentView !== "run" ? null : chosenLiveOutput(state.outputs, slot.choice, slot.fallback);
    if (!chosen || (slot.shown && slot.shown.name !== chosen.name)) {
      holder.replaceChildren();
      slot.shown = null;
    }
    holder.hidden = !chosen;
    if (!chosen) continue;
    if (!slot.shown) {
      const view = el("div", "frame-view");
      const frame = document.createElement("iframe");
      frame.src = peekPath(chosen.path);
      frame.title = "Live preview of " + chosen.name;
      frame.tabIndex = -1;
      view.appendChild(frame);
      holder.appendChild(view);
      holder.appendChild(el("figcaption", "muted", chosen.name));
      slot.shown = { name: chosen.name, view: view, frame: frame };
    }
    applyShape(slot.shown, chosen.shape);
  }
  rescalePreviews();
}

function rescalePreviews() {
  for (const slot of LIVE_SLOTS) {
    if (!slot.shown) continue;
    const scale = previewScale(slot.shown.view.clientWidth);
    if (scale) slot.shown.frame.style.transform = "scale(" + scale + ")";
  }
}

/* ---------------- suggestions under a search box ---------------- */
const SUGGEST_DELAY_MS = 150;

/* Which row is highlighted after Up or Down (-1 = none). Down from the last row wraps to the first. */
function stepSuggestion(current, count, key) {
  if (!count) return -1;
  if (key === "ArrowDown") return current < 0 || current >= count - 1 ? 0 : current + 1;
  if (key === "ArrowUp") return current <= 0 ? count - 1 : current - 1;
  return current;
}

/* A dropdown of names (songs and decks only, nothing else) under a search box. An empty box shows what
   was used lately. `onPick(entry)` runs when a row is chosen with the mouse or with Enter after the arrows;
   Enter with no row highlighted keeps doing whatever it did before. */
function attachSuggest(input, onPick, enabled) {
  const box = el("span", "suggest-box");
  input.replaceWith(box);
  box.appendChild(input);
  const list = el("ul", "suggest");
  list.id = input.id + "-suggest";
  list.setAttribute("role", "listbox");
  list.hidden = true;
  box.appendChild(list);
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", list.id);
  input.setAttribute("aria-expanded", "false");
  let entries = [], active = -1, timer = null, counter = 0, recent = false;

  const close = () => {
    list.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  };
  const draw = () => {
    list.replaceChildren();
    if (!entries.length) { close(); return; }
    if (recent) { const head = el("li", "suggest-head", "Used recently"); head.setAttribute("role", "presentation"); list.appendChild(head); }
    entries.forEach((entry, i) => {
      const row = el("li", "suggest-item" + (i === active ? " active" : ""), entry.title);
      if (entry.kind === "media") row.appendChild(el("span", "suggest-type", " " + entryBadge(entry)));
      row.id = list.id + "-" + i;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(i === active));
      row.addEventListener("mousedown", (event) => { event.preventDefault(); pick(i); });   // keeps the box focused
      list.appendChild(row);
    });
    const rect = input.getBoundingClientRect();          // fixed, so a panel's edges never clip the list
    list.style.left = rect.left + "px";
    list.style.top = rect.bottom + 2 + "px";
    list.style.width = rect.width + "px";
    list.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (active >= 0) {
      input.setAttribute("aria-activedescendant", list.id + "-" + active);
      const row = document.getElementById(list.id + "-" + active);
      if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  };
  const ask = async () => {
    const mine = ++counter;                               // answers can arrive out of order; only the newest counts
    if (enabled && !enabled()) { close(); return; }
    const text = input.value.trim();
    try {
      const res = await fetch("/api/suggest?q=" + encodeURIComponent(text) + kindParam(), { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (mine !== counter || document.activeElement !== input) return;
      entries = data.entries || [];
      recent = text === "";
      active = -1;
      draw();
    } catch (err) { close(); }
  };
  const pick = (index) => {
    const entry = entries[index];
    if (!entry) return;
    counter += 1;
    clearTimeout(timer);
    close();
    onPick(entry);
  };
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(ask, SUGGEST_DELAY_MS); });
  input.addEventListener("focus", () => { if (input.value.trim() === "") ask(); });
  input.addEventListener("blur", () => { counter += 1; clearTimeout(timer); close(); });
  input.addEventListener("keydown", (event) => {
    if (list.hidden) {
      if (event.key === "ArrowDown" && !event.altKey) ask();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      active = stepSuggestion(active, entries.length, event.key);
      draw();
    } else if (event.key === "Enter" && active >= 0) {
      event.preventDefault();
      event.stopImmediatePropagation();
      pick(active);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopImmediatePropagation();           // the first Esc only closes the list
      close();
    } else if (event.key === "Tab") close();
  }, true);
  window.addEventListener("resize", close);
  window.addEventListener("scroll", close, true);
  return { close: close };
}

/* Choosing a name from a dropdown puts that song or deck at the end of the service. */
async function addSuggested(entry) {
  const done = await act("add", { kind: entry.kind, id: entry.id });
  if (done) showNotice("Added " + entry.title + " to the service");
}

/* ---------------- views ---------------- */
let currentView = "run";
/* With no service open, the Run tab is only a start page: open or begin a service, or go to the Library. */
/* Autofill hint: while a reference is typed, show what it will be read as ("lev 3" -> "Leviticus 3"). */
function watchReference(inputId, hintId) {
  let timer = null;
  let latest = 0;
  const input = $(inputId);
  const hint = $(hintId);
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const text = input.value.trim();
    if (!text) { hint.textContent = ""; return; }
    timer = setTimeout(async () => {
      const mine = ++latest;
      const result = await call("reference_hint", { text: text });
      if (mine !== latest || input.value.trim() !== text) return;
      hint.textContent = result.ok && result.data.hint ? "→ " + result.data.hint : "";
    }, 120);
  });
  input.addEventListener("blur", () => { clearTimeout(timer); });
  input.form && input.form.addEventListener("submit", () => { hint.textContent = ""; });
}

/* ---------- "Ready to start?" (the empty Service tab only): a read-only recheck before a service ---------- */
let readyShown = false;
let readyRun = 0;
function goSettings(fold) {
  showView("shortcuts");
  const box = document.querySelector('details[data-settings-fold="' + fold + '"]');
  if (!box) return;
  const parent = box.parentElement && box.parentElement.closest("details");
  if (parent) parent.open = true;
  box.open = true;
  box.scrollIntoView({ block: "start" });
}
function readyFix(check) {
  const go = {
    windows: ["Open screens", async () => {
      readyBusyNow();
      const data = await act("screens_open", { operator_at: pwOperatorAt() });
      if (data && data.problems && data.problems.length) showMessage(data.problems.join(" "));
      refreshReady();
    }],
    fixscreens: ["Open Settings", () => goSettings("windows")],
    screens: ["Open Settings", () => goSettings("screens")],
    bibles: ["Add a Bible", () => showView("bibles")],
    clicker: ["Open Settings", () => goSettings("clicker")],
    obs: ["Open setup guide", () => wizardShow(WIZARD_STEPS.indexOf("obs"))],
    layout: ["This layout is correct", async () => { if (await act("layout_accept") !== null) { showNotice("Saved. I will compare against this layout next time."); refreshReady(); } }],
    backup: ["Back up now", async () => { if (await act("backup_now") !== null) { showNotice("Backed up."); refreshReady(); } }],
  }[check.fix];
  if (!go) return null;
  const button = el("button", "", go[0]);
  button.type = "button";
  button.addEventListener("click", go[1]);
  return button;
}
function drawReadyRow(check) {
  const row = el("li", "ready-row " + (check.ok ? "ok" : "todo"));
  row.dataset.check = check.id;
  row.dataset.label = check.label.replace(/\.$/, "");
  row.appendChild(el("span", "ready-mark", check.ok ? "✓" : "!"));
  const text = el("span", "ready-text");
  text.appendChild(el("strong", "", check.label));
  text.appendChild(document.createTextNode(" " + check.detail));
  row.appendChild(text);
  if (!check.ok) { const fix = readyFix(check); if (fix) row.appendChild(fix); }
  return row;
}
/* The bar's one line, so the answer shows without opening the panel: all good, what needs checking, or still checking. */
function readyHeadline(checking, busy) {
  const box = $("ready");
  const todo = Array.from($("ready-list").querySelectorAll("li.todo:not(.checking)")).map((row) => row.dataset.label);
  let text, kind;
  if (busy) { text = "Checking…"; kind = "checking"; }
  else if (todo.length) { text = todo.length + (todo.length === 1 ? " thing" : " things") + " to check: " + todo.join(", ") + ". Open to fix."; kind = "todo"; }
  else if (checking) { text = "Checking…"; kind = "checking"; }
  else { text = "✓ Everything is good to go"; kind = "good"; }
  $("ready-headline").textContent = text;
  box.classList.remove("ready-good", "ready-todo", "ready-checking");
  box.classList.add("ready-" + kind);
}
/* While a re-check runs, the rows already on screen turn to "Checking…" so an old red row never looks current. */
function readyBusyNow() {
  for (const row of $("ready-list").querySelectorAll("li.ready-row")) {
    row.classList.add("checking");
    const mark = row.querySelector(".ready-mark");
    if (mark) mark.textContent = "…";
  }
  readyHeadline(true, true);
}
let readyTimer = null;
let readyPolls = 0;
/* A red row other than OBS (which needs a button press) is looked at again every 5 seconds while the Service tab shows, up to two minutes. */
function readyRecheckLater(mine) {
  clearTimeout(readyTimer);
  readyTimer = null;
  const needs = Array.from($("ready-list").querySelectorAll("li.todo")).some((row) => row.dataset.check !== "obs");
  if (!needs || readyPolls >= 24 || mine !== readyRun) return;
  readyTimer = setTimeout(() => {
    readyTimer = null;
    if ($("landing").hidden || document.hidden) { readyPolls = 0; return; }
    readyPolls += 1;
    refreshReady(true);
  }, 5000);
}
async function refreshReady(quiet) {
  const mine = ++readyRun;
  clearTimeout(readyTimer);
  readyTimer = null;
  if (!quiet) readyPolls = 0;
  const list = $("ready-list");
  if (!quiet) readyBusyNow(); else readyHeadline(true);
  const result = await call("ready_checks");
  if (mine !== readyRun) return;
  list.replaceChildren();
  if (!result.ok) { list.appendChild(drawReadyRow({ id: "x", label: "The check could not run", ok: false, detail: result.error })); readyHeadline(false); return; }
  const checks = result.data.checks.slice();
  checks.push({ id: "page", label: "This page is connected to the program", ok: !connectionDown, detail: connectionDown ? "Reload the page." : "" });
  for (const check of checks) list.appendChild(drawReadyRow(check));
  if (result.data.no_livestream) { readyHeadline(false); readyRecheckLater(mine); return; }       // chose "I don't need the OBS connection": OBS is not asked about
  readyHeadline(true);
  const obs = state && state.obs;
  const pending = drawReadyRow({ id: "obs", label: "OBS (or other broadcast software)", ok: true, detail: "Checking…" });
  list.appendChild(pending);
  const link = await call("obs_connect", { host: obs ? obs.host : "127.0.0.1", port: obs ? obs.port : 4455 });
  if (mine !== readyRun) return;
  const done = link.ok ? { id: "obs", label: "OBS is connected", ok: true, detail: "" } :
    { id: "obs", label: "OBS is not connected.", ok: false, detail: (link.error ? link.error + " " : "") + "Start OBS and check its connection in the setup guide. If you do not use OBS, tick \"I don't need the OBS connection\" there.", fix: "obs" };
  pending.replaceWith(drawReadyRow(done));
  readyHeadline(false);
  readyRecheckLater(mine);
}

function applyLanding() {
  const landing = currentView === "run" && !!state && !state.service;
  $("landing").hidden = !landing;
  $("view-run").hidden = currentView !== "run" || landing;
  document.querySelector(".service-bar").hidden = landing;
  if (!landing) { readyShown = false; return; }
  if (!readyShown) { readyShown = true; refreshReady(); }
  const list = $("landing-list");
  list.textContent = "";
  const services = state.services || [];
  if (!services.length) list.appendChild(el("li", "muted", "No earlier services yet. Type a name above and press Start new service to make your first one."));
  for (const s of services.slice(0, 12)) {
    const row = el("li", "row");
    row.appendChild(el("span", "title", s.title + (s.date ? " — " + s.date : "")));
    const open = el("button", "", "Open");
    open.type = "button";
    open.addEventListener("click", () => act("open_service", { id: s.id }));
    row.appendChild(open);
    list.appendChild(row);
  }
}

/* ---------------- appearance ---------------- */
const LOOK_CHOICES = {
  theme: [["auto", "Auto"], ["light", "Light"], ["dark", "Dark"]],
  size: [["small", "Small"], ["medium", "Medium"], ["large", "Large"]],
};

function initLook() {
  const look = window.ChurchLook;
  const panel = $("look-panel");
  const toggle = $("btn-look");
  if (!look) { toggle.hidden = true; return; }
  const group = (title, key) => {
    panel.appendChild(el("h3", "", title));
    const row = el("div", "seg");
    for (const [value, label] of LOOK_CHOICES[key]) {
      const b = button(label, () => { look.set({ [key]: value }); draw(); });
      b.dataset.value = value;
      row.appendChild(b);
    }
    row.dataset.key = key;
    panel.appendChild(row);
  };
  group("Colors", "theme");
  group("Text size", "size");
  panel.appendChild(el("h3", "", "Accent color"));
  const swatches = el("div", "swatches");
  for (const name of look.ACCENTS) {
    const b = button("", () => { look.set({ accent: name }); draw(); }, "swatch " + name, name + " accent");
    b.dataset.value = name;
    swatches.appendChild(b);
  }
  panel.appendChild(swatches);
  const own = el("label", "look-own");
  own.appendChild(el("span", "", "Or choose your own"));
  const picker = document.createElement("input");
  picker.type = "color";
  picker.id = "look-accent-custom";
  picker.setAttribute("aria-label", "Your own accent color");
  picker.value = "#4a9cff";
  picker.addEventListener("input", () => { look.set({ accent: picker.value }); draw(); });
  own.appendChild(picker);
  panel.appendChild(own);
  const ownNote = el("p", "muted help look-own-note", "");
  ownNote.id = "look-accent-note";
  ownNote.hidden = true;
  panel.appendChild(ownNote);
  const label = el("label", "look-check");
  const box = document.createElement("input");
  box.type = "checkbox";
  box.id = "look-contrast";
  box.addEventListener("change", () => look.set({ contrast: box.checked }));
  label.append(box, document.createTextNode("High contrast"));
  panel.appendChild(el("h3", "", "Contrast"));
  panel.appendChild(label);
  panel.appendChild(el("p", "muted help", "Kept in this browser only. The screens the congregation sees are not affected."));

  function draw() {
    const now = look.get();
    for (const row of panel.querySelectorAll(".seg")) {
      for (const b of row.children) b.setAttribute("aria-pressed", String(now[row.dataset.key] === b.dataset.value));
    }
    for (const b of swatches.children) b.setAttribute("aria-pressed", String(now.accent === b.dataset.value));
    const custom = look.HEX.test(now.accent);
    own.classList.toggle("on", custom);
    if (custom) picker.value = now.accent;
    ownNote.hidden = !custom;
    ownNote.textContent = custom ? (look.fitted && look.fitted.adjusted ? "Adjusted slightly so words stay readable on this light or dark look." : "Your colour is readable as it is.") : "";
    box.checked = now.contrast;
  }
  const open = (on) => { panel.hidden = !on; toggle.setAttribute("aria-expanded", String(on)); if (on) draw(); };
  toggle.addEventListener("click", () => open(panel.hidden));
  document.addEventListener("click", (event) => { if (!panel.hidden && !event.target.closest(".look")) open(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !panel.hidden) open(false); });
  draw();
}

const VIEWS = ["run", "themes", "songs", "decks", "media", "bibles", "broadcast", "shortcuts", "deckedit"];
const LIBRARY_VIEWS = ["songs", "decks", "media", "bibles"];

/* Settings and Alerts Settings always start folded each time they are entered; nothing is remembered. */
function foldOnEntry(name) {
  if (name === "shortcuts") for (const section of document.querySelectorAll("details[data-settings-fold]")) section.open = false;
  if (name === "broadcast") for (const id of ["ts-proj", "ts-speak"]) $(id).open = false;
}

/* "Bringing your slides into ChurchLight": a guide with one tab per slide program. */
let slidesGuideReturn = null;
function slidesGuideTab(name) {
  for (const tab of document.querySelectorAll("#slides-guide [data-sg]")) tab.setAttribute("aria-selected", String(tab.dataset.sg === name));
  for (const panel of document.querySelectorAll("#slides-guide [data-sg-panel]")) panel.hidden = panel.dataset.sgPanel !== name;
}
function openSlidesGuide() {
  slidesGuideReturn = document.activeElement;
  slidesGuideTab("powerpoint");
  $("slides-guide").hidden = false;
  $("sg-title").focus();
}
function closeSlidesGuide() {
  $("slides-guide").hidden = true;
  if (slidesGuideReturn && slidesGuideReturn.focus && document.contains(slidesGuideReturn)) slidesGuideReturn.focus();
  slidesGuideReturn = null;
}
function wireSlidesGuide() {
  for (const tab of document.querySelectorAll("#slides-guide [data-sg]")) tab.addEventListener("click", () => slidesGuideTab(tab.dataset.sg));
  $("sg-close").addEventListener("click", closeSlidesGuide);
  $("sg-open").addEventListener("click", openSlidesGuide);
  $("slides-guide").addEventListener("keydown", (event) => { if (event.key === "Escape") { event.stopPropagation(); closeSlidesGuide(); } });
  $("slides-guide").addEventListener("click", (event) => { if (event.target === $("slides-guide")) closeSlidesGuide(); });
}

function showView(name) {
  if (!VIEWS.includes(name)) name = "run";
  const was = currentView;
  currentView = name;
  if (name !== was) foldOnEntry(name);
  applyLanding();
  fitBoard();
  for (const view of VIEWS) {
    if (view !== "run") $("view-" + view).hidden = name !== view;
    const tab = $("tab-" + view);
    if (!tab) continue;
    tab.classList.toggle("active", view === name);
    tab.setAttribute("aria-pressed", String(view === name));
  }
  if (LIBRARY_VIEWS.includes(name)) loadManage();
  if (name === "themes") openThemesTab();
  syncLivePreview();
  if (name === "broadcast") openTimerStyle();
  if (name === "deckedit") { $("tab-decks").classList.add("active"); $("tab-decks").setAttribute("aria-pressed", "true"); }
}

/* ---------------- the library page ---------------- */
let manage = { songs: [], decks: [], translations: [] };
let manageText = "";
let manageCounter = 0;
let renamingDeck = null;
let lastJobs = "";

async function loadManage() {
  const mine = ++manageCounter;
  try {
    const res = await fetch("/api/manage", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const text = await res.text();
    if (mine !== manageCounter || text === manageText) return;      // newest answer only; skip if unchanged
    manageText = text;
    manage = JSON.parse(text);
    drawManage();
  } catch (err) {
    showMessage("Cannot reach the program. Is it still running?");
  }
}

/* The toolbar's shut-down button: press, then "Sure?", then everything is closed and this page says so. */
function wireShutdown() {
  confirmClick($("shutdown-btn"), async () => {
    const result = await call("shutdown", {});
    if (!result.ok) { showMessage(result.error); return; }
    shutDown = true;
    const page = el("main", "goodbye");
    page.setAttribute("role", "status");
    page.appendChild(el("h1", "", "ChurchLight has shut down"));
    page.appendChild(el("p", "", "You can close this window. To use it again, start ChurchLight from its icon or start_churchlight."));
    document.body.replaceChildren(page);
    try { window.close(); } catch (err) { /* only works for windows the program opened itself */ }
  }, "Shut down ChurchLight? The screens close and any livestream scene using them goes dark.", "Yes, shut down");
}

/* Press, then press again. With a `message` (text, or a function that gives the text), the first press says in plain words
   what will be lost and the button reads "Yes, delete"; without one it reads "Sure?". */
function confirmClick(button, action, message, yes) {
  const original = button.textContent;
  let timer = null;
  let said = "";
  const hideSaid = () => { if (said && !messageSticky && $("message-text").textContent === said) hideMessageBox(); said = ""; };
  // Pressing anything else while it is armed puts it back at once (it does not wait for the timer).
  const elsewhere = (event) => { if (!button.contains(event.target)) { clearTimeout(timer); reset(); } };
  const reset = () => {
    timer = null; hideSaid(); button.textContent = original; button.classList.remove("confirm"); button.style.minWidth = "";
    document.removeEventListener("pointerdown", elsewhere, true);
  };
  button.addEventListener("click", () => {
    if (timer === null) {
      const text = typeof message === "function" ? message() : message;
      if (text === null) { action(); return; }                                      // a message function may say "nothing to confirm": just do it
      document.addEventListener("pointerdown", elsewhere, true);
      button.style.minWidth = button.getBoundingClientRect().width + "px";        // the second wording keeps the button's size, so nothing around it moves
      button.textContent = text ? ((typeof yes === "function" ? yes() : yes) || "Yes, delete") : "Sure?";
      button.classList.add("confirm");
      if (text) { said = text; showNotice(text, null, true); }      // the question must be seen, even over an older problem
      timer = setTimeout(reset, text ? 8000 : 4000);
      return;
    }
    clearTimeout(timer);
    said = "";
    reset();
    action();
  });
}

function button(label, handler, cls, aria) {
  const node = el("button", cls || "", label);
  node.type = "button";
  if (aria) node.setAttribute("aria-label", aria);
  if (handler) node.addEventListener("click", handler);
  return node;
}

function titleBlock(badge, title, detail) {
  const box = el("span", "title");
  if (badge) box.appendChild(el("span", "badge", badge));
  box.appendChild(document.createTextNode(title));
  if (detail) box.appendChild(el("span", "detail", detail));
  return box;
}

const LIBRARY_FOLD_KEY = "churchlight.libraryfolds";
let libraryFolds = ["bibles"];
try { libraryFolds = parseLibraryFolds(localStorage.getItem(LIBRARY_FOLD_KEY)); } catch (err) { /* default */ }

function initLibraryFolds() {
  for (const section of document.querySelectorAll(".lib-view [data-fold]")) {
    const name = section.dataset.fold;
    const title = section.querySelector(":scope > h2");
    const head = el("div", "panel-head foldable");
    section.insertBefore(head, title);
    head.appendChild(title);
    const summary = el("span", "panel-summary muted");
    const fold = el("button", "fold", "▾");
    fold.type = "button";
    fold.setAttribute("aria-label", "Fold or open " + title.textContent);
    const toggle = () => {
      libraryFolds = libraryFolds.includes(name) ? libraryFolds.filter((n) => n !== name) : libraryFolds.concat(name);
      try { localStorage.setItem(LIBRARY_FOLD_KEY, JSON.stringify(libraryFolds)); } catch (err) { /* convenience only */ }
      applyLibraryFolds();
    };
    fold.addEventListener("click", (event) => { event.stopPropagation(); toggle(); });
    head.addEventListener("click", (event) => { if (!event.target.closest("button")) toggle(); });
    if (name === "songs") head.append(summary, $("new-song"), fold);   // New song stays on the line when folded
    else head.append(summary, fold);
  }
  applyLibraryFolds();
}

/* Projector windows (Settings): which monitor each screen opens on, and open/close them. The program does the opening. */
let pwView = null;
function pwOperatorAt() {
  const ratio = window.devicePixelRatio || 1;
  return [Math.round((window.screenX + window.outerWidth / 2) * ratio), Math.round((window.screenY + window.outerHeight / 2) * ratio)];
}
async function pwRefresh() {
  const result = await call("screens_get", {});
  if (!result.ok) return;
  pwView = result.data;
  drawProjectorWindows();
}
function drawProjectorWindows() {
  const list = $("pw-list");
  if (!list || !pwView) return;
  const view = pwView;
  list.replaceChildren();
  $("pw-open").disabled = !view.available || !Object.keys(view.assign).length;
  $("pw-close").disabled = !view.open.length;
  if (document.activeElement !== $("pw-auto")) $("pw-auto").checked = !!view.auto;
  $("pw-auto").disabled = !view.available;
  if (!view.available) { $("pw-status").textContent = "This needs Windows with Edge or Chrome, so it is not available on this computer."; return; }
  for (const output of view.outputs) {
    const row = el("li", "row");
    row.appendChild(el("span", "", output.name + (view.open.includes(output.name) ? " (open)" : "")));
    const select = el("select");
    select.setAttribute("aria-label", "Monitor for " + output.name);
    const none = el("option", "", "Not opened by ChurchLight");
    none.value = "";
    select.appendChild(none);
    for (const m of view.monitors) {
      const option = el("option", "", "Monitor " + (m.index + 1) + " (" + m.width + "×" + m.height + (m.primary ? ", main" : "") + ")");
      option.value = String(m.index);
      select.appendChild(option);
    }
    select.value = view.assign[output.name] === undefined ? "" : String(view.assign[output.name]);
    select.addEventListener("change", async () => {
      const assign = Object.assign({}, view.assign);
      if (select.value === "") delete assign[output.name]; else assign[output.name] = parseInt(select.value, 10);
      await act("screens_set", { assign: assign, auto: $("pw-auto").checked });
      pwRefresh();
    });
    row.appendChild(select);
    list.appendChild(row);
  }
}
function initProjectorWindows() {
  if (!$("pw-list")) return;
  $("pw-open").addEventListener("click", async () => {
    const data = await act("screens_open", { operator_at: pwOperatorAt() });
    if (data && data.problems && data.problems.length) $("pw-status").textContent = data.problems.join(" ");
    else $("pw-status").textContent = data ? "Opened." : "";
    pwRefresh();
  });
  $("pw-close").addEventListener("click", async () => { await act("screens_close"); $("pw-status").textContent = ""; pwRefresh(); });
  $("pw-auto").addEventListener("change", async () => {
    if (!pwView) return;
    await act("screens_set", { assign: pwView.assign, auto: $("pw-auto").checked });
    pwRefresh();
  });
  pwRefresh();
  setInterval(() => { if (!document.hidden) pwRefresh(); }, 5000);        // a window closed with Esc or Alt+F4
}

function initSettingsFolds() {
  initSettingsHelp();
  const sections = Array.from(document.querySelectorAll("details[data-settings-fold]"));
  for (const section of sections) section.open = false;   // always folded; nothing is remembered between visits
  const foldAll = $("settings-fold-all");
  const drawFoldAll = () => { foldAll.textContent = foldAllPlan(sections.map((s) => s.open)).label; };
  for (const section of sections) {
    section.addEventListener("toggle", () => {
      drawFoldAll();
    });
  }
  foldAll.addEventListener("click", () => {
    const plan = foldAllPlan(sections.map((s) => s.open));
    for (const section of sections) section.open = plan.openAll;
  });
  drawFoldAll();
}

function applyLibraryFolds() {
  for (const section of document.querySelectorAll(".lib-view [data-fold]")) {
    const name = section.dataset.fold;
    const folded = libraryFolds.includes(name);
    section.classList.toggle("collapsed", folded);
    const fold = section.querySelector(".fold");
    fold.textContent = folded ? "▸" : "▾";
    fold.setAttribute("aria-expanded", String(!folded));
    section.querySelector(".panel-summary").textContent = librarySummary(name, manage);
  }
}

/* ---- choosing several songs or slideshows to delete together ---- */
const librarySelect = {
  songs: { on: false, picked: new Set(), armed: null },
  decks: { on: false, picked: new Set(), armed: null },
};
const SELECT_WORDS = { songs: ["song", "songs"], decks: ["slideshow", "slideshows"] };

function selectWord(kind, count) { return SELECT_WORDS[kind][count === 1 ? 0 : 1]; }

/* The ids that can be chosen on screen: not the ones a service uses. */
function selectableIds(kind, shown) { return shown.filter((item) => !item.uses).map((item) => item.id); }

function drawSelectBar(kind, shown) {
  const state = librarySelect[kind];
  const bar = $(kind === "songs" ? "song-select-bar" : "deck-select-bar");
  const toggle = $(kind === "songs" ? "song-select-toggle" : "deck-select-toggle");
  toggle.textContent = state.on ? "Done" : "Select several";
  toggle.setAttribute("aria-pressed", String(state.on));
  bar.hidden = !state.on;
  bar.replaceChildren();
  if (!state.on) return;
  const choosable = selectableIds(kind, shown);
  state.picked = new Set([...state.picked].filter((id) => choosable.includes(id)));      // only what is shown can be deleted
  const count = state.picked.size;
  bar.appendChild(button("Select all shown", () => { state.picked = new Set(choosable); redrawSelectLists(kind); }, "", "Select every " + selectWord(kind, 1) + " shown"));
  bar.appendChild(button("Clear", () => { state.picked = new Set(); redrawSelectLists(kind); }));
  bar.appendChild(el("span", "muted grow", count + " selected" + (shown.length > choosable.length ? ". Ones used in a service cannot be chosen." : "")));
  const del = button(state.armed ? "Sure? Delete " + count : "Delete selected", null, "danger");
  del.disabled = count === 0;
  del.addEventListener("click", async () => {
    if (!state.armed) {
      state.armed = setTimeout(() => { state.armed = null; if (state.on) redrawSelectLists(kind); }, 4000);
      redrawSelectLists(kind);
      return;
    }
    clearTimeout(state.armed);
    state.armed = null;
    const ids = [...state.picked];
    const result = await act(kind === "songs" ? "songs_delete" : "decks_delete", { ids: ids });
    state.picked = new Set();
    state.on = false;
    if (result) {
      let text = "Deleted " + result.deleted + " " + selectWord(kind, result.deleted) + ".";
      if (result.skipped.length) text += " Skipped " + result.skipped.length + ": " + result.skipped.map((s) => s.reason).join(" ");
      if (result.backed_up) text += " A backup was saved first.";
      showNotice(text);
    }
    loadManage();
  });
  bar.appendChild(del);
}

function redrawSelectLists(kind) { if (kind === "songs") drawSongList(); else drawDeckList(); }

function selectBox(kind, id, label, blockedReason) {
  const state = librarySelect[kind];
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = state.picked.has(id);
  box.disabled = !!blockedReason;
  if (blockedReason) box.title = blockedReason;
  box.setAttribute("aria-label", "Select " + label);
  box.addEventListener("change", () => {
    if (box.checked) state.picked.add(id); else state.picked.delete(id);
    if (state.armed) { clearTimeout(state.armed); state.armed = null; }
    redrawSelectLists(kind);
  });
  return box;
}

function wireSelectToggles() {
  for (const kind of ["songs", "decks"]) {
    $(kind === "songs" ? "song-select-toggle" : "deck-select-toggle").addEventListener("click", () => {
      const state = librarySelect[kind];
      state.on = !state.on;
      state.picked = new Set();
      if (state.armed) { clearTimeout(state.armed); state.armed = null; }
      redrawSelectLists(kind);
    });
  }
}

function drawDraftList() {
  const list = $("draft-list");
  list.replaceChildren();
  const drafts = manage.songs.filter((song) => song.draft);
  if (!drafts.length) { list.appendChild(el("li", "muted", "No drafts. Use Save as draft in the song editor.")); return; }
  for (const song of drafts) {
    const row = el("li", "row");
    row.appendChild(titleBlock("draft", song.title, song.author));
    row.appendChild(button("Edit", () => openEditor(song.id), "", "Edit the draft " + song.title));
    row.appendChild(button("Publish", async () => { if (await act("song_publish", { id: song.id })) loadManage(); }, "primary", "Publish " + song.title));
    const del = button("Delete", null, "danger", "Delete the draft " + song.title);
    confirmClick(del, () => act("song_delete", { id: song.id }).then(() => loadManage()),
      "Delete the draft “" + song.title + "”? It cannot be brought back.");
    row.appendChild(del);
    list.appendChild(row);
  }
}

function drawManage() {
  applyLibraryFolds();
  drawSongList();
  if (document.activeElement !== $("church-ccli")) $("church-ccli").value = manage.church_ccli || "";
  drawDraftList();
  drawDeckList();
  drawMediaList();
  drawMediaSources();
  drawMediaPreview();
  drawBibleList();
}

/* Save a file from the program. It loads in a hidden frame, so a refusal (an empty library, say) shows as a message
   here instead of replacing the whole page with the error text. */
let downloadFrame = null;
function downloadFile(url) {
  if (downloadFrame) downloadFrame.remove();
  const frame = document.createElement("iframe");
  frame.hidden = true;
  frame.setAttribute("aria-hidden", "true");
  frame.addEventListener("load", () => {
    let text = "";
    try { text = frame.contentDocument.body.textContent || ""; } catch (err) { /* a file saved: nothing to read */ }
    if (!text) return;
    let message = "Could not save that file";
    try { message = JSON.parse(text).error || message; } catch (err) { /* not our error text */ }
    showMessage(message);
  });
  frame.src = url;
  downloadFrame = frame;
  document.body.appendChild(frame);
}

function songExportFormat() {
  const select = $("song-export-format");
  return select && select.value === "txt" ? "txt" : "xml";
}

function drawSongList() {
  const list = $("song-list");
  list.replaceChildren();
  const published = manage.songs.filter((song) => !song.draft);
  $("song-export-all").disabled = !published.length;
  $("song-export-all").title = published.length ? "Save every finished song in one .zip file" : "There are no songs to export yet";
  const songs = filterSongs(published, $("song-filter").value);
  $("song-select-toggle").disabled = !published.length;
  drawSelectBar("songs", songs);
  if (!songs.length) { list.appendChild(el("li", "muted", published.length ? "No song matches" : "No songs yet. Add song files above, or press New song to type one in.")); return; }
  for (const song of songs) {
    const row = el("li", "row");
    const songInfo = titleBlock("", song.operator_name || song.title, [song.operator_name ? "Song name: " + song.title : "", song.author, song.ccli ? "CCLI " + song.ccli : "", usesText(song.uses)].filter(Boolean).join(" · "));
    if (librarySelect.songs.on) {
      row.classList.add("selecting");
      row.appendChild(selectBox("songs", song.id, song.title, song.uses ? "It is used in a service. Take it out of those services first." : ""));
      row.appendChild(songInfo);
      list.appendChild(row);
      continue;
    }
    row.appendChild(songInfo);
    row.appendChild(button("Edit", () => openEditor(song.id), "", "Edit " + song.title));
    const save = button("Export", () => { downloadFile("/api/export?kind=songs&format=" + songExportFormat() + "&ids=" + song.id); },
      "", "Export " + song.title + " as a file");
    save.title = "Save this song as a file (the format is chosen above the list)";
    row.appendChild(save);
    const del = button("Delete", null, "danger", "Delete " + song.title);
    if (song.uses) { del.disabled = true; del.title = "Take it out of the services that use it first"; }
    else confirmClick(del, () => act("song_delete", { id: song.id }).then(() => loadManage()),
      "Delete the song “" + song.title + "”? Its words are gone for good.");
    row.appendChild(del);
    list.appendChild(row);
  }
}

/* The picker under a deck: how the projector changes to its slides, and for how long (0 to 2 seconds). */
function transitionControls(deck) {
  const box = el("div", "transition-controls");
  box.appendChild(el("span", "muted", "Projector transition:"));
  const select = document.createElement("select");
  select.setAttribute("aria-label", "Transition for " + deck.name);
  for (const choice of TRANSITION_CHOICES) {
    const option = el("option", "", choice.text);
    option.value = choice.value;
    select.appendChild(option);
  }
  select.value = deck.transition;
  const seconds = document.createElement("input");
  seconds.type = "number";
  seconds.min = "0";
  seconds.max = "2";
  seconds.step = "0.1";
  seconds.value = msToSeconds(deck.transition_ms);
  seconds.disabled = deck.transition === "none";
  seconds.setAttribute("aria-label", "Transition length in seconds for " + deck.name);
  const label = el("span", "muted", "seconds");
  const save = async () => {
    const ms = secondsToMs(seconds.value);
    if (ms === null) { showMessage("Type a time from 0 to 2 seconds, in steps of 0.1."); seconds.value = msToSeconds(deck.transition_ms); return; }
    if (await act("deck_transition", { id: deck.id, transition: select.value, milliseconds: ms })) loadManage();
    else { select.value = deck.transition; seconds.value = msToSeconds(deck.transition_ms); }
  };
  select.addEventListener("change", () => { seconds.disabled = select.value === "none"; save(); });
  seconds.addEventListener("change", save);
  seconds.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); save(); } });
  box.appendChild(select);
  box.appendChild(seconds);
  box.appendChild(label);
  return box;
}

/* How a deck's pictures fill the projector: the whole picture, covering the screen (crops), or stretched. */
const PICTURE_SIZES = [
  { value: "fit", text: "Fit: the whole picture (bands if its shape differs)" },
  { value: "fill", text: "Fill: cover the screen (crops the edges)" },
  { value: "stretch", text: "Stretch: exactly the screen (changes the shape)" },
];

function pictureSizeControls(deck) {
  const box = el("div", "transition-controls");
  box.appendChild(el("span", "muted", "Picture size:"));
  const select = document.createElement("select");
  select.setAttribute("aria-label", "How the pictures of " + deck.name + " fill the screen");
  for (const choice of PICTURE_SIZES) select.appendChild(new Option(choice.text, choice.value));
  select.value = deck.image_fit || "fit";
  select.addEventListener("change", async () => {
    if (await act("deck_image_fit", { id: deck.id, fit: select.value })) loadManage();
    else select.value = deck.image_fit || "fit";
  });
  box.appendChild(select);
  return box;
}

function autoplayControls(deck) {
  const box = el("div", "transition-controls");
  box.appendChild(el("span", "muted", "Timed advance:"));
  const seconds = document.createElement("input");
  seconds.type = "number"; seconds.min = "0"; seconds.max = String(MAX_AUTO_SECONDS); seconds.step = "1";
  seconds.value = deck.auto_seconds;
  seconds.setAttribute("aria-label", "Seconds per slide for " + deck.name + " (0 for manual)");
  const end = document.createElement("select");
  end.setAttribute("aria-label", "At the end of " + deck.name);
  for (const choice of END_CHOICES) end.appendChild(new Option(choice.text, choice.value));
  end.value = deck.end_action;
  end.disabled = !(deck.auto_seconds > 0);
  const save = async () => {
    const n = parseAutoSeconds(seconds.value);
    if (n === null) { showMessage("Type a whole number of seconds from 0 to " + MAX_AUTO_SECONDS + " (0 means manual)."); seconds.value = deck.auto_seconds; return; }
    if (await act("deck_autoplay", { id: deck.id, seconds: n, end_action: end.value })) loadManage();
    else { seconds.value = deck.auto_seconds; end.value = deck.end_action; }
  };
  seconds.addEventListener("change", () => { end.disabled = !(parseAutoSeconds(seconds.value) > 0); save(); });
  seconds.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); save(); } });
  end.addEventListener("change", save);
  box.append(seconds, el("span", "muted", "seconds per slide (0 = manual), then"), end);
  return box;
}

function drawDeckList() {
  const list = $("deck-list");
  list.replaceChildren();
  $("deck-export-all").disabled = !manage.decks.length;
  $("deck-export-all").title = manage.decks.length ? "Save every slideshow, with pictures and settings, in one .zip bundle" : "There are no slideshows to export yet";
  $("deck-select-toggle").disabled = !manage.decks.length;
  const decks = filterDecks(manage.decks, $("deck-filter").value);
  $("deck-filter").hidden = !manage.decks.length;
  drawSelectBar("decks", decks);
  if (!decks.length) {
    const empty = el("li", "muted", manage.decks.length ? "No slideshows match" : "No slideshows yet. Choose a PDF above to add your first one. ");
    if (!manage.decks.length) empty.appendChild(button("How do I get my slides in?", openSlidesGuide));
    list.appendChild(empty);
    return;
  }
  for (const deck of decks) {
    const row = el("li", "row" + (renamingDeck === deck.id ? " editing" : ""));
    if (librarySelect.decks.on) {                          // choosing several: just a tick and the name
      row.classList.add("selecting");
      row.appendChild(selectBox("decks", deck.id, deck.name, deck.uses ? "It is used in a service. Take it out of those services first." : ""));
      row.appendChild(titleBlock("slideshow", deck.name, [deck.pages + " page" + (deck.pages === 1 ? "" : "s"), usesText(deck.uses)].filter(Boolean).join(" · ")));
      list.appendChild(row);
      continue;
    }
    if (renamingDeck === deck.id) {
      const input = el("input");
      input.type = "text";
      input.maxLength = 200;
      input.value = deck.name;
      input.setAttribute("aria-label", "New name for " + deck.name);
      const save = async () => {
        if (await act("deck_rename", { id: deck.id, name: input.value })) { renamingDeck = null; loadManage(); }
      };
      input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") { event.preventDefault(); save(); }
        if (event.key === "Escape") { renamingDeck = null; drawDeckList(); }
      });
      row.appendChild(input);
      row.appendChild(button("Save", save, "primary"));
      row.appendChild(button("Cancel", () => { renamingDeck = null; drawDeckList(); }));
      list.appendChild(row);
      setTimeout(() => input.focus(), 0);
      continue;
    }
    row.appendChild(titleBlock("slideshow", deck.name, [deck.pages + " page" + (deck.pages === 1 ? "" : "s"), usesText(deck.uses),
      transitionText(deck.transition, deck.transition_ms), autoplayText(deck.auto_seconds, deck.end_action)].filter(Boolean).join(" · ")));
    const edit = button("Edit slideshow", () => openDeckEditor(deck.id), "primary", "Edit the slides of " + deck.name);
    if (deck.live) { edit.disabled = true; edit.title = "This slideshow is live right now, so it cannot be changed"; }
    row.appendChild(edit);
    row.appendChild(button("Rename", () => { renamingDeck = deck.id; drawDeckList(); }, "", "Rename " + deck.name));
    const exportBtn = button("Export", () => { downloadFile("/api/export?kind=slideshows&ids=" + deck.id); }, "", "Export " + deck.name + " as a bundle");
    exportBtn.title = "Save this slideshow with its pictures and settings, to open on another computer";
    row.appendChild(exportBtn);
    const del = button("Delete", null, "danger", "Delete " + deck.name);
    if (deck.uses) { del.disabled = true; del.title = "Take it out of the services that use it first"; }
    else confirmClick(del, () => act("deck_delete", { id: deck.id }).then(() => loadManage()),
      "Delete the slideshow “" + deck.name + "” and all its slides? Export it first if you may want it again.");
    row.appendChild(del);
    const settings = el("div", "deck-settings");         // picture size, transition and autoplay: fold away to keep the list short
    settings.id = "deck-settings-" + deck.id;
    settings.appendChild(pictureSizeControls(deck));
    settings.appendChild(transitionControls(deck));
    settings.appendChild(autoplayControls(deck));
    const open = openDecks.includes(deck.id);
    settings.hidden = !open;
    const fold = button(open ? "Hide settings" : "Show settings", null, "", "Show or hide the picture size, transition and autoplay of " + deck.name);
    fold.setAttribute("aria-expanded", String(open));
    fold.setAttribute("aria-controls", settings.id);
    fold.addEventListener("click", () => {
      const nowOpen = settings.hidden;
      settings.hidden = !nowOpen;
      fold.textContent = nowOpen ? "Hide settings" : "Show settings";
      fold.setAttribute("aria-expanded", String(nowOpen));
      openDecks = toggleDeckOpen(openDecks, deck.id);
      try { localStorage.setItem(DECK_OPEN_KEY, JSON.stringify(openDecks)); } catch (err) { /* convenience only */ }
    });
    row.appendChild(fold);
    row.appendChild(settings);
    list.appendChild(row);
  }
}

/* Which slideshows in the library have their settings showing; kept in this browser. Folded is the default. */
const DECK_OPEN_KEY = "churchlight.deckopen";
function parseDeckOpen(raw) {
  try {
    const data = JSON.parse(raw);
    if (Array.isArray(data)) return data.filter((n) => Number.isInteger(n)).slice(0, 500);
  } catch (err) { /* nothing stored, or damaged */ }
  return [];
}
function toggleDeckOpen(open, id) { return open.includes(id) ? open.filter((n) => n !== id) : open.concat(id); }
let openDecks = [];
try { openDecks = parseDeckOpen(localStorage.getItem(DECK_OPEN_KEY)); } catch (err) { /* default: all folded */ }

/* ---------------- the deck editor ---------------- */
/* Pure helpers first (tested without a browser): selection, moving and what each button may do. */

/* Click rules: plain click selects one; Ctrl/Cmd toggles one; Shift selects the range from the anchor. */
function selectPages(sel, order, id, mods) {
  const m = mods || {};
  const chosen = new Set(sel.selected);
  let anchor = sel.anchor;
  if (m.shift && anchor !== null && order.includes(anchor)) {
    const a = order.indexOf(anchor);
    const b = order.indexOf(id);
    const [from, to] = a < b ? [a, b] : [b, a];
    if (!(m.ctrl)) chosen.clear();
    for (const pid of order.slice(from, to + 1)) chosen.add(pid);
  } else if (m.ctrl) {
    if (chosen.has(id)) chosen.delete(id); else chosen.add(id);
    anchor = id;
  } else {
    chosen.clear();
    chosen.add(id);
    anchor = id;
  }
  return { selected: order.filter((pid) => chosen.has(pid)), anchor: anchor };
}

/* Move the selected slides one place earlier (-1) or later (+1); a block stays together at the edge. */
function movePages(order, ids, delta) {
  const picked = new Set(ids);
  const out = order.slice();
  if (delta < 0) {
    for (let i = 1; i < out.length; i++) {
      if (picked.has(out[i]) && !picked.has(out[i - 1])) { const t = out[i - 1]; out[i - 1] = out[i]; out[i] = t; }
    }
  } else {
    for (let i = out.length - 2; i >= 0; i--) {
      if (picked.has(out[i]) && !picked.has(out[i + 1])) { const t = out[i + 1]; out[i + 1] = out[i]; out[i] = t; }
    }
  }
  return out;
}

/* Drop the selected slides in front of `beforeId` (null puts them at the end), keeping their own order. */
function dropPages(order, ids, beforeId) {
  const picked = new Set(ids);
  const moving = order.filter((pid) => picked.has(pid));
  const rest = order.filter((pid) => !picked.has(pid));
  const at = beforeId === null || picked.has(beforeId) ? (beforeId === null ? rest.length : -1) : rest.indexOf(beforeId);
  if (at < 0) return order.slice();               // dropped onto itself: nothing moves
  return rest.slice(0, at).concat(moving, rest.slice(at));
}

/* What the editor buttons may do right now. */
function deckEditorState(data, selected) {
  const count = selected.length;
  const pages = (data && data.pages) || [];
  const locked = !!(data && data.live);
  const order = pages.map((p) => p.id);
  const picked = pages.filter((p) => selected.includes(p.id));
  const allSkipped = count > 0 && picked.every((p) => p.skipped);
  const first = order.findIndex((pid) => selected.includes(pid));
  const last = order.length - 1 - order.slice().reverse().findIndex((pid) => selected.includes(pid));
  const others = pages.filter((p) => !selected.includes(p.id));
  return {
    locked: locked,
    canLeft: !locked && count > 0 && first > 0,
    canRight: !locked && count > 0 && last < order.length - 1,
    canDuplicate: !locked && count > 0,
    canSkip: !locked && count > 0 && (allSkipped || others.some((p) => !p.skipped)),
    skipLabel: allSkipped ? "Put back" : "Skip",
    canDelete: !locked && count > 0 && others.length > 0 && others.some((p) => !p.skipped),
    canNotes: !locked && count === 1,
    canSelectAll: pages.length > 0,
    canAdd: !locked,
    canReplace: !locked && count === 1,
    canFrom: !locked,
    canSplit: count > 0,                 // reads only, so it is allowed while the deck is live
    canTiming: !locked && count > 0,
  };
}

/* What the selected slides share: a delay ("deck", "manual", "seconds" or "mixed") and a transition. */
function commonTiming(pages, selected) {
  const picked = pages.filter((p) => selected.includes(p.id));
  if (!picked.length) return null;
  const delayKey = (p) => p.auto_seconds === null || p.auto_seconds === undefined ? "deck" : p.auto_seconds === 0 ? "manual" : "s" + p.auto_seconds;
  const transKey = (p) => p.transition === null || p.transition === undefined ? "deck" : p.transition + ":" + p.transition_ms;
  const first = picked[0];
  const sameDelay = picked.every((p) => delayKey(p) === delayKey(first));
  const sameTrans = picked.every((p) => transKey(p) === transKey(first));
  const delay = !sameDelay ? { mode: "mixed" } : delayKey(first) === "deck" ? { mode: "deck" }
    : delayKey(first) === "manual" ? { mode: "manual" } : { mode: "seconds", seconds: first.auto_seconds };
  const transition = !sameTrans ? { mode: "mixed" } : transKey(first) === "deck" ? { mode: "deck" }
    : { mode: first.transition, ms: first.transition_ms };
  return { delay: delay, transition: transition };
}

/* The small labels under a thumbnail. */
function pageFlags(page) {
  const flags = [];
  if (page.skipped) flags.push("Skipped");
  if (page.kind === "video") flags.push(page.muted ? "Video, no sound" : "Video");
  if (page.sound_id) flags.push("Sound");
  if (page.notes) flags.push("Notes");
  if (page.auto_seconds === 0) flags.push("Waits");
  else if (page.auto_seconds > 0) flags.push(page.auto_seconds + " s");
  if (page.transition !== null && page.transition !== undefined) {
    const text = transitionText(page.transition, page.transition_ms);
    flags.push(text === "No transition" ? "Cut" : text.split(",")[0]);
  }
  return flags;
}

/* What "follow the deck" means for this deck, in words. */
function deckTimingText(deck) {
  if (!deck) return "";
  return autoplayText(deck.auto_seconds, deck.end_action).replace("Manual advance", "manual advance") + "; " +
    transitionText(deck.transition, deck.transition_ms).replace("No transition", "no transition");
}

/* The slide new pictures go after: the last selected one, or null for the end of the deck. */
function afterPageFor(order, selected) {
  let id = null;
  for (const pid of order) if (selected.includes(pid)) id = pid;
  return id;
}

function splitName(deckName) { return (String(deckName).trim() + " (part)").slice(0, 200); }

function usedText(services) {
  if (!services || !services.length) return "Not used in any service";
  const names = services.slice(0, 3).map((s) => s.title + (s.date ? " (" + s.date + ")" : "")).join(", ");
  const more = services.length > 3 ? " and " + (services.length - 3) + " more" : "";
  return "Used in " + services.length + " service" + (services.length === 1 ? "" : "s") + ": " + names + more + ". Changes apply to all of them.";
}

let deckEdit = null;            // { id, data, selected: [page ids], anchor, drag: [ids] | null }
let deckEditCounter = 0;

async function openDeckEditor(id) {
  deckEdit = { id: id, data: null, selected: [], anchor: null, drag: null, from: null, library: null };
  loadManage();                                      // the sound files and pictures in the Media Library
  $("de-rename-box").hidden = true;
  $("de-title").hidden = false;
  $("de-rename").hidden = false;
  $("de-from").hidden = true;
  $("de-split").hidden = true;
  $("de-status").textContent = "";
  showView("deckedit");
  drawDeckEditor();
  await refreshDeckEditor(true);
}

async function refreshDeckEditor(first) {
  if (!deckEdit) return;
  const mine = ++deckEditCounter;
  const result = await call("deck_get", { id: deckEdit.id });
  if (mine !== deckEditCounter || !deckEdit) return;
  if (!result.ok) { showMessage(result.error); deckEdit = null; showView("decks"); return; }
  deckEdit.data = result.data;
  const ids = result.data.pages.map((p) => p.id);
  deckEdit.selected = deckEdit.selected.filter((pid) => ids.includes(pid));
  if (deckEdit.anchor !== null && !ids.includes(deckEdit.anchor)) deckEdit.anchor = deckEdit.selected[0] || null;
  if (first && !deckEdit.selected.length && ids.length) { deckEdit.selected = [ids[0]]; deckEdit.anchor = ids[0]; }
  drawDeckEditor();
}

/* Run one change, then redraw from what the program says now. */
async function deckChange(action, args, afterSelect) {
  if (!deckEdit) return null;
  const result = await act(action, Object.assign({ id: deckEdit.id }, args));
  if (result === null) { await refreshDeckEditor(false); return null; }
  if (afterSelect) afterSelect(result);
  await refreshDeckEditor(false);
  return result;
}

function drawDeckEditor() {
  const view = $("view-deckedit");
  const data = deckEdit && deckEdit.data;
  const buttons = deckEditorState(data, deckEdit ? deckEdit.selected : []);
  view.classList.toggle("locked", buttons.locked);
  $("de-title").textContent = data ? "Editing: " + data.name : "Edit slideshow";
  $("de-rename").disabled = !data;
  $("de-pdf").disabled = !data || buttons.locked;
  $("de-used").textContent = data ? usedText(data.services) : "";
  $("de-live").hidden = !buttons.locked;
  $("de-count").textContent = data ? "(" + plural(data.pages.length, "slide") + (data.pages.some((p) => p.skipped) ? ", " + data.pages.filter((p) => p.skipped).length + " skipped" : "") + ")" : "";
  $("de-left").disabled = !buttons.canLeft;
  $("de-right").disabled = !buttons.canRight;
  $("de-duplicate").disabled = !buttons.canDuplicate;
  $("de-skip").disabled = !buttons.canSkip;
  $("de-skip").textContent = buttons.skipLabel;
  $("de-delete").disabled = !buttons.canDelete;
  $("de-all").disabled = !buttons.canSelectAll;
  $("de-add").disabled = !buttons.canAdd;
  $("de-replace").disabled = !buttons.canReplace;
  $("de-from-open").disabled = !buttons.canFrom;
  $("de-library-open").disabled = !buttons.canAdd;
  if (buttons.locked) $("de-library").hidden = true;
  $("de-split-open").disabled = !buttons.canSplit;
  $("de-tomedia").disabled = !buttons.canSplit;
  const pickedCount = deckEdit ? deckEdit.selected.length : 0;
  $("de-selbar").classList.toggle("active", pickedCount > 0);
  $("de-sel-label").textContent = pickedCount ? plural(pickedCount, "slide") + " selected" : "Selected slides: none yet. Click a slide to select it.";
  if (buttons.locked) $("de-from").hidden = true;
  $("de-from-add").disabled = !buttons.canFrom || !deckEdit || !deckEdit.from || !deckEdit.from.selected.length;
  drawDeckGrid(data, buttons);
  drawDeckSide(data, buttons);
}

function drawDeckGrid(data, buttons) {
  const grid = $("de-grid");
  grid.replaceChildren();
  if (!data) return;
  const order = data.pages.map((p) => p.id);
  for (const page of data.pages) {
    const picked = deckEdit.selected.includes(page.id);
    const card = el("li", "de-card" + (picked ? " selected" : "") + (page.skipped ? " skipped" : ""));
    card.tabIndex = 0;
    card.setAttribute("role", "option");
    card.setAttribute("aria-selected", String(picked));
    card.setAttribute("aria-label", "Slide " + page.number + (page.title ? ", " + page.title : "") + (page.skipped ? ", skipped" : "") + (picked ? ", selected" : ""));
    card.dataset.id = String(page.id);
    card.draggable = !buttons.locked;
    if (page.kind === "video") {                      // a video page: a play tile, as the browser cannot draw a still from it here
      card.appendChild(el("div", "de-videotile", "▶ Video"));
    } else {
      const img = el("img");
      img.loading = "lazy";
      img.alt = "";
      img.draggable = false;
      img.src = mediaUrl(page.image);
      card.appendChild(img);
    }
    card.appendChild(el("span", "de-num", String(page.number)));
    if (page.title) card.appendChild(el("span", "de-title", page.title));
    const flags = el("div", "de-flags");
    for (const text of pageFlags(page)) flags.appendChild(el("span", "", text));
    card.appendChild(flags);
    const choose = (event) => {
      const next = selectPages({ selected: deckEdit.selected, anchor: deckEdit.anchor }, order, page.id,
        { ctrl: event.ctrlKey || event.metaKey, shift: event.shiftKey });
      deckEdit.selected = next.selected;
      deckEdit.anchor = next.anchor;
      drawDeckEditor();
      const again = $("de-grid").querySelector('[data-id="' + page.id + '"]');
      if (again && event.type === "keydown") again.focus();
    };
    card.addEventListener("click", choose);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(event); }
    });
    card.addEventListener("dragstart", (event) => {
      if (!deckEdit.selected.includes(page.id)) { deckEdit.selected = [page.id]; deckEdit.anchor = page.id; }
      deckEdit.drag = deckEdit.selected.slice();
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", "slides");
    });
    card.addEventListener("dragover", (event) => {
      if (!deckEdit.drag) return;
      event.preventDefault();
      const box = card.getBoundingClientRect();
      const after = event.clientX > box.left + box.width / 2;
      for (const other of grid.children) other.classList.remove("drop-before", "drop-after");
      card.classList.add(after ? "drop-after" : "drop-before");
    });
    card.addEventListener("drop", (event) => {
      if (!deckEdit.drag) return;
      event.preventDefault();
      const box = card.getBoundingClientRect();
      const after = event.clientX > box.left + box.width / 2;
      const index = order.indexOf(page.id) + (after ? 1 : 0);
      const beforeId = index >= order.length ? null : order[index];
      const moving = deckEdit.drag;
      deckEdit.drag = null;
      const next = dropPages(order, moving, beforeId);
      if (next.join() !== order.join()) deckChange("deck_pages_reorder", { order: next });
      else drawDeckEditor();
    });
    card.addEventListener("dragend", () => { if (deckEdit) deckEdit.drag = null; for (const other of grid.children) other.classList.remove("drop-before", "drop-after"); });
    grid.appendChild(card);
  }
}

function drawDeckSide(data, buttons) {
  const notes = $("de-notes");
  const preview = $("de-preview");
  const page = data && deckEdit.selected.length ? data.pages.find((p) => p.id === (deckEdit.selected.includes(deckEdit.anchor) ? deckEdit.anchor : deckEdit.selected[0])) : null;
  preview.hidden = !page;
  $("de-preview-empty").hidden = !!page;
  const isVideo = !!page && page.kind === "video";
  preview.hidden = !page || isVideo;
  $("de-preview-empty").hidden = !!page && !isVideo;
  $("de-preview-empty").textContent = isVideo ? "Video page. It plays when the slide comes up." : "Select a slide";
  if (page && !isVideo) { preview.src = mediaUrl(page.image); preview.alt = "Slide " + page.number; }
  $("de-side-label").textContent = !page ? "" : deckEdit.selected.length > 1
    ? deckEdit.selected.length + " slides selected. Select one slide to edit its notes."
    : "Slide " + page.number + " of " + data.pages.length + (page.skipped ? " (skipped)" : "");
  const titleBox = $("de-slide-title");
  const titled = buttons.canNotes && page ? page : null;
  const titleDirty = titleBox.dataset.page === String(titled && titled.id) && titleBox.value !== (titleBox.dataset.saved || "");
  if (!titleDirty) { titleBox.value = titled ? titled.title : ""; titleBox.dataset.saved = titleBox.value; }
  titleBox.dataset.page = titled ? String(titled.id) : "";
  titleBox.disabled = !titled;
  const shown = buttons.canNotes && page ? page : null;
  const dirty = notes.dataset.page === String(shown && shown.id) && notes.value !== (notes.dataset.saved || "");
  if (!dirty) { notes.value = shown ? shown.notes : ""; notes.dataset.saved = notes.value; }
  notes.dataset.page = shown ? String(shown.id) : "";
  notes.disabled = !shown;
  $("de-notes-save").disabled = !shown;
  drawSoundControls(data, buttons);
  drawTimingControls(data, buttons);
}

/* The Sound part of the side panel: mute a video page's own sound and choose a sound file for the selected slides. */
async function openLibraryPanel() {
  if (!deckEdit) return;
  await loadManage();
  deckEdit.library = [];
  $("de-library-filter").value = "";
  $("de-library").hidden = false;
  drawLibraryPicker();
}

function drawLibraryPicker() {
  const grid = $("de-library-grid");
  grid.replaceChildren();
  const all = (manage.media || []).filter((m) => m.kind === "image" || m.kind === "video");
  const files = filterDecks(all, $("de-library-filter").value);          // by name; the files already chosen stay chosen while filtering
  $("de-library-filter").hidden = !all.length;
  if (!files.length) {
    grid.appendChild(el("li", "muted", all.length ? "No files match" : "The Media Library has no pictures or videos yet. Add some on the Media Library tab."));
    $("de-library-add").disabled = !deckEdit.library.length;
    $("de-library-add").textContent = deckEdit.library.length ? "Add " + plural(deckEdit.library.length, "file") : "Add";
    return;
  }
  for (const file of files) {
    const picked = deckEdit.library.includes(file.id);
    const card = el("li", "de-card" + (picked ? " selected" : ""));
    card.tabIndex = 0;
    card.setAttribute("role", "option");
    card.setAttribute("aria-selected", String(picked));
    card.setAttribute("aria-label", file.name + (file.kind === "video" ? ", video" : ", picture") + (picked ? ", chosen" : ""));
    if (file.kind === "video") card.appendChild(el("div", "de-videotile", "▶ Video"));
    else {
      const img = el("img");
      img.loading = "lazy";
      img.alt = "";
      img.src = mediaUrl(file.path);
      card.appendChild(img);
    }
    card.appendChild(el("span", "de-title", file.name));
    const toggle = () => {
      deckEdit.library = picked ? deckEdit.library.filter((id) => id !== file.id) : deckEdit.library.concat(file.id);
      drawLibraryPicker();
    };
    card.addEventListener("click", toggle);
    card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    grid.appendChild(card);
  }
  $("de-library-add").disabled = !deckEdit.library.length;
  $("de-library-add").textContent = deckEdit.library.length ? "Add " + plural(deckEdit.library.length, "file") : "Add";
}

function drawSoundControls(data, buttons) {
  const select = $("de-sound-select");
  const chosen = data ? data.pages.filter((p) => deckEdit.selected.includes(p.id)) : [];
  const files = (manage.media || []).filter((m) => m.kind === "audio");
  const wanted = JSON.stringify(files.map((f) => [f.id, f.name]));
  if (select.dataset.files !== wanted) {
    fillSelect(select, [["", "No extra sound"]].concat(files.map((f) => [String(f.id), f.name])));
    select.dataset.files = wanted;
  }
  const on = !buttons.locked && chosen.length > 0;
  select.disabled = !on;
  const ids = Array.from(new Set(chosen.map((p) => p.sound_id === null || p.sound_id === undefined ? "" : String(p.sound_id))));
  for (const old of select.querySelectorAll('option[value="' + MIXED + '"]')) old.remove();
  if (ids.length > 1) { const o = el("option", "", "Mixed"); o.value = MIXED; select.appendChild(o); }
  select.value = ids.length > 1 ? MIXED : (ids[0] || "");
  const loop = $("de-sound-loop");
  loop.disabled = !on || (ids.length === 1 && ids[0] === "");
  loop.checked = chosen.length > 0 && chosen.every((p) => p.sound_loop);
  const videos = chosen.filter((p) => p.kind === "video");
  $("de-mute-line").hidden = !videos.length;
  $("de-video-mute").disabled = buttons.locked || !videos.length;
  $("de-video-mute").checked = videos.length > 0 && videos.every((p) => p.muted);
  $("de-sound-for").textContent = !data ? "" : !chosen.length ? "Select slides to give them a sound."
    : "Applies to " + (chosen.length === 1 ? "the selected slide" : plural(chosen.length, "selected slide")) + ".";
  if (!files.length && !manage.media) loadManage();
}

const DELAY_CHOICES = [["deck", "Follow the slideshow"], ["manual", "Wait until Next is pressed"], ["seconds", "Move on after…"]];
const MIXED = "mixed";

function fillSelect(select, choices) {
  select.replaceChildren();
  for (const [value, text] of choices) {
    const option = el("option", "", text);
    option.value = value;
    select.appendChild(option);
  }
}

function drawTimingControls(data, buttons) {
  const delay = $("de-delay"), delaySec = $("de-delay-sec"), trans = $("de-trans"), transSec = $("de-trans-sec");
  if (!delay.options.length) {
    fillSelect(delay, DELAY_CHOICES);
    fillSelect(trans, [["deck", "Follow the slideshow"]].concat(TRANSITION_CHOICES.map((c) => [c.value, c.value === "none" ? "None (cut)" : c.text.split(" (")[0]])));
  }
  for (const select of [delay, trans]) {                  // a "Mixed" choice appears only while the slides differ
    const old = select.querySelector('option[value="' + MIXED + '"]');
    if (old) old.remove();
  }
  const common = data && deckEdit.selected.length ? commonTiming(data.pages, deckEdit.selected) : null;
  const on = buttons.canTiming && !!common;
  delay.disabled = trans.disabled = !on;
  $("de-timing-for").textContent = !data ? "" : !deckEdit.selected.length ? "Select slides to set their timing."
    : "Applies to " + (deckEdit.selected.length === 1 ? "the selected slide" : plural(deckEdit.selected.length, "selected slide")) + ".";
  $("de-timing-hint").textContent = data ? "Follow the slideshow means: " + deckTimingText(data.deck) +
    ". A service may set its own delay for the slideshow; a slide's own setting wins over both." : "";
  if (!common) { delaySec.hidden = transSec.hidden = true; $("de-delay-unit").hidden = $("de-trans-unit").hidden = true; return; }
  if (common.delay.mode === MIXED) { const o = el("option", "", "Mixed"); o.value = MIXED; delay.appendChild(o); }
  if (common.transition.mode === MIXED) { const o = el("option", "", "Mixed"); o.value = MIXED; trans.appendChild(o); }
  delay.value = common.delay.mode;
  trans.value = common.transition.mode;
  const showDelay = common.delay.mode === "seconds";
  delaySec.hidden = $("de-delay-unit").hidden = !showDelay;
  if (showDelay && document.activeElement !== delaySec) delaySec.value = String(common.delay.seconds);
  const showTrans = common.transition.mode !== "deck" && common.transition.mode !== "none" && common.transition.mode !== MIXED;
  transSec.hidden = $("de-trans-unit").hidden = !showTrans;
  if (showTrans && document.activeElement !== transSec) transSec.value = msToSeconds(common.transition.ms);
  delaySec.disabled = transSec.disabled = !on;
}

/* Send the delay and transition the controls now show to every selected slide. */
async function applyDelay() {
  const mode = $("de-delay").value;
  if (mode === MIXED || !deckEdit) return;
  let seconds = null;
  if (mode === "manual") seconds = 0;
  if (mode === "seconds") {
    seconds = parseAutoSeconds($("de-delay-sec").value || "5");
    if (seconds === null || seconds < 1) { showMessage("Type a whole number of seconds from 1 to " + MAX_AUTO_SECONDS + "."); drawDeckEditor(); return; }
  }
  await deckChange("deck_pages_timing", { pages: deckEdit.selected.slice(), seconds: seconds });
}

async function applyTransition() {
  const kind = $("de-trans").value;
  if (kind === MIXED || !deckEdit) return;
  let args = { transition: null, ms: null };
  if (kind !== "deck") {
    const ms = kind === "none" ? 0 : secondsToMs($("de-trans-sec").value || "0.5");
    if (ms === null) { showMessage("Type a time from 0 to 2 seconds, in steps of 0.1."); drawDeckEditor(); return; }
    args = { transition: kind, ms: ms };
  }
  await deckChange("deck_pages_timing", Object.assign({ pages: deckEdit.selected.slice() }, args));
}

async function saveDeckNotes() {
  const notes = $("de-notes");
  if (!deckEdit || !notes.dataset.page || notes.value === (notes.dataset.saved || "")) return;
  const pageId = Number(notes.dataset.page);
  const text = notes.value;
  const done = await deckChange("deck_page_notes", { page: pageId, notes: text });
  if (done !== null) { if (notes.value === text) notes.value = text.trim(); notes.dataset.saved = text.trim(); $("de-notes-hint").textContent = "Saved"; setTimeout(() => { $("de-notes-hint").textContent = ""; }, 2000); }
}

async function saveDeckTitle() {
  const box = $("de-slide-title");
  if (!deckEdit || !box.dataset.page || box.value === (box.dataset.saved || "")) return;
  const text = box.value.trim();
  const done = await deckChange("deck_page_title", { page: Number(box.dataset.page), title: text });
  if (done !== null) { box.value = text; box.dataset.saved = text; }
}

/* ---- Replace from a PDF (pure helpers, then the page code) ---- */
/* [1,2,3,5] -> "1–3, 5" */
function numberRanges(numbers) {
  const out = [];
  let start = null;
  let prev = null;
  const flush = () => { if (start !== null) out.push(start === prev ? String(start) : start + "–" + prev); };
  for (const n of numbers || []) {
    if (prev !== null && n === prev + 1) { prev = n; continue; }
    flush(); start = n; prev = n;
  }
  flush();
  return out.join(", ");
}

/* What replacing would do, in plain words. `plan` is the server's {changed, same, added, extra, old_pages, new_pages}. */
function replacePlanText(plan) {
  const covered = (plan.changed || []).length + (plan.same || []).length;
  const bits = [];
  if (covered) {
    const all = (plan.changed || []).concat(plan.same || []).sort((a, b) => a - b);
    bits.push((covered === 1 ? "Slide " : "Slides ") + numberRanges(all) + (covered === 1 ? " is checked" : " are checked") + ": "
      + ((plan.changed || []).length ? (plan.changed.length === covered ? "all changed" : plan.changed.length + " changed") : "")
      + ((plan.changed || []).length && (plan.same || []).length ? ", " : "")
      + ((plan.same || []).length ? (plan.same.length === covered ? "all look identical (nothing to replace)" : plan.same.length + " look identical") : "")
      + ".");
  }
  if (plan.added) bits.push(plan.added + (plan.added === 1 ? " new page will be added" : " new pages will be added") + " at the end.");
  if ((plan.extra || []).length) {
    bits.push((plan.extra.length === 1 ? "Slide " : "Slides ") + numberRanges(plan.extra) + (plan.extra.length === 1 ? " is" : " are") + " not in the new PDF and will be kept but skipped, unless you tick the box to remove them.");
  }
  return bits.join(" ") || "The new PDF has nothing to change.";
}

let replacePending = null;      // {deckId, job, plan, keep} while the summary is showing; keep = slide numbers to leave alone

/* The slides that look different, each with the picture now, the new one and a Change / Keep choice. */
function drawReplaceList() {
  const pending = replacePending;
  const list = $("de-pdf-list");
  list.replaceChildren();
  if (!pending) return;
  for (const slide of pending.plan.slides.filter((s) => !s.same)) {
    const kept = pending.keep.has(slide.number);
    const row = el("li", "de-replace-row");
    row.appendChild(el("span", "de-replace-name", "Slide " + slide.number + (slide.title ? " · " + slide.title : "")));
    // each picture is its own choice: Old keeps the slide as it is, New puts the new page in
    for (const [value, text, path] of [["keep", "Old", slide.old], ["change", "New", slide.new]]) {
      const label = el("label", "de-replace-pic" + ((value === "keep") === kept ? " chosen" : ""));
      const radio = document.createElement("input");
      radio.type = "radio"; radio.name = "de-pdf-slide-" + slide.number; radio.value = value;
      radio.checked = (value === "keep") === kept;
      radio.setAttribute("aria-label", text + " version of slide " + slide.number);
      radio.addEventListener("change", () => {
        if (value === "keep") pending.keep.add(slide.number); else pending.keep.delete(slide.number);
        drawReplaceList();
      });
      const img = el("img"); img.loading = "lazy"; img.alt = text + " picture of slide " + slide.number; img.src = mediaUrl(path);
      const caption = el("span", "de-replace-cap");
      caption.append(radio, document.createTextNode(" " + text));
      label.append(img, caption);
      row.appendChild(label);
    }
    list.appendChild(row);
  }
}

function showReplacePlan(deckId, job, plan) {
  replacePending = { deckId: deckId, job: job, plan: plan, keep: new Set() };
  $("de-pdf-summary").textContent = replacePlanText(plan);
  $("de-pdf-changed").hidden = !plan.changed.length;
  drawReplaceList();
  $("de-pdf-same-line").hidden = !plan.same.length;
  $("de-pdf-same").value = "leave";
  $("de-pdf-same-label").textContent = (plan.same.length === 1 ? "Slide " : "Slides ") + numberRanges(plan.same) + (plan.same.length === 1 ? " looks identical" : " look identical");
  const extra = (plan.extra || []).length;
  $("de-pdf-extra-line").hidden = !extra;
  $("de-pdf-extra").checked = false;
  if (extra) $("de-pdf-extra-text").textContent = "Also remove " + (extra === 1 ? "slide " : "slides ") + numberRanges(plan.extra) + " (not in the new PDF)";
  $("de-pdf-apply").disabled = !(plan.changed.length || plan.added || extra);
  $("de-pdfpanel").hidden = false;
}

async function chooseReplacementPdf(file) {
  if (!deckEdit || !file) return;
  const status = $("de-status");
  const { good, problems } = checkFiles("deck", [file]);
  if (problems.length) { showMessage(problems[0]); return; }
  if (!good.length) return;
  const deckId = deckEdit.id;
  const made = await act("import_batch");
  if (!made) return;
  status.textContent = "Uploading " + file.name + "…";
  if (!(await uploadOne(made.batch, "deck", file))) { status.textContent = ""; return; }
  const started = await act("import_start", { kind: "deck", batch: made.batch, replace_for: deckId, name: "New pages for " + deckEdit.data.name });
  if (!started) { status.textContent = ""; return; }
  status.textContent = "Reading the new pages…";
  let job = null;
  for (let i = 0; i < 1200 && !(job && (job.status === "done" || job.status === "failed")); i++) {
    await sleep(500);
    job = ((state && state.imports) || []).find((entry) => entry.id === started.job) || null;
  }
  status.textContent = "";
  if (!job || job.status === "failed") { showMessage(job ? job.message : "Reading the PDF took too long"); return; }
  const plan = await act("deck_replace_plan", { deck_id: deckId, job: started.job });
  if (plan && deckEdit && deckEdit.id === deckId) showReplacePlan(deckId, started.job, plan);
}

async function applyReplacement() {
  const pending = replacePending;
  if (!pending) return;
  $("de-pdf-apply").disabled = true;
  const done = await act("deck_replace_apply", { deck_id: pending.deckId, job: pending.job, remove_extra: $("de-pdf-extra").checked,
    keep: Array.from(pending.keep), replace_same: $("de-pdf-same").value === "replace" });
  if (done === null) { $("de-pdf-apply").disabled = false; return; }
  replacePending = null;
  $("de-pdfpanel").hidden = true;
  const replaced = done.replaced || [];
  showNotice((replaced.length ? "Replaced " + numberRanges(replaced) : "Replaced nothing") + (done.added ? (replaced.length ? " and added " : "; added ") + done.added + " new" : "") + ".");
  if (deckEdit && deckEdit.id === pending.deckId) await refreshDeckEditor(false);
}

async function cancelReplacement() {
  const pending = replacePending;
  replacePending = null;
  $("de-pdfpanel").hidden = true;
  if (pending) await act("deck_replace_cancel", { deck_id: pending.deckId, job: pending.job });
}

/* Upload picture files for the editor; resolves to the batch to hand to the server, or null. */
async function uploadSlidePictures(files) {
  const status = $("de-status");
  const { good, problems } = checkFiles("slidepics", files);
  if (problems.length) showMessage(problems.slice(0, 3).join(". ") + (problems.length > 3 ? " (and " + (problems.length - 3) + " more)" : ""));
  if (!good.length) return null;
  const made = await act("import_batch");
  if (!made) return null;
  let done = 0;
  for (const file of good) {
    status.textContent = "Uploading " + (done + 1) + " of " + good.length + "…";
    if (!(await uploadOne(made.batch, "slidepics", file))) { status.textContent = ""; return null; }
    done += 1;
  }
  status.textContent = "";
  return made.batch;
}

async function loadFromDeck() {
  const id = Number($("de-from-deck").value);
  if (!id) { deckEdit.from = null; drawFromGrid(); return; }
  const result = await call("deck_get", { id: id });
  if (!result.ok) { showMessage(result.error); return; }
  deckEdit.from = { id: id, data: result.data, selected: [] };
  drawFromGrid();
  drawDeckEditor();
}

function drawFromGrid() {
  const grid = $("de-from-grid");
  grid.replaceChildren();
  const from = deckEdit && deckEdit.from;
  if (!from) return;
  for (const page of from.data.pages) {
    const picked = from.selected.includes(page.id);
    const card = el("li", "de-card" + (picked ? " selected" : ""));
    card.tabIndex = 0;
    card.setAttribute("role", "option");
    card.setAttribute("aria-selected", String(picked));
    card.setAttribute("aria-label", "Slide " + page.number + (picked ? ", chosen" : ""));
    const img = el("img");
    img.loading = "lazy"; img.alt = ""; img.draggable = false; img.src = mediaUrl(page.image);
    card.append(img, el("span", "de-num", String(page.number)));
    const toggle = () => {
      from.selected = picked ? from.selected.filter((pid) => pid !== page.id) : from.selected.concat(page.id);
      drawFromGrid();
      drawDeckEditor();
    };
    card.addEventListener("click", toggle);
    card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); toggle(); } });
    grid.appendChild(card);
  }
  $("de-from-add").textContent = from.selected.length ? "Add " + plural(from.selected.length, "slide") : "Add";
}

function openFromPanel() {
  const others = (manage.decks || []).filter((deck) => deck.id !== deckEdit.id);
  if (!others.length) { showMessage("There is no other slideshow to copy from yet."); return; }
  const select = $("de-from-deck");
  select.replaceChildren();
  for (const deck of others) {
    const option = el("option", "", deck.name + " (" + plural(deck.pages, "slide") + ")");
    option.value = String(deck.id);
    select.appendChild(option);
  }
  $("de-split").hidden = true;
  $("de-from").hidden = false;
  loadFromDeck();
}

function wireDeckEditor() {
  const stopRename = () => { $("de-rename-box").hidden = true; $("de-title").hidden = false; $("de-rename").hidden = false; };
  $("de-rename").addEventListener("click", () => {
    if (!deckEdit || !deckEdit.data) return;
    $("de-title").hidden = true;
    $("de-rename").hidden = true;
    $("de-rename-box").hidden = false;
    $("de-rename-input").value = deckEdit.data.name;
    $("de-rename-input").focus();
    $("de-rename-input").select();
  });
  const saveRename = async () => {
    const name = $("de-rename-input").value.trim();
    if (!name) { showMessage("Give the slideshow a name first"); return; }
    if (await act("deck_rename", { id: deckEdit.id, name: name })) { stopRename(); await refreshDeckEditor(false); loadManage(); }
  };
  $("de-rename-save").addEventListener("click", saveRename);
  $("de-rename-cancel").addEventListener("click", stopRename);
  $("de-rename-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); saveRename(); }
    if (event.key === "Escape") { event.preventDefault(); stopRename(); }
  });
  $("de-delay").addEventListener("change", applyDelay);
  $("de-trans").addEventListener("change", applyTransition);
  for (const [input, apply] of [[$("de-delay-sec"), applyDelay], [$("de-trans-sec"), applyTransition]]) {
    input.addEventListener("change", apply);      // Enter in the box also fires change
  }
  $("de-library-open").addEventListener("click", openLibraryPanel);
  $("de-library-cancel").addEventListener("click", () => { $("de-library").hidden = true; });
  $("de-library-add").addEventListener("click", async () => {
    const picked = deckEdit && deckEdit.library ? deckEdit.library.slice() : [];
    if (!picked.length) return;
    const after = afterPageFor(deckEdit.data.pages.map((p) => p.id), deckEdit.selected);
    $("de-library").hidden = true;
    await deckChange("deck_add_library", { media_ids: picked, after_page_id: after }, (result) => {
      deckEdit.selected = result.page_ids.slice();
      deckEdit.anchor = result.page_ids[0] === undefined ? null : result.page_ids[0];
    });
  });
  $("de-video-mute").addEventListener("change", () => deckChange("deck_pages_sound", { page_ids: deckEdit.selected.slice(), muted: $("de-video-mute").checked }));
  $("de-sound-select").addEventListener("change", () => {
    const value = $("de-sound-select").value;
    if (value === MIXED) return;
    deckChange("deck_pages_sound", { page_ids: deckEdit.selected.slice(), sound_id: value === "" ? null : Number(value) });
  });
  $("de-sound-loop").addEventListener("change", () => deckChange("deck_pages_sound", { page_ids: deckEdit.selected.slice(), sound_loop: $("de-sound-loop").checked }));
  $("de-add").addEventListener("click", () => $("de-add-file").click());
  $("de-add-file").addEventListener("change", async () => {
    const input = $("de-add-file");
    const files = Array.from(input.files);
    input.value = "";
    if (!deckEdit || !files.length) return;
    const batch = await uploadSlidePictures(files);
    if (!batch) return;
    const after = afterPageFor(deckEdit.data.pages.map((p) => p.id), deckEdit.selected);
    await deckChange("deck_pages_add", { batch: batch, after: after }, (result) => {
      deckEdit.selected = result.new.slice();
      deckEdit.anchor = result.new[0] === undefined ? null : result.new[0];
    });
  });
  $("de-pdf").addEventListener("click", () => $("de-pdf-file").click());
  $("de-pdf-file").addEventListener("change", async () => {
    const input = $("de-pdf-file");
    const file = input.files[0];
    input.value = "";
    await chooseReplacementPdf(file);
  });
  $("de-pdf-apply").addEventListener("click", applyReplacement);
  $("de-pdf-change-all").addEventListener("click", () => { if (replacePending) { replacePending.keep.clear(); drawReplaceList(); } });
  $("de-pdf-keep-all").addEventListener("click", () => {
    if (replacePending) { replacePending.keep = new Set(replacePending.plan.changed); drawReplaceList(); }
  });
  $("de-pdf-cancel").addEventListener("click", cancelReplacement);
  $("de-replace").addEventListener("click", () => $("de-replace-file").click());
  $("de-replace-file").addEventListener("change", async () => {
    const input = $("de-replace-file");
    const files = Array.from(input.files);
    input.value = "";
    if (!deckEdit || !files.length || deckEdit.selected.length !== 1) return;
    const page = deckEdit.selected[0];
    const batch = await uploadSlidePictures(files.slice(0, 1));
    if (batch) await deckChange("deck_page_replace", { page: page, batch: batch });
  });
  $("de-from-open").addEventListener("click", openFromPanel);
  $("de-from-deck").addEventListener("change", loadFromDeck);
  $("de-from-cancel").addEventListener("click", () => { $("de-from").hidden = true; deckEdit.from = null; });
  $("de-from-all").addEventListener("click", () => {
    if (!deckEdit.from) return;
    deckEdit.from.selected = deckEdit.from.data.pages.map((p) => p.id);
    drawFromGrid();
    drawDeckEditor();
  });
  $("de-from-add").addEventListener("click", async () => {
    const from = deckEdit.from;
    if (!from || !from.selected.length) return;
    const after = afterPageFor(deckEdit.data.pages.map((p) => p.id), deckEdit.selected);
    const done = await deckChange("deck_pages_from", { source: from.id, pages: from.selected.slice(), after: after }, (result) => {
      deckEdit.selected = result.new.slice();
      deckEdit.anchor = result.new[0] === undefined ? null : result.new[0];
    });
    if (done !== null) { $("de-from").hidden = true; deckEdit.from = null; }
  });
  $("de-split-open").addEventListener("click", () => {
    $("de-from").hidden = true;
    $("de-split").hidden = false;
    $("de-split-name").value = splitName(deckEdit.data.name);
    $("de-split-name").focus();
    $("de-split-name").select();
  });
  $("de-split-cancel").addEventListener("click", () => { $("de-split").hidden = true; });
  document.querySelector("#view-deckedit .de-main").addEventListener("click", (event) => {     // empty space anywhere in the Slides box deselects everything
    if (event.target.closest("button, summary, details, input, select, textarea, label, a, .de-card, .de-panel")) return;
    if (!deckEdit || !deckEdit.selected.length) return;
    deckEdit.selected = [];
    deckEdit.anchor = null;
    drawDeckEditor();
  });
  $("de-tomedia").addEventListener("click", async () => {
    const result = await act("deck_pages_to_media", { id: deckEdit.id, pages: deckEdit.selected.slice() });
    if (!result) return;
    const added = (result.added || []).length;
    showNotice("Added " + plural(added, "picture") + " to the Media Library" + (result.skipped ? ". " + plural(result.skipped, "video slide") + " skipped: already Media Library files" : "") + ".");
    loadManage();
  });
  for (const menu of document.querySelectorAll("#view-deckedit .de-menu")) {          // close a menu after a choice, outside clicks and Esc
    menu.addEventListener("click", (event) => { if (event.target.closest(".de-menu-list button")) menu.open = false; });
    menu.addEventListener("keydown", (event) => { if (event.key === "Escape" && menu.open) { event.stopPropagation(); menu.open = false; menu.querySelector("summary").focus(); } });
  }
  document.addEventListener("click", (event) => {
    for (const menu of document.querySelectorAll("#view-deckedit .de-menu[open]")) if (!menu.contains(event.target)) menu.open = false;
  });
  const createSplit = async () => {
    const name = $("de-split-name").value;
    if (!name.trim()) { showMessage("Give the new slideshow a name first"); return; }
    const made = await act("deck_split", { id: deckEdit.id, pages: deckEdit.selected.slice(), name: name });
    if (!made) return;
    $("de-split").hidden = true;
    showNotice("Made the slideshow " + name.trim() + " with " + plural(deckEdit.selected.length, "slide") + ". Find it in the Slideshow Library.");
    loadManage();
  };
  $("de-split-create").addEventListener("click", createSplit);
  $("de-split-name").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); createSplit(); }
    if (event.key === "Escape") { event.preventDefault(); $("de-split").hidden = true; }
  });
  $("de-back").addEventListener("click", () => showView("decks"));
  $("de-left").addEventListener("click", () => {
    const d = deckEdit.data;
    deckChange("deck_pages_reorder", { order: movePages(d.pages.map((p) => p.id), deckEdit.selected, -1) });
  });
  $("de-right").addEventListener("click", () => {
    const d = deckEdit.data;
    deckChange("deck_pages_reorder", { order: movePages(d.pages.map((p) => p.id), deckEdit.selected, 1) });
  });
  $("de-duplicate").addEventListener("click", () => {
    deckChange("deck_pages_duplicate", { pages: deckEdit.selected.slice() }, (result) => {
      deckEdit.selected = result.new.slice();
      deckEdit.anchor = result.new[0] === undefined ? null : result.new[0];
    });
  });
  $("de-skip").addEventListener("click", () => {
    const picked = deckEdit.data.pages.filter((p) => deckEdit.selected.includes(p.id));
    deckChange("deck_pages_skip", { pages: deckEdit.selected.slice(), skipped: !picked.every((p) => p.skipped) });
  });
  confirmClick($("de-delete"), () => {
    const gone = deckEdit.selected.slice();
    const order = deckEdit.data.pages.map((p) => p.id);
    const next = order.find((pid, i) => !gone.includes(pid) && i > order.indexOf(gone[gone.length - 1]))
      || order.slice().reverse().find((pid) => !gone.includes(pid));
    deckChange("deck_pages_delete", { pages: gone }, () => { deckEdit.selected = next === undefined ? [] : [next]; deckEdit.anchor = next === undefined ? null : next; });
  }, () => { const n = deckEdit.selected.length; return "Delete " + (n === 1 ? "the selected slide" : "the " + n + " selected slides") + " from this slideshow?"; });
  $("de-all").addEventListener("click", () => {
    deckEdit.selected = deckEdit.data.pages.map((p) => p.id);
    deckEdit.anchor = deckEdit.selected[0] === undefined ? null : deckEdit.selected[0];
    drawDeckEditor();
  });
  $("de-slide-title").addEventListener("blur", saveDeckTitle);
  $("de-slide-title").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); saveDeckTitle(); }
  });
  $("de-notes-save").addEventListener("click", saveDeckNotes);
  $("de-notes").addEventListener("blur", saveDeckNotes);
  $("de-notes").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) { event.preventDefault(); saveDeckNotes(); }
    if (event.key === "Escape") { event.preventDefault(); event.target.blur(); }
  });
  document.addEventListener("keydown", (event) => {
    if (currentView !== "deckedit" || event.ctrlKey || event.metaKey || event.altKey) return;
    const tag = event.target && event.target.tagName;
    if (event.key === "Escape" && tag !== "INPUT" && tag !== "TEXTAREA" && tag !== "SELECT") { event.preventDefault(); showView("decks"); }
  });
}

let shownDefaultBible;
function drawBibleList() {
  const list = $("bible-list");
  list.replaceChildren();
  if (!manage.translations.length) { list.appendChild(el("li", "muted", "No Bible installed yet. Add one above to show verses.")); return; }
  for (const bible of manage.translations) {
    const row = el("li", "row");
    row.appendChild(titleBlock("", bible.abbreviation + (bible.name && bible.name !== bible.abbreviation ? " — " + bible.name : ""),
      [bible.verses + " verses", usesText(bible.uses)].filter(Boolean).join(" · ")));
    const del = button("Remove", null, "danger", "Remove the Bible " + bible.abbreviation);
    if (bible.uses) { del.disabled = true; del.title = "Take it out of the services that use it first"; }
    else confirmClick(del, () => act("bible_delete", { abbreviation: bible.abbreviation }).then(() => loadManage()),
      "Remove the Bible " + bible.abbreviation + " (" + bible.verses + " verses)? You can add it again from its file.", "Yes, remove");
    const inUse = state && state.translation === bible.abbreviation;
    const use = button(inUse ? "Default" : "Use for verses", inUse ? null : () => act("set_translation", { abbreviation: bible.abbreviation }).then(() => loadManage()),
      "", inUse ? "This Bible is used for typed verses and the tray" : "Use " + bible.abbreviation + " for typed verses and the tray");
    use.disabled = inUse;
    row.appendChild(use);
    row.appendChild(del);
    list.appendChild(row);
  }
}

function drawJobs(current) {
  if (typeof wizardJobsChanged === "function") wizardJobsChanged();       // the setup guide's own progress lines
  const text = JSON.stringify([current.can_import, current.imports || []]);
  for (const off of document.querySelectorAll(".import-off")) off.hidden = !!current.can_import;
  for (const box of document.querySelectorAll(".import-controls")) box.hidden = !current.can_import;
  if (text === lastJobs) return;                // do not rebuild (and close open details) for nothing
  lastJobs = text;
  for (const list of document.querySelectorAll(".import-jobs")) {
    list.replaceChildren();
    const jobs = (current.imports || []).filter((job) => job.kind === list.dataset.kind);
    if (!jobs.length) { list.appendChild(el("li", "muted", "Nothing imported yet")); continue; }
    for (const job of jobs) {
      const row = el("li", "row job");
      const head = el("div", "head");
      head.appendChild(el("span", "state " + job.status, jobStatusText(job.status)));
      head.appendChild(el("strong", "", job.label));
      if (job.message) head.appendChild(el("span", "muted", job.message));
      row.appendChild(head);
      if (job.notes && job.notes.length) {
        const details = el("details");
        details.appendChild(el("summary", "", "Details (" + job.notes.length + ")"));
        details.appendChild(el("pre", "", job.notes.join("\n")));
        row.appendChild(details);
      }
      list.appendChild(row);
    }
  }
}

/* ---- the song editor ----
   A window over the right edge of the page, not a blocking popup: Live and the service order stay clickable
   behind it, it can shrink to a bar, and what is typed is kept in this browser until it is saved or discarded. */
const SONG_BUFFER_KEY = "churchlight.songbuffer";
let editingSongId = null;
let editorBase = "";
let checkTimer = null;
let checkCounter = 0;

function editorSnapshot() {
  return JSON.stringify([$("song-title").value, $("song-author").value, $("song-ccli").value, $("song-text").value, $("song-order").value, $("song-theme").value, $("song-copyright").value, $("song-notice").value, $("song-opname").value]);
}

function saveBuffer() {
  try {
    localStorage.setItem(SONG_BUFFER_KEY, JSON.stringify({
      id: editingSongId, title: $("song-title").value, author: $("song-author").value, ccli: $("song-ccli").value,
      text: $("song-text").value, order: $("song-order").value, theme: $("song-theme").value,
      copyright: $("song-copyright").value, notice: $("song-notice").value, opname: $("song-opname").value, base: editorBase }));
  } catch (err) { /* only a safety net */ }
}

function clearBuffer() { try { localStorage.removeItem(SONG_BUFFER_KEY); } catch (err) { /* nothing stored */ } }

function readBuffer() {
  try { return parseSongBuffer(localStorage.getItem(SONG_BUFFER_KEY)); } catch (err) { return null; }
}

function editorDirty() { return editorSnapshot() !== editorBase; }

function showEditorBar(show) {
  $("editor-bar").hidden = !show;
  if (show) $("editor-bar-text").textContent = ($("song-title").value.trim() || "Untitled song") + (editingSongId === null ? " (unsaved)" : " (editing)");
}

async function openEditor(id) {
  if (!$("editor").hidden || !$("editor-bar").hidden) {
    // one song at a time: bring the open one forward rather than losing it
    resumeEditor();
    showMessage("Finish or discard the song you are already writing first");
    return;
  }
  let info = { title: "", author: "", ccli: "", text: "", order: "", draft: false };
  if (id !== null) {
    info = await act("song_get", { id: id });
    if (!info) return;
  }
  fillEditor(id, info, null);
}

/* The song's own projector theme, or "follow the service". A theme deleted meanwhile just shows as following. */
function fillSongTheme(chosen) {
  fillThemeSelect($("song-theme"), "Follow the service's theme", chosen, "projector");
}

function fillEditor(id, info, base) {
  editingSongId = id;
  $("h-editor").textContent = id === null ? "New song" : (info.draft ? "Edit draft" : "Edit song");
  $("editor-state").textContent = info.draft ? "Draft. Save publishes it." : "";
  $("song-title").value = info.title;
  $("song-author").value = info.author;
  $("song-ccli").value = info.ccli || "";
  $("song-copyright").value = info.copyright || "";
  $("song-opname").value = info.operator_name || "";
  $("song-notice").value = info.notice || "";
  $("song-text").value = info.text;
  $("song-order").value = info.order || "";
  fillSongTheme(info.theme_id === undefined ? null : info.theme_id);
  editorBase = base === null ? editorSnapshot() : base;
  $("editor").hidden = false;
  placeEditor();
  showEditorBar(false);
  checkSong();
  $("song-title").focus();
  saveBuffer();
}

/* On a wide window the editor sits exactly over the last column (Screens by default), so Live and the service
   order stay clickable beside it. On a narrow window the style sheet makes it fill the width. */
function placeEditor() {
  const box = $("editor");
  const columns = document.querySelectorAll("#view-run > .board-col");
  const last = columns.length ? columns[columns.length - 1] : null;
  if (!last || window.innerWidth <= 960 || $("view-run").hidden) {
    box.style.left = ""; box.style.right = ""; box.style.width = "";
    return;
  }
  const rect = last.getBoundingClientRect();
  const width = Math.max(rect.width, 352);
  box.style.width = width + "px";
  box.style.left = Math.max(8, rect.right - width) + "px";
  box.style.right = "auto";
}

function resumeEditor() {
  $("editor").hidden = false;
  placeEditor();
  showEditorBar(false);
  $("song-title").focus();
}

function minimizeEditor() {
  $("editor").hidden = true;
  showEditorBar(true);
}

function closeEditor() {
  $("editor").hidden = true;
  showEditorBar(false);
  editingSongId = null;
  clearBuffer();
}

function restoreEditorFromBuffer() {
  const saved = readBuffer();
  if (!saved) return;
  editingSongId = saved.id;
  $("song-title").value = saved.title;
  $("song-author").value = saved.author;
  $("song-ccli").value = saved.ccli;
  $("song-copyright").value = saved.copyright || "";
  $("song-opname").value = saved.opname || "";
  $("song-notice").value = saved.notice || "";
  $("song-text").value = saved.text;
  $("song-order").value = saved.order;
  fillSongTheme(saved.theme ? Number(saved.theme) : null);
  editorBase = saved.base;
  $("h-editor").textContent = saved.id === null ? "New song" : "Edit song";
  $("editor").hidden = true;
  showEditorBar(true);
  checkSong();
}

let cancelTimer = null;
function askToClose() {
  const button = $("song-cancel");
  if (!editorDirty() || cancelTimer !== null) {
    clearTimeout(cancelTimer);
    cancelTimer = null;
    button.textContent = "Cancel";
    closeEditor();
    return;
  }
  button.textContent = "Discard changes?";
  button.classList.add("confirm");
  cancelTimer = setTimeout(() => { cancelTimer = null; button.textContent = "Cancel"; button.classList.remove("confirm"); }, 4000);
}

function drawPartChips() {
  const holder = $("song-parts");
  holder.replaceChildren();
  const names = partNames($("song-text").value);
  holder.hidden = !names.length;
  if (!names.length) return;
  holder.appendChild(el("span", "muted", "Click a part to add it to the order:"));
  for (const name of names) {
    const chip = el("button", "chip", name);
    chip.type = "button";
    chip.addEventListener("click", () => {
      $("song-order").value = appendToOrder($("song-order").value, name);
      saveBuffer();
      checkSong();
    });
    holder.appendChild(chip);
  }
}

async function checkSong() {
  const mine = ++checkCounter;
  const box = $("song-check");
  const text = $("song-text").value;
  drawPartChips();
  if (!text.trim()) { box.textContent = ""; box.className = "check"; return; }
  const result = await call("song_check", { text: text, order: $("song-order").value });
  if (mine !== checkCounter) return;
  if (result.ok && result.data.valid) {
    const parts = result.data.sections;
    box.className = "check good";
    box.textContent = parts.length + " part" + (parts.length === 1 ? "" : "s") + ": " + parts.map((p) => p.label).join(", ") +
      ". Sung in this order: " + result.data.order.join(" → ");
  } else {
    box.className = "check bad";
    box.textContent = result.ok ? result.data.error : result.error;
  }
}

async function saveSong(mode) {
  const args = { title: $("song-title").value, author: $("song-author").value, ccli: $("song-ccli").value.trim(),
    text: $("song-text").value, order: $("song-order").value,
    copyright: $("song-copyright").value, notice: $("song-notice").value, operator_name: $("song-opname").value,
    theme_id: $("song-theme").value ? Number($("song-theme").value) : null };
  if (editingSongId !== null) args.id = editingSongId;
  if (!args.title.trim()) { showMessage("Give the song a title first"); $("song-title").focus(); return; }
  args.draft = mode === "draft";
  const saved = await act("song_save", args);
  if (!saved) return;
  let note = "";
  if (mode === "add") {
    const added = await act("add", { kind: "song", id: saved.id, position: addPosition(state ? state.items : []) });
    note = added ? "" : "The song was saved, but it could not be added to a service.";
  }
  closeEditor();
  loadManage();
  if (note) showMessage(note);
}

/* ---- video and sound ---- */
function clipSeconds(seconds) {
  const whole = Math.max(0, Math.round(seconds || 0));
  const minutes = Math.floor(whole / 60);
  return minutes + ":" + String(whole % 60).padStart(2, "0");
}

/* Where a clip is now: where it was when it last changed, plus the time since if it is playing. `skew` is the program's
   clock minus this computer's, so the two computers need not agree on the time. */
function clipNow(view, skew) {
  let position = view.pos + (view.playing ? Date.now() / 1000 + skew - view.at : 0);
  if (view.duration > 0) position = view.loop ? position % view.duration : Math.min(position, view.duration);
  return Math.max(0, position);
}

let clipSkew = 0;
let clipView = null;
let seeking = false;

function drawClipBlock(prefix, view) {
  const block = $(prefix === "clip" ? "clip-live" : "clip-music");
  block.hidden = !view;
  if (!view) return;
  if (prefix === "clip") {
    const problem = $("clip-problem");
    problem.textContent = (clipView && clipView.problem) || "";
    problem.hidden = !problem.textContent;
  }
  $(prefix === "clip" ? "clip-live-title" : "clip-music-title").textContent =
    (prefix === "clip" ? (view.kind === "audio" ? "Sound playing now: " : "Video playing now: ") : "Music under the slides: ") + (view.name || "");
  const ended = view.duration > 0 && !view.loop && !view.playing && view.pos >= view.duration - 0.05;
  $(prefix === "clip" ? "clip-play" : "music-play").textContent = view.playing ? "Pause" : (ended ? "Play again" : "Play");
  $(prefix + "-mute").textContent = view.muted ? "Sound is off (turn on)" : "Mute";
  $(prefix + "-mute").setAttribute("aria-pressed", String(!!view.muted));
  const volume = $(prefix + "-volume");
  if (document.activeElement !== volume) volume.value = String(Math.round(view.volume * 100));
  $(prefix + "-loop").checked = !!view.loop;
  tickClip(prefix, view);
}

function drawPageSound(view) {
  $("clip-page").hidden = !view;
  if (!view) return;
  $("clip-page-title").textContent = "Sound with this slide: " + (view.name || "");
  const ended = view.duration > 0 && !view.loop && !view.playing && view.pos >= view.duration - 0.05;
  $("page-play").textContent = view.playing ? "Pause" : (ended ? "Play again" : "Play");
  $("page-mute").textContent = view.muted ? "Sound is off (turn on)" : "Mute";
  $("page-mute").setAttribute("aria-pressed", String(!!view.muted));
  if (document.activeElement !== $("page-volume")) $("page-volume").value = String(Math.round(view.volume * 100));
  $("page-loop").checked = !!view.loop;
  tickPageSound(view);
}

function tickPageSound(view) {
  if (!view) return;
  const position = clipNow(view, clipSkew);
  $("page-time").textContent = view.duration > 0 ? clipSeconds(position) + " / " + clipSeconds(view.duration) : clipSeconds(position);
}

function tickClip(prefix, view) {
  if (!view) return;
  const position = clipNow(view, clipSkew);
  const seek = $(prefix + "-seek");
  seek.disabled = !(view.duration > 0);
  if (!seeking && document.activeElement !== seek) seek.value = view.duration > 0 ? String(Math.round(position / view.duration * 1000)) : "0";
  $(prefix + "-time").textContent = view.duration > 0 ? clipSeconds(position) + " / " + clipSeconds(view.duration) : clipSeconds(position);
}

function drawClips() {
  const clips = (state && state.clips) || { live: null, music: null };
  const timed = clips.live || clips.music || clips.page;
  if (timed) clipSkew = timed.now - Date.now() / 1000;
  clipView = clips;
  $("clip-panel").hidden = !(clips.live || clips.music || clips.page);
  drawClipBlock("clip", clips.live);
  drawClipBlock("music", clips.music);
  drawPageSound(clips.page);
  $("clip-advance").checked = !!clips.advance;
  const where = clips.sound_output;
  syncHearFrame(where);
  $("clip-where").textContent = !where ? "" : !hearOn() ? "This window is not playing the sound (Settings → Sound)."
    : hearLock === "held" ? "The sound plays from this ChurchLight window, on this computer. To put it on the stream, add “Desktop Audio” in OBS (or the system sound in XSplit)."
    : "Another ChurchLight window on this computer is playing the sound, so this one stays quiet.";
}

/* The sound is made by ChurchLight itself: a hidden copy of the sound screen lives in this page (see output.js, ?hear). */
const HEAR_KEY = "churchlight-hear";
function hearOn() {
  try { return localStorage.getItem(HEAR_KEY) !== "off"; } catch (err) { return true; }
}

/* Only one ChurchLight page on this computer may play the sound, or a second operator window would make it twice.
   The page that gets the lock plays; the others stay quiet until it closes or its box is unticked. */
let hearLock = "no";               // "no", "asking" or "held"
let hearRelease = null;
function takeHearLock() {
  if (hearLock !== "no") return;
  if (!navigator.locks || !navigator.locks.request) { hearLock = "held"; return; }
  hearLock = "asking";
  navigator.locks.request("churchlight-hear", { ifAvailable: true }, (lock) => {
    if (!lock) { hearLock = "no"; return undefined; }
    hearLock = "held";
    syncHearFrame(clipView && clipView.sound_output);
    return new Promise((resolve) => { hearRelease = resolve; });
  }).catch(() => { hearLock = "no"; });
}

function dropHearLock() {
  if (hearRelease) { hearRelease(); hearRelease = null; }
  hearLock = "no";
}

/* A browser allows six connections to one address, and the page already holds several (its live updates, two previews) plus
   the sound frame's own two (updates and the video file). Seeking in a long video then had to wait for a free one and froze.
   "localhost" and "127.0.0.1" are the same computer but count as different addresses, so the sound frame uses the one this
   page is not on, and gets six connections of its own. Any other address (another computer's) is left as it is. */
function hearAddress(name) {
  const other = { "127.0.0.1": "localhost", "localhost": "127.0.0.1" }[location.hostname];
  const origin = other ? location.protocol + "//" + other + (location.port ? ":" + location.port : "") : "";
  return origin + "/view/" + encodeURIComponent(name) + "?hear=1";
}

function syncHearFrame(name) {
  let frame = document.getElementById("hear-frame");
  const wanted = name && hearOn();
  if (wanted) takeHearLock(); else dropHearLock();
  if (!wanted || hearLock !== "held") { if (frame) frame.remove(); return; }
  const address = hearAddress(name);
  if (frame && frame.getAttribute("src") === address) return;
  if (frame) frame.remove();
  frame = document.createElement("iframe");
  frame.id = "hear-frame";
  frame.setAttribute("src", address);
  frame.setAttribute("allow", "autoplay");
  frame.setAttribute("aria-hidden", "true");
  frame.tabIndex = -1;
  frame.title = "Sound";
  frame.className = "hear-frame";
  document.body.appendChild(frame);
}

function wireHear() {
  const box = $("hear-on");
  box.checked = hearOn();
  box.addEventListener("change", () => {
    try { localStorage.setItem(HEAR_KEY, box.checked ? "on" : "off"); } catch (err) { /* only a convenience */ }
    drawClips();
  });
}

function wireClips() {
  const send = (action, args) => act(action, args || {});
  $("clip-play").addEventListener("click", () => send(clipView.live.playing ? "media_pause" : "media_play"));
  $("clip-restart").addEventListener("click", () => send("media_restart"));
  $("clip-mute").addEventListener("click", () => send("media_mute", { on: !clipView.live.muted }));
  $("clip-loop").addEventListener("change", () => send("media_loop", { on: $("clip-loop").checked }));
  $("clip-advance").addEventListener("change", () => send("media_advance", { on: $("clip-advance").checked }));
  $("page-play").addEventListener("click", () => send(clipView.page.playing ? "page_sound_pause" : "page_sound_resume"));
  $("page-restart").addEventListener("click", () => send("page_sound_restart"));
  $("page-loop").addEventListener("change", () => send("page_sound_loop", { on: $("page-loop").checked }));
  $("page-mute").addEventListener("click", () => send("media_mute", { on: !clipView.page.muted }));
  $("page-volume").addEventListener("change", () => send("media_volume", { volume: Number($("page-volume").value) / 100 }));
  $("music-play").addEventListener("click", () => send(clipView.music.playing ? "music_pause" : "music_resume"));
  $("music-restart").addEventListener("click", () => send("music_restart"));
  $("music-stop").addEventListener("click", () => send("music_stop"));
  $("music-mute").addEventListener("click", () => send("music_mute", { on: !clipView.music.muted }));
  $("music-loop").addEventListener("change", () => send("music_loop", { on: $("music-loop").checked }));
  for (const [prefix, volumeAction, seekAction, which] of [["clip", "media_volume", "media_seek", "live"], ["music", "music_volume", "music_seek", "music"]]) {
    $(prefix + "-volume").addEventListener("change", () => send(volumeAction, { volume: Number($(prefix + "-volume").value) / 100 }));
    const seek = $(prefix + "-seek");
    seek.addEventListener("input", () => { seeking = true; });
    seek.addEventListener("change", () => {
      seeking = false;
      const view = clipView && clipView[which];
      if (view && view.duration > 0) send(seekAction, { seconds: Number(seek.value) / 1000 * view.duration });
    });
  }
  setInterval(() => {                                       // the position moves on without asking the program
    if (!clipView) return;
    for (const [prefix, which] of [["clip", "live"], ["music", "music"]]) {
      const view = clipView[which];
      if (view && view.playing) tickClip(prefix, view);
    }
    if (clipView.page && clipView.page.playing) tickPageSound(clipView.page);
  }, 500);
}

function isPicture(name) { return [".png", ".jpg", ".jpeg", ".gif", ".webp"].includes(extensionOf(name)); }

/* How long a file is, read in this page before it is uploaded (null when the browser cannot tell). */
function readDuration(file) {
  return new Promise((resolve) => {
    const node = document.createElement(file.name.toLowerCase().endsWith(".mp4") || file.name.toLowerCase().endsWith(".m4v") ? "video" : "audio");
    const url = URL.createObjectURL(file);
    const finish = (seconds) => { clearTimeout(timer); node.removeAttribute("src"); node.load(); URL.revokeObjectURL(url); resolve(seconds); };
    const timer = setTimeout(() => finish(null), 15000);
    node.preload = "metadata";
    node.addEventListener("loadedmetadata", () => finish(Number.isFinite(node.duration) ? node.duration : null));
    node.addEventListener("error", () => finish(null));
    node.src = url;
  });
}

/* One upload at a time. A browser keeps only six connections to the program, and the page already holds several for its
   live updates and previews; two big uploads at once could use them all up and make the whole page stop answering. */
let importsWaiting = 0;
let importChain = Promise.resolve();
function inImportQueue(status, work) {
  importsWaiting += 1;
  const run = importChain.then(() => work());
  const done = () => { importsWaiting -= 1; };
  importChain = run.then(done, done);
  return run;
}

function importMedia(files, statusNode) {
  return inImportQueue(statusNode || $("import-status-media"), () => importMediaNow(files, statusNode));
}

async function importMediaNow(files, statusNode) {
  const checked = checkFiles("media", files);
  const problems = checked.problems.slice();
  const good = checked.good.filter((file) => {
    if (isPicture(file.name) && file.size > 25 * MB) { problems.push(file.name + " is too large (over 25 MB)"); return false; }
    return true;
  });
  if (problems.length) showMessage(problems.join(". "));
  const status = statusNode || $("import-status-media");
  for (const file of good) {
    status.textContent = "Reading " + file.name + "…";
    const seconds = isPicture(file.name) ? 0 : await readDuration(file);
    const made = await act("import_batch");
    if (!made) { status.textContent = ""; return; }
    status.textContent = "Uploading " + file.name + "…";
    if (!(await uploadOne(made.batch, "media", file, (percent) => { status.textContent = "Uploading " + file.name + "… " + percent + "%"; }))) { status.textContent = ""; return; }
    const added = await act("media_add", { batch: made.batch, duration: seconds || 0 });
    if (added && added.warning) showMessage(added.warning);
    status.textContent = added ? "Added " + file.name + "." + (seconds || isPicture(file.name) ? "" : " Its length could not be read, so it cannot move on by itself when it ends.") : "";
    if (status.logLine && status.textContent) { status.logLine(status.textContent); status.textContent = ""; }        // the setup guide keeps one line per file
  }
  loadManage();
}

const MEDIA_BADGES = { image: "picture", video: "video", audio: "sound" };

function mediaThumb(media) {
  if (media.kind === "image") {
    const img = el("img", "media-thumb");
    img.loading = "lazy";
    img.alt = "";
    img.src = mediaUrl(media.path);
    return img;
  }
  return el("span", "media-thumb media-icon", media.kind === "video" ? "▶" : "♪");
}

/* The Media Library page: the list on the left picks a file; the preview on the right shows it and has its buttons.
   mediaPick is {type: "media"|"deck"|"theme"|"home", id}; null when nothing is chosen. */
let mediaPick = null;
let mediaPreviewKey = "";

function mediaPickKey(pick) { return pick ? pick.type + ":" + (pick.id === undefined ? "" : pick.id) : ""; }

/* One row of either list: a single button holding the picture and the words, so the whole row can be pressed. */
function mediaPickRow(pick, thumb, titled) {
  const row = el("li", "row media-row");
  const choose = el("button", "media-pick");
  choose.type = "button";
  const selected = mediaPickKey(mediaPick) === mediaPickKey(pick);
  choose.setAttribute("aria-pressed", String(selected));
  choose.classList.toggle("selected", selected);
  choose.appendChild(thumb);
  choose.appendChild(titled);
  choose.addEventListener("click", () => { mediaPick = pick; renamingMedia = null; drawMediaList(); drawMediaSources(); drawMediaPreview(); });
  row.appendChild(choose);
  return row;
}

function drawMediaList() {
  const list = $("media-list");
  list.replaceChildren();
  const filter = $("media-filter").value;
  const rows = (manage.media || []).filter((m) => filter === "all" || m.kind === filter);
  if (!rows.length) { list.appendChild(el("li", "muted", (manage.media || []).length ? "Nothing of that kind" : "Nothing added yet")); return; }
  for (const media of rows) {
    const detail = [media.kind === "image" ? "" : (media.seconds ? clipSeconds(media.seconds) : "length unknown"),
      Math.max(1, Math.round(media.size / MB)) + " MB", usesText(media.uses),
      media.decks ? "in " + plural(media.decks, "slideshow") : ""].filter(Boolean).join(" · ");
    const titled = titleBlock(MEDIA_BADGES[media.kind], media.name, detail);
    if (media.flag) {
      const bad = media.flag.level === "problem";
      const flag = el("span", "media-flag " + (bad ? "problem" : "warning"), bad ? "⚠ Will not play well" : "⚠ May stutter");
      flag.title = media.flag.text;
      titled.appendChild(flag);
    }
    list.appendChild(mediaPickRow({ type: "media", id: media.id }, mediaThumb(media), titled));
  }
}

function mediaPicture(path) {
  const img = el("img", "media-thumb");
  img.loading = "lazy";
  img.alt = "";
  img.src = mediaUrl(path);
  return img;
}

/* The pictures that did not come through the Media Library: shown so everything is in one place, managed where it came from. */
function drawMediaSources() {
  const list = $("media-sources");
  list.replaceChildren();
  const src = manage.media_sources || { decks: [], themes: [], home: null };
  const count = src.decks.length + src.themes.length + (src.home ? 1 : 0);
  $("media-sources-count").textContent = count ? "(" + count + ")" : "(none)";
  if (!count) { list.appendChild(el("li", "muted", "None yet. Slideshows, themes and the home picture that use pictures will be listed here.")); return; }
  for (const deck of src.decks) {
    const strip = el("span", "media-strip");
    for (const path of deck.pictures) strip.appendChild(mediaPicture(path));
    list.appendChild(mediaPickRow({ type: "deck", id: deck.id }, strip, titleBlock("slideshow", deck.name, plural(deck.pages, "slide"))));
  }
  for (const theme of src.themes) {
    list.appendChild(mediaPickRow({ type: "theme", id: theme.path }, mediaPicture(theme.path), titleBlock("theme", theme.name, "background picture")));
  }
  if (src.home) {
    list.appendChild(mediaPickRow({ type: "home" }, mediaPicture(src.home), titleBlock("home", "Home screen picture", "shown when the screens are blanked")));
  }
}

/* Stop a preview player from holding a connection to the program once it is no longer shown. */
function releasePreviewPlayers(box) {
  for (const player of box.querySelectorAll("video, audio")) { player.pause(); player.removeAttribute("src"); player.load(); }
}

function mediaFacts(media) {
  const rows = [["Kind", MEDIA_BADGES[media.kind]]];
  if (media.kind !== "image") rows.push(["Length", media.seconds ? clipSeconds(media.seconds) : "unknown"]);
  if (media.spec) rows.push(["Details", media.spec]);
  rows.push(["Size", Math.max(1, Math.round(media.size / MB)) + " MB"]);
  rows.push(["Used", [usesText(media.uses), media.decks ? "in " + plural(media.decks, "slideshow") : ""].filter(Boolean).join(" · ") || "not used yet"]);
  const list = el("dl", "media-facts");
  for (const [name, value] of rows) { list.appendChild(el("dt", "", name)); list.appendChild(el("dd", "", value)); }
  return list;
}

function drawMediaPreview() {
  const box = $("media-preview");
  const found = mediaPick && mediaPick.type === "media" ? (manage.media || []).find((m) => m.id === mediaPick.id) : null;
  const src = manage.media_sources || { decks: [], themes: [], home: null };
  const deck = mediaPick && mediaPick.type === "deck" ? src.decks.find((d) => d.id === mediaPick.id) : null;
  const theme = mediaPick && mediaPick.type === "theme" ? src.themes.find((t) => t.path === mediaPick.id) : null;
  const home = mediaPick && mediaPick.type === "home" ? src.home : null;
  const chosen = found || deck || theme || home;
  // Nothing about the chosen file changed (the page refreshes now and then): leave a playing preview alone.
  const signature = mediaPickKey(mediaPick) + "|" + JSON.stringify(chosen || null) + "|" + renamingMedia + "|" + !!(state && state.service) + "|" + (found && found.kind === "audio");
  if (signature === mediaPreviewKey) return;
  mediaPreviewKey = signature;
  releasePreviewPlayers(box);
  box.replaceChildren();
  if (!chosen) {
    if (mediaPick) mediaPick = null;
    box.appendChild(el("p", "muted", "Choose a file on the left to see it here and to use it."));
    return;
  }
  if (deck) {
    box.appendChild(el("h3", "media-preview-name", deck.name));
    box.appendChild(el("p", "muted", plural(deck.pages, "slide") + " in this slideshow. Its pictures are managed in the slideshow."));
    const strip = el("div", "media-preview-strip");
    for (const path of deck.pictures) strip.appendChild(mediaPicture(path));
    box.appendChild(strip);
    box.appendChild(button("Open slideshow", () => openDeckEditor(deck.id), "primary", "Open the slideshow " + deck.name));
    return;
  }
  if (theme || home) {
    box.appendChild(el("h3", "media-preview-name", theme ? theme.name : "Home screen picture"));
    const img = el("img", "media-preview-image");
    img.alt = "";
    img.src = mediaUrl(theme ? theme.path : home);
    box.appendChild(img);
    box.appendChild(el("p", "muted", theme ? "A theme's background picture. Change it on the Theme Library tab." : "Shown when the screens are blanked. Change it in Settings."));
    return;
  }
  const media = found;
  if (renamingMedia === media.id) {
    const input = el("input");
    input.type = "text";
    input.maxLength = 200;
    input.value = media.name;
    input.setAttribute("aria-label", "New name for " + media.name);
    const save = async () => { if (await act("media_rename", { id: media.id, name: input.value })) { renamingMedia = null; await loadManage(); } };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); save(); }
      if (event.key === "Escape") { renamingMedia = null; drawMediaPreview(); }
    });
    const edit = el("div", "inline");
    edit.appendChild(input);
    edit.appendChild(button("Save", save, "primary"));
    edit.appendChild(button("Cancel", () => { renamingMedia = null; drawMediaPreview(); }));
    box.appendChild(edit);
    setTimeout(() => input.focus(), 0);
  } else {
    box.appendChild(el("h3", "media-preview-name", media.name));
  }
  if (media.kind === "image") {
    const img = el("img", "media-preview-image");
    img.alt = media.name;
    img.src = mediaUrl(media.path);
    box.appendChild(img);
  } else {
    const player = el(media.kind === "video" ? "video" : "audio", "media-preview-player");
    player.controls = true;
    player.preload = "metadata";
    player.src = mediaUrl(media.path);
    box.appendChild(player);
    box.appendChild(el("p", "muted", "This preview plays on this computer only; nothing goes to the screens until you press a button below."));
  }
  if (media.flag) box.appendChild(el("p", "media-flag-text", (media.flag.level === "problem" ? "⚠ Will not play well: " : "⚠ May stutter: ") + media.flag.text));
  box.appendChild(mediaFacts(media));
  const options = el("div", "buttons media-options");
  const add = button("Add to service", () => act("add", { kind: "media", id: media.id }), "primary", "Add " + media.name + " to the service");
  if (!state || !state.service) { add.disabled = true; add.title = "Open or start a service first"; }
  options.appendChild(add);
  options.appendChild(button(media.kind === "video" ? "Play now" : "Show now", () => act("go_entry", { kind: "media", id: media.id }), "", "Put " + media.name + " on the screens now"));
  if (media.kind === "audio") options.appendChild(button("Play as music", () => act("music_play", { id: media.id }), "", "Play " + media.name + " as music under the slides"));
  options.appendChild(button("Rename", () => { renamingMedia = media.id; drawMediaPreview(); }, "", "Rename " + media.name));
  const del = button("Delete", null, "danger", "Delete " + media.name);
  if (media.uses || media.decks) { del.disabled = true; del.title = "Take it out of the services and slideshows that use it first"; }
  else confirmClick(del, () => act("media_delete", { id: media.id }).then(() => { mediaPick = null; return loadManage(); }),
    "Delete “" + media.name + "” from the library? Keep your own copy of the file if you want it again.");
  options.appendChild(del);
  box.appendChild(options);
}
let renamingMedia = null;

/* ---- importing ---- */
/* Uploads with XMLHttpRequest so a big file can show how far it has got; `progress(percent)` is optional. */
function uploadOne(batch, kind, file, progress) {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/upload?batch=" + batch + "&kind=" + kind + "&name=" + encodeURIComponent(file.name));
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-ChurchLight", "operator");
    request.responseType = "text";
    if (progress && request.upload) {
      request.upload.onprogress = (event) => { if (event.lengthComputable && event.total) progress(Math.floor(event.loaded / event.total * 100)); };
    }
    request.onerror = () => { showMessage("Cannot reach the program. Is it still running?"); resolve(false); };
    request.onload = () => {
      let data = {};
      try { data = JSON.parse(request.responseText); } catch (err) { data = {}; }
      if (request.status < 200 || request.status >= 300) { showMessage(data.error || "The upload of " + file.name + " failed (" + request.status + ")"); resolve(false); return; }
      resolve(true);
    };
    request.send(file);
  });
}

function importFiles(kind, files, extra, statusNode) {
  const status = statusNode || $("import-status-" + ({ songs: "songs", deck: "decks", bible: "bibles" }[kind]));
  return inImportQueue(status, () => importFilesNow(kind, files, extra, status));
}

async function importFilesNow(kind, files, extra, status) {
  const { good, problems } = checkFiles(kind, files);
  if (problems.length) showMessage(problems.slice(0, 3).join(". ") + (problems.length > 3 ? " (and " + (problems.length - 3) + " more)" : ""));
  if (!good.length) return;
  // Several PDFs are several slideshows: each one is imported on its own, named after its file.
  const pdfs = good.filter((f) => extensionOf(f.name) === ".pdf");
  if (kind === "deck" && pdfs.length > 1 && pdfs.length === good.length) {
    let started = 0;
    for (const pdf of pdfs) {
      status.textContent = "Slideshow " + (started + 1) + " of " + pdfs.length + ": " + pdf.name;
      if (!(await importBatch(kind, [pdf], Object.assign({}, extra || {}, { name: "" }), status))) break;
      started += 1;
    }
    if (started) status.textContent = "Uploaded " + started + " of " + pdfs.length + " slideshows. The imports are running; they are listed below and appear in the Slideshow Library when done.";
    return;
  }
  const started = await importBatch(kind, good, extra, status);
  status.textContent = started ? "Uploaded. The import is running; you can see how it goes below." : "";
}

async function importBatch(kind, files, extra, status) {
  const made = await act("import_batch");
  if (!made) return false;
  let done = 0;
  for (const file of files) {
    status.textContent = "Uploading " + (done + 1) + " of " + files.length + ": " + file.name;
    if (!(await uploadOne(made.batch, kind, file))) { status.textContent = ""; return false; }
    done += 1;
  }
  return (await act("import_start", Object.assign({ kind: kind, batch: made.batch }, extra || {}))) !== null;
}

function wireLibrary() {
  $("tab-run").addEventListener("click", () => showView("run"));
  for (const chip of ["chip-countdown", "chip-alert"]) $(chip).addEventListener("click", () => { showView("run"); showRunPane("announce", true); });
  initLook();
  initLibraryFolds();
  initSettingsFolds();
  initProjectorWindows();
  restoreWindowPlace();
  // Once the saved place has had time to take effect, that is the starting point; later changes are saved.
  setTimeout(() => { if (hadSavedPlace) reportWindowPlace(); setInterval(reportWindowPlace, 2000); }, 800);
  window.addEventListener("pagehide", reportWindowPlace);
  document.addEventListener("visibilitychange", reportWindowPlace);
  watchReference("tray-text", "tray-hint");
  for (const view of ["songs", "decks", "bibles"]) $("landing-" + view).addEventListener("click", () => showView(view));
  $("ready-again").addEventListener("click", refreshReady);
  $("landing-practice").addEventListener("click", async () => { if (await act("setup_practice", {}) !== null) showNotice("Made a practice service."); });
  $("landing-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("landing-name").value.trim();
    if (title && await act("new_service", { title: title })) $("landing-name").value = "";
  });
  for (const view of ["songs", "decks", "media", "bibles", "broadcast", "shortcuts"]) $("tab-" + view).addEventListener("click", () => showView(view));
  $("shortcuts-back").addEventListener("click", () => showView("run"));
  $("service-settings-btn").addEventListener("click", () => {
    const box = $("service-settings");
    box.hidden = !box.hidden;
    $("service-settings-btn").setAttribute("aria-expanded", String(!box.hidden));
    $("service-settings-btn").textContent = box.hidden ? "Change" : "Done";
  });
  $("home-choose").addEventListener("click", () => $("home-file").click());
  $("home-file").addEventListener("change", () => { const file = $("home-file").files[0]; $("home-file").value = ""; chooseHome(file); });
  $("home-remove").addEventListener("click", () => act("home_set", { image: null }));
  $("conf-hide-words").addEventListener("change", () => act("confidence_words_set", { value: $("conf-hide-words").checked }));
  $("home-on").addEventListener("change", () => act("home_set", { on: $("home-on").checked }));
  $("home-idle").addEventListener("change", () => act("home_set", { idle: $("home-idle").checked }));
  $("home-fit").addEventListener("change", () => act("home_set", { fit: $("home-fit").value }));
  $("hook-on").addEventListener("change", () => act("hook_set", { on: $("hook-on").checked }));
  $("hook-new").addEventListener("click", hookNewClick);
  $("gk-toggle").addEventListener("click", () => act("global_keys_arm", { on: !(state && state.global_keys && state.global_keys.armed) }));
  $("keys-reset").addEventListener("click", () => { keyChoices = defaultKeys(); saveKeyChoices(); stopListening("Shortcuts are back to the standard keys"); });
  drawKeyTable();
  wireThemes();
  $("service-theme").addEventListener("change", (e) => act("service_theme", Object.assign({ kind: "projector" }, e.target.value ? { id: Number(e.target.value) } : {})));
  $("service-verse-split").addEventListener("change", (e) => act("service_verse_split", { value: e.target.value }));
  $("service-overlay-theme").addEventListener("change", (e) => act("service_theme", Object.assign({ kind: "overlay" }, e.target.value ? { id: Number(e.target.value) } : {})));
  $("new-song").addEventListener("click", () => openEditor(null));
  $("church-ccli-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (await act("church_ccli", { number: $("church-ccli").value.trim() })) { showNotice("Saved the church's CCLI license number."); loadManage(); }
  });
  $("new-song-run").addEventListener("click", () => openEditor(null));
  $("song-cancel").addEventListener("click", askToClose);
  $("editor-close").addEventListener("click", askToClose);
  $("editor-min").addEventListener("click", minimizeEditor);
  $("editor-resume").addEventListener("click", resumeEditor);
  confirmClick($("editor-discard"), closeEditor, "Throw away the changes you made to this song since you opened it?", "Yes, discard");
  $("song-save").addEventListener("click", () => saveSong("save"));
  $("song-save-add").addEventListener("click", () => saveSong("add"));
  $("song-save-draft").addEventListener("click", () => saveSong("draft"));
  for (const id of ["song-title", "song-author", "song-ccli", "song-text", "song-order", "song-opname"]) $(id).addEventListener("input", saveBuffer);
  $("song-theme").addEventListener("change", saveBuffer);
  for (const id of ["song-text", "song-order"]) $(id).addEventListener("input", () => { clearTimeout(checkTimer); checkTimer = setTimeout(checkSong, 400); });
  window.addEventListener("resize", placeEditor);
  restoreEditorFromBuffer();
  $("song-filter").addEventListener("input", drawSongList);
  $("deck-filter").addEventListener("input", drawDeckList);
  $("de-library-filter").addEventListener("input", drawLibraryPicker);
  wireSlidesGuide();
  wireSelectToggles();
  $("song-export-all").addEventListener("click", () => { downloadFile("/api/export?kind=songs&all=1&format=" + songExportFormat()); });
  const videoDecks = () => (manage.decks || []).filter((d) => d.has_video);
  confirmClick($("deck-export-all"), () => { downloadFile("/api/export?kind=slideshows&all=1" + (videoDecks().length ? "&skip_video=1" : "")); },
    () => {
      const skipped = videoDecks();
      if (!skipped.length) return null;
      const rest = manage.decks.length - skipped.length;
      return rest ? plural(skipped.length, "slideshow") + " " + (skipped.length === 1 ? "has" : "have") + " video and cannot be exported yet: " + skipped.map((d) => d.name).join(", ")
        + ". Export the other " + rest + "?"
        : "Every slideshow has video, and slideshows with video cannot be exported yet.";
    }, () => { const rest = (manage.decks || []).filter((d) => !d.has_video).length; return rest ? "Yes, export the other " + rest : "OK"; });

  const pick = (buttonId, inputId, kind, extra) => {
    $(buttonId).addEventListener("click", () => $(inputId).click());
    $(inputId).addEventListener("change", async () => {
      const input = $(inputId);
      const files = Array.from(input.files || []);
      input.value = "";
      if (files.length) await importFiles(kind, files, extra ? extra() : {});
    });
  };
  pick("pick-songs", "file-songs", "songs");
  pick("pick-bible", "file-bible", "bible", () => ({ abbreviation: $("bible-abbr").value.trim(), name: $("bible-name").value.trim() }));
  pick("pick-deck", "file-deck", "deck", () => ({ name: $("deck-name").value.trim() }));
  pick("pick-pictures", "file-pictures", "deck", () => ({ name: $("deck-name").value.trim() }));
  pick("pick-bundle", "file-bundle", "deck", () => ({ name: "" }));
  $("pick-media").addEventListener("click", () => $("file-media").click());
  $("media-filter").addEventListener("change", drawMediaList);
  $("file-media").addEventListener("change", async () => {
    const files = Array.from($("file-media").files || []);
    $("file-media").value = "";
    if (files.length) await importMedia(files);
  });
}

/* ---------------- wiring ---------------- */
function wire() {
  initRunPanel();
  wireClips();
  wireHear();
  initDock();
  wireLibrary();
  wireDeckEditor();
  attachSuggest($("search"), async (entry) => { clearSearch(true); await addSuggested(entry); }, () => !resultsUnder);
  $("btn-next").addEventListener("click", () => act("next"));
  $("btn-previous").addEventListener("click", () => act("previous"));
  $("btn-fold-items").addEventListener("click", foldItems);
  $("btn-blank").addEventListener("click", () => act("blank"));
  $("btn-words").addEventListener("click", () => act("text_hide"));
  $("btn-timer").addEventListener("click", () => act("timer_pause", { paused: !(state && state.timer && state.timer.paused) }));
  $("btn-clear").addEventListener("click", () => act("clear"));
  $("btn-lower").addEventListener("click", () => act("lower_hide"));
  wireCountdown();
  wireReport();
  wireTimerStyle();
  wireClicker();
  wireShutdown();
  wireMessageClear();
  $("btn-clear-verse").addEventListener("click", () => act("hide_verse"));
  $("preview-close").addEventListener("click", () => { $("preview").hidden = true; previewFor = ""; updateSearchButtons(); });
  $("tray-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("tray-text").value.trim();
    if (!text) return;                                               // (Enter on an empty "Go to verse" box is the add-the-passage one)
    if (await act("tray_load", { text: text })) {
      trayEnter = { key: null, added: false, shown: false, pendingAdded: false };
      $("tray-text").value = ""; clearJump(); focusVerseLine();
    }
  });
  const loadAndAdd = async () => {
    const text = $("tray-text").value.trim();
    if (!text) return;
    trayEnter = { key: null, added: false, shown: false, pendingAdded: true };        // the chapter will be in the service: set before asking, so an Enter that
    if (await act("tray_load", { text: text, add: true })) {                           // beats the reply still knows it
      $("tray-text").value = ""; $("tray-hint").textContent = ""; clearJump(); focusVerseLine();
    } else trayEnter = { key: null, added: false, shown: false, pendingAdded: false };
  };
  $("tray-load-add").addEventListener("click", loadAndAdd);
  $("tray-text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.shiftKey)) { event.preventDefault(); loadAndAdd(); }
  });
  $("tray-clear").addEventListener("click", () => act("tray_clear"));
  const showChecked = async () => {
    if (trayChecked.length && await act("tray_show", { verses: trayChecked.slice() })) { trayChecked = []; trayAnchor = null; clearJump(); paintTray(); backToPassage(); }
  };
  $("tray-show-checked").addEventListener("click", showChecked);
  $("tray-add-checked").addEventListener("click", async () => {
    if (trayChecked.length && await addToService(trayChecked.slice())) { trayChecked = []; trayAnchor = null; clearJump(); paintTray(); backToPassage(); }
  });
  $("tray-add-all").addEventListener("click", async () => {
    if (state && state.tray && await addToService(state.tray.verses.map((v) => v[0]))) backToPassage();
  });
  $("tray-jump").addEventListener("input", () => { if (!handOverToPassage()) jumpInTray(); });
  $("tray-jump").addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const step = jumpEnter(cuedKey, trayChecked, !!(state && state.cue), event.shiftKey);
      if (step === "cue") await cueTicked();
      else if (step === "take") await takeReady();
      else if (step === "cue_take") { if (await cueTicked()) await takeReady(); }
      else if (!$("tray-jump").value.trim()) await trayEnterEmpty();       // nothing typed: add the whole passage, then show verse 1
    } else if (event.key === "Escape") {
      if (state && state.cue) act("cue_clear");
      cuedKey = null;
      clearJump();
      trayChecked = [];
      trayAnchor = null;
      paintTray();
    }
  });
  wireNotice();
  $("tray-uncheck").addEventListener("click", () => { trayChecked = []; trayAnchor = null; paintTray(); });
  $("tray-next").addEventListener("click", () => act("tray_step", { delta: 1 }));
  $("tray-prev").addEventListener("click", () => act("tray_step", { delta: -1 }));
  document.addEventListener("visibilitychange", syncLivePreview);
  window.addEventListener("resize", rescalePreviews);
  window.addEventListener("resize", fitBoard);

  $("service-select").addEventListener("change", () => {
    const value = $("service-select").value;
    if (value) act("open_service", { id: Number(value) });
  });
  $("new-service-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("new-service-name").value.trim();
    if (title && await act("new_service", { title: title })) $("new-service-name").value = "";
  });
  $("add-verse-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = $("add-verse-text").value.trim();
    if (text && await act("add_verse", { text: text })) $("add-verse-text").value = "";
  });
  $("add-announcement-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = $("ann-title").value.trim();
    if (!title && !$("ann-body").value.trim()) { showMessage("Type a title or the text for the announcement"); return; }
    if (await act("add_announcement", { title: title, body: $("ann-body").value })) {
      $("ann-title").value = "";
      $("ann-body").value = "";
    }
  });
  $("search").addEventListener("input", () => {
    if (!resultsUnder) return;
    showAll = false;
    updateSearchButtons();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadLibrary, 200);
  });
  $("search").addEventListener("keydown", (event) => {
    if (event.key === "Escape") clearSearch(false);
  });
  try { const saved = localStorage.getItem(KIND_KEY); if (SEARCH_KINDS.includes(saved || "")) searchKind = saved || ""; } catch (err) { /* only a convenience */ }
  $("search-kind").value = searchKind;
  $("search-kind").addEventListener("change", () => {
    searchKind = $("search-kind").value;
    try { localStorage.setItem(KIND_KEY, searchKind); } catch (err) { /* only a convenience */ }
    if (resultsUnder && searchKind && !$("search").value.trim()) showAll = true;     // no words yet: list everything of that kind
    loadLibrary();
    $("search").focus();
  });
  $("search-clear").addEventListener("click", () => clearSearch(true));
  $("search-all").addEventListener("click", () => { showAll = true; loadLibrary(); });
  $("results-switch").addEventListener("change", () => {
    resultsUnder = $("results-switch").checked;
    try { localStorage.setItem(RESULTS_KEY, resultsUnder ? "on" : "off"); } catch (err) { /* only a convenience */ }
    clearSearch(false);
    applyResultsSwitch();
  });
  for (const slot of LIVE_SLOTS) {
    $(slot.select).addEventListener("change", () => {
      slot.choice = $(slot.select).value;
      try { localStorage.setItem(slot.key, slot.choice); } catch (err) { /* only a convenience */ }
      syncLivePreview();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (capturingKey || capturingCombo || event.ctrlKey || event.metaKey || event.altKey || (currentView !== "run" && currentView !== "broadcast")) return;
    if (typeof wizardOpen !== "undefined" && wizardOpen) return;            // the setup guide has the keyboard
    const target = event.target;
    const tag = target && target.tagName;
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || (target && target.isContentEditable);
    const result = keyAction(event.key, {
      typing: !!typing, onButton: tag === "BUTTON" || tag === "SUMMARY",
      verseShown: !!(state && state.live.overlay), cueReady: !!(state && state.cue), view: currentView,
    }, keyChoices);
    if (!result) return;
    event.preventDefault();
    if (result.blur) target.blur();
    else if (result.focus === "search") { setRunMode("plan", true); $("search").focus(); }
    else if (result.focus === "tray") { showRunPane("bible", true); $("tray-text").focus(); }
    else if (result.focus === "jump") { showRunPane("bible", true); if (!$("tray-body").hidden) $("tray-jump").focus(); else $("tray-text").focus(); }
    else if (result.fold) foldItems();
    else if (result.action) act(result.action, result.args);
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

let shutDown = false;       // set once the program has been told to close; the page then stops asking it for anything

async function follow() {
  let since = null;
  let failures = 0;
  while (!shutDown) {
    try {
      const query = since === null ? "" : "?since=" + since + "&wait=20";
      const res = await fetch("/api/state" + query, { cache: "no-store" });
      if (res.status === 304) continue;
      if (!res.ok) throw new Error("HTTP " + res.status);
      const next = await res.json();
      if (next.schema !== SCHEMA) throw new Error("This page is out of date; reload it");
      since = next.version;
      render(next);
      if (connectionDown || $("connection").textContent !== "Saved ✓") setConnection("Connected", false);
      failures = 0;
    } catch (err) {
      failures += 1;
      setConnection(failures > 1 || since === null ? "Not connected: " + (err.message || err) : "Reconnecting…", true);
      await sleep(Math.min(5000, 500 * failures));
    }
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { clampDock, parseDock, parseRunMode, keyHint, withHint, keysLine, comboFromEvent, globalStatusText, slideSummary, liveSummary, keyAction, KEY_ACTIONS, defaultKeys, parseKeyChoices, assignKey, keyLabel, normalizeKey, moveTarget, mediaUrl, describeServiceSelect,
    checkFiles, filterSongs, filterDecks, usesText, jobStatusText, extensionOf,
    chosenLiveOutput, SCREEN_SHAPES, hasScreenShape, blankText, shapeParts, frameHeight, previewScale,
    trayView, toggleChecked, checkRun, parseVerseJump,
    runPaneName, serviceSummary, nextItemIndex, addPosition, parseSongBuffer, partNames, appendToOrder, partsSummary, outlineView, deckSlideView, LIBRARY_FOLDS, parseLibraryFolds, SETTINGS_FOLDS, parseSettingsFolds, foldAllPlan, parseDeckOpen, toggleDeckOpen, pdfLine, librarySummary, jumpEnter, jumpHandover, numberRanges, replacePlanText, libraryMode,
    trayEnterStep, ahkKeyName, cleanClickerRows, buildClickerScript, timerPreviewStyles, dragRect, colorWithOpacity, splitOpacity, TRANSITION_CHOICES, secondsToMs, msToSeconds, transitionText,
    END_CHOICES, parseAutoSeconds, autoplayText, timerText,
    selectPages, movePages, dropPages, deckEditorState, usedText, afterPageFor, splitName, commonTiming, pageFlags, deckTimingText, stepSuggestion };
} else {
  wire();
  applyResultsSwitch();
  follow();
}
