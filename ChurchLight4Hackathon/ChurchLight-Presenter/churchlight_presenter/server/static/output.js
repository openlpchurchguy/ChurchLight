/* ChurchLight output page.
   viewModel() is a pure function (payload in, plain description out) so it can
   be tested without a browser; everything else just draws that description.
   Text is only ever inserted with textContent, never as HTML. */
"use strict";

// Where each public layout draws its two layers. 'full' fills the frame,
// 'lower' is a band at the bottom. A band cannot show a picture, so images are
// skipped there.
const PLACEMENT = {
  projector:   { base: "full",  overlay: "lower" },
  ppt_verse:   { base: "full",  overlay: "lower" },
  lower_third: { base: "lower", overlay: "lower" },
  song_lower:  { base: "lower", overlay: "lower" },
  verse_lower: { base: "lower", overlay: "lower" },
  song_full:   { base: "full",  overlay: "full" },
  verse_full:  { base: "full",  overlay: "full" },
  confidence:  { base: "confidence", overlay: "confidence" },   // the speaker's monitor: a verse replaces the slide
};

// Theme style (already validated by the server) as [property, value] pairs for one layer.
/* Where an overlay box sits: v_pos top|middle|bottom, h_pos left|center|right, box_width and margin
   (both as a percent of the screen). Nothing is changed unless the theme names a position. */
function boxPosition(style) {
  if (!(style.v_pos || style.h_pos || style.box_width || style.margin !== undefined)) return [];
  const gap = (style.margin !== undefined ? style.margin : 4) + "vh";
  const v = style.v_pos || "bottom";
  const h = style.h_pos || "center";
  const moves = [];
  const css = [["margin", "0"], ["width", (style.box_width || 90) + "%"]];
  if (style.max_height) css.push(["max-height", style.max_height + "vh"], ["overflow", "hidden"]);
  if (v === "top") css.push(["top", gap], ["bottom", "auto"]);
  else if (v === "middle") { css.push(["top", "50%"], ["bottom", "auto"]); moves.push("translateY(-50%)"); }
  else css.push(["bottom", gap], ["top", "auto"]);
  if (h === "left") css.push(["left", gap], ["right", "auto"]);
  else if (h === "right") css.push(["right", gap], ["left", "auto"]);
  else { css.push(["left", "50%"], ["right", "auto"]); moves.push("translateX(-50%)"); }
  if (moves.length) css.push(["transform", moves.join(" ")]);
  return css;
}

/* A theme's background picture: filled or fitted, optionally dimmed with a black wash. */
function pictureCss(style) {
  const dim = Number(style.background_dim) || 0;
  const picture = "url(" + mediaUrl(style.background_image) + ")";
  const css = [["background-image", dim > 0 ? "linear-gradient(rgba(0,0,0," + (dim / 100) + "), rgba(0,0,0," + (dim / 100) + ")), " + picture : picture],
               ["background-position", "center"], ["background-repeat", "no-repeat"],
               ["background-size", dim > 0 ? "100% 100%, " : ""]];
  css[3][1] += style.background_fit === "fit" ? "contain" : "cover";
  return css;
}

/* Only full-frame text gets a band behind it; pictures and the speaker's monitor never do. */
function hasTextBand(layer) {
  const style = layer.view.style;
  return layer.placement === "full" && !layer.view.image && !!style && style.band === true &&
    !layer.view.wordsHidden;     // nothing to put on a band while the words are hidden
}

/* The projector theme's own margins (top and bottom as a share of the screen height, left and right of the
   width) as a CSS padding, or null to keep the page's default (4vh 6vw). */
function textMargins(style) {
  const keys = ["margin_top", "margin_right", "margin_bottom", "margin_left"];
  if (!keys.some((k) => typeof style[k] === "number")) return null;
  const get = (k, fallback) => (typeof style[k] === "number" ? style[k] : fallback);
  return get("margin_top", 4) + "vh " + get("margin_right", 6) + "vw " + get("margin_bottom", 4) + "vh " + get("margin_left", 6) + "vw";
}

function styleToCss(style, placement, picture) {
  const css = [];
  if (!style) return css;
  if (style.font_family) css.push(["font-family", style.font_family]);
  if (style.font_size) css.push(["font-size", style.font_size + "vh"]);
  if (style.color) css.push(["color", style.color]);
  if (style.line_height) css.push(["line-height", String(style.line_height)]);
  if (style.align) {
    css.push(["text-align", style.align]);
    // full-frame layers are flex columns, so the block itself must move too
    css.push(["align-items", { left: "flex-start", center: "center", right: "flex-end" }[style.align]]);
  }
  if (style.shadow === false) css.push(["text-shadow", "none"]);
  if (style.uppercase) css.push(["text-transform", "uppercase"]);
  if (style.reference_scale) css.push(["--ref-scale", String(style.reference_scale)]);
  if (style.title_scale) css.push(["--title-scale", String(style.title_scale)]);
  if (style.number_font) css.push(["--num-font", style.number_font === "same" ? "inherit" : style.number_font]);
  if (placement === "lower") {
    if (style.band === false) css.push(["background", "transparent"]);
    else if (style.band_color) css.push(["background", style.band_color]);
    css.push(...boxPosition(style));
  } else {
    const space = textMargins(style);
    if (space && placement === "full" && !picture) css.push(["padding", space]);     // a slide picture always fills the screen
    if (style.background_color) css.push(["background-color", style.background_color]);
    if (style.background_image && placement === "full") css.push(...pictureCss(style));
  }
  return css;
}

function layerView(slide, showReference) {
  if (!slide) return null;
  const titleFirst = !!slide.title_first;
  const pos = slide.style && slide.style.reference_pos;       // a theme can put the reference above or below the text
  const above = pos === "above" ? true : pos === "below" ? false : titleFirst;
  let parts;
  if (slide.segments && slide.segments.length) {
    parts = slide.segments.map(([, t]) => ({ number: null, text: t }));          // under the reference: only the verse text
  } else if (slide.text) {
    parts = slide.text.split("\n").map((t) => ({ number: null, text: t }));
  } else {
    parts = [];
  }
  return {
    kind: slide.kind,
    image: (slide.kind === "video" || slide.kind === "audio") ? null : (slide.image_path || null),   // a clip is played, not drawn
    clip: slide.kind === "video" || slide.kind === "audio",
    parts: parts,
    reference: showReference && slide.kind !== "notice" ? (slide.reference || "") : "",      // a notice slide already has the title in its text
    titleFirst: titleFirst && above && showReference,      // a passage's title ("John 3") above the text, in title size
    refAbove: !titleFirst && above && showReference,        // a single verse's reference above the text, in reference size
    continued: !!slide.continued && showReference,
    label: slide.label || "",
    style: slide.style || null,
    fit: slide.image_fit || "fit",             // how a picture fills the screen (a deck's "Picture size")
  };
}

