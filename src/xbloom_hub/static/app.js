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
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs, ...children) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, String(value));
  for (const child of children.flat()) if (child) node.append(child);
  return node;
}

// replaceChildren() would print a null child as the word "null".
function show(view, ...nodes) { view.replaceChildren(...nodes.flat().filter(Boolean)); }

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
  const match = location.hash.match(/^#t=([A-Za-z0-9_\-.~]+)$/);
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
  const circles = [];
  for (const char of String(text)) {
    const rows = (GLYPHS[char] || GLYPHS["-"]).split(" ");
    const width = rows[0].length;
    rows.forEach((row, y) => [...row].forEach((bit, dx) => {
      circles.push(svg("circle", { cx: x + dx + 0.5, cy: y + 0.5, r: 0.36, class: bit === "1" ? "on" : "off" }));
    }));
    x += width + 1;
  }
  return svg("svg", { viewBox: `0 0 ${Math.max(1, x - 1)} 7`, class: "dots", role: "img", "aria-label": label || String(text) }, circles);
}

function readout(value, label, series, spoken) {
  return h("div", { class: "readout", "data-series": series }, dots(value, `${label} ${spoken || value}`), h("div", { class: "label" }, label));
}

// ---- chart -------------------------------------------------------------------
// series: [{cls, points: [[seconds, value], ...]}], marks: [seconds]
function chart(series, marks, legend) {
  // Drawn in its own coordinate space and scaled to the column; the type is
  // sized so it is still readable when that column is a phone.
  const W = 640, H = 340, L = 52, R = 26, T = 14, B = 36;
  const all = series.flatMap((s) => s.points);
  const xMax = Math.max(30, ...all.map((p) => p[0]), ...(marks || []));
  const yMax = Math.max(50, ...all.map((p) => p[1]));
  const xStep = xMax > 240 ? 120 : xMax > 90 ? 60 : 30;
  const yStep = yMax > 300 ? 200 : 100;
  const xTop = Math.ceil(xMax / xStep) * xStep, yTop = Math.ceil(yMax / yStep) * yStep;
  const px = (time) => L + (time / xTop) * (W - L - R), py = (v) => H - B - (v / yTop) * (H - T - B);
  const parts = [];
  for (let v = 0; v <= yTop; v += yStep) {
    parts.push(svg("line", { class: "grid", x1: L, x2: W - R, y1: py(v), y2: py(v) }));
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
    parts.push(svg("path", { class: s.cls, d }));
  }
  return h("div", { class: "chart" },
    svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": t("chart.label") }, parts),
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
let recipeList = null;
const plans = new Map();     // slug -> {recipe, as_sent, plan}
const live = { id: null, water: [], weight: [], marks: [], lastPour: null };
const log = [];              // console: [Date, snapshot] of each change, newest last
const picked = new Set();    // history: brews chosen to compare

const stateName = (name) => (MESSAGES.en[`state.${name}`] ? t(`state.${name}`) : name);
const reasonText = (code) => (code && MESSAGES.en[`reason.${code}`] ? t(`reason.${code}`) : code || "");
const endingText = (code) => (!code ? t("ending.running") : MESSAGES.en[`ending.${code}`] ? t(`ending.${code}`) : code);

async function act(fn) {
  if (busy) return;
  busy = true; flash = null; render();
  try { await fn(); } catch (error) { flash = { text: error.message, bad: true }; }
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
    if (live.id !== brew.id) Object.assign(live, { id: brew.id, water: [], weight: [], marks: [], lastPour: null });
    if (next.water_ml !== null) live.water.push([brew.elapsed_s, next.water_ml]);
    if (next.weight_g !== null) live.weight.push([brew.elapsed_s, next.weight_g]);
    if (brew.pour !== live.lastPour) { live.lastPour = brew.pour; live.marks.push(brew.elapsed_s); }
  }
  const slug = (next.recipe && next.recipe.slug) || (brew && brew.slug);
  if (slug && !plans.has(slug)) loadPlan(slug);
  renderStatus();
  // Every view: the ones that fetch redraw only when their key changes, and
  // the machine's state is part of that key (a recipe opened directly must
  // enable its Load button once the state arrives).
  render();
}

async function loadPlan(slug) {
  plans.set(slug, null);
  try { plans.set(slug, await api("GET", `/api/recipes/${encodeURIComponent(slug)}/plan`)); } catch (_) { plans.delete(slug); }
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
  text.textContent = stateName(state.state);
  const name = (state.brew && state.brew.recipe_name) || (state.recipe && state.recipe.name) || "";
  detail.textContent = state.state === "offline" ? reasonText(state.error) : name;
}

// ---- views ----------------------------------------------------------------------
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "recipes") return { name: "recipes", id: parts[1] };
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

