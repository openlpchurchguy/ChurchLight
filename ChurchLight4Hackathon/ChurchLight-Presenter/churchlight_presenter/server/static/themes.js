/* The Themes tab: build and edit themes, see them live, and choose which theme is used where.
   A theme is for one kind of output: the projector, the overlay (a translucent box over the broadcast)
   or the confidence monitor (the speaker's screen). Pure functions come first (testable in node); the
   page code below uses helpers from operator.js ($, el, act, state), which are only called after that
   file has loaded. */
"use strict";

/* [CSS font-family value, name shown in the list]. The first group is bundled with the program (themefonts.css),
   so it looks the same on every PC; the system fonts after it only exist where they are installed. */
const THEME_FONTS = [
  ["'Open Sans', Arial, sans-serif", "Open Sans (clean)"],
  ["'Lato', Arial, sans-serif", "Lato (warm)"],
  ["'Source Sans 3', Arial, sans-serif", "Source Sans 3 (clean)"],
  ["'Roboto', Arial, sans-serif", "Roboto (sturdy)"],
  ["'Nunito', Arial, sans-serif", "Nunito (rounded, friendly)"],
  ["'Atkinson Hyperlegible', Arial, sans-serif", "Atkinson Hyperlegible (easy to read far away)"],
  ["'Montserrat', Arial, sans-serif", "Montserrat (modern, bold)"],
  ["'Poppins', Arial, sans-serif", "Poppins (modern, geometric)"],
  ["'Oswald', Impact, sans-serif", "Oswald (condensed, titles)"],
  ["'Anton', Impact, sans-serif", "Anton (heavy, titles)"],
  ["'Merriweather', Georgia, serif", "Merriweather (sturdy serif)"],
  ["'Lora', Georgia, serif", "Lora (elegant serif)"],
  ["'Libre Baskerville', Georgia, serif", "Libre Baskerville (classic serif)"],
  ["'PT Serif', Georgia, serif", "PT Serif (plain serif)"],
  ["'Playfair Display', Georgia, serif", "Playfair Display (dramatic, titles)"],
  ["'Cinzel', Georgia, serif", "Cinzel (Roman capitals, titles)"],
  ["'Dancing Script', cursive", "Dancing Script (script, titles only)"],
  ["Arial, Helvetica, sans-serif", "Arial (system font)"],
  ["Verdana, Geneva, sans-serif", "Verdana (system font)"],
  ["Trebuchet MS, sans-serif", "Trebuchet MS (system font)"],
  ["Segoe UI, Roboto, sans-serif", "Segoe UI (system font)"],
  ["Calibri, Carlito, sans-serif", "Calibri (system font)"],
  ["Georgia, serif", "Georgia (system font)"],
  ["Times New Roman, Times, serif", "Times New Roman (system font)"],
  ["Impact, Haettenschweiler, sans-serif", "Impact (system font, heavy)"],
];

const THEME_KINDS = [
  { value: "projector", text: "Projector", full: "The room screen (styles, colors)",
    heading: "Projector themes" },
  { value: "overlay", text: "Overlay (lower third)", full: "The overlay laid over the livestream (where the verse sits)",
    heading: "Overlay themes (lower third)" },
  { value: "confidence", text: "Confidence monitor", full: "The speaker's monitor (large, readable text)",
    heading: "Confidence monitor themes" },
];

/* Every style key has a value here, so each control always has something to show. */
const THEME_DEFAULTS = {
  font_family: "Arial, Helvetica, sans-serif", font_size: 7, color: "#ffffff", line_height: 1.3,
  align: "center", shadow: true, uppercase: false, band: true, band_color: "#000000b0",
  background_color: "#000000", background_fit: "fill", background_dim: 0, reference_scale: 0.6, title_scale: 1.2, reference_pos: "auto", balance_lines: false, projector_hide: false, number_font: "Segoe UI, Roboto, sans-serif", v_pos: "bottom", h_pos: "center", box_width: 90, margin: 4, max_height: 0,
  margin_top: 4, margin_bottom: 4, margin_left: 6, margin_right: 6,
};
const KIND_DEFAULTS = {
  projector: {},
  overlay: { font_size: 5, align: "left", shadow: false, band: true, band_color: "#101820d0", reference_scale: 0.7,
             v_pos: "bottom", h_pos: "left", box_width: 60 },
  confidence: { font_size: 12, line_height: 1.2, shadow: false, background_color: "#000000", reference_scale: 0.5, balance_lines: true },
};
const BUILD_DEFAULTS = { lines_per_slide: 4, scripture_max_chars: 220, scripture_split: "verse", chars_per_line: 0, song_count: "lines" };

/* Which controls each kind of theme shows. `key` names a builder in the page code below. */
const CONTROL_SPEC = [
  { key: "font", group: "text", label: "Font", kinds: ["projector", "overlay", "confidence"] },
  { key: "size", group: "text", label: "Text size", kinds: ["projector", "overlay", "confidence"] },
  { key: "color", group: "text", label: "Text color", kinds: ["projector", "overlay", "confidence"] },
  { key: "line_height", group: "text", label: "Line spacing", kinds: ["projector", "overlay", "confidence"] },
  { key: "align", group: "text", label: "Alignment", kinds: ["projector", "overlay", "confidence"] },
  { key: "shadow", group: "text", label: "Shadow behind text", kinds: ["projector", "overlay"] },
  { key: "uppercase", group: "text", label: "ALL CAPITALS", kinds: ["projector", "overlay", "confidence"] },
  { key: "v_pos", group: "place", label: "Up and down", kinds: ["overlay"] },
  { key: "h_pos", group: "place", label: "Left to right", kinds: ["overlay"] },
  { key: "box_width", group: "place", label: "Box width", kinds: ["overlay"] },
  { key: "margin", group: "place", label: "Gap to the screen edge", kinds: ["overlay"] },
  { key: "max_height", group: "place", label: "Tallest the box may grow", kinds: ["overlay"] },
  { key: "margin_top", group: "place", label: "Space above the text", kinds: ["projector"] },
  { key: "margin_bottom", group: "place", label: "Space below the text", kinds: ["projector"] },
  { key: "margin_left", group: "place", label: "Space left of the text", kinds: ["projector"] },
  { key: "margin_right", group: "place", label: "Space right of the text", kinds: ["projector"] },
  { key: "background_color", group: "background", label: "Full-screen color", kinds: ["projector", "confidence"] },
  { key: "picture", group: "background", label: "Background picture", kinds: ["projector"] },
  { key: "background_fit", group: "background", label: "Picture size", kinds: ["projector"] },
  { key: "background_dim", group: "background", label: "Darken the picture", kinds: ["projector"] },
  { key: "band", group: "background", label: "Band behind the text", kinds: ["projector"] },
  { key: "band_color", group: "background", label: "Box color", kinds: ["overlay"] },
  { key: "band_color_projector", group: "background", label: "Band color", kinds: ["projector"] },
  { key: "reference_scale", group: "bible", label: "Reference size", kinds: ["projector", "overlay", "confidence"] },
  { key: "projector_hide", group: "bible", label: "Lower third only (for a verse over a slideshow: keep it off the projector)", kinds: ["overlay"] },
  { key: "reference_pos", group: "bible", label: "Reference position", kinds: ["overlay"] },
  { key: "title_scale", group: "bible", label: "Book and chapter size", kinds: ["projector", "overlay", "confidence"] },
  { key: "number_font", group: "bible", label: "Verse number font", kinds: ["projector", "overlay", "confidence"] },
  { key: "scripture_split", group: "bible", label: "Slide length", kinds: ["projector"] },
  { key: "scripture_lines", group: "bible", label: "Room for text", kinds: ["projector"] },
  { key: "balance_lines", group: "songs", label: "Balance long lines", kinds: ["projector", "overlay", "confidence"] },
  { key: "lines_per_slide", group: "songs", label: "Lines per slide", kinds: ["projector"] },
  { key: "song_count", group: "songs", label: "Divide songs by", kinds: ["projector"] },
];
const GROUP_TITLES = { text: "Text", place: "Where the box sits", background: "Backgrounds", bible: "Bible verses", songs: "Songs" };