function viewModel(payload) {
  const stage = payload.audience === "stage";
  if (stage) {
    const pos = payload.position || {};
    const of = (i, n) => (i === null || i === undefined || !n ? "" : `${i + 1} of ${n}`);
    return {
      audience: "stage",
      blank: !!payload.blank,
      current: layerView(payload.base, true),
      overlay: layerView(payload.overlay, true),
      next: layerView(payload.next, true),
      notes: (payload.base && payload.base.notes) || "",
      position: of(pos.index, pos.count),
      overlayPosition: of(pos.overlay_index, pos.overlay_count),
    };
  }
  const place = PLACEMENT[payload.layout];
  if (!place) return { audience: "public", unknownLayout: payload.layout, layers: [] };
  const layers = [];
  const hideText = !!payload.text_hidden;
  const add = (slide, placement) => {
    let view = layerView(slide, slide && slide.kind === "scripture");
    if (!view || view.clip) return;                     // a video or sound slide draws nothing: syncMedia plays it
    if (placement === "lower" && view.image) return;
    if (hideText && !view.image) {
      // The words go, the theme's background stays: a lower third is only its words, so it goes entirely.
      if (placement === "lower") return;
      view = Object.assign({}, view, { parts: [], reference: "", continued: false, wordsHidden: true });
    }
    layers.push({ placement: placement, view: view });
  };
  add(payload.base, place.base);
  add(payload.overlay, place.overlay);
  // An overlay in the same place as the base replaces it rather than stacking on it.
  if (layers.length === 2 && layers[0].placement === layers[1].placement) layers.shift();
  return { audience: "public", layers: layers, blank: !!payload.blank, confidence: payload.audience === "confidence",
           peekBlank: !!payload.peek_blank,
           home: payload.home && payload.home.image_path ? { image: payload.home.image_path, fill: payload.home.fit === "fill" } : null };
}

/* ---- slide transitions (pure) ----
   A deck can ask for a transition when the projector changes to one of its slides. The server only
   sends `transitions: true` to layouts that draw them (the projector). */
const TRANSITION_TYPES = ["dissolve", "circle_out", "circle_in",
  "slide_left", "slide_right", "slide_up", "slide_down",
  "split_horizontal", "split_vertical", "split_diagonal_left", "split_diagonal_right",
  "wipe_left", "wipe_right", "wipe_up", "wipe_down", "fade_black", "fade_white"];
const CIRCLE_FULL = "circle(75% at 50% 50%)";     // 75% of the frame's reference size covers every corner
const CIRCLE_NONE = "circle(0% at 50% 50%)";

function slideKey(slide) {
  if (!slide) return "";
  return [slide.kind, slide.image_path, slide.text, slide.label, slide.reference, slide.source_item_id,
    slide.continued, (slide.verse_numbers || []).join(",")].join("\u0001");
}

/* What to animate going from the payload last drawn to `next`, or null for "just change". */
function planTransition(prev, next) {
  if (!prev || !next || !next.transitions || !next.base) return null;
  if (next.base.kind === "video" || (prev.base && prev.base.kind === "video")) return null;     // a video page has no picture to animate: a plain change, not a flash of the background
  const type = next.base.transition;
  const ms = Number(next.base.transition_ms);
  if (!TRANSITION_TYPES.includes(type) || !(ms > 0)) return null;
  if (slideKey(prev.base) === slideKey(next.base)) return null;     // only the verse or the blanking changed
  // A slideshow's transition is for moving between its own slides. Arriving from another item (a song, another
  // slideshow, a verse item) is a plain change.
  if (prev.base && (prev.base.item_type !== "presentation" || prev.base.source_item_id !== next.base.source_item_id)) return null;
  return { type: type, ms: Math.min(ms, 2000) };
}

/* Which of the two frames moves, and how. Dissolve and circle-out reveal the new frame on top;
   circle-in shrinks the old frame (kept on top) down to the center, uncovering the new one. */
const SLIDE_FROM = { slide_left: "100%, 0", slide_right: "-100%, 0", slide_up: "0, 100%", slide_down: "0, -100%" };
const WIPE_HIDDEN = {                  // clip-path inset(top right bottom left): the part still hidden at the start
  wipe_left: "inset(0 0 0 100%)", wipe_right: "inset(0 100% 0 0)", wipe_up: "inset(100% 0 0 0)", wipe_down: "inset(0 0 100% 0)",
  split_horizontal: "inset(50% 0 50% 0)", split_vertical: "inset(0 50% 0 50%)",
};
const FULL_RECT = "inset(0 0 0 0)";
/* A diagonal split is a four-cornered shape that starts as a line along a diagonal and grows to the whole frame. */
const DIAGONAL = {
  split_diagonal_right: ["polygon(0% 100%, 100% 0%, 100% 0%, 0% 100%)", "polygon(0% 0%, 100% 0%, 100% 100%, 0% 100%)"],   // opens from a / line
  split_diagonal_left: ["polygon(0% 0%, 0% 0%, 100% 100%, 100% 100%)", "polygon(0% 0%, 100% 0%, 100% 100%, 0% 100%)"],    // opens from a \ line
};

/* Fade to black or white: the old slide fades under a cover, the new one is put in at the midpoint (hidden by the
   cover), and the cover fades away. */
const FADE_COLOR = { fade_black: "#000000", fade_white: "#ffffff" };

function transitionSteps(type) {
  if (FADE_COLOR[type]) return { moves: "cover", color: FADE_COLOR[type] };
  if (SLIDE_FROM[type]) {
    const [x, y] = SLIDE_FROM[type].split(", ");
    return { moves: "new", keyframes: [{ transform: "translate(" + x + ", " + y + ")" }, { transform: "translate(0, 0)" }] };
  }
  if (WIPE_HIDDEN[type]) return { moves: "new", keyframes: [{ clipPath: WIPE_HIDDEN[type] }, { clipPath: FULL_RECT }] };
  if (DIAGONAL[type]) return { moves: "new", keyframes: [{ clipPath: DIAGONAL[type][0] }, { clipPath: DIAGONAL[type][1] }] };
  if (type === "dissolve") return { moves: "new", keyframes: [{ opacity: 0 }, { opacity: 1 }] };
  if (type === "circle_out") return { moves: "new", keyframes: [{ clipPath: CIRCLE_NONE }, { clipPath: CIRCLE_FULL }] };
  if (type === "circle_in") return { moves: "old", keyframes: [{ clipPath: CIRCLE_FULL }, { clipPath: CIRCLE_NONE }] };
  return null;
}

/* While the public screens stay blanked, a new slide going live changes nothing anyone can see, so the page
   must not redraw (that rebuilt the home picture and made it blink). The speaker's monitor and the stage page
   keep showing slides while blanked, so they always redraw. */
function sameBlankScreen(prev, next) {
  if (!prev || !next || !prev.blank || !next.blank) return false;
  if (next.audience !== "public" || prev.audience !== "public" || prev.layout !== next.layout) return false;
  const home = (p) => (p.home && p.home.image_path ? p.home.image_path + "|" + p.home.fit : "");
  return home(prev) === home(next);
}

/* Everything the public screen would show, as one string. Two payloads with the same key look identical, so the
   page leaves the screen alone (a redraw makes pictures blink). Hiding the words over a picture slide is one
   such change: the picture stays, the payload still changes. */
