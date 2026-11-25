/* How the operator page looks: light or dark, text size, high contrast and an accent color.
   It runs in the page head so the right look is in place before anything is drawn. The choice is kept in this
   browser only (localStorage, wrapped in try/catch). The screens that the congregation sees are not affected;
   they follow the slide themes. */
(function () {
  "use strict";
  const KEY = "churchlight.look";
  const THEMES = ["auto", "light", "dark"];
  const SIZES = ["small", "medium", "large"];
  const ACCENTS = ["blue", "green", "purple", "orange"];
  const DEFAULTS = { theme: "auto", size: "medium", contrast: false, accent: "blue" };
  const HEX = /^#[0-9a-f]{6}$/i;                       // an accent of your own is stored as "#rrggbb"

  /* ---- an accent colour of your own: kept readable ----
     The accent is used as a button background (with text on it) and as text on the panels, so whatever is chosen is nudged
     in lightness until it is readable on the current light or dark look. The hue is kept. */
  function toRgb(hex) { return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16)); }
  function toHex(rgb) { return "#" + rgb.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join(""); }
  function toHsl(hex) {
    const [r, g, b] = toRgb(hex).map((v) => v / 255);
    const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2, d = max - min;
    if (d === 0) return [0, 0, l];
    const sat = d / (1 - Math.abs(2 * l - 1));
    let h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    h = (h * 60 + 360) % 360;
    return [h, sat, l];
  }
  function fromHsl(h, sat, l) {
    const c = (1 - Math.abs(2 * l - 1)) * sat, x = c * (1 - Math.abs(((h / 60) % 2) - 1)), m = l - c / 2;
    const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
    return toHex([(r + m) * 255, (g + m) * 255, (b + m) * 255]);
  }
  function luminanceOf(hex) {
    const [r, g, b] = toRgb(hex).map((v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  }
  /* {accent, text, hover, adjusted} for a chosen colour on the "light" or "dark" look. */
  function fitAccent(hex, theme) {
    const dark = theme !== "light";
    const fits = (lum) => (dark ? lum >= 0.30 && lum <= 0.60 : lum >= 0.04 && lum <= 0.15);
    const [h, sat, l0] = toHsl(hex);
    let best = hex.toLowerCase();
    if (!fits(luminanceOf(best))) {
      for (let d = 0; d <= 1.0001; d += 0.005) {
        const up = fromHsl(h, sat, Math.min(1, l0 + d)), down = fromHsl(h, sat, Math.max(0, l0 - d));
        if (fits(luminanceOf(up))) { best = up; break; }
        if (fits(luminanceOf(down))) { best = down; break; }
      }
    }
    const [bh, bs, bl] = toHsl(best);
    const hover = dark ? fromHsl(bh, bs, Math.min(0.95, bl + 0.08)) : fromHsl(bh, bs, bl * 0.8);
    return { accent: best, text: dark ? "#06121f" : "#ffffff", hover: hover, adjusted: best !== hex.toLowerCase() };
  }

  function parseLook(raw) {
    const look = Object.assign({}, DEFAULTS);
    try {
      const data = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (data && typeof data === "object") {
        if (THEMES.includes(data.theme)) look.theme = data.theme;
        if (SIZES.includes(data.size)) look.size = data.size;
        if (typeof data.contrast === "boolean") look.contrast = data.contrast;
        if (ACCENTS.includes(data.accent)) look.accent = data.accent;
        else if (typeof data.accent === "string" && HEX.test(data.accent)) look.accent = data.accent.toLowerCase();
      }
    } catch (err) { /* nothing stored, or damaged: the defaults */ }
    return look;
  }

  function resolveTheme(theme, prefersLight) {
    return theme === "auto" ? (prefersLight ? "light" : "dark") : theme;
  }

  function attributesFor(look, prefersLight) {
    return { "data-theme": resolveTheme(look.theme, prefersLight), "data-size": look.size,
             "data-contrast": look.contrast ? "high" : "normal", "data-accent": HEX.test(look.accent) ? "custom" : look.accent };
  }

  const api = { KEY, THEMES, SIZES, ACCENTS, DEFAULTS, HEX, parseLook, resolveTheme, attributesFor, fitAccent };

  if (typeof document !== "undefined") {
    const media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
    let current = DEFAULTS;
    try { current = parseLook(localStorage.getItem(KEY)); } catch (err) { /* defaults */ }

    const apply = () => {
      const attrs = attributesFor(current, !!(media && media.matches));
      for (const name of Object.keys(attrs)) document.documentElement.setAttribute(name, attrs[name]);
      const style = document.documentElement.style;
      if (HEX.test(current.accent)) {                              // an accent of your own: set its three colours, fitted to this look
        const fitted = fitAccent(current.accent, attrs["data-theme"]);
        style.setProperty("--accent", fitted.accent);
        style.setProperty("--accent-text", fitted.text);
        style.setProperty("--accent-hover", fitted.hover);
        api.fitted = fitted;
      } else {
        for (const name of ["--accent", "--accent-text", "--accent-hover"]) style.removeProperty(name);
        api.fitted = null;
      }
    };
    api.get = () => Object.assign({}, current);
    api.set = (change) => {
      current = parseLook(Object.assign({}, current, change));
      try { localStorage.setItem(KEY, JSON.stringify(current)); } catch (err) { /* only a convenience */ }
      apply();
      return api.get();
    };
    if (media && media.addEventListener) media.addEventListener("change", apply);
    apply();
    window.ChurchLook = api;
  }
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