function controlsFor(kind) {
  return CONTROL_SPEC.filter((spec) => spec.kinds.includes(kind));
}

function kindInfo(kind) {
  return THEME_KINDS.find((k) => k.value === kind) || THEME_KINDS[0];
}

/* "#rrggbbaa" -> { hex: "#rrggbb", alpha: 0..1 } */
function splitColor(value) {
  let c = String(value || "#000000").toLowerCase();
  if (/^#[0-9a-f]{3}$/.test(c)) c = "#" + c.slice(1).split("").map((d) => d + d).join("");
  if (!/^#[0-9a-f]{6}([0-9a-f]{2})?$/.test(c)) return { hex: "#000000", alpha: 1 };
  return { hex: c.slice(0, 7), alpha: c.length === 9 ? parseInt(c.slice(7), 16) / 255 : 1 };
}

function joinColor(hex, alpha) {
  const a = Math.max(0, Math.min(1, Number(alpha)));
  if (a >= 0.995) return hex.toLowerCase();
  return hex.toLowerCase() + Math.round(a * 255).toString(16).padStart(2, "0");
}

/* A theme as the editor holds it: every setting present, so each control has a value. */
function themeDraft(theme, kind) {
  const chosen = theme ? (theme.kind || "projector") : (kind || "projector");
  const base = Object.assign({}, THEME_DEFAULTS, KIND_DEFAULTS[chosen] || {});
  if (!theme) return { id: null, kind: chosen, name: "", style: base, build: Object.assign({}, BUILD_DEFAULTS) };
  return {
    id: theme.id, kind: chosen, name: theme.name,
    style: Object.assign(base, theme.style || {}),
    build: Object.assign({}, BUILD_DEFAULTS, theme.build || {}),
  };
}

function draftKey(draft) {
  return JSON.stringify([draft.kind, draft.name, draft.style, draft.build]);
}

/* Sample content for the preview. Each is a payload exactly like the output pages receive. */
const PREVIEW_SAMPLES = {
  lyrics: { label: "Lyrics", slide: {
    kind: "song", item_type: "song", label: "Verse 1", reference: "Amazing Grace",
    text: "Amazing grace, how sweet the sound\nThat saved a wretch like me\nI once was lost, but now am found\nWas blind, but now I see" } },
  lyrics_short: { label: "Lyrics", slide: {
    kind: "song", item_type: "song", label: "Chorus", reference: "Amazing Grace",
    text: "My chains are gone, I've been set free\nMy God, my Savior has ransomed me" } },
  bible: { label: "Bible verse", slide: {
    kind: "scripture", item_type: "scripture", label: "KJV", reference: "John 3", title_first: true,
    verse_numbers: [16, 17],
    segments: [[16, "For God so loved the world, that he gave his only begotten Son, that whosoever believeth in him should not perish, but have everlasting life."],
               [17, "For God sent not his Son into the world to condemn the world."]] } },
  bible_long: { label: "Longest verse", slide: {
    kind: "scripture", item_type: "scripture", label: "KJV", reference: "Esther 8:9", title_first: true,
    verse_numbers: [9],
    segments: [[9, "Then were the king's scribes called at that time in the third month, that is, the month Sivan, on the three and twentieth day thereof; and it was written according to all that Mordecai commanded unto the Jews, and to the lieutenants, and the deputies and rulers of the provinces which are from India unto Ethiopia, an hundred twenty and seven provinces, unto every province according to the writing thereof, and unto every people after their language, and to the Jews according to their writing, and according to their language."]] } },
  bible_many: { label: "Several verses", many: true, slide: {
    kind: "scripture", item_type: "scripture", label: "KJV", reference: "Psalm 23", title_first: true,
    verse_numbers: [1],
    segments: [[1, "The LORD is my shepherd; I shall not want."],
               [2, "He maketh me to lie down in green pastures: he leadeth me beside the still waters."],
               [3, "He restoreth my soul: he leadeth me in the paths of righteousness for his name's sake."],
               [4, "Yea, though I walk through the valley of the shadow of death, I will fear no evil: for thou art with me; thy rod and thy staff they comfort me."],
               [5, "Thou preparest a table before me in the presence of mine enemies: thou anointest my head with oil; my cup runneth over."],
               [6, "Surely goodness and mercy shall follow me all the days of my life: and I will dwell in the house of the LORD for ever."]] } },
  verse_short: { label: "Bible verse", slide: {
    kind: "scripture", item_type: "scripture", label: "KJV", reference: "John 3:16", title_first: false,
    verse_numbers: [16],
    segments: [[16, "For God so loved the world, that he gave his only begotten Son."]] } },
  announcement: { label: "Announcement", slide: {
    kind: "announcement", item_type: "announcement", label: "Announcement",
    text: "Potluck lunch after the service\nBring a dish to share" } },
};
const SAMPLES_BY_KIND = {
  projector: ["lyrics", "bible", "bible_long", "bible_many", "announcement"],
  overlay: ["lyrics_short", "verse_short"],
  confidence: ["lyrics", "bible"],
};
const LAYOUT_BY_KIND = { projector: "projector", overlay: "lower_third", confidence: "confidence" };

/* The same arithmetic as projector_char_capacity() in core/themes.py (a test keeps the two equal):
   how many lines of text, and about how many characters, fit on the projector for this look. */
function projectorLayout(style, aspect) {
  style = style || {};
  const size = style.font_size || 7;
  const lineHeight = style.line_height || 1.3;
  const title = style.title_scale || 1.2;
  const width = (aspect || 16 / 9) * 100;
  const mv = (style.margin_top === undefined ? 4 : style.margin_top) + (style.margin_bottom === undefined ? 4 : style.margin_bottom);
  const mh = (style.margin_left === undefined ? 6 : style.margin_left) + (style.margin_right === undefined ? 6 : style.margin_right);
  let height = 100 - mv, room = width * (100 - mh) / 100;
  if (style.band === true) { height -= 3; room -= width * 0.06; }
  const lines = Math.max(1, Math.floor((height - size * title * (lineHeight + 0.35)) / (size * lineHeight)));
  const letter = style.uppercase ? 0.62 : 0.5;
  const perLine = Math.max(8, Math.floor(room / (size * letter) * 0.85));
  return { lines: lines, chars: Math.max(40, Math.floor(lines * perLine * 0.9)) };
}

/* Which verses share the first slide when "fit as many whole verses" is chosen. */
function packSegments(segments, chars, mode) {
  if (mode !== "chars") return segments.slice(0, 1);
  const out = [];
  let used = 0;
  for (const seg of segments) {
    const cost = String(seg[1]).length + (out.length ? 1 : 0);
    if (out.length && used + cost > chars) break;
    out.push(seg); used += cost;
  }
  return out;
}

/* ---- the text-area guide: a box over the preview that shows (and drags) where text may go ----
   Everything is a fraction of the screen (0 to 1) from its top-left corner. Pure, so it is tested without a browser. */
const MARGIN_MAX = { margin_top: 40, margin_bottom: 40, margin_left: 45, margin_right: 45 };
const MIN_TEXT_AREA = 20;
const GUIDE_ASPECT = 16 / 9;

const snap = (value, step) => Math.round(value / step) * step;
const clampTo = (value, low, high) => Math.max(low, Math.min(high, value));
const pick = (style, key, fallback) => (typeof style[key] === "number" ? style[key] : fallback);

/* Keep the margins leaving room for the text after `changed` was set (the server refuses less). */
function clampMargins(style, changed) {
  const pair = { margin_top: "margin_bottom", margin_bottom: "margin_top", margin_left: "margin_right", margin_right: "margin_left" }[changed];
  if (!pair) return style;
  const other = pick(style, pair, pair.endsWith("top") || pair.endsWith("bottom") ? 4 : 6);
  style[changed] = clampTo(style[changed], 0, Math.min(MARGIN_MAX[changed], 100 - MIN_TEXT_AREA - other));
  return style;
}

/* The box a theme leaves for its text: projector themes by their four margins, overlay themes by their
   position, width and height cap (`contentHeight` = how tall the box really came out when there is no cap). */
function guideRect(kind, style, contentHeight) {
  if (kind === "projector") {
    return { left: pick(style, "margin_left", 6) / 100, top: pick(style, "margin_top", 4) / 100,
             right: 1 - pick(style, "margin_right", 6) / 100, bottom: 1 - pick(style, "margin_bottom", 4) / 100 };
  }
  if (kind !== "overlay") return null;
  const width = pick(style, "box_width", 90) / 100;
  const gapY = pick(style, "margin", 4) / 100;
  const gapX = gapY / GUIDE_ASPECT;                       // the gap is a share of the height; this is the same gap across
  const cap = pick(style, "max_height", 0) / 100;
  const height = cap > 0 ? cap : (contentHeight > 0 ? contentHeight : 0.15);
  let left;
  if (style.h_pos === "left") left = gapX;
  else if (style.h_pos === "right") left = 1 - gapX - width;
  else left = (1 - width) / 2;
  let top;
  if (style.v_pos === "top") top = gapY;
  else if (style.v_pos === "middle") top = (1 - height) / 2;
  else top = 1 - gapY - height;
  return { left: left, top: top, right: left + width, bottom: top + height };
}

/* Dragging one edge of the guide to `pos`: the style changes that follow (an object of key: value).
   For an overlay, the edge next to where the box sits moves the box (its gap to the screen edge) and the
   other edges resize it. */
function dragGuide(kind, style, edge, pos, contentHeight) {
  const patch = {};
  if (kind === "projector") {
    const key = { left: "margin_left", right: "margin_right", top: "margin_top", bottom: "margin_bottom" }[edge];
    const raw = edge === "left" || edge === "top" ? pos * 100 : (1 - pos) * 100;
    const draft = Object.assign({}, style, { [key]: snap(raw, 0.5) });
    patch[key] = clampMargins(draft, key)[key];
    return patch;
  }
  if (kind !== "overlay") return patch;
  const rect = guideRect(kind, style, contentHeight);
  const horizontal = edge === "left" || edge === "right";
  const near = horizontal ? (style.h_pos || "center") : (style.v_pos || "bottom");
  const anchoredHere = near === edge;           // the edge on the side the box is anchored to
  const setMargin = (share, perHeight) => { patch.margin = clampTo(snap(perHeight ? share * 100 : share * 100 * GUIDE_ASPECT, 0.5), 0, 15); };
  if (horizontal) {
    const width = edge === "left" ? rect.right - pos : pos - rect.left;
    if (anchoredHere) setMargin(edge === "left" ? pos : 1 - pos, false);
    else if (near === "center") patch.box_width = clampTo(snap(Math.abs(pos - 0.5) * 2 * 100, 1), 20, 100);
    else patch.box_width = clampTo(snap(width * 100, 1), 20, 100);
  } else {
    const height = edge === "top" ? rect.bottom - pos : pos - rect.top;
    if (anchoredHere) setMargin(edge === "top" ? pos : 1 - pos, true);
    else if (near === "middle") patch.max_height = clampTo(snap(Math.abs(pos - 0.5) * 2 * 100, 1), 8, 60);
    else patch.max_height = clampTo(snap(height * 100, 1), 8, 60);
  }
  return patch;
}

function previewPayload(sampleKey, style, kind, build) {
  const sample = PREVIEW_SAMPLES[sampleKey] || PREVIEW_SAMPLES.lyrics;
  const chosen = kind || "projector";
  const slide = Object.assign({ segments: [], verse_numbers: [], continued: false, image_path: null,
    title_first: false, source_item_id: null, theme_id: null }, sample.slide, { style: Object.assign({}, style) });
  if (sample.many) {
    const chosen2 = packSegments(sample.slide.segments, projectorLayout(style).chars, (build || {}).scripture_split);
    slide.segments = chosen2;
    slide.verse_numbers = chosen2.map((s) => s[0]);
    slide.reference = "Psalm 23:" + (chosen2.length > 1 ? chosen2[0][0] + "–" + chosen2[chosen2.length - 1][0] : chosen2[0][0]);
  }
  if (slide.kind === "scripture") slide.text = slide.segments.map((s) => s[1]).join(" ");
  return { schema: 1, version: 1, output: "preview", layout: LAYOUT_BY_KIND[chosen] || "projector",
    audience: chosen === "confidence" ? "confidence" : "public", theme_id: null, blank: false, base: slide, overlay: null };
}

/* The theme names grouped by kind, in the order the list shows them. */
function groupThemes(themes) {
  return THEME_KINDS.map((k) => ({ kind: k.value, heading: k.heading,
    themes: (themes || []).filter((t) => (t.kind || "projector") === k.value) }));
}

function themeToSave(draft) {
  const style = Object.assign({}, draft.style);
  if (!style.background_image) delete style.background_image;
  return { name: draft.name.trim(), style: style, build: draft.build, kind: draft.kind };
}

function isUsed(theme) {
  const u = theme.usage || {};
  return !!(u.default || u.bible || u.verse_over || u.confidence || u.services || u.items || u.outputs || u.songs);
}

function byName(a, b) {
  return String(a.name).localeCompare(String(b.name), undefined, { sensitivity: "base", numeric: true });
}

/* Themes that are used come first; each section is sorted by name. */
function themeSections(themes) {
  const sorted = (themes || []).slice().sort(byName);
  return { inUse: sorted.filter(isUsed), rest: sorted.filter((t) => !isUsed(t)) };
}

function filterThemes(themes, text) {
  const needle = String(text || "").trim().toLowerCase();
  return (themes || []).filter((t) => !needle || String(t.name).toLowerCase().includes(needle));
}

/* Where is this theme used? A sentence for the list and the delete warning. */
function usageText(usage) {
  const parts = [];
  if (usage.default) parts.push("start of new services");
  if (usage.bible) parts.push("Bible verses");
  if (usage.verse_over) parts.push("verses over a slideshow");
  if (usage.confidence) parts.push("the confidence monitor");
  if (usage.services) parts.push(usage.services + (usage.services === 1 ? " service" : " services"));
  if (usage.items) parts.push(usage.items + (usage.items === 1 ? " item" : " items"));
  if (usage.songs) parts.push(usage.songs + (usage.songs === 1 ? " song" : " songs"));
  if (usage.outputs) parts.push(usage.outputs + (usage.outputs === 1 ? " screen" : " screens"));
  return parts.length ? "Used by: " + parts.join(", ") : "Not used yet";
}

/* What an import of a themes file said, in plain words (pure). */
function themeImportSummary(result) {
  const added = (result && result.added) || [];
  const same = (result && result.duplicates) || 0;
  let text = added.length ? "Added " + added.length + " theme" + (added.length === 1 ? "" : "s") + ": " + added.join(", ") + "." : "Nothing new was added.";
  if (same) text += " " + same + " already here, skipped.";
  return text;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { THEME_FONTS, THEME_KINDS, THEME_DEFAULTS, KIND_DEFAULTS, BUILD_DEFAULTS, CONTROL_SPEC, SAMPLES_BY_KIND,
    controlsFor, kindInfo, splitColor, joinColor, themeDraft, draftKey, PREVIEW_SAMPLES, previewPayload, groupThemes,
    themeToSave, usageText, themeSections, filterThemes, projectorLayout, packSegments, guideRect, dragGuide, clampMargins, themeImportSummary };
}

/* ---------------- the page ---------------- */
let themeEdit = null;          // the draft being edited (null until a theme is chosen)
let themeSaved = "";           // draftKey of the saved version, to tell whether there are unsaved changes
let themeSample = "lyrics";
let themeFrame = null;
let themeFrameReady = false;
let themeBuilt = false;
const themeControls = [];      // { spec, row, set(draft) } for each control

function themeDirty() {
  return !!themeEdit && draftKey(themeEdit) !== themeSaved;
}

/* ---- the guide box over the preview (drawing and dragging) ---- */
let guideLowerHeight = null;         // how tall the lower-third box really came out in the preview

function drawGuide() {
  const layer = $("theme-guide");
  if (!layer) return;
  const rect = themeEdit ? guideRect(themeEdit.kind, themeEdit.style, guideLowerHeight) : null;
  layer.hidden = !rect;
  $("theme-guide-line").hidden = !themeEdit || !guideRect(themeEdit.kind, themeEdit.style, 0);
  if (!rect) return;
  const box = $("theme-guide-box");
  box.style.left = (rect.left * 100) + "%";
  box.style.top = (rect.top * 100) + "%";
  box.style.width = ((rect.right - rect.left) * 100) + "%";
  box.style.height = ((rect.bottom - rect.top) * 100) + "%";
}

function wireGuide() {
  const frame = document.querySelector(".theme-frame-box");
  const move = (edge, pos) => {
    if (!themeEdit) return;
    Object.assign(themeEdit.style, dragGuide(themeEdit.kind, themeEdit.style, edge, clampTo(pos, 0, 1), guideLowerHeight));
    for (const control of themeControls) control.set(themeEdit);
    themeStyleChanged();
  };
  for (const handle of document.querySelectorAll(".guide-handle")) {
    const edge = handle.dataset.edge;
    const across = edge === "left" || edge === "right";
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
    });
    handle.addEventListener("pointermove", (event) => {
      if (!handle.hasPointerCapture(event.pointerId)) return;
      const box = frame.getBoundingClientRect();
      move(edge, across ? (event.clientX - box.left) / box.width : (event.clientY - box.top) / box.height);
    });
    const stop = (event) => { handle.classList.remove("dragging"); if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId); };
    handle.addEventListener("pointerup", stop);
    handle.addEventListener("pointercancel", stop);
    handle.addEventListener("keydown", (event) => {      // arrow keys nudge the edge (Shift: bigger steps)
      const delta = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 }[event.key];
      if (!delta || !themeEdit) return;
      if ((across && !event.key.includes("Left") && !event.key.includes("Right")) || (!across && !event.key.includes("Up") && !event.key.includes("Down"))) return;
      event.preventDefault();
      const rect = guideRect(themeEdit.kind, themeEdit.style, guideLowerHeight);
      if (rect) move(edge, rect[edge] + delta * (event.shiftKey ? 0.02 : 0.005));
    });
  }
}