function viewHome() {
  if (!state) return [h("p", { class: "muted" }, t("connecting_hub"))];
  const s = state.state;
  if (s === "offline") {
    return [h("h1", {}, t("home.offline")), h("p", { class: "muted" }, reasonText(state.error) || t("home.offline_retry")),
      h("p", {}, h("a", { href: "#/console" }, t("home.to_console")))];
  }
  if (s === "released") {
    return [h("h1", {}, t("home.released")), h("p", { class: "muted" }, t("home.released_hint")),
      h("div", { class: "actions" }, h("button", { disabled: busy, on: { click: () => act(() => api("POST", "/api/link/acquire")) } }, t("action.acquire")))];
  }
  if (s === "brewing") return viewBrewing();
  if (s === "armed") return viewArmed();
  if (s === "busy") return [h("h1", {}, t("home.busy")), h("p", { class: "muted" }, t("home.busy_hint"))];

  if (!recipeList) { api("GET", "/api/recipes").then((list) => { recipeList = list; render(); }).catch(() => {}); }
  const items = (recipeList || []).slice(0, 6).map((r) => h("li", {}, h("a", { href: `#/recipes/${encodeURIComponent(r.slug)}` },
    h("span", { class: "title" }, r.name || r.slug), h("span", { class: "sub" }, t("home.open")))));
  return [
    h("h1", {}, s === "asleep" ? t("home.asleep") : t("home.ready")),
    h("dl", { class: "facts" }, cupLine(), h("dt", {}, t("tank")), h("dd", {}, tankText())),
    h("div", { class: "stack" }, h("h2", {}, t("home.pick")),
      !recipeList ? h("p", { class: "muted" }, t("loading"))
        : items.length ? h("ul", { class: "list" }, items) : h("p", { class: "muted" }, t("recipes.empty")),
      recipeList && recipeList.length > 6 ? h("p", {}, h("a", { href: "#/recipes" }, t("home.all_recipes"))) : null),
  ];
}

function viewArmed() {
  const slug = state.recipe.slug;
  const info = plans.get(slug);
  const mine = ticket && ticket.slug === slug;
  const out = [h("h1", {}, state.recipe.name || slug)];
  if (info) {
    const recipe = info.recipe;
    // The same three things the machine's own display shows now.
    out.push(h("div", { class: "readouts" },
      readout(recipe.grind ? String(recipe.grind_size) : "--", t("readout.grind"), null, recipe.grind ? String(recipe.grind_size) : t("fact.no_grind")),
      readout(info.as_sent.ratio.slice(2), t("readout.ratio"), null, info.as_sent.ratio),
      readout(">", t("readout.waiting"), null, t("readout.waiting"))));
    out.push(recipeFacts(info));
  }
  out.push(h("dl", { class: "facts" }, cupLine()));
  if (!mine) out.push(h("p", { class: "notice" }, t("armed.not_mine")));
  out.push(h("div", { class: "actions" },
    mine
      ? h("button", { disabled: busy || !info, on: { click: () => confirmStart(info) } }, t("action.start"))
      : h("button", { disabled: busy, on: { click: () => act(async () => { await api("POST", "/api/machine/cancel"); await load(slug); }) } }, t("action.reload")),
    h("button", { class: "quiet", disabled: busy, on: { click: () => act(() => api("POST", "/api/machine/cancel")) } }, t("action.cancel_load"))));
  return out;
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
    chart: chart(series, live.marks, legend),
  };
}

function viewBrewing() {
  const parts = brewingParts();
  return [
    h("div", { class: "row" }, h("h1", {}, state.brew.recipe_name || t("brew.at_machine")),
      h("span", { class: "muted num", id: "live-pour" }, parts.pour)),
    h("div", { class: "readouts", id: "live-readouts" }, parts.readouts),
    h("div", { id: "live-chart" }, parts.chart),
    // Not redrawn while the brew runs, so a press on it is never lost.
    h("div", { class: "actions" }, h("button", { class: "danger", disabled: busy, on: { click: confirmStop } }, t("action.stop"))),
  ];
}

