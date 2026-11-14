// xbloom hub dashboard. No framework, no build step.
//
// Two rules for this file:
// 1. Every string that comes from the hub (recipe names, notes, machine
//    state) goes into the page as text, through h() or textContent. Nothing
//    here builds markup from data.
// 2. Every string a person reads comes from i18n.js through t(). There is no
//    interface text in this file.
"use strict";

// ---- small helpers --------------------------------------------------------
const SVG = "http://www.w3.org/2000/svg";

function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "on") for (const [name, fn] of Object.entries(value)) node.addEventListener(name, fn);
    else if (key === "disabled" || key === "checked" || key === "selected") node[key] = Boolean(value);
    else node.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  for (const child of children.flat(Infinity)) if (child) node.append(child);
  return node;
}

// The four places, drawn in dots like the machine's display: a drop, a list,
// a brew's curve, a prompt. Seven rows of seven; only the lit dots are drawn.
const DOT_ICONS = {
  drop: "0001000 0001000 0011100 0111110 1111111 1111111 0111110",
  list: "0000000 1011111 0000000 1011111 0000000 1011100 0000000",
  curve: "0000000 0000111 0001000 0010000 0100000 1000000 1111111",
  prompt: "0000000 1000000 0100000 0010000 0100000 1000111 0000000",
  apart: "0000000 0000000 1110111 1010101 1110111 0000000 0000000",
  // A cup, for a finished brew; nine by nine, and the two top rows are steam.
  cup: "001010100 010101000 000000000 111111100 100000111 100000101 100000111 010001000 001110000",
  // Water falling: three columns of drops; each row lights in turn.
  pour: "01010 10101 01010 10101 01010 10101 01010",
};
const DOT_ICON_R = 0.4;      // of a 1-unit cell: dots stay apart at 20 px, still read as filled

// Icons for actions, on a 24-unit grid. Strokes unless marked solid.
const ICONS = {
  play: [["M8 5.2v13.6a.6.6 0 0 0 .92.5l10.6-6.8a.6.6 0 0 0 0-1L8.92 4.7a.6.6 0 0 0-.92.5z"]],
  stop: [["M7.5 6h9A1.5 1.5 0 0 1 18 7.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 16.5v-9A1.5 1.5 0 0 1 7.5 6z"]],
  load: ["M12 4v11", "M7.5 10.5L12 15l4.5-4.5", "M5 19.5h14"],
  close: ["M6.5 6.5l11 11", "M17.5 6.5l-11 11"],
  trash: ["M5 7h14", "M9.5 7V5h5v2", "M7 7l.8 12h8.4L17 7"],
  link: ["M10 14a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7L11.5 6.8", "M14 10a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1.3-1.3"],
  unlink: ["M9 15l-1.8 1.8a3.5 3.5 0 0 1-5-5L5 9", "M15 9l1.8-1.8a3.5 3.5 0 0 1 5 5L19 15", "M9 4v2.5", "M15 20v-2.5", "M4 9h2.5", "M20 15h-2.5"],
  layers: ["M12 4l8 4.5-8 4.5-8-4.5z", "M4 13l8 4.5 8-4.5"],
};

function iconSvg(name) {
  if (DOT_ICONS[name]) {
    const lit = [], rows = DOT_ICONS[name].split(" ");
    rows.forEach((row, y) => [...row].forEach((bit, x) => {
      if (bit === "1") lit.push(svg("circle", { cx: x + 0.5, cy: y + 0.5, r: DOT_ICON_R,
        class: name === "cup" && y < 2 ? `steam s${y}` : name === "pour" ? `fall f${y}` : "" }));
    }));
    return svg("svg", { viewBox: `0 0 ${rows[0].length} ${rows.length}`, class: `matrix dot-${name}`, "aria-hidden": "true" }, lit);
  }
  return svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, (ICONS[name] || []).map((part) =>
    Array.isArray(part) ? svg("path", { d: part[0], class: "solid" }) : svg("path", { d: part })));
}

// A button with its icon. The label is always there; the icon is decoration.
function button(label, icon, attrs) {
  return h("button", attrs, icon ? h("span", { class: "icon" }, iconSvg(icon)) : null, label);
}

// replaceChildren() would print a null child as the word "null".
function show(view, ...nodes) { view.replaceChildren(...nodes.flat().filter(Boolean)); }

// For views that fetch: a painter that does nothing once the address has
// changed, so a slow answer cannot draw one page over another.
function painter(view) {
  const here = location.hash;
  return (...nodes) => { if (location.hash === here) show(view, ...nodes); };
}