function themeStyleChanged() {
  drawGuide();
  for (const line of document.querySelectorAll(".theme-lines")) {
    if (themeEdit) line.textContent = linesText(themeEdit.style);
  }
  drawThemeButtons();
  postThemePreview();
}

function postThemePreview() {
  if (!themeFrame || !themeFrameReady || !themeEdit || !themeFrame.contentWindow) return;
  themeFrame.contentWindow.postMessage(previewPayload(themeSample, themeEdit.style, themeEdit.kind, themeEdit.build), location.origin);
}

function rangeControl(group, key, min, max, step, unit) {
  const wrap = el("span", "theme-range");
  const range = document.createElement("input");
  range.type = "range"; range.min = min; range.max = max; range.step = step;
  const number = document.createElement("input");
  number.type = "number"; number.min = min; number.max = max; number.step = step;
  wrap.append(range, number);
  if (unit) wrap.appendChild(el("span", "muted", unit));
  const apply = (value) => {
    const v = Number(value);
    if (!Number.isFinite(v)) return;
    themeEdit[group][key] = Math.max(min, Math.min(max, v));
    if (group === "style" && key in MARGIN_MAX) {              // the margins must leave room for the text
      clampMargins(themeEdit.style, key);
      range.value = number.value = themeEdit.style[key];
    }
    themeStyleChanged();
  };
  range.addEventListener("input", () => { number.value = range.value; apply(range.value); });
  number.addEventListener("change", () => { range.value = number.value; apply(number.value); });
  return { node: wrap, set(d) { range.value = number.value = d[group][key]; } };
}

