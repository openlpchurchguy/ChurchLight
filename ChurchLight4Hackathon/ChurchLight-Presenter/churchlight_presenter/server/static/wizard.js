/* The first-time setup guide: a short walk through the computer check, OBS (scenes and room screens), content,
   and the clicker. It opens by itself on a new, empty ChurchLight and can be reopened from Settings.
   It uses the page's own helpers (state, act, call, el, $, showView, addressRow) and never touches a screen. */
"use strict";

const WIZARD_STEPS = ["welcome", "computer", "screens", "obs", "content", "extras", "done"];
let wizardAt = 0;
let wizardOpen = false;
let wizardAutoTried = false;
let wizardChecks = null;
let wizardReturnFocus = null;

const WIZARD_TITLES = {
  welcome: "Welcome to ChurchLight",
  computer: "Check this computer",
  screens: "Your church and screens",
  obs: "Set up OBS",
  content: "Add your content",
  extras: "Look, clicker and Stream Deck",
  done: "You are ready",
};

/* While the "screens" step is open each screen shows its own name (PROJECTOR, STAGE...). Leaving the step puts the real screens back. */
async function wizardScreensLive() {
  if (!wizardMon.naming) return;
  wizardMon.naming = false;
  const ratio = window.devicePixelRatio || 1;
  await call("screens_open", { operator_at: [Math.round((window.screenX + window.outerWidth / 2) * ratio), Math.round((window.screenY + window.outerHeight / 2) * ratio)] });
  wizardRefreshOpen();
}

function wizardHide() {
  wizardScreensLive();
  wizardOpen = false;
  $("wizard").hidden = true;
  if (typeof refreshReady === "function" && typeof currentView !== "undefined" && !$("landing").hidden) refreshReady();      // whatever the guide changed shows in the bar at once
  if (wizardReturnFocus && document.contains(wizardReturnFocus)) wizardReturnFocus.focus();
}

function wizardShow(start) {
  wizardAt = start || 0;
  wizardObs.skip = !!(state && state.setup && state.setup.no_livestream);        // the "I don't livestream" choice is kept
  wizardOpen = true;
  wizardReturnFocus = document.activeElement;
  $("wizard").hidden = false;
  drawWizard();
}

async function wizardFinish() {
  await act("setup_done", { value: true });
  wizardHide();
}

function wizardButton(label, onClick, primary) {
  const button = el("button", primary ? "primary" : "", label);
  button.type = "button";
  button.addEventListener("click", onClick);
  return button;
}

function wizardCheckRow(check) {
  const row = el("div", "wizard-check " + (check.ok ? "ok" : "bad"));
  row.appendChild(el("span", "mark", check.ok ? "✓" : "!"));
  const text = el("div");
  text.appendChild(el("div", "what", check.label));
  text.appendChild(el("div", "muted", check.detail));
  row.appendChild(text);
  return row;
}

async function wizardLoadChecks(into) {
  into.replaceChildren(el("p", "muted", "Checking…"));
  const result = await call("setup_checks", {});
  into.replaceChildren();
  if (!result || !result.ok) { into.appendChild(el("p", "muted", "The check could not run. You can carry on.")); return; }
  wizardChecks = result.data.checks || [];
  for (const check of wizardChecks) into.appendChild(wizardCheckRow(check));
  const bad = wizardChecks.filter((c) => !c.ok).length;
  into.appendChild(el("p", "wizard-note", bad
    ? "Anything marked ! can be fixed later. ChurchLight still runs; only that one thing will not work yet."
    : "Everything this computer needs is in place."));
}

/* ---- the automatic OBS setup (step "Set up OBS") ---- */
const wizardObs = { port: "", password: "", scenes: { projector: true, livestream: true, stage: true, confidence: true, home: true },
                    remember: false, result: null, picks: {}, auto: null, status: "", busy: false, reports: [], skip: false };
const WIZARD_SCENE_LABELS = [
  ["projector", "Projector", "the picture for the room. Recommended."],
  ["livestream", "Livestream", "the lower third over your camera. Recommended if you livestream."],
  ["stage", "Stage", "the screen for the singers and speaker. Optional: only if you have a screen for them."],
  ["confidence", "Confidence monitor", "the screen the speaker and band read from. Optional: only if you have a screen for them."],
  ["home", "Home", "a plain scene for before and after the service. Optional."],
];

/* ---- the monitors, chosen on the "Your church and screens" step and handed on to the OBS step ---- */
const wizardMon = { naming: false, loaded: false, monitors: [], picks: {}, shape: "", status: "", open: [] };       // picks: scene name -> ChurchLight monitor index ("" = none)
const LOWER_THIRD_LAYOUTS = ["lower_third", "song_lower", "verse_lower"];
const PROJECTOR_LAYOUTS = ["projector", "ppt_verse", "song_full", "verse_full"];
const STAGE_LAYOUTS = ["stage", "ppt_notes"];

/* The room screens that need a monitor, as [{scene, label}], for the scenes the church ticked and the outputs it has. */
function wizardRoomScenes() {
  const rows = [];
  const outputs = (state && state.outputs) || [];
  const find = (test) => outputs.find(test);
  const projector = find((o) => PROJECTOR_LAYOUTS.includes(o.layout));
  const stage = find((o) => STAGE_LAYOUTS.includes(o.layout));
  const confidence = find((o) => o.layout === "confidence");
  if (wizardObs.scenes.projector && projector) rows.push({ scene: "ChurchLight Projector", label: "Projector (the room)", output: projector.name });
  if (wizardObs.scenes.stage && stage) rows.push({ scene: "ChurchLight Stage", label: "Stage (singers and speaker)", output: stage.name });
  if (wizardObs.scenes.confidence && confidence) rows.push({ scene: "ChurchLight Confidence", label: "Confidence monitor", output: confidence.name });
  return rows;
}

/* Test: show the screen full screen on the chosen monitor until the same button is pressed again ("Stop test"). It is never put on
   the monitor this window is on (the server refuses), because nothing there could be clicked to stop it. */