const clock = (seconds) => {
  const s = Math.max(0, Math.round(seconds || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const whole = (value) => (value === null || value === undefined ? "-" : String(Math.round(value)));
const when = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleString(locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
const timeOfDay = (date) => date.toLocaleTimeString(locale());

// ---- the token ------------------------------------------------------------
// It arrives once in the address (/#t=...), is kept here, and leaves the
// address bar. It is only ever sent in the Authorization header.
const TOKEN_KEY = "xbloom-hub-token";
(function takeToken() {
  const match = location.hash.match(/^#t=([A-Za-z0-9_\-.~+\/=]+)$/);
  if (!match) return;
  try { localStorage.setItem(TOKEN_KEY, match[1]); } catch (_) { /* private mode */ }
  history.replaceState(null, "", location.pathname + "#/");
})();
const token = () => { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; } };

async function api(method, path, body) {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers.Authorization = `Bearer ${token()}`;
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch(path, options); } catch (_) { throw new Error(t("error.no_hub")); }
  let data = null;
  try { data = await response.json(); } catch (_) { /* no body */ }
  if (!response.ok) {
    const name = data && data.error;
    const known = name && MESSAGES.en[`error.${name}`];
    const error = new Error(known ? t(`error.${name}`) : t("error.http", { status: response.status }));
    error.code = name;
    error.fields = data && data.fields;
    throw error;
  }
  return data;
}

// ---- dot-matrix numerals, like the machine's display ------------------------
const GLYPHS = {
  "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
  "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11110 00001 00001 01110 00001 00001 11110",
  "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
  "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
  "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
  ":": "0 0 1 0 1 0 0", ".": "0 0 0 0 0 0 1", "-": "00000 00000 00000 11111 00000 00000 00000",
  ">": "10000 11000 11100 11110 11100 11000 10000", " ": "00 00 00 00 00 00 00",
};

function dots(text, label) {
  let x = 0;
  const circles = [], widths = [];
  for (const char of String(text)) {
    const rows = (GLYPHS[char] || GLYPHS["-"]).split(" ");
    const width = rows[0].length;
    widths.push(width);
    rows.forEach((row, y) => [...row].forEach((bit, dx) => {
      circles.push(svg("circle", { cx: x + dx + 0.5, cy: y + 0.5, r: 0.36, class: bit === "1" ? "on" : "off" }));
    }));
    x += width + 1;
  }
  // data-grid: the glyph widths, so two readouts can be told to have their dots in the same places.
  return svg("svg", { viewBox: `0 0 ${Math.max(1, x - 1)} 7`, class: "dots", role: "img", "aria-label": label || String(text), "data-grid": widths.join(",") }, circles);
}

// Put new dots where old ones are. When the grid is the same size only the
// dots that differ change class, and the stylesheet turns them over.
function swapDots(holder, fresh) {
  const old = holder.querySelector("svg.dots");
  if (!old || old.dataset.grid !== fresh.dataset.grid || old.children.length !== fresh.children.length) {
    if (old) old.replaceWith(fresh); else holder.prepend(fresh);
    return;
  }
  const before = old.children, after = fresh.children;
  for (let i = 0; i < before.length; i += 1) {
    const cls = after[i].getAttribute("class");
    if (before[i].getAttribute("class") !== cls) before[i].setAttribute("class", cls);
  }
  old.setAttribute("aria-label", fresh.getAttribute("aria-label"));
}

function readout(value, label, series, spoken) {
  return h("div", { class: "readout", "data-series": series }, dots(value, `${label} ${spoken || value}`), h("div", { class: "label" }, label));
}

// ---- chart -------------------------------------------------------------------
// series: [{cls, points: [[seconds, value], ...]}], marks: [seconds]
const ENTER_MS = 700;        // how long a page's arrival may animate; redraws after that are instant
const TIP_W = 240;           // the reading's box, in the chart's own units: fits "Water (other brew)  131 ml"
function chart(series, marks, legend, live) {
  // Drawn in its own coordinate space and scaled to the column; the type is
  // sized so it is still readable when that column is a phone.
  const W = 640, H = 340, L = 52, R = 26, T = 14, B = 36;
  const all = series.flatMap((s) => s.points);
  const xMax = Math.max(30, ...all.map((p) => p[0]), ...(marks || []));
  const yMax = Math.max(50, ...all.map((p) => p[1]));
  const xStep = xMax > 240 ? 120 : xMax > 90 ? 60 : 30;
  // At most five steps, whatever arrives: a wrong unit must not draw a thousand labels.
  const yStep = [10, 20, 50, 100, 200, 500].find((step) => yMax / step <= 5) || Math.ceil(yMax / 5);
  const xTop = Math.ceil(xMax / xStep) * xStep, yTop = Math.ceil(yMax / yStep) * yStep;
  const px = (time) => L + (time / xTop) * (W - L - R), py = (v) => H - B - (v / yTop) * (H - T - B);
  const parts = [];
  for (let v = 0; v <= yTop; v += yStep) {
    parts.push(svg("line", { class: "grid-line", x1: L, x2: W - R, y1: py(v), y2: py(v) }));
    const label = svg("text", { class: "axis", x: L - 8, y: py(v) + 6, "text-anchor": "end" });
    label.textContent = String(v);
    parts.push(label);
  }
  for (let time = 0; time <= xTop; time += xStep) {
    const label = svg("text", { class: "axis", x: px(time), y: H - 10, "text-anchor": "middle" });
    label.textContent = clock(time);
    parts.push(label);
  }
  for (const time of marks || []) parts.push(svg("line", { class: "mark", x1: px(time), x2: px(time), y1: T, y2: H - B }));
  for (const s of series) {
    if (s.points.length < 2) continue;
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${px(p[0]).toFixed(1)} ${py(p[1]).toFixed(1)}`).join(" ");
    // Solid curves are drawn in from the left; dashed ones keep their dashes and fade in.
    const solid = !live && (s.cls === "water" || s.cls === "coffee");
    parts.push(svg("path", solid ? { class: `${s.cls} drawn`, d, pathLength: 1 } : { class: live ? s.cls : `${s.cls} faded`, d }));
  }
  // Under the pointer: a line at that moment, a dot on each curve, the values.
  const names = Object.fromEntries(legend);
  const shown = series.filter((s) => s.points.length);
  const line = svg("line", { class: "cursor-line", y1: T, y2: H - B });
  const marksOn = shown.map((s) => svg("circle", { class: `cursor-dot ${s.cls}`, r: 5 }));
  const rows = [svg("text", { class: "tip-time", x: 12, y: 22 }), ...shown.map((s, i) => svg("text", { x: 12, y: 46 + i * 22 }))];
  const box = svg("rect", { class: "tip-box", width: TIP_W, height: 34 + shown.length * 22, rx: 8 });
  const tip = svg("g", {}, box, rows);
  const cursor = svg("g", { class: "cursor", visibility: "hidden" }, line, marksOn, tip);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("chart.label") }, parts, cursor);
  const move = (event) => {
    const bounds = root.getBoundingClientRect();
    const x = ((event.clientX - bounds.left) / bounds.width) * W;
    const time = Math.max(0, Math.min(xTop, ((x - L) / (W - L - R)) * xTop));
    line.setAttribute("x1", px(time)); line.setAttribute("x2", px(time));
    rows[0].textContent = clock(time);
    shown.forEach((s, i) => {
      // The value at this moment, on the line between the readings either
      // side of it; before the first or after the last, that reading itself.
      let before = s.points[0], after = null;
      for (const point of s.points) { if (point[0] > time) { after = point; break; } before = point; }
      const at = after && after[0] > before[0] && time >= before[0]
        ? [time, before[1] + ((after[1] - before[1]) * (time - before[0])) / (after[0] - before[0])] : before;
      marksOn[i].setAttribute("cx", px(at[0])); marksOn[i].setAttribute("cy", py(at[1]));
      const name = names[s.cls] || (s.cls.endsWith(" other") ? t("chart.other", { name: names[s.cls.split(" ")[0]] || "" }) : "");
      rows[i + 1].textContent = `${name}  ${Math.round(at[1] * 10) / 10} ${s.cls.startsWith("coffee") ? "g" : "ml"}`;
    });
    const left = px(time) > W / 2 ? px(time) - TIP_W - 14 : px(time) + 14;
    tip.setAttribute("transform", `translate(${left} ${T + 6})`);
    cursor.setAttribute("visibility", "visible");
  };
  if (shown.length) {
    root.addEventListener("pointermove", move);
    root.addEventListener("pointerdown", move);
    root.addEventListener("pointerleave", () => cursor.setAttribute("visibility", "hidden"));
  }
  return h("div", { class: "chart" }, root,
    h("div", { class: "legend" }, legend.map(([cls, text]) => h("span", {}, h("i", { class: cls }), text))));
}

const LEGEND = () => [["water", t("chart.water")], ["coffee", t("chart.coffee")]];

// The planned water line: flat during pauses, rising during pours.
function planLine(plan) {
  const points = [[0, 0]];
  let before = 0;
  for (const pour of plan.pours) {
    points.push([pour.start_s, before], [pour.end_s, pour.cumulative_ml]);
    before = pour.cumulative_ml;
  }
  return points;
}

// ---- state --------------------------------------------------------------------
let state = null;            // the hub's snapshot
let ticket = null;           // {slug, value}: the start ticket for a load this page made
let flash = null;            // {text, bad}
let busy = false;            // a request is in flight
let library = null;          // recipes with what a card shows
let lastBrew = null;         // the newest brew record, for the desktop summary
let lastBrewAsked = "";      // which machine state it was last fetched in
// slug -> {recipe, as_sent, plan}; null while it is being fetched; false when
// the fetch failed (so it is not asked for again on every snapshot).
// A recipe can be changed elsewhere at any time it is not loaded, so an entry
// is trusted only for a recipe that is loaded or brewing: it is fetched anew
// whenever the machine arms, and a recipe's own page always fetches.
const plans = new Map();
const live = { id: null, water: [], weight: [], marks: [], lastPour: null };
const log = [];              // console: [Date, snapshot] of each change, newest last
const picked = new Set();    // history: brews chosen to compare

const stateName = (name) => (MESSAGES.en[`state.${name}`] ? t(`state.${name}`) : name);
const reasonText = (code) => (code && MESSAGES.en[`reason.${code}`] ? t(`reason.${code}`) : code || "");
const endingText = (code) => (!code ? t("ending.running") : MESSAGES.en[`ending.${code}`] ? t(`ending.${code}`) : code);

// While a request to the hub is on its way, say so: a pill with what is being
// done, wherever on the page the press was. A load takes a couple of seconds.
const workingText = h("span", {});
const workingNode = h("div", { id: "working", role: "status", "aria-live": "polite" },
  h("span", { class: "beat" }, h("i", {}), h("i", {}), h("i", {})), workingText);

async function act(fn, what) {
  if (busy) return;
  workingText.textContent = what || t("working");
  document.body.dataset.busy = "";
  busy = true; flash = null; render();
  try { await fn(); } catch (error) { flash = { text: error.message, bad: true }; }
  delete document.body.dataset.busy;
  busy = false; render();
}

function onSnapshot(next) {
  const before = state;
  state = next;
  if (!before || before.state !== next.state || before.activity !== next.activity || before.error !== next.error) {
    log.push([new Date(), next]);
    if (log.length > 200) log.shift();
  }
  if (next.state !== "armed") ticket = null;
  const brew = next.brew;
  if (brew) {
    if (live.id !== brew.id) {
      // lastPour starts at the current pour: joining a brew is not a pour starting.
      Object.assign(live, { id: brew.id, water: [], weight: [], marks: [], lastPour: brew.pour });
      if (brew.elapsed_s > 2) backfill(brew.id);       // opened or reloaded mid-brew
    }
    if (next.water_ml !== null) live.water.push([brew.elapsed_s, next.water_ml]);
    if (next.weight_g !== null) live.weight.push([brew.elapsed_s, next.weight_g]);
    if (brew.pour !== live.lastPour) { live.lastPour = brew.pour; live.marks.push(brew.elapsed_s); }
  }
  const slug = (next.recipe && next.recipe.slug) || (brew && brew.slug);
  // The machine has just armed: whatever this page had cached for that recipe
  // may predate an edit. Fetch what is stored now; it cannot change while loaded.
  if (slug && next.state === "armed" && (!before || before.state !== "armed")) plans.delete(slug);
  if (slug && !plans.has(slug)) loadPlan(slug);
  renderStatus();
  // Every view: the ones that fetch redraw only when their key changes, and
  // the machine's state is part of that key (a recipe opened directly must
  // enable its Load button once the state arrives).
  render();
}

function fetchLibrary() {
  if (library || fetchLibrary.pending) return;
  fetchLibrary.pending = true;
  api("GET", "/api/library").then((list) => { library = list; }).catch(() => {})
    .finally(() => { fetchLibrary.pending = false; render(); });
}

// Once each time the machine comes to rest, so a brew that just ended shows up.
let recent = [];             // every brew's summary, newest first
function fetchLastBrew() {
  // live.id is the last brew this page watched; a new one means a new record.
  const mark = `rest:${live.id || ""}`;
  if (lastBrewAsked === mark) return;
  lastBrewAsked = mark;
  api("GET", "/api/brews").then((brews) => { recent = brews; return brews.length ? api("GET", `/api/brews/${brews[0].id}`) : null; })
    .then((record) => { lastBrew = record; render(); }).catch(() => {});
}

async function loadPlan(slug) {
  plans.set(slug, null);
  try { plans.set(slug, await api("GET", `/api/recipes/${encodeURIComponent(slug)}/plan`)); } catch (_) { plans.set(slug, false); }
  render();
}

// The part of a running brew that happened before this page was looking.
async function backfill(id) {
  let record;
  try { record = await api("GET", `/api/brews/${encodeURIComponent(id)}`); } catch (_) { return; }
  if (live.id !== id) return;
  const first = (points) => (points.length ? points[0][0] : Infinity);
  live.water = record.samples.water.filter((p) => p[0] < first(live.water)).concat(live.water);
  live.weight = record.samples.weight.filter((p) => p[0] < first(live.weight)).concat(live.weight);
  const starts = pourStarts(record);
  live.marks = starts.filter((time) => time < first(live.marks.map((m) => [m]))).concat(live.marks);
  render();
}

let hubReachable = true;
function connect() {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => { hubReachable = true; onSnapshot(JSON.parse(event.data)); };
  source.onerror = () => { hubReachable = false; renderStatus(); };
}

function renderStatus() {
  const lamp = document.getElementById("status-lamp"), text = document.getElementById("status-text");
  const detail = document.getElementById("status-detail");
  if (!hubReachable) { lamp.dataset.state = "offline"; text.textContent = t("status.no_hub"); detail.textContent = ""; return; }
  if (!state) { text.textContent = t("status.connecting"); return; }
  lamp.dataset.state = state.state;
  document.body.dataset.state = state.state;      // the ground's lights follow the machine
  text.textContent = stateName(state.state);
  const name = (state.brew && state.brew.recipe_name) || (state.recipe && state.recipe.name) || "";
  detail.textContent = state.state === "offline" ? reasonText(state.error) : name;
  const key = [state.state, state.error, name, state.cup, state.water_enough, state.fw_version, LANGUAGE].join("|");
  if (key === renderStatus.last) return;
  renderStatus.last = key;
  document.getElementById("machine").replaceChildren(
    h("div", { class: "state" }, h("span", { class: "lamp", "data-state": state.state }), stateName(state.state)),
    (state.state === "offline" ? reasonText(state.error) : name) ? h("div", { class: "muted" }, state.state === "offline" ? reasonText(state.error) : name) : "",
    h("dl", { class: "facts" },
      h("dt", {}, t("cup")), h("dd", {}, state.cup === true ? t("cup.on") : state.cup === false ? t("cup.off") : t("cup.unknown")),
      h("dt", {}, t("tank")), h("dd", {}, state.water_enough === 1 ? t("tank.ok") : state.water_enough === 0 ? t("tank.low") : "-"),
      h("dt", {}, t("console.firmware")), h("dd", { class: "num" }, state.fw_version || "-")),
    state.state === "released"
      ? button(t("action.acquire"), "link", { class: "quiet small", on: { click: () => act(() => api("POST", "/api/link/acquire")) } })
      : button(t("action.release"), "unlink", { class: "quiet small", disabled: state.state === "brewing", on: { click: confirmRelease } }));
}

// ---- views ----------------------------------------------------------------------
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "recipes") return { name: "recipes", id: parts[1], sub: parts[2] };
  if (parts[0] === "history") return { name: "history", id: parts[1] };
  if (parts[0] === "compare") return { name: "compare", ids: parts.slice(1) };
  if (parts[0] === "console") return { name: "console" };
  return { name: "home" };
}

function cupLine() {
  const text = state.cup === true ? t("cup.on") : state.cup === false ? t("cup.off") : t("cup.unknown");
  return [h("dt", {}, t("cup")), h("dd", {}, text)];
}

const tankText = () => (state.water_enough === 1 ? t("tank.ok") : state.water_enough === 0 ? t("tank.low") : "-");

function recipeFacts(info) {
  const sent = info.as_sent, recipe = info.recipe;
  return h("dl", { class: "facts" },
    h("dt", {}, t("fact.dose")), h("dd", { class: "num" }, `${sent.dose_g} g`),
    h("dt", {}, t("fact.water")), h("dd", { class: "num" }, `${sent.total_ml} ml`),
    h("dt", {}, t("fact.pours")), h("dd", { class: "num" }, t("fact.pours_value", { count: recipe.pours.length, time: clock(info.plan.pour_end_s) })),
    h("dt", {}, t("fact.grind")), h("dd", { class: "num" },
      recipe.grind ? t("fact.grind_value", { size: recipe.grind_size, rpm: recipe.grinder_speed_rpm }) : t("fact.no_grind")));
}

// One row per pour: its share of the water in large numerals, as the official
// app lays a recipe out. `current` marks the pour in progress while brewing.
function poursList(info, current) {
  const { recipe, plan } = info;
  return h("ol", { class: "pours", id: current === undefined ? null : "live-pours" }, plan.pours.map((p, i) => h("li",
    { "data-now": current === i ? "true" : null, "data-done": current !== undefined && current !== null && i < current ? "true" : null },
    h("div", { class: "share num" }, Math.round((p.volume_ml / plan.total_ml) * 100), h("small", {}, "%")),
    h("div", { class: "detail num" },
      h("span", {}, `${p.volume_ml} ml`), h("span", {}, `${p.temperature_c} °C`),
      h("span", {}, `${recipe.pours[i].flow_rate.toFixed(1)} ml/s`),
      h("span", {}, p.pause_s ? t("recipe.pause", { s: p.pause_s }) : t("recipe.no_pause")),
      h("span", { class: "when" }, t("recipe.when", { start: clock(p.start_s), end: clock(p.end_s), total: p.cumulative_ml }))))));
}

const planChart = (info) => chart([{ cls: "plan", points: planLine(info.plan) }], info.plan.pours.map((p) => p.start_s), [["plan", t("chart.plan")]]);

// A recipe as a card: name, the numbers that tell recipes apart, and a bar of
// its pours, each as wide as its share of the water and tinted by temperature.
function recipeCard(r) {
  const parts = [];
  if (r.valid) {
    let x = 0;
    for (const pour of r.pours) {
      const width = (pour.volume_ml / r.total_ml) * 100;
      const cls = pour.temperature_c >= 92 ? "hot" : pour.temperature_c >= 80 ? "warm" : "cool";
      parts.push(svg("rect", { x: x + 0.4, y: 0, width: Math.max(0.5, width - 0.8), height: 8, rx: 2, class: cls }));
      x += width;
    }
  }
  return h(r.valid ? "a" : "div", { class: "recipe", href: r.valid ? `#/recipes/${encodeURIComponent(r.slug)}` : null },
    h("div", { class: "name" }, r.name || r.slug),
    h("div", { class: "line num" }, r.valid
      ? t("library.line", { dose: r.dose_g, ratio: r.ratio, total: r.total_ml, time: clock(r.pour_end_s) }) : t("library.invalid")),
    r.valid ? svg("svg", { class: "bar", viewBox: "0 0 100 8", preserveAspectRatio: "none", "aria-hidden": "true" }, parts) : null);
}

function lastBrewCard() {
  if (!lastBrew) return h("section", { class: "card wide-only" }, h("h2", {}, t("home.last_brew")), h("p", { class: "muted" }, t("history.empty")));
  const water = lastBrew.samples.water, weight = lastBrew.samples.weight;
  return h("section", { class: "card wide-only" },
    h("div", { class: "row" }, h("h2", {}, t("home.last_brew")), h("a", { href: `#/history/${lastBrew.id}` }, t("home.open"))),
    h("div", {}, recordName(lastBrew)),
    h("dl", { class: "facts" },
      h("dt", {}, t("record.time")), h("dd", { class: "num" }, when(lastBrew.started_at)),
      h("dt", {}, t("record.water")), h("dd", { class: "num" }, water.length ? `${Math.round(water[water.length - 1][1])} ml` : "-"),
      h("dt", {}, t("record.coffee")), h("dd", { class: "num" }, weight.length ? `${Math.round(weight[weight.length - 1][1])} g` : "-"),
      h("dt", {}, t("record.result")), h("dd", {}, endingText(lastBrew.reason))),
    chart([{ cls: "water", points: water }, { cls: "coffee", points: weight }], [], LEGEND()));
}

// Just after a brew, as the machine does it: the final numbers, and the cup.
function viewFinished() {
  fetchLastBrew();
  const title = h("div", { class: "done" }, iconSvg("cup"), h("h1", {}, t("home.finished")));
  // Until the record of the brew this page just watched has arrived, show no numbers.
  if (!lastBrew || (live.id && lastBrew.id !== live.id)) return [title];
  const last = (points) => (points.length ? points[points.length - 1][1] : null);
  const water = lastBrew.samples.water, weight = lastBrew.samples.weight;
  return [
    title,
    h("div", { class: "muted" }, recordName(lastBrew)),
    h("div", { class: "readouts" },
      readout(clock(lastBrew.duration_s || 0), t("readout.time"), null),
      readout(whole(last(water)), t("readout.water"), "water"),
      readout(whole(last(weight)), t("readout.coffee"), "coffee")),
    h("section", { class: "card done-chart" },
      chart([{ cls: "water", points: water }, { cls: "coffee", points: weight }], pourStarts(lastBrew), LEGEND())),
    h("p", {}, h("a", { href: `#/history/${lastBrew.id}` }, t("home.open"))),
  ];
}

const scaleDots = () => dots(state.weight_g === null || state.weight_g === undefined ? "-" : String(Math.round(state.weight_g)), t("home.scale"));

// The scale changes several times a second; only its dots are redrawn.
function updateScale() {
  const node = document.getElementById("live-scale");
  if (node) swapDots(node, scaleDots());
}

// The last few brews and what they add up to.
function recentCard() {
  if (!recent.length) return null;
  const water = recent.reduce((sum, b) => sum + (b.water_ml || 0), 0);
  return h("section", { class: "card wide-only" },
    h("div", { class: "row" }, h("h2", {}, t("home.recent")), h("a", { href: "#/history" }, t("home.all_brews"))),
    h("dl", { class: "facts" },
      h("dt", {}, t("home.count")), h("dd", { class: "num" }, String(recent.length)),
      h("dt", {}, t("home.total_water")), h("dd", { class: "num" }, `${Math.round(water)} ml`)),
    h("ul", { class: "list compact" }, recent.slice(0, 5).map((b) => h("li", {},
      h("a", { href: `#/history/${b.id}` },
        h("span", { class: "title" }, b.recipe_name || t("brew.at_machine")),
        h("span", { class: "sub num" }, [when(b.started_at), `${b.water_ml == null ? "-" : Math.round(b.water_ml)} ml`, endingText(b.reason)].join("   ")))))));
}

// What is reached for most, as large as the machine draws its own three.
function tile(icon, label, attrs) {
  return h(attrs.href ? "a" : "button", { class: "tile", ...attrs }, h("span", { class: "icon" }, iconSvg(icon)), h("span", {}, label));
}

function viewHome() {
  if (!state) return [h("p", { class: "muted" }, t("connecting_hub"))];
  const s = state.state;
  if (s === "offline") {
    return [h("h1", {}, t("home.offline")), h("p", { class: "muted" }, reasonText(state.error) || t("home.offline_retry")),
      h("p", {}, h("a", { href: "#/console" }, t("home.to_console")))];
  }
  if (s === "released") {
    return [h("h1", {}, t("home.released")), h("p", { class: "muted" }, t("home.released_hint")),
      h("div", { class: "actions" }, button(t("action.acquire"), "link", { disabled: busy, on: { click: () => act(() => api("POST", "/api/link/acquire")) } }))];
  }
  if (s === "brewing") return viewBrewing();
  if (s === "armed") return viewArmed();
  if (s === "busy") return [h("h1", {}, t("home.busy")), h("p", { class: "muted" }, t("home.busy_hint"))];
  if (s === "finished") return viewFinished();

  fetchLibrary();
  fetchLastBrew();
  const cards = (library || []).slice(0, 6).map(recipeCard);
  return [
    h("h1", {}, s === "asleep" ? t("home.asleep") : t("home.ready")),
    h("div", { class: "home" },
      // What the machine is doing right now: its scale, as its own display shows it.
      h("div", { class: "home-now" },
        h("section", { class: "card scale" },
          h("div", { id: "live-scale" }, scaleDots()),
          h("div", { class: "label" }, t("home.scale")),
          h("dl", { class: "facts" }, cupLine(), h("dt", {}, t("tank")), h("dd", {}, tankText()))),
        h("div", { class: "tiles" },
          tile("list", t("tab.recipes"), { href: "#/recipes" }),
          tile("curve", t("tab.history"), { href: "#/history" }))),
      h("div", { class: "home-pick" },
        h("h2", {}, t("home.pick")),
        !library ? h("p", { class: "muted" }, t("loading"))
          : cards.length ? h("div", { class: "grid" }, cards) : h("p", { class: "muted" }, t("recipes.empty")),
        library && library.length > 6 ? h("p", {}, h("a", { href: "#/recipes" }, t("home.all_recipes"))) : null),
      h("div", { class: "home-past" }, lastBrewCard(), recentCard())),
  ];
}

function viewArmed() {
  const slug = state.recipe.slug;
  const info = plans.get(slug);
  const mine = ticket && ticket.slug === slug;
  const left = [];
  if (info) {
    const recipe = info.recipe;
    // The same three things the machine's own display shows now.
    left.push(h("div", { class: "readouts" },
      readout(recipe.grind ? String(recipe.grind_size) : "--", t("readout.grind"), null, recipe.grind ? String(recipe.grind_size) : t("fact.no_grind")),
      readout(info.as_sent.ratio.slice(2), t("readout.ratio"), null, info.as_sent.ratio),
      readout(">", t("readout.waiting"), null, t("readout.waiting"))));
    left.push(h("section", { class: "card" }, recipeFacts(info), h("dl", { class: "facts" }, cupLine())));
  } else left.push(h("section", { class: "card" }, h("dl", { class: "facts" }, cupLine())));
  if (!mine) left.push(h("p", { class: "notice" }, t("armed.not_mine")));
  left.push(h("div", { class: "actions" },
    mine
      ? button(t("action.start"), "play", { disabled: busy || !info, on: { click: () => confirmStart(info) } })
      : button(t("action.reload"), "load", { disabled: busy, on: { click: () => act(async () => { await api("POST", "/api/machine/cancel"); await load(slug); }, t("working.load")) } }),
    button(t("action.cancel_load"), "close", { class: "quiet", disabled: busy, on: { click: () => act(() => api("POST", "/api/machine/cancel"), t("working.cancel")) } })));
  return [
    h("h1", {}, state.recipe.name || slug),
    h("div", { class: "cols" }, h("div", {}, left),
      info ? h("div", { class: "wide-only" }, h("section", { class: "card" }, h("h2", {}, t("card.plan")), planChart(info)), poursList(info)) : null),
  ];
}

// The parts of the brewing screen that change several times a second.
function brewingParts() {
  const brew = state.brew;
  const info = brew.slug ? plans.get(brew.slug) : null;
  const total = info ? info.recipe.pours.length : null;
  const n = brew.pour === null || brew.pour === undefined ? null : brew.pour + 1;
  const series = [{ cls: "water", points: live.water }, { cls: "coffee", points: live.weight }];
  const legend = LEGEND();
  if (info) { series.unshift({ cls: "plan", points: planLine(info.plan) }); legend.push(["plan", t("chart.plan")]); }
  return {
    pour: n ? (total ? t("brew.pour_of", { n, total }) : t("brew.pour", { n })) : t("brew.preparing"),
    readouts: [readout(clock(brew.elapsed_s), t("readout.time"), null),
      readout(whole(state.water_ml), t("readout.water"), "water"),
      readout(whole(state.weight_g), t("readout.coffee"), "coffee")],
    chart: chart(series, live.marks, legend, true),
    index: brew.pour === undefined ? null : brew.pour,
  };
}

function viewBrewing() {
  const parts = brewingParts();
  const info = state.brew.slug ? plans.get(state.brew.slug) : null;
  return [
    h("div", { class: "row" }, h("div", { class: "done" }, iconSvg("pour"), h("h1", {}, state.brew.recipe_name || t("brew.at_machine"))),
      h("span", { class: "muted num", id: "live-pour" }, parts.pour)),
    h("div", { class: "readouts", id: "live-readouts" }, parts.readouts),
    h("div", { class: "cols" },
      h("div", {}, h("div", { id: "live-chart" }, parts.chart),
        // Not redrawn while the brew runs, so a press on it is never lost.
        h("div", { class: "actions" }, button(t("action.stop"), "stop", { id: "stop", class: "danger", disabled: stopping, on: { click: confirmStop } }))),
      info ? h("div", { class: "wide-only" }, poursList(info, parts.index)) : null),
  ];
}

function updateBrewing() {
  const pour = document.getElementById("live-pour");
  if (!pour) return false;
  const parts = brewingParts();
  pour.textContent = parts.pour;
  const shown = document.getElementById("live-readouts").children;
  parts.readouts.forEach((fresh, i) => swapDots(shown[i], fresh.querySelector("svg.dots")));
  document.getElementById("live-chart").replaceChildren(parts.chart);
  const list = document.getElementById("live-pours");
  if (list) [...list.children].forEach((item, i) => {
    if (i === parts.index) item.dataset.now = "true"; else delete item.dataset.now;
    if (parts.index !== null && i < parts.index) item.dataset.done = "true"; else delete item.dataset.done;
  });
  return true;
}

async function load(slug) {
  plans.delete(slug);       // what is about to be loaded is what the hub holds now
  const loaded = await api("POST", "/api/machine/load", { slug });
  ticket = { slug, value: loaded.ticket };
  location.hash = "#/";
}

async function confirmStart(info) {
  const body = [h("p", {}, t("start.body")), recipeFacts(info), h("dl", { class: "facts" }, cupLine())];
  if (await ask(t("start.title", { name: info.recipe.name }), body, t("action.start"))) {
    act(async () => {
      try {
        await api("POST", "/api/machine/start", { ticket: ticket && ticket.value });
        ticket = null;
      } catch (error) {
        // The hub keeps the ticket when it refuses for a missing cup; in every
        // other case it is spent or void, and so is ours.
        if (error.code !== "no_cup") ticket = null;
        throw error;
      }
    }, t("working.start"));
  }
}

// Stop is not held behind another request: a start can still be waiting for
// its answer when the brew has already begun. The hub serialises stops
// itself and refuses a second one while the first is on its way.
let stopping = false;
async function confirmStop() {
  if (!(await ask(t("stop.title"), [h("p", {}, t("stop.body"))], t("action.stop"), true)) || stopping) return;
  stopping = true;
  const node = document.getElementById("stop");
  if (node) node.disabled = true;          // the same node: it is never rebuilt during a brew
  workingText.textContent = t("working.stop");
  document.body.dataset.busy = "";
  try { await api("POST", "/api/machine/stop"); } catch (error) { flash = { text: error.message, bad: true }; last = ""; }
  stopping = false;
  if (node) node.disabled = false;
  if (!busy) delete document.body.dataset.busy;
  render();
}

// On the home screen this sits beside two links, so it asks first.
async function confirmRelease() {
  if (await ask(t("release.title"), [h("p", {}, t("release.body"))], t("action.release"), false)) act(() => api("POST", "/api/link/release"));
}

function ask(title, body, yes, danger) {
  const dialog = document.getElementById("confirm");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").replaceChildren(...body);
  document.getElementById("confirm-no").textContent = t("back");
  const confirm = document.getElementById("confirm-yes");
  confirm.textContent = yes;
  confirm.className = danger ? "danger" : "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true });
    dialog.returnValue = "no";
    dialog.showModal();
    document.getElementById("confirm-no").focus();
  });
}

// ---- recipes ------------------------------------------------------------------------
async function viewRecipes(view, id, sub) {
  const paint = painter(view);
  if (id === "new") return viewRecipeEdit(view, null, sub);         // sub: a recipe to start from
  if (id && sub === "edit") return viewRecipeEdit(view, id, id);
  if (id) return viewRecipe(view, id);
  if (!library) { paint(h("p", { class: "muted" }, t("loading"))); library = await api("GET", "/api/library"); }
  paint(h("div", { class: "row" }, h("h1", {}, t("recipes.title")),
    h("a", { class: "pill", href: "#/recipes/new" }, t("action.new_recipe"))),
    library.length ? h("div", { class: "grid three" }, library.map(recipeCard)) : h("p", { class: "muted" }, t("recipes.empty")));
}

// ---- writing a recipe ------------------------------------------------------------------
// The draft outlives a repaint of the page (the machine falling asleep causes
// one), so what has been typed is not lost.
let editing = null;          // {key, draft, slug, errors}
const NEW_RECIPE = () => ({ name: "", cup_type: 2, dose_g: 15, ratio: 15, grind: true, grind_size: 60, grinder_speed_rpm: 80,
  pours: [{ volume_ml: 45, temperature_c: 92, pause_s: 30, pattern: 2, flow_rate: 3, agitate_before: 2, agitate_after: 2 },
    { volume_ml: 180, temperature_c: 92, pause_s: 0, pattern: 3, flow_rate: 3.5, agitate_before: 2, agitate_after: 2 }],
  meta: { bean: "", roaster: "", process: "", roast: "", notes: "", source: "" } });

function field(label, input, wide) {
  return h("label", { class: wide ? "field wide" : "field" }, h("span", {}, label), input);
}

function numberInput(object, key, attrs, after) {
  return h("input", { type: "number", inputmode: "decimal", value: object[key], ...attrs,
    on: { input: (event) => { object[key] = event.target.value === "" ? null : Number(event.target.value); if (after) after(); } } });
}

function textInput(object, key, attrs) {
  return h("input", { type: "text", value: object[key] || "", ...attrs, on: { input: (event) => { object[key] = event.target.value; } } });
}

function choice(object, key, options, after) {
  return h("select", { on: { change: (event) => { object[key] = Number(event.target.value); if (after) after(); } } },
    options.map(([value, text]) => h("option", { value, selected: object[key] === value }, text)));
}

function onOff(object, key, on, off) {
  return h("input", { type: "checkbox", checked: object[key] === on, on: { change: (event) => { object[key] = event.target.checked ? on : off; } } });
}

async function viewRecipeEdit(view, slug, from) {
  const paint = painter(view);
  const key = location.hash;
  if (!editing || editing.key !== key) {
    const mine = { key, draft: NEW_RECIPE(), slug: slug || "", errors: null, ready: null };
    editing = mine;
    mine.ready = from ? api("GET", `/api/recipes/${encodeURIComponent(from)}`).then((stored) => {
      mine.draft = { ...mine.draft, ...stored, ratio: Number(String(stored.ratio).split(":")[1]),
        meta: { ...mine.draft.meta, ...(stored.meta || {}) }, pours: stored.pours.map((pour) => ({ ...pour })) };
      if (!slug) mine.draft.name = t("edit.copy_of", { name: stored.name });
    }) : Promise.resolve();
  }
  const mine = editing;
  paint(h("p", { class: "muted" }, t("loading")));
  await mine.ready;
  if (editing !== mine) return;
  const { draft } = editing;
  const total = h("p", { class: "muted num", id: "edit-total" });
  const sum = () => {
    const poured = draft.pours.reduce((n, pour) => n + (pour.volume_ml || 0), 0);
    total.textContent = t("edit.total", { poured, target: Math.round((draft.dose_g || 0) * (draft.ratio || 0) * 10) / 10 });
  };
  const redraw = () => { last = ""; render(); };
  const pours = draft.pours.map((pour, i) => h("li", { class: "pour-edit" },
    h("div", { class: "n" }, String(i + 1)),
    field(t("edit.volume"), numberInput(pour, "volume_ml", { min: 1, max: 240, step: 1 }, sum)),
    field(t("edit.temperature"), numberInput(pour, "temperature_c", { min: 20, max: 98, step: 1 })),
    field(t("edit.flow"), numberInput(pour, "flow_rate", { min: 3, max: 3.5, step: 0.1 })),
    field(t("edit.pause"), numberInput(pour, "pause_s", { min: 0, max: 59, step: 1 })),
    field(t("edit.pattern"), choice(pour, "pattern", [[1, t("pattern.1")], [2, t("pattern.2")], [3, t("pattern.3")]])),
    field(t("edit.agitate_before"), onOff(pour, "agitate_before", 1, 2)),
    field(t("edit.agitate_after"), onOff(pour, "agitate_after", 1, 2)),
    draft.pours.length > 1 ? button(t("action.remove"), "close", { class: "quiet small", type: "button",
      on: { click: () => { draft.pours.splice(i, 1); redraw(); } } }) : null));
  const save = () => act(async () => {
    const target = slug || editing.slug.trim();
    if (!slug) {
      // A new recipe gets an id of its own: well formed, not the word this
      // page uses for "new", and not one that is already taken.
      const taken = /^[a-z0-9][a-z0-9-]{0,63}$/.test(target) && target !== "new"
        ? await api("GET", `/api/recipes/${encodeURIComponent(target)}`).then(() => true, (error) => { if (error.code === "not_found") return false; throw error; })
        : null;
      if (taken !== false) {
        editing.errors = { slug: taken ? "in_use" : "bad_slug" };
        throw new Error(t(taken ? "edit.slug_taken" : "edit.slug_bad"));
      }
    }
    const meta = Object.fromEntries(Object.entries(draft.meta).filter(([, value]) => value && value.trim()));
    const body = { ...draft, ratio: `1:${draft.ratio}`, pours: draft.pours.map((pour) => ({ ...pour })) };
    if (Object.keys(meta).length) body.meta = meta; else delete body.meta;
    try {
      await api("PUT", `/api/recipes/${encodeURIComponent(target)}`, body);
    } catch (error) {
      editing.errors = error.fields || { [error.code || "error"]: error.message };
      throw error;
    }
    editing = null; library = null; plans.delete(target);
    location.hash = `#/recipes/${encodeURIComponent(target)}`;
  }, t("working.save"));
  paint(
    h("p", {}, h("a", { href: slug ? `#/recipes/${encodeURIComponent(slug)}` : "#/recipes" }, t("back"))),
    h("h1", {}, slug ? t("edit.title") : t("edit.title_new")),
    editing.errors ? h("section", { class: "notice bad" }, h("div", {}, t("edit.errors")),
      h("ul", {}, Object.entries(editing.errors).map(([name, code]) => h("li", { class: "num" }, `${name}: ${code}`)))) : null,
    h("div", { class: "cols" },
      h("div", {},
        h("section", { class: "card form" },
          field(t("edit.name"), textInput(draft, "name", { maxlength: 80 }), true),
          slug ? null : field(t("edit.slug"), textInput(editing, "slug", { maxlength: 64, pattern: "[a-z0-9][a-z0-9-]*", placeholder: "my-recipe", autocapitalize: "none", spellcheck: "false" }), true),
          field(t("edit.cup"), choice(draft, "cup_type", [[1, t("cup_type.1")], [2, t("cup_type.2")], [3, t("cup_type.3")], [4, t("cup_type.4")]])),
          field(t("fact.dose"), numberInput(draft, "dose_g", { min: 1, max: 25, step: 1 }, sum)),
          field(t("edit.ratio"), numberInput(draft, "ratio", { min: 5, max: 25, step: 0.5 }, sum)),
          field(t("edit.grind"), h("input", { type: "checkbox", checked: draft.grind, on: { change: (event) => { draft.grind = event.target.checked; } } })),
          field(t("edit.grind_size"), numberInput(draft, "grind_size", { min: 1, max: 80, step: 1 })),
          field(t("edit.rpm"), numberInput(draft, "grinder_speed_rpm", { min: 60, max: 120, step: 10 }))),
        h("h2", {}, t("edit.pours")),
        h("ol", { class: "pours-edit" }, pours),
        total,
        h("div", { class: "actions" },
          button(t("action.add_pour"), null, { class: "quiet", type: "button", disabled: draft.pours.length >= 9, on: { click: () => {
            const lastPour = draft.pours[draft.pours.length - 1];
            draft.pours.push({ ...lastPour, pause_s: 0 }); redraw();
          } } }))),
      h("div", {},
        h("section", { class: "card form" },
          field(t("meta.bean"), textInput(draft.meta, "bean", { maxlength: 200 }), true),
          field(t("meta.roaster"), textInput(draft.meta, "roaster", { maxlength: 200 }), true),
          field(t("meta.process"), textInput(draft.meta, "process", { maxlength: 200 })),
          field(t("meta.roast"), textInput(draft.meta, "roast", { maxlength: 200 })),
          field(t("meta.notes"), h("textarea", { rows: 4, on: { input: (event) => { draft.meta.notes = event.target.value; } } }, draft.meta.notes || ""), true)),
        h("div", { class: "actions" },
          button(t("action.save"), null, { disabled: busy, on: { click: save } })),
        h("p", { class: "muted" }, t("edit.hint")))));
  sum();
}

async function viewRecipe(view, slug) {
  const paint = painter(view);
  let info;
  if (!plans.get(slug)) paint(h("p", { class: "muted" }, t("loading")));
  try { info = await api("GET", `/api/recipes/${encodeURIComponent(slug)}/plan`); plans.set(slug, info); }
  catch (error) { paint(h("p", { class: "notice bad" }, error.message)); return; }
  const { recipe } = info;
  const canLoad = state && (state.state === "idle" || state.state === "asleep");
  const meta = recipe.meta || {};
  // Only an https address becomes a link; anything else stays text.
  const source = meta.source && /^https:\/\//.test(meta.source)
    ? h("a", { href: meta.source, rel: "noopener noreferrer", target: "_blank" }, meta.source) : meta.source;
  const metaRows = ["bean", "roaster", "process", "roast"].filter((key) => meta[key])
    .map((key) => [h("dt", {}, t(`meta.${key}`)), h("dd", {}, meta[key])]);
  if (source) metaRows.push([h("dt", {}, t("meta.source")), h("dd", {}, source)]);
  if (meta.notes) metaRows.push([h("dt", {}, t("meta.notes")), h("dd", {}, meta.notes)]);
  paint(
    flashNode(),
    h("p", {}, h("a", { href: "#/recipes" }, t("recipe.all"))),
    h("h1", {}, recipe.name || slug),
    h("div", { class: "cols" },
      h("div", {}, poursList(info), h("p", { class: "muted" }, t("recipe.computed"))),
      h("div", {},
        h("section", { class: "card" }, recipeFacts(info)),
        h("div", { class: "actions" },
          button(t("action.load"), "load", { disabled: busy || !canLoad, on: { click: () => act(() => load(slug), t("working.load")) } }),
          h("a", { class: "pill", href: `#/recipes/${encodeURIComponent(slug)}/edit` }, t("action.edit")),
          h("a", { class: "pill", href: `#/recipes/new/${encodeURIComponent(slug)}` }, t("action.duplicate")),
          button(t("action.delete"), "trash", { class: "quiet", disabled: busy, on: { click: async () => {
            if (await ask(t("delete.title", { name: recipe.name }), [h("p", {}, t("delete.body"))], t("action.delete"), true)) {
              act(async () => { await api("DELETE", `/api/recipes/${encodeURIComponent(slug)}`); library = null; plans.delete(slug); location.hash = "#/recipes"; });
            }
          } } })),
        canLoad ? null : h("p", { class: "muted" }, t("recipe.load_hint")),
        h("section", { class: "card" }, h("h2", {}, t("card.plan")), planChart(info)),
        metaRows.length ? h("section", { class: "card" }, h("dl", { class: "facts long" }, metaRows)) : null)));
}

// ---- history ---------------------------------------------------------------------------
async function viewHistory(view, id) {
  const paint = painter(view);
  if (id) return viewBrew(view, id);
  paint(h("p", { class: "muted" }, t("loading")));
  const brews = await api("GET", "/api/brews");
  const items = brews.map((b) => h("li", { class: "pick" },
    h("input", { type: "checkbox", "aria-label": t("history.pick"), checked: picked.has(b.id), on: { change: (event) => {
      if (event.target.checked) picked.add(b.id); else picked.delete(b.id);
      document.getElementById("compare").disabled = picked.size !== 2;
    } } }),
    h("a", { href: `#/history/${b.id}` },
      h("span", { class: "title" }, b.recipe_name || t("brew.at_machine")),
      h("span", { class: "sub num" }, [when(b.started_at), `${b.water_ml === null ? "-" : Math.round(b.water_ml)} ml`,
        b.duration_s === null ? "" : clock(b.duration_s), endingText(b.reason)].filter(Boolean).join("   ")))));
  paint(h("h1", {}, t("history.title")),
    brews.length ? h("ul", { class: "list" }, items) : h("p", { class: "muted" }, t("history.empty")),
    brews.length > 1 ? h("div", { class: "actions" }, button(t("action.compare"), "layers", { id: "compare", class: "quiet", disabled: picked.size !== 2,
      on: { click: () => { location.hash = `#/compare/${[...picked].join("/")}`; } } })) : null);
}

const pourStarts = (record) => record.events.filter((e) => e[1].cmd === 40510).map((e) => e[0]);
const recordName = (record) => (record.recipe && record.recipe.name) || t("brew.at_machine");

async function viewBrew(view, id) {
  const paint = painter(view);
  paint(h("p", { class: "muted" }, t("loading")));
  let record;
  try { record = await api("GET", `/api/brews/${encodeURIComponent(id)}`); }
  catch (error) { paint(h("p", { class: "notice bad" }, error.message)); return; }
  const starts = pourStarts(record);
  const offset = starts.length ? starts[0] : 0;      // the plan's zero is the first pour
  const series = [{ cls: "water", points: record.samples.water }, { cls: "coffee", points: record.samples.weight }];
  const legend = LEGEND();
  if (record.plan) {
    series.unshift({ cls: "plan", points: planLine(record.plan).map((p) => [p[0] + offset, p[1]]) });
    legend.push(["plan", t("chart.plan")]);
  }
  const water = record.samples.water, weight = record.samples.weight;
  const rows = record.plan ? record.plan.pours.map((p, i) => {
    const actual = starts[i] === undefined ? null : starts[i] - offset;
    const diff = actual === null ? null : Math.round(actual - p.start_s);
    return h("tr", {}, h("td", {}, t("table.pour_n", { n: i + 1 })), h("td", { class: "num" }, clock(p.start_s)),
      h("td", { class: "num" }, actual === null ? "-" : clock(actual)),
      h("td", { class: "num" }, diff === null ? "-" : t("table.seconds", { sign: diff >= 0 ? "+" : "−", s: Math.abs(diff) })));
  }) : [];
  paint(
    h("p", {}, h("a", { href: "#/history" }, t("history.all"))),
    h("h1", {}, recordName(record)),
    h("div", { class: "cols" },
      h("div", {}, chart(series, starts, legend)),
      h("div", {},
        h("section", { class: "card" }, h("dl", { class: "facts" },
          h("dt", {}, t("record.time")), h("dd", { class: "num" }, when(record.started_at)),
          h("dt", {}, t("record.result")), h("dd", {}, endingText(record.reason)),
          h("dt", {}, t("record.duration")), h("dd", { class: "num" }, record.duration_s === null ? "-" : clock(record.duration_s)),
          h("dt", {}, t("record.water")), h("dd", { class: "num" }, water.length ? `${Math.round(water[water.length - 1][1])} ml` : "-"),
          h("dt", {}, t("record.coffee")), h("dd", { class: "num" }, weight.length ? `${Math.round(weight[weight.length - 1][1])} g` : "-"),
          h("dt", {}, t("record.started")), h("dd", {}, record.hub_started ? t("record.started_here") : t("record.started_machine")))),
        rows.length ? h("section", { class: "card" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, t("table.pour")), h("th", {}, t("table.planned")),
          h("th", {}, t("table.actual")), h("th", {}, t("table.diff")))), h("tbody", {}, rows)), h("p", { class: "muted" }, t("table.note"))) : null)));
}