function colorControl(key, withAlpha) {
  const wrap = el("span", "theme-range");
  const picker = document.createElement("input");
  picker.type = "color";
  wrap.appendChild(picker);
  let alpha = null;
  if (withAlpha) {
    alpha = document.createElement("input");
    alpha.type = "range"; alpha.min = 0; alpha.max = 100; alpha.step = 5;
    alpha.setAttribute("aria-label", "How solid the color is, in percent");
    wrap.append(alpha, el("span", "muted", "solid %"));
  }
  const apply = () => {
    themeEdit.style[key] = joinColor(picker.value, alpha ? alpha.value / 100 : 1);
    themeStyleChanged();
  };
  picker.addEventListener("input", apply);
  if (alpha) alpha.addEventListener("input", apply);
  return { node: wrap, set(d) {
    const c = splitColor(d.style[key]);
    picker.value = c.hex;
    if (alpha) {
      alpha.max = d.kind === "overlay" ? 90 : 100;        // an overlay is always translucent
      alpha.value = Math.min(Math.round(c.alpha * 100), Number(alpha.max));
    }
  } };
}

/* The full-screen color, with a "Transparent" box for a projector theme: nothing is drawn behind the words,
   so a video or picture the broadcast program shows below the page stays visible. */
function backgroundColorControl() {
  const wrap = el("span", "theme-range");
  const picker = document.createElement("input");
  picker.type = "color";
  const label = el("label", "check");
  const box = document.createElement("input");
  box.type = "checkbox";
  label.append(box, document.createTextNode(" Transparent (a video behind the page shows through)"));
  const hint = el("span", "muted", " The band behind the text is switched off too, so the video is not covered.");
  wrap.append(picker, label, hint);
  const apply = () => {
    picker.disabled = box.checked;
    hint.hidden = !box.checked;
    themeEdit.style.background_color = box.checked ? picker.value.toLowerCase() + "00" : picker.value.toLowerCase();
    themeStyleChanged();
  };
  picker.addEventListener("input", apply);
  box.addEventListener("change", () => {
    apply();
    if (box.checked && themeEdit.style.band !== false) {        // a dark band behind the words would hide the video, so switch it off too
      themeEdit.style.band = false;
      for (const control of themeControls) control.set(themeEdit);
      themeStyleChanged();
    }
  });
  return { node: wrap, set(d) {
    const c = splitColor(d.style.background_color);
    picker.value = c.hex;
    label.hidden = d.kind !== "projector";
    box.checked = d.kind === "projector" && c.alpha === 0;
    picker.disabled = box.checked;
    hint.hidden = !box.checked;
  } };
}