let wizardTestTimer = null;
async function wizardRefreshOpen() {
  const result = await call("screen_monitors", {});
  if (result.ok) wizardMon.open = result.data.open || [];
  clearTimeout(wizardTestTimer);
  if (wizardMon.open.length && wizardOpen && WIZARD_STEPS[wizardAt] === "screens") wizardTestTimer = setTimeout(wizardWatchTests, 1500);
}

async function wizardWatchTests() {                         // a test can also be closed with Alt+F4 or Esc: keep the buttons true
  if (!wizardOpen || WIZARD_STEPS[wizardAt] !== "screens") return;
  const before = JSON.stringify(wizardMon.open);
  await wizardRefreshOpen();
  if (JSON.stringify(wizardMon.open) !== before) drawWizard();
}

async function wizardLoadMonitors() {
  wizardMon.loaded = true;
  const result = await call("screen_monitors", {});
  if (result.ok) { wizardMon.monitors = result.data.monitors || []; wizardMon.open = result.data.open || []; }
  const saved = await call("screens_get", {});                      // the monitors chosen earlier (they open at every start)
  if (saved.ok) {
    for (const room of wizardRoomScenes()) {
      const at = saved.data.assign[room.output];
      if (at !== undefined && wizardMon.picks[room.scene] === undefined) wizardMon.picks[room.scene] = String(at);
    }
  }
  if (wizardOpen && WIZARD_STEPS[wizardAt] === "screens") drawWizard();
}

/* The monitor this operator window is on (so it is never offered as a room screen without a warning), or -1. */
function wizardOperatorMonitor() {
  const ratio = window.devicePixelRatio || 1;
  const cx = Math.round((window.screenX + window.outerWidth / 2) * ratio);
  const cy = Math.round((window.screenY + window.outerHeight / 2) * ratio);
  const found = wizardMon.monitors.find((m) => cx >= m.x && cx < m.x + m.width && cy >= m.y && cy < m.y + m.height);
  return found ? found.index : -1;
}

/* The screen shape (one of SCREEN_SHAPES) that is closest to a monitor's width and height. "" when the size is unknown. */
function wizardShapeFor(width, height) {
  if (!(width > 0 && height > 0)) return "";
  const ratio = width / height;
  let best = "", gap = Infinity;
  for (const shape of SCREEN_SHAPES) {
    const [w, h] = shapeParts(shape);
    const away = Math.abs(w / h - ratio);
    if (away < gap) { gap = away; best = shape; }
  }
  return best;
}

/* Set the projector screen's shape from the monitor it will be shown on. Returns the shape set, or "". */
async function wizardApplyShape(width, height) {
  const projector = state.outputs.find((o) => hasScreenShape(o.layout) && PROJECTOR_LAYOUTS.includes(o.layout));
  const shape = wizardShapeFor(width, height);
  if (!projector || !shape) return "";
  await act("screen_shape_set", { name: projector.name, shape: shape });      // always send it: the page's own copy of the shape may be a moment behind
  return shape;
}

/* Turn a monitor chosen here into the number OBS uses for the same monitor: match by where it is, else by its place in the list. */
function wizardObsIndexFor(clMonitorIndex, obsMonitors) {
  const mine = wizardMon.monitors.find((m) => m.index === clMonitorIndex);
  if (!mine) return "";
  const same = obsMonitors.find((o) => o.x !== null && o.x !== undefined && o.x === mine.x && o.y === mine.y);
  if (same) return String(same.index);
  return obsMonitors.some((o) => o.index === mine.index) ? String(mine.index) : "";
}

function wizardField(label, input) {
  const wrap = el("label", "theme-control");
  wrap.appendChild(el("span", "theme-label", label));
  wrap.appendChild(input);
  return wrap;
}

function wizardObsTarget() {
  const saved = (state && state.obs) || {};
  const port = parseInt(wizardObs.port || saved.port || "4455", 10);
  const target = { host: saved.host || "127.0.0.1", port: port };
  if (wizardObs.password) target.password = wizardObs.password;
  return target;
}

async function wizardObsRun(action, args) {
  wizardObs.busy = true;
  wizardObs.status = "Talking to OBS…";
  drawWizard();
  const result = await call(action, args);
  wizardObs.busy = false;
  if (!result.ok) { wizardObs.status = result.error; drawWizard(); return null; }
  wizardObs.status = "";
  return result.data;
}

async function wizardObsSetup() {
  const wanted = Object.keys(wizardObs.scenes).filter((k) => wizardObs.scenes[k]);
  if (!wanted.length) { wizardObs.status = "Choose at least one scene to make."; drawWizard(); return; }
  const data = await wizardObsRun("obs_setup", Object.assign(wizardObsTarget(), { scenes: wanted, remember: wizardObs.remember }));
  if (!data) return;
  wizardObs.result = data;
  wizardObs.reports = data.report;
  const saved = (state && state.obs && state.obs.monitors) || {};
  const last = data.monitors.length - 1;
  for (const scene of data.scenes) {
    if (!/Projector$|Stage$|Confidence$/.test(scene)) continue;
    const chosen = wizardMon.picks[scene];                                // from the "Your church and screens" step
    const carried = (chosen !== undefined && chosen !== "") ? wizardObsIndexFor(parseInt(chosen, 10), data.monitors) : "";
    if (carried !== "") wizardObs.picks[scene] = carried;
    else if (saved[scene] !== undefined) wizardObs.picks[scene] = String(saved[scene]);
    else wizardObs.picks[scene] = (/Projector$/.test(scene) && last >= 1) ? String(last) : "";
  }
  drawWizard();
  const heading = document.querySelector(".wizard-obs h3");          // show the monitor choices that just appeared
  if (heading) heading.scrollIntoView({ block: "start" });
}

async function wizardObsProject() {
  const monitors = {};
  for (const [scene, value] of Object.entries(wizardObs.picks)) if (value !== "") monitors[scene] = parseInt(value, 10);
  const data = await wizardObsRun("obs_project", Object.assign(wizardObsTarget(), { monitors: monitors, remember: wizardObs.remember }));
  if (!data) return;
  const projectorAt = monitors["ChurchLight Projector"];
  const there = projectorAt === undefined || !wizardObs.result ? null : wizardObs.result.monitors.find((m) => m.index === projectorAt);
  if (there) await wizardApplyShape(there.width, there.height);
  wizardObs.reports = data.report;
  wizardObs.status = "Done. Check the screens.";
  drawWizard();
}