function screenKey(vm) {
  return JSON.stringify(vm);
}

function mediaUrl(path) {
  return "/media/" + String(path).split("/").map(encodeURIComponent).join("/");
}

/* ---- drawing (browser only) ---- */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/* Balanced lines: songs on the projector and overlay when the theme asks. The speaker's monitor balances every
   plain line (songs and Bible text) unless its theme switches it off, since one word alone on a line is hard
   to read at a glance. */
function wantsBalance(view, confidence) {
  const style = view.style || {};
  if (confidence) return style.balance_lines !== false;
  return view.kind === "lyrics" && !!style.balance_lines;
}

function drawBody(view, into, confidence) {
  if (view.image) {
    const img = el("img");
    img.decoding = "sync";                    // paint it with the first frame instead of a moment later
    img.src = mediaUrl(view.image);
    img.alt = view.label;
    into.appendChild(img);
  }
  const drawReference = (extra) => {
    const ref = el("div", "ref" + extra, view.reference);
    if (view.continued) ref.appendChild(el("span", "cont", " (continued)"));
    into.appendChild(ref);
  };
  const balance = wantsBalance(view, !!confidence);
  if (view.reference && view.titleFirst) drawReference(" top");
  else if (view.reference && view.refAbove) drawReference(" above");
  view.parts.forEach((part, at) => {
    const line = el("p", (balance ? "line balance" : "line") + (view.kind === "notice" ? (at === 0 ? " notice-title" : " notice-detail") : ""));
    if (part.number !== null) line.appendChild(el("span", "num", String(part.number)));
    line.appendChild(document.createTextNode(part.text));
    into.appendChild(line);
  });
  if (view.reference && !view.titleFirst && !view.refAbove) drawReference("");
}

/* ---- balanced lines (a theme option for songs) ----
   A lyric line too long for the screen wraps; left alone, the first line is filled and a word or two is left
   on the second. Browsers have `text-wrap: balance` for this, but not every one does (the browser inside XSplit
   or OBS can be older than Edge), so the page does the work itself: it finds the narrowest width that keeps the
   same number of lines, then puts the line breaks in as real breaks. The result is the same on every screen. */

/* `text` with a line break in front of each offset in `starts` (pure; the space before a break is dropped). */
function breakText(text, starts) {
  let out = "";
  let prev = 0;
  for (const start of starts) {
    if (start <= prev || start >= text.length) continue;
    out += text.slice(prev, start).replace(/\s+$/, "") + "\n";
    prev = start;
  }
  return out + text.slice(prev);
}

/* The offsets of the words that begin a wrapped line (not the first) in a laid-out text node. */
function lineStarts(node) {
  const range = document.createRange();
  const starts = [];
  const words = /\S+/g;
  let top = null;
  let match;
  while ((match = words.exec(node.data))) {
    range.setStart(node, match.index);
    range.setEnd(node, match.index + 1);
    const box = range.getClientRects()[0];
    if (!box) continue;
    if (top !== null && box.top > top + box.height / 2) starts.push(match.index);
    top = box.top;
  }
  return starts;
}

const MAX_BALANCED_LINES = 8;

function balanceLine(line) {
  if (line.dataset.text === undefined) {
    if (line.childNodes.length !== 1 || line.firstChild.nodeType !== 3) return;      // plain text only
    line.dataset.text = line.textContent;
  }
  const text = line.dataset.text;
  line.textContent = text;                       // start again from the whole line (the fonts may have just loaded)
  line.style.maxWidth = "";
  line.style.whiteSpace = "";
  line.style.textWrap = "wrap";
  const node = line.firstChild;
  const count = lineStarts(node).length + 1;
  if (count < 2 || count > MAX_BALANCED_LINES) return;
  let narrow = 0;
  let wide = line.getBoundingClientRect().width;
  for (let i = 0; i < 14; i++) {
    const middle = (narrow + wide) / 2;
    line.style.maxWidth = middle + "px";
    if (lineStarts(node).length + 1 <= count) wide = middle; else narrow = middle;
  }
  line.style.maxWidth = wide + "px";
  const starts = lineStarts(node);
  line.style.maxWidth = "";
  if (starts.length + 1 !== count) return;       // something unexpected: leave the browser's own wrapping
  line.textContent = breakText(text, starts);
  line.style.whiteSpace = "pre-line";
}

function balanceLines(frame) {
  for (const line of frame.querySelectorAll(".line.balance")) balanceLine(line);
}

/* The speaker's monitor never has to scroll or cut text off: shrink the text until it fits the screen. */
function fitToBox(box, floor) {
  let size = parseFloat(getComputedStyle(box).fontSize);
  const smallest = floor || 12;
  for (let i = 0; i < 30 && box.scrollHeight > box.clientHeight + 1 && size > smallest; i++) {
    size = Math.max(size * 0.93, smallest);
    box.style.setProperty("font-size", size + "px");
  }
}

/* Text can only be measured once the frame is on the page, so shrinking happens after it is attached:
   the speaker's monitor shrinks to fit the screen; a height-capped overlay shrinks, but never below
   60% of its size (the builder breaks verses sooner so they fit at that size). */
function fitLayers(frame) {
  balanceLines(frame);
  if (document.fonts && document.fonts.ready) {          // a font that arrives late changes where lines wrap
    document.fonts.ready.then(() => { if (frame.isConnected) balanceLines(frame); });
  }
  for (const box of frame.querySelectorAll(".layer.confidence")) fitToBox(box);
  for (const box of frame.querySelectorAll(".layer[data-capped]")) {
    fitToBox(box, parseFloat(getComputedStyle(box).fontSize) * 0.6);
  }
  balanceLines(frame);                                   // shrinking changes where lines wrap: balance at the final size
}

/* One picture element per file and fit, kept and reused, so it stays decoded between blankings. */
const homePictures = new Map();
function homePicture(home) {
  const key = home.image + "|" + (home.fill ? "fill" : "fit");
  let picture = homePictures.get(key);
  if (!picture) {
    picture = el("img", "home-pic" + (home.fill ? " fill" : ""));
    picture.src = mediaUrl(home.image);
    picture.alt = "";
    homePictures.clear();                 // only the current choice is worth keeping
    homePictures.set(key, picture);
  }
  return picture;
}

function drawPublic(vm, root) {
  if (vm.home) {            // the projector's home screen: the server sends it while blanked or while nothing is live
    root.appendChild(homePicture(vm.home));
  }
  for (const layer of vm.layers) {
    const box = el("div", "layer " + layer.placement + (layer.view.image ? " image fit-" + layer.view.fit : ""));
    for (const [name, value] of styleToCss(layer.view.style, layer.placement, !!layer.view.image)) {
      box.style.setProperty(name, value);   // CSSOM, so the page's strict CSP allows it
    }
    if (hasTextBand(layer)) {
      const band = el("div", "textband");
      band.style.setProperty("background", layer.view.style.band_color || "#000000b0");
      drawBody(layer.view, band, vm.confidence);
      box.appendChild(band);
    } else {
      drawBody(layer.view, box, vm.confidence);
    }
    root.appendChild(box);
    const style = layer.view.style;
    if (layer.placement === "lower" && style && style.max_height) box.dataset.capped = "1";
  }
  if (vm.confidence && vm.blank) root.appendChild(el("div", "flag-blank", "PROJECTOR IS BLANKED"));
  if (vm.peekBlank) root.appendChild(el("div", "flag-blank", "SCREENS BLANKED"));   // the operator's preview only
}