function choiceControl(group, key, options) {
  const select = document.createElement("select");
  for (const [value, text] of options) select.appendChild(new Option(text, value));
  select.addEventListener("change", () => { themeEdit[group][key] = select.value; themeStyleChanged(); });
  return { node: select, set(d) { select.value = String(d[group][key]); } };
}

/* "About 9 lines of text fit" — worked out from the look, so there is no number to guess. */
function linesReadout() {
  const note = el("span", "muted theme-lines", "");
  return { node: note, set(d) {
    note.textContent = linesText(d.style);
  } };
}

function linesText(style) {
  return "About " + projectorLayout(style).lines + " lines fit on the screen with this look. Applies to every screen, including the lower third.";
}

function flagControl(key) {
  const box = document.createElement("input");
  box.type = "checkbox";
  box.addEventListener("change", () => { themeEdit.style[key] = box.checked; themeStyleChanged(); });
  return { node: box, set(d) { box.checked = !!d.style[key]; } };
}

function fontControl() {
  const select = document.createElement("select");
  for (const [value, text] of THEME_FONTS) select.appendChild(new Option(text, value));
  select.appendChild(new Option("Other (typed below)", "__other"));
  const typed = document.createElement("input");
  typed.type = "text"; typed.maxLength = 120; typed.placeholder = "Another font installed on the projector PC";
  typed.setAttribute("aria-label", "Font name");
  const wrap = el("span", "theme-font");
  wrap.append(select, typed);
  select.addEventListener("change", () => {
    if (select.value === "__other") { typed.hidden = false; typed.focus(); return; }
    typed.hidden = true;
    themeEdit.style.font_family = select.value;
    themeStyleChanged();
  });
  typed.addEventListener("change", () => {
    const value = typed.value.trim();
    if (!/^[A-Za-z0-9 ,'"\-]{1,120}$/.test(value)) { showMessage("A font name may only use letters, numbers, spaces, commas and hyphens."); return; }
    themeEdit.style.font_family = value;
    themeStyleChanged();
  });
  return { node: wrap, set(d) {
    const known = THEME_FONTS.some(([v]) => v === d.style.font_family);
    select.value = known ? d.style.font_family : "__other";
    typed.hidden = known;
    typed.value = known ? "" : d.style.font_family;
  } };
}

async function importThemesFile(file) {
  const status = $("theme-transfer-status");
  status.textContent = "Uploading " + file.name + "…";
  const made = await act("import_batch");
  if (!made || !(await uploadOne(made.batch, "themes", file))) { status.textContent = ""; return; }
  const result = await act("theme_import", { batch: made.batch });
  status.textContent = "";
  if (!result) return;
  const notes = (result.notes || []).slice(0, 5);
  showNotice(themeImportSummary(result) + (notes.length ? " " + notes.join(" ") : ""));
}

/* Choose a picture from the computer; it is uploaded at once and used when the theme is saved. */
function pictureControl() {
  const wrap = el("span", "theme-picture");
  const file = document.createElement("input");
  file.type = "file"; file.accept = ".png,.jpg,.jpeg,.gif,.webp,image/png,image/jpeg,image/gif,image/webp"; file.hidden = true;
  const choose = el("button", "", "Choose picture…");
  choose.type = "button";
  const remove = el("button", "", "Remove");
  remove.type = "button";
  const note = el("span", "muted", "");
  wrap.append(file, choose, remove, note);
  choose.addEventListener("click", (e) => { e.preventDefault(); file.click(); });
  remove.addEventListener("click", (e) => {
    e.preventDefault();
    delete themeEdit.style.background_image;
    remove.hidden = true; note.textContent = "No picture";
    themeStyleChanged();
  });
  file.addEventListener("change", async () => {
    const chosen = file.files[0];
    file.value = "";
    if (!chosen) return;
    note.textContent = "Uploading…";
    const made = await act("import_batch");
    if (!made || !(await uploadOne(made.batch, "background", chosen))) { note.textContent = themeEdit.style.background_image ? "Picture set" : "No picture"; return; }
    const stored = await act("background_import", { batch: made.batch });
    if (!stored) { note.textContent = "No picture"; return; }
    themeEdit.style.background_image = stored.path;
    remove.hidden = false; note.textContent = chosen.name;
    themeStyleChanged();
  });
  return { node: wrap, set(d) {
    const has = !!d.style.background_image;
    remove.hidden = !has;
    note.textContent = has ? "Picture set" : "No picture";
  } };
}

/* One builder per control key in CONTROL_SPEC. */
const CONTROL_BUILDERS = {
  font: () => fontControl(),
  size: () => rangeControl("style", "font_size", 2, 20, 0.5, "% of screen height"),
  color: () => colorControl("color", false),
  line_height: () => rangeControl("style", "line_height", 1, 2.5, 0.05, ""),
  align: () => choiceControl("style", "align", [["left", "Left"], ["center", "Center"], ["right", "Right"]]),
  shadow: () => flagControl("shadow"),
  uppercase: () => flagControl("uppercase"),
  v_pos: () => choiceControl("style", "v_pos", [["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]]),
  h_pos: () => choiceControl("style", "h_pos", [["left", "Left"], ["center", "Center"], ["right", "Right"]]),
  box_width: () => rangeControl("style", "box_width", 20, 100, 5, "% of the screen width"),
  margin: () => rangeControl("style", "margin", 0, 15, 0.5, "% of the screen height"),
  max_height: () => rangeControl("style", "max_height", 0, 60, 1, "% of the screen height (0 = no limit)"),
  margin_top: () => rangeControl("style", "margin_top", 0, 40, 0.5, "% of the screen height"),
  margin_bottom: () => rangeControl("style", "margin_bottom", 0, 40, 0.5, "% of the screen height"),
  margin_left: () => rangeControl("style", "margin_left", 0, 45, 0.5, "% of the screen width"),
  margin_right: () => rangeControl("style", "margin_right", 0, 45, 0.5, "% of the screen width"),
  background_color: () => backgroundColorControl(),
  picture: () => pictureControl(),
  background_fit: () => choiceControl("style", "background_fit", [["fill", "Fill the screen (crops the edges)"], ["fit", "Fit inside the screen (shows all of it)"]]),
  background_dim: () => rangeControl("style", "background_dim", 0, 80, 5, "% darker"),
  band: () => flagControl("band"),
  balance_lines: () => flagControl("balance_lines"),
  projector_hide: () => flagControl("projector_hide"),
  band_color: () => colorControl("band_color", true),
  band_color_projector: () => colorControl("band_color", true),
  reference_scale: () => rangeControl("style", "reference_scale", 0.4, 1.2, 0.05, "of the text size"),
  reference_pos: () => choiceControl("style", "reference_pos", [["auto", "Automatic (under a verse, above a passage)"], ["above", "Above the verse text"], ["below", "Below the verse text"]]),
  title_scale: () => rangeControl("style", "title_scale", 0.6, 2, 0.05, "of the verse text size"),
  number_font: () => choiceControl("style", "number_font", [["same", "Same as the verse text"]].concat(THEME_FONTS)),
  scripture_split: () => choiceControl("build", "scripture_split", [["verse", "One verse per slide"], ["chars", "Fit as many whole verses as will fit"]]),
  scripture_lines: () => linesReadout(),
  lines_per_slide: () => rangeControl("build", "lines_per_slide", 1, 12, 1, "lines"),
  song_count: () => choiceControl("build", "song_count", [["lines", "Lines in the song"], ["screen", "Lines on the screen (a long lyric line counts as the lines it takes)"]]),
};

function buildThemeControls() {
  if (themeBuilt) return;
  themeBuilt = true;
  for (const spec of CONTROL_SPEC) {
    const row = el("label", "theme-control");
    row.appendChild(el("span", "theme-label", spec.label));
    const control = CONTROL_BUILDERS[spec.key]();
    row.appendChild(control.node);
    $("theme-group-" + spec.group).appendChild(row);
    themeControls.push({ spec: spec, row: row, set: control.set });
  }
}

/* Show only the controls that mean something for this kind of theme (and hide empty groups). */
function showControlsFor(kind) {
  for (const control of themeControls) control.row.hidden = !control.spec.kinds.includes(kind);
  for (const group of Object.keys(GROUP_TITLES)) {
    $("theme-group-" + group).parentElement.hidden = !controlsFor(kind).some((spec) => spec.group === group);
  }
}

function drawSampleButtons() {
  const holder = $("theme-samples");
  holder.replaceChildren();
  const keys = SAMPLES_BY_KIND[themeEdit ? themeEdit.kind : "projector"];
  if (!keys.includes(themeSample)) themeSample = keys[0];
  for (const key of keys) {
    const button = el("button", "", PREVIEW_SAMPLES[key].label);
    button.type = "button";
    button.setAttribute("aria-pressed", String(key === themeSample));
    button.addEventListener("click", () => { themeSample = key; drawSampleButtons(); postThemePreview(); });
    holder.appendChild(button);
  }
}

function fillThemeControls() {
  showControlsFor(themeEdit.kind);
  for (const control of themeControls) control.set(themeEdit);
  $("theme-name").value = themeEdit.name;
  $("theme-kind").value = themeEdit.kind;
  drawGuide();
  $("theme-kind").disabled = themeEdit.id !== null;
  $("theme-kind-help").textContent = kindInfo(themeEdit.kind).full +
    (themeEdit.id !== null ? ". The kind is fixed once a theme is saved." : "");
  drawSampleButtons();
}

function themeById(id) {
  return ((state && state.themes) || []).find((t) => t.id === id) || null;
}

function chooseTheme(id) {
  if (themeDirty() && themeEdit.id !== id) {
    showMessage("Save or Revert your changes to this theme before choosing another.");
    return;
  }
  themeEdit = themeDraft(themeById(id));
  themeSaved = draftKey(themeEdit);
  themeListKind = themeEdit.kind;
  fillThemeControls();
  themeStyleChanged();
  drawThemeList();
}

function newThemeDraft() {
  if (themeDirty()) { showMessage("Save or Revert your changes to this theme first."); return; }
  themeEdit = themeDraft(null, "projector");
  themeEdit.name = "New theme";
  themeSaved = "";                      // a new theme always counts as unsaved
  fillThemeControls();
  themeStyleChanged();
  $("theme-kind").focus();
}

function changeThemeKind(kind) {
  if (!themeEdit || themeEdit.id !== null) return;        // only a theme that is not saved yet
  const name = themeEdit.name;
  themeEdit = themeDraft(null, kind);
  themeEdit.name = name;
  themeListKind = kind;
  fillThemeControls();
  themeStyleChanged();
  drawThemeList();
}

function drawThemeButtons() {
  const dirty = themeDirty();
  $("theme-save").disabled = !themeEdit || !dirty;
  $("theme-revert").disabled = !themeEdit || !dirty;
  $("theme-dup").disabled = !themeEdit || themeEdit.id === null;
  $("theme-del").disabled = !themeEdit || themeEdit.id === null;
  $("theme-state").textContent = !themeEdit ? "" : themeEdit.id === null ? "Not saved yet" : dirty ? "Unsaved changes" : "Saved";
  $("theme-editor").hidden = !themeEdit;
  $("theme-empty").hidden = !!themeEdit;
}

let themeListKind = "projector";     // which kind of theme the bottom list shows
let themeFilterText = "";

function showThemeKind(kind) {
  themeListKind = kind;
  drawThemeList();
}

function drawThemeList() {
  const groups = groupThemes((state && state.themes) || []);
  const show = $("theme-show");
  if (show.options.length !== groups.length) {
    show.replaceChildren();
    for (const group of groups) show.appendChild(new Option("", group.kind));
  }
  groups.forEach((group, i) => { show.options[i].textContent = kindInfo(group.kind).text + " (" + group.themes.length + ")"; });
  show.value = themeListKind;
  const list = $("theme-list");
  list.replaceChildren();
  const shown = groups.find((g) => g.kind === themeListKind);
  const parts = themeSections(filterThemes(shown.themes, themeFilterText));
  if (!parts.inUse.length && !parts.rest.length) {
    const none = themeFilterText.trim() ? "No " + kindInfo(themeListKind).text.toLowerCase() + " themes match “" + themeFilterText.trim() + "”."
      : "No " + kindInfo(themeListKind).text.toLowerCase() + " themes yet. Press New theme.";
    list.appendChild(el("li", "muted theme-section", none));
  }
  const addAll = (title, themes) => {
    if (!themes.length) return;
    list.appendChild(el("li", "muted theme-section", title));
    for (const theme of themes) {
      const row = el("li", "theme-row" + (themeEdit && themeEdit.id === theme.id ? " current" : ""));
      const pick = el("button", "theme-pick");
      pick.type = "button";
      pick.title = theme.name + " — " + usageText(theme.usage);
      pick.setAttribute("aria-pressed", String(!!themeEdit && themeEdit.id === theme.id));
      pick.appendChild(el("strong", "", theme.name));
      pick.appendChild(el("span", "muted", usageText(theme.usage).replace("Used by: ", "")));
      pick.addEventListener("click", () => chooseTheme(theme.id));
      row.appendChild(pick);
      list.appendChild(row);
    }
  };
  addAll("In use", parts.inUse);
  addAll("Not used yet", parts.rest);
}

/* A <select> of the themes of one kind. `none` is the first option's text; its value is "". */
function fillThemeSelect(select, none, selected, kind) {
  select.replaceChildren(new Option(none, ""));
  for (const theme of (state && state.themes) || []) {
    if (kind && (theme.kind || "projector") !== kind) continue;
    select.appendChild(new Option(theme.name, String(theme.id)));
  }
  select.value = selected === null || selected === undefined ? "" : String(selected);
}

/* The Settings page's export list keeps the chosen theme when the list is redrawn. */
function fillExportPick() {
  const pick = $("theme-export-pick");
  const chosen = pick.value;
  pick.replaceChildren(new Option("Choose a theme…", ""));
  for (const theme of (state && state.themes) || []) pick.appendChild(new Option(theme.name, String(theme.id)));
  pick.value = Array.from(pick.options).some((o) => o.value === chosen) ? chosen : "";
  $("theme-export-one").disabled = !pick.value;
}

function drawThemeAssignments() {
  fillExportPick();
  fillThemeSelect($("assign-default"), "No theme (plain)", state && state.default_theme, "projector");
  fillThemeSelect($("assign-default-overlay"), "No theme (plain)", state && state.default_overlay_theme, "overlay");
  fillThemeSelect($("assign-bible"), "Same as the service", state && state.bible_theme, "projector");
  fillThemeSelect($("bibles-theme"), "Same as the service", state && state.bible_theme, "projector");
  fillThemeSelect($("assign-verse-over"), "Same as the Bible theme", state && state.verse_over_theme, "overlay");
  fillThemeSelect($("bibles-over-theme"), "Same as the Bible theme", state && state.verse_over_theme, "overlay");
  fillThemeSelect($("assign-confidence"), "Plain large text", state && state.confidence_theme, "confidence");
}

function drawThemesTab() {
  if (!state) return;
  if (themeEdit && themeEdit.id !== null && !themeById(themeEdit.id)) themeEdit = null;   // deleted elsewhere
  drawThemeList();
  drawThemeAssignments();
  drawThemeButtons();
}

async function saveTheme() {
  if (!themeEdit || !themeEdit.name.trim()) { showMessage("Give the theme a name first."); return; }
  const payload = themeToSave(themeEdit);
  if (themeEdit.kind !== "projector") delete payload.build;       // slide splitting is the projector theme's job
  const result = await act("theme_save", Object.assign({ id: themeEdit.id }, payload));
  if (!result) return;
  themeEdit.id = result.id;
  themeSaved = draftKey(themeEdit);
  fillThemeControls();
  showNotice("Saved. Screens already showing this theme have changed.");
}

function revertTheme() {
  if (!themeEdit) return;
  if (themeEdit.id === null) { themeEdit = null; themeSaved = ""; drawThemeButtons(); return; }
  themeEdit = themeDraft(themeById(themeEdit.id));
  themeSaved = draftKey(themeEdit);
  fillThemeControls();
  themeStyleChanged();
}

function wireThemes() {
  buildThemeControls();
  themeFrame = $("theme-frame");
  wireGuide();
  for (const kind of THEME_KINDS) $("theme-kind").appendChild(new Option(kind.text, kind.value));
  $("tab-themes").addEventListener("click", () => showView("themes"));
  $("theme-new").addEventListener("click", newThemeDraft);
  $("theme-show").addEventListener("change", (e) => showThemeKind(e.target.value));
  $("theme-filter").addEventListener("input", (e) => { themeFilterText = e.target.value; drawThemeList(); });
  $("theme-kind").addEventListener("change", (e) => changeThemeKind(e.target.value));
  $("theme-name").addEventListener("input", () => { if (themeEdit) { themeEdit.name = $("theme-name").value; drawThemeButtons(); } });
  $("theme-save").addEventListener("click", saveTheme);
  $("theme-revert").addEventListener("click", revertTheme);
  $("theme-dup").addEventListener("click", async () => {
    if (!themeEdit || themeEdit.id === null) return;
    if (themeDirty()) { showMessage("Save or Revert first, then duplicate."); return; }
    const made = await act("theme_duplicate", { id: themeEdit.id });
    if (made) { await new Promise((r) => setTimeout(r, 400)); chooseTheme(made.id); }
  });
  $("theme-export-pick").addEventListener("change", (e) => { $("theme-export-one").disabled = !e.target.value; });
  $("theme-export-one").addEventListener("click", () => {
    const id = $("theme-export-pick").value;
    if (id) downloadFile("/api/export?kind=themes&ids=" + encodeURIComponent(id));
  });
  $("theme-export-all").addEventListener("click", () => { downloadFile("/api/export?kind=themes&all=1"); });
  $("theme-import").addEventListener("click", () => $("file-themes").click());
  $("file-themes").addEventListener("change", async () => {
    const input = $("file-themes");
    const file = (input.files || [])[0];
    input.value = "";
    if (file) await importThemesFile(file);
  });
  confirmClick($("theme-del"), async () => {
    if (!themeEdit || themeEdit.id === null) return;
    const theme = themeById(themeEdit.id);
    if (await act("theme_delete", { id: themeEdit.id })) {
      showNotice("Deleted" + (theme ? " “" + theme.name + "”" : "") + ". Anything that used it now uses the default look.");
      themeEdit = null; themeSaved = "";
      drawThemeButtons();
    }
  }, () => { const theme = themeEdit && themeEdit.id !== null ? themeById(themeEdit.id) : null; return "Delete the theme" + (theme ? " “" + theme.name + "”" : "") + "? Anything that uses it goes back to the default look."; });
  const pick = (action, extra) => (e) => act(action, Object.assign(e.target.value ? { id: Number(e.target.value) } : {}, extra || {}));
  $("assign-default").addEventListener("change", pick("default_theme", { kind: "projector" }));
  $("assign-default-overlay").addEventListener("change", pick("default_theme", { kind: "overlay" }));
  $("assign-bible").addEventListener("change", pick("bible_theme"));
  $("bibles-theme").addEventListener("change", pick("bible_theme"));
  $("assign-verse-over").addEventListener("change", pick("verse_over_theme"));
  $("bibles-over-theme").addEventListener("change", pick("verse_over_theme"));
  $("assign-confidence").addEventListener("change", pick("confidence_theme"));
  window.addEventListener("message", (event) => {
    if (event.origin !== location.origin || !themeFrame || event.source !== themeFrame.contentWindow) return;
    if (event.data && event.data.sandbox === "ready") { themeFrameReady = true; postThemePreview(); }
    if (event.data && event.data.sandbox === "measure") {
      const height = Number(event.data.lowerHeight);
      guideLowerHeight = height > 0 && height < 1 ? height : null;
      drawGuide();
    }
  });
  window.addEventListener("resize", rescaleThemeFrame);
  window.addEventListener("beforeunload", (event) => { if (themeDirty()) { event.preventDefault(); event.returnValue = ""; } });
}

/* The preview page is only loaded once the tab is first opened. */
function openThemesTab() {
  const outputs = (state && state.outputs) || [];
  if (themeFrame && !themeFrame.getAttribute("src") && outputs.length) themeFrame.src = outputs[0].path + "?sandbox&preview";
  $("theme-preview-off").hidden = outputs.length > 0;
  drawThemesTab();
  rescaleThemeFrame();
}

function rescaleThemeFrame() {
  const box = document.querySelector(".theme-frame-box");
  if (!box || !themeFrame) return;
  const scale = previewScale(box.clientWidth);
  if (scale) themeFrame.style.transform = "scale(" + scale + ")";
}