function drawWizardKinds(body) {
  const scenes = el("div", "wizard-scenes");
  scenes.appendChild(el("p", "", "What does your church use?"));
  for (const [key, label, why] of WIZARD_SCENE_LABELS) {
    const line = el("label", "check");
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.checked = !!wizardObs.scenes[key];
    tick.addEventListener("change", () => { wizardObs.scenes[key] = tick.checked; drawWizard(); });
    line.appendChild(tick);
    line.appendChild(document.createTextNode(" " + label + ": " + why));
    scenes.appendChild(line);
  }
  body.appendChild(scenes);
}

function drawWizardObs(body) {
  const box = el("div", "wizard-obs");
  box.appendChild(el("p", "muted", "In OBS, turn on its connection so ChurchLight can set it up:"));
  const steps = el("ol");
  for (const text of [
    "Open OBS.",
    "Choose Tools, then WebSocket Server Settings. Tick Enable WebSocket server.",
    "If it asks for a password, press Show Connect Info and copy the port and the password.",
  ]) steps.appendChild(el("li", "", text));
  box.appendChild(steps);
  const port = document.createElement("input");
  port.type = "text";
  port.inputMode = "numeric";
  port.placeholder = "4455";
  port.value = wizardObs.port || ((state.obs && state.obs.port !== 4455) ? String(state.obs.port) : "");
  port.setAttribute("aria-label", "OBS WebSocket port");
  port.addEventListener("input", () => { wizardObs.port = port.value.trim(); });
  const password = document.createElement("input");
  password.type = "password";
  password.autocomplete = "off";
  password.placeholder = (state.obs && state.obs.has_password) ? "(saved)" : "";
  password.value = wizardObs.password;
  password.setAttribute("aria-label", "OBS WebSocket password");
  password.addEventListener("input", () => { wizardObs.password = password.value; });
  const row = el("div", "wizard-form");
  row.appendChild(wizardField("Port", port));
  row.appendChild(wizardField("Password", password));
  box.appendChild(row);
  const show = el("label", "check");
  const showBox = document.createElement("input");
  showBox.type = "checkbox";
  showBox.addEventListener("change", () => { password.type = showBox.checked ? "text" : "password"; });
  show.appendChild(showBox);
  show.appendChild(document.createTextNode(" Show the password, to check it is typed right (best: use Copy in OBS and paste it here)"));
  box.appendChild(show);
  const remember = el("label", "check");
  const rememberBox = document.createElement("input");
  rememberBox.type = "checkbox";
  rememberBox.checked = wizardObs.remember;
  rememberBox.addEventListener("change", () => { wizardObs.remember = rememberBox.checked; });
  remember.appendChild(rememberBox);
  remember.appendChild(document.createTextNode(" Remember the OBS password on this computer (recommended: leave this off unless the computer is only yours)"));
  box.appendChild(remember);
  const making = WIZARD_SCENE_LABELS.filter(([key]) => wizardObs.scenes[key]).map(([, label]) => label);
  box.appendChild(el("p", "muted", "Scenes it will make: " + (making.length ? making.join(", ") : "none (go Back and choose what your church uses)") + "."));
  const actions = el("div", "wizard-actions");
  const make = wizardButton(wizardObs.result ? "Make the scenes again" : "Connect and make the scenes", wizardObsSetup, !wizardObs.result);
  make.disabled = wizardObs.busy;
  actions.appendChild(make);
  box.appendChild(actions);
  const status = el("p", "wizard-note", wizardObs.status);
  status.setAttribute("role", "status");
  status.hidden = !wizardObs.status;
  box.appendChild(status);
  if (wizardObs.reports.length) {
    const log = el("ul", "wizard-report");
    for (const line of wizardObs.reports) log.appendChild(el("li", "", line));
    box.appendChild(log);
  }
  body.appendChild(box);
}

function drawWizardProjectors(box) {
  const data = wizardObs.result;
  const scenes = data.scenes.filter((s) => /Projector$|Stage$|Confidence$/.test(s));
  if (!scenes.length) return;
  box.appendChild(el("h3", "", "Put the room screens on your monitors"));
  box.appendChild(el("p", "muted", "OBS shows each scene full screen on the monitor you choose, without a browser window. Do not choose the monitor you are using now."));
  for (const scene of scenes) {
    const select = el("select", "shape-pick");
    select.setAttribute("aria-label", "Monitor for " + scene);
    select.appendChild(el("option", "", "Do not project this one"));
    select.firstChild.value = "";
    for (const m of data.monitors) {
      const option = el("option", "", "Monitor " + (m.index + 1) + ": " + m.name + (m.width ? " (" + m.width + "×" + m.height + ")" : ""));
      option.value = String(m.index);
      select.appendChild(option);
    }
    select.value = wizardObs.picks[scene] === undefined ? "" : wizardObs.picks[scene];
    select.addEventListener("change", () => { wizardObs.picks[scene] = select.value; });
    const line = el("div", "row");
    line.appendChild(el("span", "grow", scene.replace("ChurchLight ", "")));
    line.appendChild(select);
    box.appendChild(line);
  }
  const actions = el("div", "wizard-actions");
  const go = wizardButton("Open the projectors now", wizardObsProject, true);
  go.disabled = wizardObs.busy;
  actions.appendChild(go);
  box.appendChild(actions);
  const auto = el("label", "check");
  const autoBox = document.createElement("input");
  autoBox.type = "checkbox";
  autoBox.checked = wizardObs.auto === null ? !!(state.obs && state.obs.auto) : wizardObs.auto;
  autoBox.addEventListener("change", async () => {
    wizardObs.auto = autoBox.checked;
    const result = await call("obs_auto_set", { on: autoBox.checked });
    if (!result.ok) { wizardObs.auto = false; autoBox.checked = false; wizardObs.status = result.error; drawWizard(); }
  });
  auto.appendChild(autoBox);
  auto.appendChild(document.createTextNode(" Open them by themselves whenever ChurchLight starts (OBS must be open)"));
  box.appendChild(auto);
}