function drawStage(vm, root) {
  const bar = el("div", "bar");
  bar.appendChild(el("span", "", vm.position ? "Slide " + vm.position : "Nothing live"));
  if (vm.blank) bar.appendChild(el("span", "flag", "SCREEN BLANKED"));
  if (vm.overlayPosition) bar.appendChild(el("span", "", "Verse " + vm.overlayPosition));
  root.appendChild(bar);

  const panel = (cls, title, view) => {
    const box = el("section", "panel " + cls);
    box.appendChild(el("h2", "", title));
    const body = el("div", "body");
    if (view) drawBody(view, body);
    box.appendChild(body);
    return box;
  };
  root.appendChild(panel("current", "Now" + (vm.current && vm.current.label ? " — " + vm.current.label : ""), vm.current));
  if (vm.overlay) root.appendChild(panel("over", "Over it", vm.overlay));
  if (vm.notes) {
    const box = el("section", "panel notes");
    box.appendChild(el("h2", "", "Notes"));
    box.appendChild(el("div", "body", vm.notes));
    root.appendChild(box);
  }
  root.appendChild(panel("next", "Next", vm.next));
}

/* ---- the page's state: what is drawn, and any transition in flight ---- */
let shown = null;          // the payload last drawn
let shownKey = null;       // screenKey of the public screen last drawn, or null
let running = null;        // { finish() } while a transition is animating
const preloaded = new Map();   // image path -> Image kept so the browser holds it ready
const PRELOAD_LIMIT = 12;

function settle() {
  if (running) running.finish();
}

/* Fetch and decode the neighboring slides' pictures ahead of time. */
function preload(paths) {
  for (const path of paths || []) {
    if (preloaded.has(path)) continue;
    const img = new Image();
    img.src = mediaUrl(path);
    if (img.decode) img.decode().catch(() => {});
    preloaded.set(path, img);
    if (preloaded.size > PRELOAD_LIMIT) preloaded.delete(preloaded.keys().next().value);
  }
}

/* Wait (briefly) until every picture in the frame is ready, so the transition never starts on a blank. */
function picturesReady(frame) {
  const waits = Array.from(frame.querySelectorAll("img")).map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve()));
  return Promise.race([Promise.all(waits), sleep(400)]);
}

/* ---- countdown and speaker alert ----
   They sit outside #root on purpose: slide changes and transitions redraw #root, and a ticking clock must not
   be redrawn or blink with them. The server sends its own clock (`now`), so a page whose clock is off still counts right. */
function formatCountdown(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600), m = Math.floor((total % 3600) / 60), s = total % 60;
  const two = (n) => (n < 10 ? "0" : "") + n;
  return h > 0 ? h + ":" + two(m) + ":" + two(s) : m + ":" + two(s);
}

/* What to draw at `nowMs` (this page's clock): {text, ended, gone}. */
function countdownDisplay(cd, nowMs) {
  const left = cd.end - (nowMs + cd.offset);
  return { text: formatCountdown(left), ended: left <= 0, gone: left <= -cd.linger };
}

let countdown = null;
let countdownTimer = null;

/* The look the server sent, with safe fallbacks so a page never draws without one. The projector's style also carries
   `box` (the big clock's rectangle) and `bar` (the scrolling bar); the speaker screens' style has neither and uses `position`. */
const COUNTDOWN_STYLE = { font: "'Open Sans', Arial, sans-serif", color: "#ffffff", ended_color: "#ffe58a", size: 24,
  label_size: 6, position: "center", backdrop: "#00000000", mode: "clock" };
const BAR_STYLE = { x: 0, y: 88, w: 100, h: 9, bg: "#000000cc", color: "#ffffff", size: 60, speed: 14, radius: 0, every: 1,
  text: "{minutes} until service begins", zero_text: "Service is beginning", demo: false };
const ALERT_STYLE = { font: "'Open Sans', Arial, sans-serif", bg: "#ffe58a", color: "#000000", size: 7, position: "bottom" };

/* ---- the scrolling bar: pops up as the countdown passes whole minutes and scrolls its words across ---- */
/* "10 minutes" / "1 minute" into the wording; the {minutes} word is replaced, everything else stays as typed. */
function barMessage(template, minutes) {
  return String(template).replace(/\{minutes\}/g, minutes + (minutes === 1 ? " minute" : " minutes"));
}

/* Does the bar pop up now? `st` is {lastM, zeroDone}; leftMs is the time left. Returns {minutes}, {zero: true} or null.
   It pops up straight away when the countdown starts, then each time the time left drops to a whole number of minutes
   that is a multiple of `every` (10:00, 9:00 ...), and once when the time reaches zero. The last one is "1 minute". */
function barDue(st, leftMs, every) {
  if (leftMs <= 0) {
    if (st.zeroDone) return null;
    st.zeroDone = true;
    st.lastM = 0;
    return { zero: true };
  }
  const m = Math.ceil(leftMs / 60000);                       // 10:00 left is "10 minutes"; 9:59 is still 10 until 9:00
  if (st.lastM === null) { st.lastM = m; return { minutes: m }; }
  if (m < st.lastM) {
    st.lastM = m;
    if (m % every === 0) return { minutes: m };
  }
  return null;
}

let barState = null;       // {key, lastM, zeroDone, animating, token, nextDemo, demoN}

function barElement() {
  let bar = document.getElementById("cl-bar");
  if (!bar) {
    bar = el("div", "cl-bar");
    bar.id = "cl-bar";
    bar.hidden = true;
    bar.appendChild(el("span", "bar-text"));
    document.body.appendChild(bar);
  }
  return bar;
}

/* Where the bar sits and how it looks. Sizes are percentages of the screen (the bar) and of the bar's own height (text). */
function styleBar(style) {
  const bar = document.getElementById("cl-bar");
  if (!bar) return;
  const b = Object.assign({}, BAR_STYLE, style.bar || {});
  const heightPx = window.innerHeight * b.h / 100;
  bar.style.setProperty("left", b.x + "%");
  bar.style.setProperty("top", b.y + "%");
  bar.style.setProperty("width", b.w + "%");
  bar.style.setProperty("height", b.h + "%");
  bar.style.setProperty("background", b.bg);
  bar.style.setProperty("color", b.color);
  bar.style.setProperty("font-family", style.font);
  bar.style.setProperty("font-size", heightPx * b.size / 100 + "px");
  bar.style.setProperty("border-radius", heightPx * b.radius / 100 + "px");
}

function cancelBar() {
  const bar = document.getElementById("cl-bar");
  if (barState) { barState.token += 1; barState.animating = false; }
  if (bar) { for (const a of bar.getAnimations({ subtree: true })) a.cancel(); bar.remove(); }
}