async function viewCompare(view, ids) {
  const paint = painter(view);
  paint(h("p", { class: "muted" }, t("loading")));
  let records;
  try { records = await Promise.all(ids.slice(0, 2).map((id) => api("GET", `/api/brews/${encodeURIComponent(id)}`))); }
  catch (error) { paint(h("p", { class: "notice bad" }, error.message)); return; }
  if (records.length < 2) { location.hash = "#/history"; return; }
  const shifted = (record, key) => { const s = pourStarts(record)[0] || 0; return record.samples[key].map((p) => [p[0] - s, p[1]]).filter((p) => p[0] >= 0); };
  const [a, b] = records;
  const name = (record) => t("compare.name", { name: recordName(record), when: when(record.started_at) });
  paint(
    h("p", {}, h("a", { href: "#/history" }, t("history.all"))), h("h1", {}, t("compare.title")),
    h("div", { class: "cols" },
      h("div", {}, chart([{ cls: "water", points: shifted(a, "water") }, { cls: "coffee", points: shifted(a, "weight") },
        { cls: "water other", points: shifted(b, "water") }, { cls: "coffee other", points: shifted(b, "weight") }], [], LEGEND())),
      h("div", {}, h("section", { class: "card" },
        h("dl", { class: "facts long" }, h("dt", {}, t("compare.solid")), h("dd", {}, name(a)), h("dt", {}, t("compare.dashed")), h("dd", {}, name(b))),
        h("p", { class: "muted" }, t("compare.note"))))));
}