/* ---- step "Add your content": a file picker in the guide, with its own progress line ---- */
let wizardJobBase = 0;
let wizardDeckFrom = "deck";            // which slideshow row started the latest slideshow import

/* Themes from an earlier ChurchLight: the same import as the Theme Library, with the answer shown inside the guide. */
async function wizardImportThemes(file, status) {
  status.textContent = "Uploading " + file.name + "…";
  const made = await act("import_batch");
  if (!made || !(await uploadOne(made.batch, "themes", file))) { status.textContent = ""; return; }
  const result = await act("theme_import", { batch: made.batch });
  if (!result) { status.textContent = ""; return; }
  const notes = (result.notes || []).slice(0, 5);
  status.textContent = themeImportSummary(result) + (notes.length ? " " + notes.join(" ") : "");
  if (status.logLine) { status.logLine(status.textContent); status.textContent = ""; }
}

function wizardLatestJob() {
  return Math.max(0, ...((state && state.imports) || []).map((job) => job.id));
}

/* One row: a title, what it takes, the button(s), and a line that says what is happening. Nothing here leaves the guide. */
function wizardAddRow(body, row) {
  const box = el("div", "wizard-add");
  box.appendChild(el("strong", "", row.title));
  box.appendChild(el("span", "muted", " " + row.help));
  if (row.extra) { const line = el("div", "wizard-add-extra"); line.appendChild(row.extra); box.appendChild(line); }
  const actions = el("div", "wizard-actions");
  const pickers = [{ button: row.button, accept: row.accept, multiple: row.multiple }].concat(row.second ? [row.second] : []);
  for (const picker of pickers) {
    const input = el("input");
    input.type = "file";
    input.accept = picker.accept;
    input.multiple = !!picker.multiple;
    input.hidden = true;
    input.addEventListener("change", async () => {
      const files = Array.from(input.files || []);
      input.value = "";
      if (files.length) await row.run(files);
    });
    actions.appendChild(wizardButton(picker.button, () => input.click()));
    actions.appendChild(input);
  }
  if (row.guide) actions.appendChild(wizardButton("How do I get my slides in?", () => openSlidesGuide()));
  box.appendChild(actions);
  const status = el("p", "muted wizard-add-status");
  status.id = "wizard-status-" + row.kind;
  status.setAttribute("role", "status");
  box.appendChild(status);
  const log = el("ul", "wizard-add-log");                  // results of rows that do not use import jobs (themes, media), one line per batch
  log.id = "wizard-log-" + row.kind;
  status.logLine = (text) => { if (text) log.appendChild(el("li", "muted", text)); };
  box.appendChild(log);
  const job = el("ul", "wizard-add-job");                  // every import job of this kind since the guide opened, in the order started
  job.id = "wizard-job-" + row.kind;
  box.appendChild(job);
  body.appendChild(box);
  wizardJobsChanged();
}

/* Called whenever the page learns about import jobs: lists EVERY import of each kind since the guide opened, top to bottom
   in the order they were started (the server lists newest first, so sort by id), each with its own status. */
const wizardJobRow = {};              // slideshow job id -> "deck" or "bundle": the row that was in use when the job first appeared
function wizardJobsChanged() {
  if (!wizardOpen || !state) return;
  const all = (state.imports || []).filter((job) => job.id > wizardJobBase).sort((a, b) => a.id - b.id);
  for (const job of all) if (job.kind === "deck" && !(job.id in wizardJobRow)) wizardJobRow[job.id] = wizardDeckFrom || "deck";
  for (const kind of ["songs", "deck", "bundle", "bible"]) {
    const list = $("wizard-job-" + kind);
    if (!list) continue;
    const jobKind = kind === "bundle" ? "deck" : kind;
    const jobs = all.filter((job) => job.kind === jobKind && (jobKind !== "deck" || wizardJobRow[job.id] === kind));
    list.replaceChildren(...jobs.map((job, n) => el("li", "muted", (jobs.length > 1 ? (n + 1) + ". " : "") + job.label + ": " + jobStatusText(job.status) + (job.message ? ". " + job.message : ""))));
  }
}

/* ---- step "Look, clicker and Stream Deck": small versions of the Settings controls ---- */
function wizardSection(body, title, help) {
  const box = el("div", "wizard-add");
  box.appendChild(el("strong", "", title));
  if (help) box.appendChild(el("span", "muted", " " + help));
  body.appendChild(box);
  return box;
}

function wizardLookBox(body) {
  const look = window.ChurchLook;
  const box = wizardSection(body, "Light or dark, text size", "");
  if (!look) { box.appendChild(el("p", "muted", "This page cannot change its look here.")); return; }
  for (const key of ["theme", "size"]) {
    const row = el("div", "seg");
    const now = look.get()[key];
    for (const [value, label] of LOOK_CHOICES[key]) {
      const b = wizardButton(label, () => { look.set({ [key]: value }); drawWizard(); });
      b.setAttribute("aria-pressed", String(now === value));
      row.appendChild(b);
    }
    box.appendChild(row);
  }
}

function wizardThemeBox(body) {
  const box = wizardSection(body, "Theme", "The look of your slides. Recommended to begin with: the one already chosen. You can make your own later in the Theme Library.");
  const themes = (state && state.themes) || [];
  for (const [kind, label, current] of [["projector", "Projector", state.default_theme], ["overlay", "Lower third", state.default_overlay_theme]]) {
    const choices = themes.filter((theme) => theme.kind === kind);
    if (!choices.length) continue;
    const line = el("label", "wizard-line", label + " ");
    const select = el("select");
    select.setAttribute("aria-label", label + " theme");
    for (const theme of choices) {
      const option = el("option", "", theme.name);
      option.value = String(theme.id);
      select.appendChild(option);
    }
    if (current !== null && current !== undefined) select.value = String(current);
    select.addEventListener("change", async () => { await act("default_theme", { id: Number(select.value), kind: kind }); });
    line.appendChild(select);
    box.appendChild(line);
  }
}