function playBar(message) {
  const bar = barElement();
  const text = bar.firstChild;
  styleBar(countdown ? countdown.style : {});
  for (const a of bar.getAnimations({ subtree: true })) a.cancel();
  text.textContent = message;
  bar.hidden = false;
  const token = ++barState.token;
  barState.animating = true;
  const b = Object.assign({}, BAR_STYLE, (countdown && countdown.style.bar) || {});
  const width = bar.clientWidth;
  const textWidth = text.getBoundingClientRect().width;
  const pxPerSecond = Math.max(40, window.innerWidth / Math.max(1, b.speed));
  const duration = ((width + textWidth) / pxPerSecond) * 1000;
  bar.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, fill: "forwards" });
  const scroll = text.animate([{ transform: "translateX(" + width + "px)" }, { transform: "translateX(" + (-textWidth) + "px)" }],
    { duration: duration, easing: "linear", fill: "forwards" });
  scroll.finished.then(() => {
    if (token !== barState.token) return null;
    return bar.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: "forwards" }).finished;
  }).then(() => {
    if (!barState || token !== barState.token) return;
    bar.hidden = true;
    barState.animating = false;
    barState.nextDemo = Date.now() + 1200;
  }).catch(() => { /* cancelled by a newer popup or a stop */ });
}

function tickBar(leftMs) {
  if (!barState) return;
  const b = Object.assign({}, BAR_STYLE, countdown.style.bar || {});
  if (b.demo) {                                              // the editor's preview: keep it scrolling so it can be judged
    if (b.text && !barState.animating && Date.now() >= barState.nextDemo) playBar(barMessage(b.text, 10 - (barState.demoN++ % 10)));
    return;
  }
  const due = barDue(barState, leftMs, b.every);
  if (!due) return;
  if (due.zero) { if (b.zero_text) playBar(b.zero_text); }
  else if (b.text) playBar(barMessage(b.text, due.minutes));
}

/* The big clock's text, sized from its box (percent of the box height) and shrunk if the time is too wide for it. */
function fitCountdown(box) {
  const style = countdown && countdown.style;
  if (!style || !style.box) return;
  const inner = box.querySelector(".cd-box");
  const label = inner.querySelector(".cd-label");
  const time = inner.querySelector(".cd-time");
  const key = [time.textContent.length, label.textContent, window.innerWidth, window.innerHeight, style.size, style.label_size,
    style.box.w, style.box.h].join("|");
  if (box.dataset.fit === key) return;
  box.dataset.fit = key;
  const boxH = window.innerHeight * style.box.h / 100, boxW = window.innerWidth * style.box.w / 100;
  let timePx = boxH * style.size / 100, labelPx = label.textContent ? boxH * style.label_size / 100 : 0;
  const apply = (k) => {
    time.style.setProperty("font-size", timePx * k + "px");
    label.style.setProperty("font-size", labelPx * k + "px");
  };
  apply(1);
  const wide = Math.max(time.getBoundingClientRect().width, label.getBoundingClientRect().width);
  const high = timePx * 1.05 + labelPx * 1.35;
  const k = Math.min(1, (boxW * 0.96) / Math.max(1, wide), (boxH * 0.96) / Math.max(1, high));
  if (k < 1) apply(k);
}

function tickCountdown() {
  const box = document.getElementById("cl-countdown");
  if (!box) return;
  if (!countdown) { box.hidden = true; return; }
  const view = countdownDisplay(countdown, Date.now());
  const asBar = countdown.style.mode === "bar";
  box.hidden = view.gone || asBar;
  box.classList.toggle("ended", view.ended);
  const time = box.querySelector(".cd-time");
  time.textContent = view.text;
  time.style.setProperty("color", view.ended ? countdown.style.ended_color : countdown.style.color);
  if (asBar) tickBar(countdown.end - (Date.now() + countdown.offset));
  else fitCountdown(box);
}

/* Fonts, colors, sizes and where it sits: a rectangle for the projector's clock, a corner or edge for the speaker screens.
   Set through the CSSOM, which the page's strict CSP allows. */
function styleCountdown(box, style) {
  const inner = box.querySelector(".cd-box");
  if (style.box) {
    box.className = "cl-countdown rect" + (box.classList.contains("ended") ? " ended" : "");
    inner.style.setProperty("left", style.box.x + "%");
    inner.style.setProperty("top", style.box.y + "%");
    inner.style.setProperty("width", style.box.w + "%");
    inner.style.setProperty("height", style.box.h + "%");
    inner.querySelector(".cd-time").style.removeProperty("font-size");
    box.dataset.fit = "";
  } else {
    box.className = "cl-countdown pos-" + style.position + (box.classList.contains("ended") ? " ended" : "");
    for (const prop of ["left", "top", "width", "height"]) inner.style.removeProperty(prop);
    inner.querySelector(".cd-label").style.setProperty("font-size", style.size > 0 ? style.label_size + "vh" : "0");
    inner.querySelector(".cd-time").style.setProperty("font-size", style.size + "vh");
  }
  inner.style.setProperty("background", style.backdrop);
  inner.style.setProperty("font-family", style.font);
  inner.querySelector(".cd-label").style.setProperty("color", style.color);
}

function styleAlert(note, style) {
  note.className = "cl-message pos-" + style.position;
  note.style.setProperty("background", style.bg);
  note.style.setProperty("color", style.color);
  note.style.setProperty("font-family", style.font);
  note.style.setProperty("font-size", style.size + "vh");
}

function syncOverlays(payload) {
  const cd = payload.countdown;
  let box = document.getElementById("cl-countdown");
  if (cd) {
    const style = Object.assign({}, COUNTDOWN_STYLE, cd.style || {});
    countdown = { end: cd.end * 1000, offset: cd.now * 1000 - Date.now(), linger: (cd.linger || 5) * 1000, style: style };
    if (!box) {
      box = el("div", "cl-countdown");
      box.id = "cl-countdown";
      const inner = el("div", "cd-box");
      inner.appendChild(el("div", "cd-label"));
      inner.appendChild(el("div", "cd-time"));
      box.appendChild(inner);
      document.body.appendChild(box);
    }
    box.querySelector(".cd-label").textContent = cd.label || "";
    styleCountdown(box, style);
    if (style.mode === "bar") {
      const key = cd.end + "|" + (cd.label || "");
      if (!barState || barState.key !== key) { cancelBar(); barState = { key: key, lastM: null, zeroDone: false, animating: false, token: 0, nextDemo: 0, demoN: 0 }; }
      styleBar(style);
    } else if (barState) {
      cancelBar();
      barState = null;
    }
    if (!countdownTimer) countdownTimer = setInterval(tickCountdown, 250);
    tickCountdown();
  } else {
    // Stopped by the operator: the bar goes at once. Ran out by itself: a bar still scrolling finishes its pass.
    const ranOut = countdown && countdown.end <= Date.now() + countdown.offset;
    countdown = null;
    if (!ranOut || !barState || !barState.animating) { cancelBar(); barState = null; }
    if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    if (box) box.remove();
  }
  let note = document.getElementById("cl-message");
  if (payload.message) {
    if (!note) { note = el("div", "cl-message"); note.id = "cl-message"; note.setAttribute("role", "alert"); document.body.appendChild(note); }
    if (note.textContent !== payload.message) note.textContent = payload.message;
    styleAlert(note, Object.assign({}, ALERT_STYLE, payload.message_style || {}));
  } else if (note) {
    note.remove();
  }
}