function updateBrewing() {
  const pour = document.getElementById("live-pour");
  if (!pour) return false;
  const parts = brewingParts();
  pour.textContent = parts.pour;
  document.getElementById("live-readouts").replaceChildren(...parts.readouts);
  document.getElementById("live-chart").replaceChildren(parts.chart);
  return true;
}

async function load(slug) {
  const loaded = await api("POST", "/api/machine/load", { slug });
  ticket = { slug, value: loaded.ticket };
  location.hash = "#/";
}

async function confirmStart(info) {
  const body = [h("p", {}, t("start.body")), recipeFacts(info), h("dl", { class: "facts" }, cupLine())];
  if (await ask(t("start.title", { name: info.recipe.name }), body, t("action.start"))) {
    act(async () => { const value = ticket && ticket.value; ticket = null; await api("POST", "/api/machine/start", { ticket: value }); });
  }
}

async function confirmStop() {
  if (await ask(t("stop.title"), [h("p", {}, t("stop.body"))], t("action.stop"), true)) act(() => api("POST", "/api/machine/stop"));
}

function ask(title, body, yes, danger) {
  const dialog = document.getElementById("confirm");
  document.getElementById("confirm-title").textContent = title;
  document.getElementById("confirm-body").replaceChildren(...body);
  document.getElementById("confirm-no").textContent = t("back");
  const button = document.getElementById("confirm-yes");
  button.textContent = yes;
  button.className = danger ? "danger" : "";
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(dialog.returnValue === "yes"), { once: true });
    dialog.returnValue = "no";
    dialog.showModal();
    document.getElementById("confirm-no").focus();
  });
}

// ---- recipes ------------------------------------------------------------------------
async function viewRecipes(view, id) {
  if (id) return viewRecipe(view, id);
  if (!recipeList) { show(view, h("p", { class: "muted" }, t("loading"))); recipeList = await api("GET", "/api/recipes"); }
  const items = recipeList.map((r) => h("li", {}, h("a", { href: `#/recipes/${encodeURIComponent(r.slug)}` },
    h("span", { class: "title" }, r.name || r.slug), h("span", { class: "sub" }, r.slug))));
  show(view, h("h1", {}, t("recipes.title")),
    recipeList.length ? h("ul", { class: "list" }, items) : h("p", { class: "muted" }, t("recipes.empty")));
}

async function viewRecipe(view, slug) {
  let info = plans.get(slug);
  if (!info) {
    show(view, h("p", { class: "muted" }, t("loading")));
    try { info = await api("GET", `/api/recipes/${encodeURIComponent(slug)}/plan`); plans.set(slug, info); }
    catch (error) { show(view, h("p", { class: "notice bad" }, error.message)); return; }
  }
  const { recipe, plan } = info;
  const canLoad = state && (state.state === "idle" || state.state === "asleep");
  const meta = recipe.meta || {};
  // Only an https address becomes a link; anything else stays text.
  const source = meta.source && /^https:\/\//.test(meta.source)
    ? h("a", { href: meta.source, rel: "noopener noreferrer", target: "_blank" }, meta.source) : meta.source;
  const pours = plan.pours.map((p, i) => h("li", {},
    h("div", { class: "share num" }, Math.round((p.volume_ml / plan.total_ml) * 100), h("small", {}, "%")),
    h("div", { class: "detail num" },
      h("span", {}, `${p.volume_ml} ml`), h("span", {}, `${p.temperature_c} °C`),
      h("span", {}, `${recipe.pours[i].flow_rate.toFixed(1)} ml/s`),
      h("span", {}, p.pause_s ? t("recipe.pause", { s: p.pause_s }) : t("recipe.no_pause")),
      h("span", { class: "when" }, t("recipe.when", { start: clock(p.start_s), end: clock(p.end_s), total: p.cumulative_ml })))));
  const metaRows = ["bean", "roaster", "process", "roast"].filter((key) => meta[key])
    .map((key) => [h("dt", {}, t(`meta.${key}`)), h("dd", {}, meta[key])]);
  if (source) metaRows.push([h("dt", {}, t("meta.source")), h("dd", {}, source)]);
  if (meta.notes) metaRows.push([h("dt", {}, t("meta.notes")), h("dd", {}, meta.notes)]);
  show(view,
    flashNode(),
    h("p", {}, h("a", { href: "#/recipes" }, t("recipe.all"))),
    h("h1", {}, recipe.name || slug),
    recipeFacts(info),
    h("ol", { class: "pours" }, pours),
    h("p", { class: "muted" }, t("recipe.computed")),
    metaRows.length ? h("dl", { class: "facts long" }, metaRows) : null,
    h("div", { class: "actions" },
      h("button", { disabled: busy || !canLoad, on: { click: () => act(() => load(slug)) } }, t("action.load")),
      h("button", { class: "quiet", disabled: busy, on: { click: async () => {
        if (await ask(t("delete.title", { name: recipe.name }), [h("p", {}, t("delete.body"))], t("action.delete"), true)) {
          act(async () => { await api("DELETE", `/api/recipes/${encodeURIComponent(slug)}`); recipeList = null; plans.delete(slug); location.hash = "#/recipes"; });
        }
      } } }, t("action.delete"))),
    canLoad ? null : h("p", { class: "muted" }, t("recipe.load_hint")));
}