/* The page learns what the server did a moment after each button: when what step 6 shows has changed since it was drawn
   (the home picture, the trigger addresses, the clicker), draw the step again. Called after every state update. */
let wizardExtrasShown = "";
function wizardExtrasKey() {
  const s = state || {};
  return JSON.stringify([(s.home || {}).image || null, !!(s.hook || {}).on, (s.hook || {}).secret || (s.hook || {}).url || "", (s.clicker || {}).running || false, (s.clicker || {}).auto || false]);
}
function wizardStateChanged() {
  if (!wizardOpen) return;
  if (WIZARD_STEPS[wizardAt] === "content") {                       // only the home picture box, so a file being added is not disturbed
    if ((((state || {}).home || {}).image || null) !== wizardHomeShown) wizardRefreshHome();
    return;
  }
  if (WIZARD_STEPS[wizardAt] !== "extras") return;
  if (wizardExtrasKey() !== wizardExtrasShown) drawWizard();
}

let wizardHomeShown = null;
function wizardRefreshHome() {
  const old = document.getElementById("wizard-home-box");
  if (!old) return;
  const holder = el("div");
  wizardHomeBox(holder);
  old.replaceWith(holder.firstChild);
}
function wizardHomeBox(body) {
  const box = wizardSection(body, "Home screen picture", "Optional. Shown on the projector when the screens are blanked or nothing is live.");
  box.id = "wizard-home-box";
  const home = (state && state.home) || { image: null };
  wizardHomeShown = home.image || null;
  if (home.image) {
    const thumb = el("img", "wizard-thumb");
    thumb.alt = "The home screen picture";
    thumb.src = mediaUrl(home.image);
    box.appendChild(thumb);
  }
  const input = el("input");
  input.type = "file";
  input.accept = "image/png,image/jpeg,image/webp,image/gif";
  input.hidden = true;
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    input.value = "";
    if (file) { await chooseHome(file); wizardRefreshHome(); }
  });
  const actions = el("div", "wizard-actions");
  actions.appendChild(wizardButton(home.image ? "Change picture…" : "Choose picture…", () => input.click()));
  actions.appendChild(input);
  if (home.image) actions.appendChild(wizardButton("Remove", async () => { await act("home_set", { image: null }); wizardRefreshHome(); }));
  box.appendChild(actions);
}

function wizardClickerBox(body) {
  const box = wizardSection(body, "Presentation clicker (Windows)", "Optional. Turns the buttons of a clicker into ChurchLight keys.");
  const helper = (state && state.clicker) || { available: false, running: false, auto: false };
  if (!helper.available) {
    box.appendChild(el("p", "muted", "The clicker helper works on Windows only, and needs the program to run there. You can set it up later in Settings."));
    return;
  }
  const actions = el("div", "wizard-actions");
  const status = el("p", "muted");
  status.setAttribute("role", "status");
  const start = async () => {
    if (!state.hook.on && (await act("hook_set", { on: true })) === null) return;
    clickerRows = CLICKER_DEFAULTS.map((row) => Object.assign({}, row));
    saveClickerRows();
    const rows = cleanClickerRows(clickerRows, state.hook.actions);
    if ((await act("clicker_start", { rows })) !== null) { status.textContent = "The clicker is on: Page Down is next, Page Up is back, and the full stop is blank."; drawWizard(); }
  };
  actions.appendChild(wizardButton(helper.running ? "Restart with the usual buttons" : "Start the clicker (usual buttons)", start, !helper.running));
  if (helper.running) actions.appendChild(wizardButton("Stop the clicker", async () => { await act("clicker_stop"); drawWizard(); }));
  box.appendChild(actions);
  const auto = el("label", "check");
  const tick = el("input");
  tick.type = "checkbox";
  tick.checked = !!helper.auto;
  tick.addEventListener("change", () => act("clicker_auto_set", { on: tick.checked }));
  auto.append(tick, document.createTextNode(" Start it whenever ChurchLight starts"));
  box.appendChild(auto);
  box.appendChild(el("p", "muted", "Most clickers send Page Down for the forward button and Page Up for back. To use other keys or buttons, use Settings later."));
  box.appendChild(status);
}

function wizardHookBox(body) {
  const box = wizardSection(body, "Stream Deck and remote button links", "Optional. Web addresses that press ChurchLight's buttons for you.");
  const hook = state && state.hook;
  if (!hook) return;
  const tick = el("input");
  tick.type = "checkbox";
  tick.checked = hook.on;
  tick.addEventListener("change", async () => { await act("hook_set", { on: tick.checked }); drawWizard(); });
  const line = el("label", "check");
  line.append(tick, document.createTextNode(" Turn remote button links on"));
  box.appendChild(line);
  if (!hook.on) return;
  for (const name of ["next", "previous", "blank"]) {
    const action = hook.actions.find((a) => a.name === name);
    if (!action) continue;
    const row = el("div", "wizard-line");
    row.appendChild(el("span", "", action.label + " "));
    const field = el("input");
    field.type = "text";
    field.readOnly = true;
    field.value = hookUrl(name);
    field.setAttribute("aria-label", "Address for: " + action.label);
    field.addEventListener("focus", () => field.select());
    row.appendChild(field);
    const copy = wizardButton("Copy", async () => {
      try { await navigator.clipboard.writeText(field.value); copy.textContent = "Copied"; }
      catch (err) { field.focus(); field.select(); copy.textContent = "Press Ctrl+C"; }
      setTimeout(() => { copy.textContent = "Copy"; }, 1500);
    });
    row.appendChild(copy);
    box.appendChild(row);
  }
  box.appendChild(el("p", "muted", "In Stream Deck, use the Website action with one of these. There are more buttons in Settings."));
}