async function render(payload, root) {
  const vm = viewModel(payload);
  syncOverlays(payload);
  syncMedia(payload);
  document.body.classList.toggle("stage", vm.audience === "stage");
  document.body.classList.toggle("confidence", payload.audience === "confidence");
  settle();
  if (vm.unknownLayout) { root.replaceChildren(); shown = payload; shownKey = null; return; }
  if (vm.audience === "stage") {
    shownKey = null;
    root.replaceChildren();
    drawStage(vm, root);
    shown = payload;
    return;
  }
  if (sameBlankScreen(shown, payload)) { shown = payload; return; }        // nothing visible changes: leave the screen alone
  const key = screenKey(vm);
  if (key === shownKey) { shown = payload; return; }                       // the same picture and words: no redraw, no blink
  shownKey = key;
  const plan = planTransition(shown, payload);
  const frame = el("div", "frame");
  drawPublic(vm, frame);
  const previous = shown;
  if (!plan) {
    // A picture that is not loaded yet would show as an empty screen for a moment (the background "flickers"
    // between slides). Keep the old frame up until the new pictures are ready, as a transition does.
    if (frame.querySelector("img")) {
      await picturesReady(frame);
      if (shown !== previous) return;
      // Older browsers (the one inside XSplit) can show an empty frame when the old picture is removed in the
      // same moment the new one is added. So the new picture goes on top first, and the old one is taken away
      // only after the new one has had time to paint.
      const old = Array.from(root.children);
      root.appendChild(frame);
      fitLayers(frame);
      shown = payload;
      preload(payload.preload);
      let cleared = false;
      const clear = () => { if (cleared) return; cleared = true; for (const node of old) if (node !== frame) node.remove(); };
      requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(clear, 40)));
      setTimeout(clear, 300);                 // a window that is not being painted never runs the frames above
      return;
    }
    root.replaceChildren(frame);
    fitLayers(frame);
    shown = payload;
    preload(payload.preload);
    return;
  }
  await picturesReady(frame);
  if (shown !== previous) return;          // (cannot happen: payloads are drawn one at a time) stay safe
  const old = Array.from(root.children);
  const steps = transitionSteps(plan.type);
  if (steps.moves === "cover") {
    const cover = el("div", "frame");
    cover.style.setProperty("background", steps.color);       // CSSOM, so the page's strict CSP allows it
    root.appendChild(frame);
    root.appendChild(cover);
    fitLayers(frame);
    shown = payload;
    preload(payload.preload);
    if (!frame.animate || !cover.animate) { cover.remove(); for (const node of old) node.remove(); return; }
    const timing = { duration: plan.ms, easing: "linear", fill: "both" };
    // the new slide is put in at the midpoint, while the cover is solid, so the swap is never seen
    const arrive = frame.animate([{ opacity: 0 }, { opacity: 0, offset: 0.5 }, { opacity: 1, offset: 0.5001 }, { opacity: 1 }], timing);
    const veil = cover.animate([{ opacity: 0 }, { opacity: 1, offset: 0.5 }, { opacity: 0 }], timing);
    const finish = () => {
      if (running !== handle) return;
      running = null;
      arrive.cancel();
      veil.cancel();
      cover.remove();
      for (const node of old) node.remove();
    };
    const handle = { finish };
    running = handle;
    veil.onfinish = finish;
    setTimeout(finish, plan.ms + 600);
    return;
  }
  if (steps.moves === "old") root.insertBefore(frame, old[0] || null);   // the new frame waits underneath
  else root.appendChild(frame);
  fitLayers(frame);
  const mover = steps.moves === "old" ? old[old.length - 1] : frame;
  shown = payload;
  preload(payload.preload);
  if (!mover || !mover.animate) { for (const node of old) node.remove(); return; }
  const animation = mover.animate(steps.keyframes, { duration: plan.ms, easing: "ease-in-out", fill: "both" });
  const done = () => {
    if (running !== handle) return;
    running = null;
    animation.cancel();
    for (const node of old) node.remove();
  };
  const handle = { finish: done };
  running = handle;
  animation.onfinish = done;
  setTimeout(done, plan.ms + 600);          // a backgrounded window may not run the animation; never leave old frames behind
}

/* ---- talking to the server ---- */
const SCHEMA = 1;

/* ---- video and sound ----
   The server only streams the file and says where the clip is: its position when it last changed (pos, at), whether
   it is playing, and the server's clock (now). This page plays it with a <video> or <audio> element and works out
   the position itself, so nothing is polled and no progress is sent. Only the one screen named in plays_sound makes
   sound; the others (and every preview) show the picture without it, or just a label. */
const DRIFT_PLAYING = 0.5;          // seconds out before the page jumps to where the clip should be
const DRIFT_PAUSED = 0.05;
const DRIFT_PICTURE = 1.0;          // a silent picture may trail its clock this far before it jumps (the sound is made elsewhere)
let skew = 0;                       // server clock minus this computer's clock, in seconds
let clips = { video: null, clip: null, music: null, page: null };      // the live slide's video, its sound slide, the music under the slides
let soundBlocked = false;           // the browser would not start sound without a click (an ordinary tab; ChurchLight's own windows are allowed)

function clipPosition(view, serverNow) {
  let position = view.pos + (view.playing ? Math.max(0, serverNow - view.at) : 0);      // before `at` the clip has not started
  const length = view.duration || 0;
  if (length > 0) position = view.loop ? position % length : Math.min(position, length);
  return Math.max(0, position);
}

function clipDistance(a, b, view) {          // how far apart two positions are (a looping clip wraps round)
  const gap = Math.abs(a - b);
  return view.loop && view.duration > 0 ? Math.min(gap, view.duration - gap) : gap;
}

function mediaLayer() {
  let box = document.getElementById("cl-media");
  if (!box) {
    box = el("div", "");
    box.id = "cl-media";
    document.body.insertBefore(box, document.getElementById("root"));
  }
  return box;
}

let spareVideo = null;               // the page's one <video>, kept between clips: making and breaking a 1080p decoder for every clip
let spareTimer = null;               // can run a computer out of them when clips are started and cleared quickly

function dropClip(slot) {
  const held = clips[slot];
  if (!held) return;
  clips[slot] = null;
  if (held.retry) clearTimeout(held.retry);
  if (held.startTimer) clearTimeout(held.startTimer);
  held.node.__held = null;
  held.node.pause();
  if (held.node === spareVideo) {
    held.node.style.display = "none";
    clearTimeout(spareTimer);
    spareTimer = setTimeout(() => {          // let go of the file once nothing has used the player for a while
      if (!clips.video && spareVideo) { spareVideo.removeAttribute("src"); spareVideo.load(); }
    }, 10000);
    return;
  }
  held.node.removeAttribute("src");
  held.node.load();                          // let go of the file
  held.node.remove();
}