// ---- history ---------------------------------------------------------------------------
async function viewHistory(view, id) {
  if (id) return viewBrew(view, id);
  show(view, h("p", { class: "muted" }, t("loading")));
  const brews = await api("GET", "/api/brews");
  const items = brews.map((b) => h("li", { class: "pick" },
    h("input", { type: "checkbox", "aria-label": t("history.pick"), checked: picked.has(b.id), on: { change: (event) => {
      if (event.target.checked) picked.add(b.id); else picked.delete(b.id);
      document.getElementById("compare").disabled = picked.size !== 2;
    } } }),
    h("a", { href: `#/history/${b.id}` },
      h("span", { class: "title" }, b.recipe_name || t("brew.at_machine")),
      h("span", { class: "sub num" }, [when(b.started_at), `${b.water_ml === null ? "-" : Math.round(b.water_ml)} ml`, endingText(b.reason)].join("  ")))));
  show(view, h("h1", {}, t("history.title")),
    brews.length ? h("ul", { class: "list" }, items) : h("p", { class: "muted" }, t("history.empty")),
    brews.length > 1 ? h("div", { class: "actions" }, h("button", { id: "compare", class: "quiet", disabled: picked.size !== 2,
      on: { click: () => { location.hash = `#/compare/${[...picked].join("/")}`; } } }, t("action.compare"))) : null);
}

const pourStarts = (record) => record.events.filter((e) => e[1].cmd === 40510).map((e) => e[0]);
const recordName = (record) => (record.recipe && record.recipe.name) || t("brew.at_machine");

async function viewBrew(view, id) {
  show(view, h("p", { class: "muted" }, t("loading")));
  let record;
  try { record = await api("GET", `/api/brews/${encodeURIComponent(id)}`); }
  catch (error) { show(view, h("p", { class: "notice bad" }, error.message)); return; }
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
  show(view,
    h("p", {}, h("a", { href: "#/history" }, t("history.all"))),
    h("h1", {}, recordName(record)),
    h("dl", { class: "facts" },
      h("dt", {}, t("record.time")), h("dd", { class: "num" }, when(record.started_at)),
      h("dt", {}, t("record.result")), h("dd", {}, endingText(record.reason)),
      h("dt", {}, t("record.duration")), h("dd", { class: "num" }, record.duration_s === null ? "-" : clock(record.duration_s)),
      h("dt", {}, t("record.water")), h("dd", { class: "num" }, water.length ? `${Math.round(water[water.length - 1][1])} ml` : "-"),
      h("dt", {}, t("record.coffee")), h("dd", { class: "num" }, weight.length ? `${Math.round(weight[weight.length - 1][1])} g` : "-"),
      h("dt", {}, t("record.started")), h("dd", {}, record.hub_started ? t("record.started_here") : t("record.started_machine"))),
    chart(series, starts, legend),
    rows.length ? h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, t("table.pour")), h("th", {}, t("table.planned")),
      h("th", {}, t("table.actual")), h("th", {}, t("table.diff")))), h("tbody", {}, rows)) : null,
    rows.length ? h("p", { class: "muted" }, t("table.note")) : null);
}