const WIZARD_DRAW = {
  welcome(body) {
    body.appendChild(el("p", "", "This short guide gets ChurchLight ready for your first service. It takes about 10 minutes, and every step can be skipped and finished later."));
    body.appendChild(el("h3", "", "What you will need"));
    const need = el("ul", "wizard-need");
    for (const text of [
      "This computer, with ChurchLight running (you already have that).",
      "One monitor or projector for each room screen you want. One screen is enough to start.",
      "OBS (or another broadcast software), only if you livestream. If you do not, you can skip that step.",
      "Your songs, slideshows or Bible, if you have them handy. You can add them later.",
    ]) need.appendChild(el("li", "", text));
    body.appendChild(need);
    const where = el("p", "wizard-note");
    where.appendChild(el("strong", "", "Before you start: "));
    where.appendChild(document.createTextNode("place this program window where it will be used to run the program during the church service. The setup uses where this window is to know which monitor that is, and keeps the room screens off it."));
    body.appendChild(where);
    body.appendChild(el("p", "muted", "Nothing here changes what is on a screen. You can open this guide again from the Settings tab."));
  },
  computer(body) {
    const box = el("div");
    body.appendChild(box);
    wizardLoadChecks(box);
    const again = wizardButton("Check again", () => wizardLoadChecks(box));
    body.appendChild(el("div", "wizard-actions")).appendChild(again);
  },
  screens(body) {
    body.appendChild(el("p", "", "Tick what your church uses, then choose which monitor shows each screen. You only choose once."));
    drawWizardKinds(body);
    const rooms = wizardRoomScenes();
    if (rooms.length) {
      if (!wizardMon.loaded) wizardLoadMonitors();
      body.appendChild(el("h3", "wizard-h", "Which monitor shows each room screen?"));
      if (!wizardMon.monitors.length) {
        body.appendChild(el("p", "muted", "This computer's monitors cannot be listed here. You can choose them later in Settings, under Projector windows."));
      } else {
        const here = wizardOperatorMonitor();
        const hereMonitor = wizardMon.monitors.find((m) => m.index === here);
        if (hereMonitor) {
          const line = el("div", "row wizard-here");
          line.appendChild(el("span", "grow", "This program window is on " + hereMonitor.name + " (" + hereMonitor.width + "×" + hereMonitor.height + "). ChurchLight treats that as the monitor you run the service from and keeps room screens off it. If that is wrong, move this window to the right monitor first."));
          line.appendChild(wizardButton("Check again", () => drawWizard()));
          body.appendChild(line);
        }
        for (const room of rooms) {
          const select = el("select", "shape-pick");
          select.setAttribute("aria-label", "Monitor for " + room.label);
          select.appendChild(el("option", "", "Not decided yet"));
          select.firstChild.value = "";
          for (const m of wizardMon.monitors) {
            const option = el("option", "", m.name + " (" + m.width + "×" + m.height + ")" + (m.index === here ? " - this window is here" : ""));
            option.value = String(m.index);
            option.disabled = m.index === here;                           // the monitor you run the service from is never offered
            select.appendChild(option);
          }
          select.value = wizardMon.picks[room.scene] === undefined ? "" : wizardMon.picks[room.scene];
          select.addEventListener("change", async () => {
            const before = wizardMon.picks[room.scene] === undefined ? "" : wizardMon.picks[room.scene];
            wizardMon.picks[room.scene] = select.value;
            wizardMon.status = "";
            const ratio = window.devicePixelRatio || 1;
            const operatorAt = [Math.round((window.screenX + window.outerWidth / 2) * ratio), Math.round((window.screenY + window.outerHeight / 2) * ratio)];
            const result = await call("screen_assign", { name: room.output, monitor: select.value === "" ? null : parseInt(select.value, 10), operator_at: operatorAt, identify: true });
            if (result.ok && select.value !== "") wizardMon.naming = true;
            if (!result.ok) { wizardMon.status = result.error; wizardMon.picks[room.scene] = before; }    // refused: nothing was saved
            await wizardRefreshOpen();
            if (room.scene === "ChurchLight Projector" && result.ok) {
              const m = wizardMon.monitors.find((x) => String(x.index) === select.value);
              wizardMon.shape = m ? await wizardApplyShape(m.width, m.height) : "";
            }
            if (wizardOpen && WIZARD_STEPS[wizardAt] === "screens") drawWizard();        // not if the guide has moved on meanwhile
          });
          const line = el("div", "row wizard-room");
          line.appendChild(el("span", "grow", room.label));
          line.appendChild(select);
          if (wizardMon.open.includes(room.output)) line.appendChild(el("span", "muted", "Open on that monitor"));
          body.appendChild(line);
          const hint = room.scene === "ChurchLight Projector" ? "Recommended: the monitor or projector the congregation sees." : "Optional: only if you have a screen for them.";
          body.appendChild(el("p", "muted wizard-hint", hint));
          if (room.scene === "ChurchLight Projector" && wizardMon.shape && select.value !== "") {
            body.appendChild(el("p", "muted", "Your projector screen is set to " + wizardMon.shape + ", from the size of that monitor. You can change it later in Settings."));
          }
        }
        if (wizardMon.status) {
          const note = el("p", "wizard-note", wizardMon.status);
          note.setAttribute("role", "status");
          body.appendChild(note);
        }
        body.appendChild(el("p", "muted", "Monitors are listed with the main one first, then left to right. As soon as you choose a monitor, that screen opens full screen on it and stays open, and ChurchLight opens it there again every time it starts. It is never put on the monitor this window is on. Choose \"Not decided yet\" to close one. The next step makes OBS capture these screens."));
      }
    }
  },
  obs(body) {
    const skipLine = el("label", "check wizard-skip-obs");
    const skipBox = document.createElement("input");
    skipBox.type = "checkbox";
    skipBox.checked = wizardObs.skip;
    skipBox.addEventListener("change", async () => {
      wizardObs.skip = skipBox.checked;
      drawWizard();
      await call("setup_no_livestream", { value: skipBox.checked });
      if (typeof refreshReady === "function" && !$("landing").hidden) refreshReady();          // the bar behind the guide follows the tick at once
    });
    skipLine.appendChild(skipBox);
    skipLine.appendChild(document.createTextNode(" I don't need the OBS connection (I don't livestream, or I use XSplit or other software)"));
    body.appendChild(skipLine);
    if (wizardObs.skip) {
      body.appendChild(el("p", "wizard-note", "OK, the OBS connection is skipped. Press Next to go on. If you start using OBS later, open this guide again from the Settings tab. XSplit and other software: capture the projector and stage windows, and add the lower third as a web page source by its address (Settings, Projector windows lists them)."));
      return;
    }
    body.appendChild(el("h3", "wizard-h", "What your broadcast software needs"));
    body.appendChild(el("p", "", "ChurchLight shows the projector, stage and confidence screens on your monitors by itself. Your broadcast software (OBS, XSplit or similar) only needs to put them in your stream:"));
    const needs = el("ul", "wizard-need");
    const live = el("li");
    live.appendChild(el("strong", "", "Livestream scene: "));
    live.appendChild(document.createTextNode("your camera with the lower third (the words and verses over the picture) on top. The lower third is a web page (a web page source, called a \"browser source\" in OBS) with a see-through background, so your camera shows behind the words. It is the only screen you add by address."));
    needs.appendChild(live);
    const rooms = el("li");
    rooms.appendChild(el("strong", "", "Projector, stage and confidence scenes: "));
    rooms.appendChild(document.createTextNode("capture that monitor (OBS: Display Capture. XSplit: Screen capture) instead of adding an address."));
    needs.appendChild(rooms);
    body.appendChild(needs);
    const lowerThird = ((state && state.outputs) || []).find((o) => LOWER_THIRD_LAYOUTS.includes(o.layout));
    if (lowerThird) {
      const addr = el("ul", "list address-list");
      addr.appendChild(addressRow("Lower third address", location.origin + lowerThird.path));
      body.appendChild(addr);
    }
    const tested = el("p", "wizard-note");
    tested.appendChild(el("strong", "", "Tested issue in XSplit: "));
    tested.appendChild(document.createTextNode("videos in a browser source (such as the Projector address) can freeze or stay black. Capture the projector's monitor instead."));
    body.appendChild(tested);
    const numbers = el("p", "wizard-note wizard-monitor-note");
    numbers.appendChild(el("strong", "", "Check that each scene shows the right monitor. "));
    numbers.appendChild(document.createTextNode("Each room scene must capture the monitor ChurchLight put that screen on. Do not go by the number: in XSplit, the monitor numbers did not match the Windows numbers (what Windows calls Display 2 can be a different number in XSplit). Look at the picture in the scene preview instead. If you see the wrong screen, pick another monitor in that source's settings until the right one shows. ChurchLight's automatic OBS setup matches monitors by their position, but check the preview there too."));
    body.appendChild(numbers);
    body.appendChild(el("h3", "wizard-h", "OBS: make the scenes automatically"));
    drawWizardObs(body);
    const details = el("details", "wizard-manual");
    details.appendChild(el("summary", "", "Other addresses and doing it by hand (XSplit, or OBS without the automatic setup)"));
    details.appendChild(el("p", "", "These addresses are rarely needed. Capturing the monitor is more reliable, especially for videos. If you still want a screen as a web page (browser source), use its address:"));
    const list = el("ul", "list address-list");
    for (const output of state.outputs) if (!LOWER_THIRD_LAYOUTS.includes(output.layout)) list.appendChild(addressRow(output.name, location.origin + output.path));
    details.appendChild(list);
    const steps = el("ol");
    for (const text of [
      "OBS: in Sources press +, choose Browser, paste an address, and set the size to 1920 by 1080.",
      "XSplit: add a source, choose Webpage, and paste the address.",
      "The lower third has a see-through background, so your camera shows behind the words.",
    ]) steps.appendChild(el("li", "", text));
    details.appendChild(steps);
    body.appendChild(details);
  },
  content(body) {
    body.appendChild(el("p", "", "Add what you will use on Sunday, right here. Each file you choose is added to your library; you stay in this guide."));
    wizardJobBase = wizardLatestJob();
    wizardHomeBox(body);
    wizardAddRow(body, {
      title: "Songs", help: "OpenLyrics, OpenLP, OpenSong, ChordPro or plain text song files.", kind: "songs", button: "Choose song files…",
      accept: ".xml,.txt,.cho,.chordpro,.crd,.chopro", multiple: true,
      run: (files) => importFiles("songs", files, {}, document.getElementById("wizard-status-songs")),
    });
    const deckName = el("input");
    deckName.type = "text";
    deckName.maxLength = 200;
    deckName.placeholder = "Name in the library (optional)";
    deckName.setAttribute("aria-label", "Name of the slideshow");
    wizardAddRow(body, {
      title: "A slideshow", help: "A PDF (in PowerPoint choose Save As, then PDF) or the slide pictures.", kind: "deck", button: "Choose a PDF…", guide: true,
      accept: ".pdf", multiple: true, extra: deckName,
      run: (files) => { wizardDeckFrom = "deck"; return importFiles("deck", files, { name: deckName.value.trim() }, document.getElementById("wizard-status-deck")); },
      second: { button: "Choose slide pictures…", accept: ".png,.jpg,.jpeg,.gif,.webp,image/*", multiple: true },
    });
    const abbr = el("input");
    abbr.type = "text";
    abbr.maxLength = 16;
    abbr.placeholder = "Short name, e.g. KJV";
    abbr.setAttribute("aria-label", "Short name for the Bible");
    const bibleName = el("input");
    bibleName.type = "text";
    bibleName.maxLength = 200;
    bibleName.placeholder = "Full name (optional)";
    bibleName.setAttribute("aria-label", "Full name of the Bible");
    const bibleBox = el("span", "inline");
    bibleBox.append(abbr, bibleName);
    wizardAddRow(body, {
      title: "Another Bible", help: "ChurchLight already comes with the King James Version (KJV), so Scripture is ready to show. Add another only if you want one: a Zefania .xml file, or a .csv with book, chapter, verse, text. Use a Bible you are licensed to use.", kind: "bible",
      button: "Choose a Bible file…", accept: ".xml,.csv,.tsv,.txt", multiple: false, extra: bibleBox,
      run: (files) => importFiles("bible", files, { abbreviation: abbr.value.trim(), name: bibleName.value.trim() }, document.getElementById("wizard-status-bible")),
    });
    wizardAddRow(body, {
      title: "Pictures, videos and sound", help: "For the Media Library: pictures, MP4 videos (H.264), MP3 or WAV sound. A video that will not add is explained here, with how to re-save it for free. The same steps are in the Media Library tab under \"How to prepare a video\".", kind: "media",
      button: "Choose files…", accept: ".mp4,.m4v,.mp3,.wav,.png,.jpg,.jpeg,.gif,.webp", multiple: true,
      run: (files) => importMedia(files, document.getElementById("wizard-status-media")),
    });
    const earlier = el("details", "wizard-manual wizard-earlier");
    earlier.appendChild(el("summary", "", "Moving from an earlier ChurchLight? (optional)"));
    earlier.appendChild(el("p", "muted", "Skip this if ChurchLight is new to you. Songs and Bibles from an earlier ChurchLight are added with the rows above. These two bring over what the rows above cannot."));
    wizardAddRow(earlier, {
      title: "An exported slideshow bundle", help: "The .zip made by Export on another ChurchLight computer. It keeps page order, notes, timing and picture size.", kind: "bundle", button: "Choose a bundle…",
      accept: ".zip", multiple: false,
      run: (files) => { wizardDeckFrom = "bundle"; return importFiles("deck", files, {}, document.getElementById("wizard-status-bundle")); },
    });
    wizardAddRow(earlier, {
      title: "Themes from a previous version", help: "The .zip made by Export in the Theme Library of the earlier ChurchLight.", kind: "themes", button: "Choose a themes file…",
      accept: ".zip", multiple: false,
      run: (files) => wizardImportThemes(files[0], document.getElementById("wizard-status-themes")),
    });
    body.appendChild(earlier);
  },
  extras(body) {
    wizardExtrasShown = wizardExtrasKey();
    body.appendChild(el("p", "", "A few optional things you can set now or later. Everything here works without leaving this guide."));
    wizardLookBox(body);
    wizardThemeBox(body);
    wizardClickerBox(body);
    wizardHookBox(body);
  },
  done(body) {
    body.appendChild(el("p", "", "That is everything. To run a service: open or make a service on the Service tab, press Go on an item, then use Next, Previous and Blank."));
    body.appendChild(el("p", "", "Nothing added yet, or just want to try it out? Make a practice service:"));
    const practice = el("div", "wizard-actions");
    const practiceNote = el("span", "muted");
    practiceNote.id = "wizard-practice-note";
    practice.appendChild(wizardButton("Make a practice service", async () => {
      const result = await act("setup_practice", {});
      if (result) document.getElementById("wizard-practice-note").textContent = "A practice service is ready. You will find it on the Service tab when you finish.";
    }, true));
    practice.appendChild(practiceNote);
    body.appendChild(practice);
    body.appendChild(el("p", "muted", "The practice service has a made-up song and John 3:16, so you can try everything before using your own songs."));
    body.appendChild(el("p", "wizard-note", "You can open this guide again any time from the Settings tab."));
  },
};