function driveClip(held) {
  const view = held.view, node = held.node;
  const sound = view.plays_sound === true && pageMaySound && !view.muted && !view.silent && !soundBlocked;
  node.muted = !sound;
  node.volume = Math.min(1, Math.max(0, view.volume));
  node.loop = !!view.loop;
  const target = clipPosition(view, Date.now() / 1000 + skew);
  const anchor = view.pos + "|" + view.at + "|" + view.playing + "|" + view.loop;
  const fresh = held.anchor !== anchor;                  // the operator just pressed play, pause, restart or moved the bar
  held.anchor = anchor;
  const picture = node.tagName === "VIDEO" && node.muted;    // a silent picture only: a late frame matters less than a jump
  const limit = view.playing ? (picture ? DRIFT_PICTURE : DRIFT_PLAYING) : DRIFT_PAUSED;
  // A clip that is about to start (its start time is a little ahead, so the screen can show the slide first) waits,
  // paused at its first frame, and is driven again the moment its time comes.
  const waitMs = view.playing ? (view.at - (Date.now() / 1000 + skew)) * 1000 : 0;
  const starting = waitMs > 0;
  if (starting && !held.startTimer) {
    held.startTimer = setTimeout(() => { held.startTimer = null; if (clips[held.slot] === held) driveClip(held); }, waitMs + 15);
  }
  const distance = node.readyState > 0 ? clipDistance(node.currentTime, target, view) : 0;
  if (fresh && node.readyState > 0 && distance > 0.15) {
    held.lastJump = Date.now();
    node.currentTime = target;                           // what the operator asked for is never held back
  } else if (distance > limit) {
    // A long file that is still loading, or a computer that cannot keep up, falls behind its clock; jumping again and
    // again only makes that worse and plays pieces of the file over each other. So: never while it is already jumping or
    // stalled, and not more than once every two seconds (four for a picture).
    const waiting = node.seeking || (view.playing && node.readyState < 3);
    if (!waiting && Date.now() - (held.lastJump || 0) > (picture ? 4000 : 2000)) { held.lastJump = Date.now(); node.currentTime = target; }
    else if (!held.retry) held.retry = setTimeout(() => { held.retry = null; if (clips[held.slot] === held) driveClip(held); }, 2100);
  }
  if (starting) {
    if (!node.paused) node.pause();
  } else if (view.playing) {
    if (node.paused) {
      const started = node.play();
      if (started && started.catch) started.catch((err) => {
        if (!node.muted) {
          // The browser would not start sound without a click: play without sound, and ask for the click.
          if (err && err.name !== "NotAllowedError") { retryClip(held); return; }
          if (soundBlocked) return;
          soundBlocked = true;
          driveClip(held);
          showSoundHint();
        } else {
          retryClip(held);                       // a silent clip was turned away (still loading, or interrupted): try again
        }
      });
    }
  } else if (!node.paused) {
    node.pause();
  }
}

function retryClip(held) {
  held.tries = (held.tries || 0) + 1;
  if (held.tries > 10 || clips[held.slot] !== held) return;
  setTimeout(() => { if (clips[held.slot] === held) driveClip(held); }, 400 * held.tries);
}

function showSoundHint() {
  if (document.getElementById("cl-sound-hint")) return;
  const hint = el("div", "media-hint", "Click this page once to turn the sound on");
  hint.id = "cl-sound-hint";
  document.body.appendChild(hint);
  document.addEventListener("click", () => {
    soundBlocked = false;
    hint.remove();
    for (const slot of Object.keys(clips)) if (clips[slot]) driveClip(clips[slot]);
  }, { once: true });
}

/* A video the browser cannot show (a wrong format, or a damaged file) is told to the program once, so the operator sees what is
   wrong and what to do instead of a frozen or black screen. Only the live video is reported; the program ignores anything else. */
const reportedProblems = new Set();
function reportClipProblem(held) {
  if (!held || held.slot !== "video" || reportedProblems.has(held.view.src)) return;
  reportedProblems.add(held.view.src);
  fetch("/api/do", {
    method: "POST", headers: { "Content-Type": "application/json", "X-ChurchLight": "output" },
    body: JSON.stringify({ action: "clip_problem", args: { src: held.view.src, output: decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "") } }),
  }).catch(() => { reportedProblems.delete(held.view.src); });
}

/* A video that never gets a picture at all (stuck loading from the start) is the other way a bad file shows itself. A video that
   has played and then waits (after jumping to another place in a long video, say) is only busy and is never reported. */
let stuckTimer = null;
function watchForStuckVideo() {
  if (stuckTimer) return;
  stuckTimer = setInterval(() => {
    const held = clips.video;
    if (!held) return;
    if (held.node.readyState >= 2) held.hasPlayed = true;
    if (held.hasPlayed || !held.view.playing) { held.stuckSince = 0; return; }
    held.stuckSince = held.stuckSince || Date.now();
    if (Date.now() - held.stuckSince > 20000) reportClipProblem(held);
  }, 2000);
}

function makeClipNode(kind) {
  const node = document.createElement(kind);
  node.preload = "auto";
  node.setAttribute("playsinline", "");
  const now = () => node.__held;
  node.addEventListener("loadedmetadata", () => { if (now()) driveClip(now()); });
  node.addEventListener("loadeddata", () => { if (now()) driveClip(now()); });
  node.addEventListener("playing", () => { if (now()) now().tries = 0; setStatus(""); });
  node.addEventListener("waiting", () => setStatus("Loading the " + kind + "…"));       // shown on the stage page or with ?debug only
  node.addEventListener("stalled", () => setStatus("The " + kind + " is slow to load…"));
  node.addEventListener("error", () => {
    if (!now()) return;
    setStatus("This " + kind + " could not be played. Is it an MP4 (H.264) or MP3/WAV file?");
    const code = node.error ? node.error.code : 0;
    if (code === 3 || code === 4) reportClipProblem(now());          // cannot be decoded / not a supported format; not a dropped network read or a stop
  });
  return node;
}

function syncClip(slot, kind, view) {
  let held = clips[slot];
  if (view && held && held.view.src !== view.src) { dropClip(slot); held = null; }
  if (!view) { dropClip(slot); return; }
  if (!held) {
    // The sound page (?hear) only has to be heard: an <audio> element plays a video's sound without decoding its picture.
    const tag = pageParams.has("hear") ? "audio" : kind;
    let node;
    if (tag === "video") {
      if (!spareVideo) { spareVideo = makeClipNode("video"); mediaLayer().appendChild(spareVideo); }
      clearTimeout(spareTimer);
      node = spareVideo;
      node.style.display = "";
    } else {
      node = makeClipNode(tag);
      document.body.appendChild(node);
    }
    held = { node: node, view: view, slot: slot };
    node.__held = held;
    clips[slot] = held;
    node.src = mediaUrl(view.src);
  }
  held.view = view;
  driveClip(held);
}