// ---- console -----------------------------------------------------------------------------
let gateRecent = [];
async function refreshGate() { try { gateRecent = await api("GET", "/api/gate"); if (route().name === "console") render(); } catch (_) { /* keep the old list */ } }

function viewConsole() {
  if (!state) return [h("p", { class: "muted" }, t("connecting_hub"))];
  const released = state.state === "released";
  const changes = log.slice().reverse().map(([at, snap]) =>
    [timeOfDay(at), stateName(snap.state), `activity=${snap.activity}`, snap.error || ""].join("  ").trimEnd());
  const commands = gateRecent.slice().reverse().map((g) => `${timeOfDay(new Date(g.at * 1000))}  ${g.code}`);
  return [
    h("h1", {}, t("console.title")),
    h("div", { class: "cols even" },
      h("div", {},
        h("section", { class: "card" }, h("dl", { class: "facts" },
          h("dt", {}, t("console.link")), h("dd", {}, stateName(state.state)),
          state.error && [h("dt", {}, t("console.reason")), h("dd", {}, reasonText(state.error))],
          h("dt", {}, t("console.firmware")), h("dd", { class: "num" }, state.fw_version || "-"),
          h("dt", {}, t("console.activity")), h("dd", { class: "num" }, state.activity === null ? "-" : state.activity),
          h("dt", {}, t("tank")), h("dd", {}, tankText()),
          cupLine(),
          h("dt", {}, t("console.token")), h("dd", {}, token() ? t("console.token_yes") : t("console.token_no")),
          h("dt", {}, h("label", { for: "language" }, t("console.language"))),
          h("dd", {}, h("select", { id: "language", on: { change: (event) => { setLanguage(event.target.value); event.target.blur(); applyLanguage(); } } },
            Object.entries(LANGUAGE_NAMES).map(([code, name]) => h("option", { value: code, selected: code === LANGUAGE }, name)))))),
        h("section", { class: "card" }, h("h2", {}, t("console.share")), h("p", { class: "muted" }, t("console.share_hint")),
          h("div", { class: "actions" }, released
            ? button(t("action.acquire"), "link", { disabled: busy, on: { click: () => act(() => api("POST", "/api/link/acquire")) } })
            : button(t("action.release"), "unlink", { class: "quiet", disabled: busy || state.state === "brewing", on: { click: () => act(() => api("POST", "/api/link/release")) } })))),
      h("div", {},
        h("section", { class: "card" }, h("h2", {}, t("console.changes")), h("pre", { class: "log" }, changes.join("\n") || t("console.no_changes"))),
        h("section", { class: "card" }, h("h2", {}, t("console.commands")), h("pre", { class: "log" }, commands.join("\n") || t("console.no_commands"))))),
  ];
}