/* One plain sentence under each step title: why this step matters, and whether it can be skipped. */
const WIZARD_WHY = {
  computer: "A quick look to make sure this computer is ready for a service.",
  screens: "This tells ChurchLight which monitor shows what, so the words land on the right screen.",
  obs: "Optional: only if you livestream with OBS. ChurchLight can set up the scenes for you. If you do not livestream, press Next.",
  content: "Optional: add your songs, slideshows or Bible now so your first service is not empty. You can do this any time later.",
  extras: "Optional: a look for your screens, plus a clicker or Stream Deck if you have one. Press Next to leave it for now.",
};

function drawWizard() {
  const name = WIZARD_STEPS[wizardAt];
  if (name !== "screens") wizardScreensLive();
  $("wizard-step").textContent = "Step " + (wizardAt + 1) + " of " + WIZARD_STEPS.length;
  $("wizard-title").textContent = WIZARD_TITLES[name];
  $("wizard-why").textContent = WIZARD_WHY[name] || "";
  $("wizard-why").hidden = !WIZARD_WHY[name];
  const body = $("wizard-body");
  body.replaceChildren();
  WIZARD_DRAW[name](body);
  $("wizard-back").hidden = wizardAt === 0;
  const last = wizardAt === WIZARD_STEPS.length - 1;
  $("wizard-next").textContent = last ? "Finish" : (wizardAt === 0 ? "Start" : "Next");
  $("wizard-skip").hidden = last;
  $("wizard-title").focus();
}