/* ?debug: a small line per clip at the bottom of the screen, so a slow or stuck picture can be described exactly. */
let debugTimer = null;
function showClipDebug() {
  let box = document.getElementById("cl-debug");
  if (!box) { box = el("div", "debug-line"); box.id = "cl-debug"; document.body.appendChild(box); }
  const rows = [];
  for (const slot of Object.keys(clips)) {
    const held = clips[slot];
    if (!held) continue;
    const node = held.node, view = held.view;
    const quality = node.getVideoPlaybackQuality ? node.getVideoPlaybackQuality() : null;
    const target = clipPosition(view, Date.now() / 1000 + skew);
    rows.push(slot + " " + node.tagName.toLowerCase() + ": at " + node.currentTime.toFixed(1) + " s, should be " + target.toFixed(1) +
      ", ready " + node.readyState + (node.paused ? ", paused" : ", playing") + (node.muted ? ", silent" : "") +
      (quality ? ", frames " + quality.totalVideoFrames + " (" + quality.droppedVideoFrames + " dropped)" : "") +
      (node.error ? ", error " + node.error.code : ""));
  }
  box.textContent = rows.join("\n");
}

function syncMedia(payload) {
  if (pageParams.has("debug") && !debugTimer) debugTimer = setInterval(showClipDebug, 500);
  const label = document.getElementById("cl-media-label");
  if (peeking || sandboxed) {                // a preview shows what is playing in words, so a second copy never plays
    const view = payload.media;
    if (view) {
      const text = (view.kind === "audio" ? "Sound: " : "Video: ") + (view.name || "") + (view.playing ? "" : " (paused)");
      const box = label || el("div", "media-label");
      box.id = "cl-media-label";
      box.textContent = text;
      if (!label) document.body.appendChild(box);
    } else if (label) {
      label.remove();
    }
    return;
  }
  watchForStuckVideo();
  const stamp = (payload.media && payload.media.now) || (payload.sound && payload.sound.now);
  if (stamp) skew = stamp - Date.now() / 1000;
  const live = payload.media || null;
  syncClip("video", "video", live && live.kind === "video" ? live : null);
  syncClip("clip", "audio", live && live.kind === "audio" ? live : null);       // a sound slide: nothing to draw, only to hear
  syncClip("music", "audio", payload.sound || null);
  syncClip("page", "audio", payload.page_sound || null);                     // the sound file a slideshow page carries
}

function setStatus(text) {
  const status = document.getElementById("status");
  status.textContent = text;
  // Public pages are on air: only show a problem on the stage page, or with ?debug.
  const visible = document.body.classList.contains("stage") ||
    new URLSearchParams(location.search).has("debug");
  status.className = (text ? "show" : "") + (visible ? " visible" : "");
}

const peeking = typeof location !== "undefined" && new URLSearchParams(location.search).has("peek");   // the operator's preview frames
const sandboxed = typeof location !== "undefined" && new URLSearchParams(location.search).has("sandbox");
/* Where the sound is made. ChurchLight's own window does it: the operator page keeps one hidden copy of the sound
   screen open (?hear), so the sound comes out of this computer, and OBS or XSplit pick it up as the computer's sound.
   Every other page is silent, so a clip is never heard twice (an OBS or XSplit web page, a full-screen test window, a
   browser tab). A page that should make the sound itself (OBS "Control audio via OBS") says so: ?sound=on. */
const pageParams = typeof location !== "undefined" ? new URLSearchParams(location.search) : new URLSearchParams();
const pageMaySound = pageParams.has("hear") || ["on", "obs", "1", "yes"].includes(pageParams.get("sound") || "");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function run(name) {
  const root = document.getElementById("root");
  let since = null;
  let failures = 0;
  let build = null;
  for (;;) {
    try {
      const peek = peeking ? "peek=1" : "";
      const query = since === null ? (peek ? "?" + peek : "") : "?since=" + since + "&wait=20" + (peek ? "&" + peek : "");
      const res = await fetch("/out/" + encodeURIComponent(name) + query, { cache: "no-store" });
      if (res.status === 304) continue;
      if (res.status === 404) throw new Error("No output named " + name);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const payload = await res.json();
      if (payload.schema !== SCHEMA) throw new Error("This page is out of date; reload it");
      // The program was restarted (maybe updated) while this page stayed open: reload, so the page runs the new code.
      if (failures > 0 && !sandboxed) { location.reload(); return; }
      if (payload.build) {                       // the program's page files changed since this page loaded: start over
        if (build !== null && payload.build !== build && !sandboxed) { location.reload(); return; }
        build = payload.build;
      }
      since = payload.version;
      await render(payload, root);
      setStatus("");
      failures = 0;
    } catch (err) {
      failures += 1;
      setStatus(failures > 1 || since === null ? String(err.message || err) : "Reconnecting…");
      await sleep(Math.min(5000, 500 * failures));
    }
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { clipPosition, barMessage, barDue, formatCountdown, countdownDisplay, wantsBalance, textMargins, screenKey, breakText, sameBlankScreen, viewModel, styleToCss, hasTextBand, boxPosition, PLACEMENT, slideKey, planTransition, transitionSteps, TRANSITION_TYPES };
} else {
  window.addEventListener("resize", () => {              // the countdown's sizes are worked out from the window
    if (!countdown) return;
    styleBar(countdown.style);
    const box = document.getElementById("cl-countdown");
    if (box) box.dataset.fit = "";
    tickCountdown();
  });
  // ?kiosk: ChurchLight opened this page full screen on a monitor. It has no close key on purpose: the program closes it.
  // ?preview: a dark backdrop, for looking at a transparent page in an ordinary browser tab.
  if (new URLSearchParams(location.search).has("preview")) document.documentElement.classList.add("preview");
  if (new URLSearchParams(location.search).has("sandbox")) {
    // ?sandbox: the Themes tab draws sample slides here. No polling; only this page's parent may send.
    const root = document.getElementById("root");
    window.addEventListener("message", (event) => {
      if (event.origin !== location.origin || event.source !== window.parent) return;
      const payload = event.data;
      if (payload && payload.schema === SCHEMA && ["public", "stage", "confidence"].includes(payload.audience)) {
        render(payload, root).then(() => {
          // Tell the Themes tab how tall the lower-third box came out, so its guide box can match.
          const lower = root.querySelector(".layer.lower");
          const height = lower ? lower.getBoundingClientRect().height / window.innerHeight : null;
          window.parent.postMessage({ sandbox: "measure", lowerHeight: height }, location.origin);
        });
      }
    });
    window.parent.postMessage({ sandbox: "ready" }, location.origin);
  } else if (new URLSearchParams(location.search).has("test")) {
    // ?test: a plain white screen with TEST, so the church can see which monitor the screen will go on.
    document.documentElement.classList.add("testscreen");
    // ?word=PROJECTOR: the setup guide names the screen, so the church can see which monitor is which.
    const word = (new URLSearchParams(location.search).get("word") || "TEST").slice(0, 20);
    const big = el("div", "testscreen-word", word);
    big.style.fontSize = "min(30vmin, " + Math.floor(100 / (word.length * 0.8)) + "vw)";       // a long word still fits across
    document.getElementById("root").replaceChildren(big);
  } else {
    run(decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || ""));
  }
}