// ---- render ----------------------------------------------------------------------------------
function flashNode() { return flash ? h("p", { class: flash.bad ? "notice bad" : "notice" }, flash.text) : null; }

// The fixed text in index.html, and anything already drawn, in the chosen language.
function applyLanguage() {
  document.documentElement.lang = LANGUAGE;
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-label]")) node.setAttribute("aria-label", t(node.dataset.i18nLabel));
  last = "";
  renderStatus();
  render();
}

let last = "";
function render() {
  const view = document.getElementById("view");
  const r = route();
  for (const tab of document.querySelectorAll("#tabs a")) {
    const on = tab.dataset.tab === (r.name === "compare" ? "history" : r.name);
    if (on) tab.setAttribute("aria-current", "page"); else tab.removeAttribute("aria-current");
  }
  if (r.name === "home" || r.name === "console") {
    // Snapshots arrive several times a second. Rebuild only when what is
    // shown changes shape; otherwise leave the nodes, and their buttons, be.
    const s = state || {};
    const slug = (s.recipe && s.recipe.slug) || (s.brew && s.brew.slug) || "";
    const shape = r.name === "home"
      ? [s.state, s.error, slug, s.brew && s.brew.id, Boolean(ticket && ticket.slug === slug), Boolean(plans.get(slug)),
        s.cup, s.water_enough, library ? library.length : -1, lastBrew ? lastBrew.id : "", recent.length]
      : [s.state, s.error, s.activity, s.cup, s.water_enough, s.fw_version, log.length, gateRecent.length, token() ? 1 : 0];
    const key = [r.name, busy, flash ? flash.text : "", LANGUAGE, ...shape].join("|");
    if (key === last) {
      if (r.name === "home" && s.state === "brewing") updateBrewing();
      else if (r.name === "home") updateScale();
      return;
    }
    // Do not rebuild the console under an open language menu.
    if (r.name === "console" && document.activeElement && document.activeElement.id === "language") return;
    last = key;
    show(view, flashNode(), r.name === "home" ? viewHome() : viewConsole());
  } else {
    // These fetch; redraw them when the address changes, not on every snapshot.
    const key = [location.hash, busy, flash ? flash.text : "", state ? state.state : "", LANGUAGE].join("|");
    if (key === last) return;
    last = key;
    const done = r.name === "recipes" ? viewRecipes(view, r.id && decodeURIComponent(r.id), r.sub && decodeURIComponent(r.sub))
      : r.name === "history" ? viewHistory(view, r.id) : viewCompare(view, r.ids);
    const paint = painter(view);
    done.catch((error) => paint(h("p", { class: "notice bad" }, error.message)));
  }
}

function arrive() {
  const view = document.getElementById("view");
  view.dataset.enter = "";
  clearTimeout(arrive.timer);
  arrive.timer = setTimeout(() => { delete view.dataset.enter; }, ENTER_MS);
}
window.addEventListener("hashchange", () => { arrive(); flash = null; last = ""; library = null;
  if (editing && editing.key !== location.hash) editing = null;    // an abandoned draft is not picked up again
  if (route().name === "console") refreshGate(); render(); document.getElementById("view").focus(); });
setInterval(() => { if (route().name === "console") refreshGate(); }, 3000);
for (const node of document.querySelectorAll("[data-icon]")) node.append(iconSvg(node.dataset.icon));
document.body.append(workingNode);
arrive();
applyLanguage();
connect();
if (route().name === "console") refreshGate();