async function viewCompare(view, ids) {
  show(view, h("p", { class: "muted" }, t("loading")));
  let records;
  try { records = await Promise.all(ids.slice(0, 2).map((id) => api("GET", `/api/brews/${encodeURIComponent(id)}`))); }
  catch (error) { show(view, h("p", { class: "notice bad" }, error.message)); return; }
  if (records.length < 2) { location.hash = "#/history"; return; }
  const shifted = (record, key) => { const s = pourStarts(record)[0] || 0; return record.samples[key].map((p) => [p[0] - s, p[1]]).filter((p) => p[0] >= 0); };
  const [a, b] = records;
  const name = (record) => t("compare.name", { name: recordName(record), when: when(record.started_at) });
  show(view,
    h("p", {}, h("a", { href: "#/history" }, t("history.all"))), h("h1", {}, t("compare.title")),
    h("dl", { class: "facts long" }, h("dt", {}, t("compare.solid")), h("dd", {}, name(a)), h("dt", {}, t("compare.dashed")), h("dd", {}, name(b))),
    chart([{ cls: "water", points: shifted(a, "water") }, { cls: "coffee", points: shifted(a, "weight") },
      { cls: "water other", points: shifted(b, "water") }, { cls: "coffee other", points: shifted(b, "weight") }], [], LEGEND()),
    h("p", { class: "muted" }, t("compare.note")));
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
    h("dl", { class: "facts" },
      h("dt", {}, t("console.link")), h("dd", {}, stateName(state.state)),
      state.error && [h("dt", {}, t("console.reason")), h("dd", {}, reasonText(state.error))],
      h("dt", {}, t("console.firmware")), h("dd", { class: "num" }, state.fw_version || "-"),
      h("dt", {}, t("console.activity")), h("dd", { class: "num" }, state.activity === null ? "-" : state.activity),
      h("dt", {}, t("tank")), h("dd", {}, tankText()),
      cupLine(),
      h("dt", {}, t("console.token")), h("dd", {}, token() ? t("console.token_yes") : t("console.token_no")),
      h("dt", {}, h("label", { for: "language" }, t("console.language"))),
      h("dd", {}, h("select", { id: "language", on: { change: (event) => { setLanguage(event.target.value); applyLanguage(); } } },
        Object.entries(LANGUAGE_NAMES).map(([code, name]) => h("option", { value: code, selected: code === LANGUAGE }, name))))),
    h("div", { class: "stack" }, h("h2", {}, t("console.share")), h("p", { class: "muted" }, t("console.share_hint")),
      h("div", { class: "actions" }, released
        ? h("button", { disabled: busy, on: { click: () => act(() => api("POST", "/api/link/acquire")) } }, t("action.acquire"))
        : h("button", { class: "quiet", disabled: busy || state.state === "brewing", on: { click: () => act(() => api("POST", "/api/link/release")) } }, t("action.release")))),
    h("div", { class: "stack" }, h("h2", {}, t("console.changes")), h("pre", { class: "log" }, changes.join("\n") || t("console.no_changes"))),
    h("div", { class: "stack" }, h("h2", {}, t("console.commands")), h("pre", { class: "log" }, commands.join("\n") || t("console.no_commands"))),
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
        s.cup, s.water_enough, recipeList ? recipeList.length : -1]
      : [s.state, s.error, s.activity, s.cup, s.water_enough, s.fw_version, log.length, gateRecent.length, token() ? 1 : 0];
    const key = [r.name, busy, flash ? flash.text : "", LANGUAGE, ...shape].join("|");
    if (key === last) {
      if (r.name === "home" && s.state === "brewing") updateBrewing();
      return;
    }
    last = key;
    show(view, flashNode(), r.name === "home" ? viewHome() : viewConsole());
  } else {
    // These fetch; redraw them when the address changes, not on every snapshot.
    const key = [location.hash, busy, flash ? flash.text : "", state ? state.state : "", LANGUAGE].join("|");
    if (key === last) return;
    last = key;
    const done = r.name === "recipes" ? viewRecipes(view, r.id && decodeURIComponent(r.id))
      : r.name === "history" ? viewHistory(view, r.id) : viewCompare(view, r.ids);
    done.catch((error) => show(view, h("p", { class: "notice bad" }, error.message)));
  }
}

window.addEventListener("hashchange", () => { flash = null; last = ""; if (route().name === "console") refreshGate(); render(); document.getElementById("view").focus(); });
setInterval(() => { if (route().name === "console") refreshGate(); }, 3000);
applyLanguage();
connect();
if (route().name === "console") refreshGate();