function wireWizard() {
  $("wizard-next").addEventListener("click", () => {
    if (wizardAt >= WIZARD_STEPS.length - 1) { wizardFinish(); return; }
    wizardAt += 1;
    if (WIZARD_STEPS[wizardAt] === "obs" && wizardObs.skip) wizardAt += 1;         // "I don't need the OBS connection": go straight past OBS
    $("wizard-message").hidden = true;
    drawWizard();
  });
  $("wizard-back").addEventListener("click", () => { if (wizardAt > 0) { wizardAt -= 1; $("wizard-message").hidden = true; drawWizard(); } });
  $("wizard-skip").addEventListener("click", wizardFinish);
  $("btn-run-setup").addEventListener("click", () => wizardShow(0));
  document.addEventListener("keydown", (event) => {
    if (!wizardOpen) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); wizardHide(); return; }
    if (event.key === "Tab") {                                     // keep the keyboard inside the guide
      const items = [...$("wizard").querySelectorAll("button, a[href], select, input")].filter((n) => !n.hidden && !n.disabled);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === $("wizard-title"))) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }, true);
}

/* Called after every redraw: a new, empty ChurchLight opens the guide once. */
function wizardMaybeOpen() {
  if (wizardAutoTried || !state || !state.setup) return;
  wizardAutoTried = true;
  if (state.setup.first_run) wizardShow(0);
}

wireWizard();
