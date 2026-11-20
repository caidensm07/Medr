// Medr frontend - plain JS, hash routes.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const app = $("#app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = t => { const s = Date.now() / 1000 - t; if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago"; return Math.floor(s / 86400) + "d ago"; };
let ME = { hosted: false };
let READONLY = false;            // admin looking at someone else's project      // hosted mode: { hosted, user, usage }
const fmt = n => n == null ? "–" : Number(n).toLocaleString();

function toast(msg, err) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (err ? " err" : "");
  clearTimeout(t._h); t._h = setTimeout(() => t.className = "", 3600);
}
async function api(path, { method, json } = {}) {
  const opt = { method: method || (json ? "POST" : "GET"), headers: { "X-Medr": "1" } };
  if (READONLY && opt.method !== "GET" && !/^\/api\/(settings|feedback|auth)/.test(path)) throw new Error("View only. This is someone else's project.");
  if (json) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(json); }
  const r = await fetch(path, opt);
  let data = null; try { data = await r.json(); } catch (e) {}
  if (r.status === 401 && ME.hosted && ME.user) { ME.user = null; authView("in"); throw new Error("Please sign in again."); }
  if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || `Error ${r.status}`);
  return data;
}
async function busy(btn, label, fn) {
  const old = btn.innerHTML; btn.disabled = true; btn.innerHTML = `<span class="spin"></span> ${label}`;
  try { return await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.innerHTML = old; }
}

// markdown with clickable citations
function md(text) {
  let html = window.marked ? marked.parse(text || "") : esc(text).replace(/\n/g, "<br>");
  html = html.replace(/\[PMID:?\s*(\d+)\]/g, '<a class="cite" target="_blank" href="https://pubmed.ncbi.nlm.nih.gov/$1/" title="Open on PubMed">PMID&nbsp;$1</a>')
    .replace(/\[(NCT\d{8})\]/g, '<a class="cite ct" target="_blank" href="https://clinicaltrials.gov/study/$1">$1</a>')
    .replace(/\[PPR:?\s*([A-Z]*\d+)\]/g, '<a class="cite pp" target="_blank" href="https://europepmc.org/article/PPR/$1">preprint</a>')
    .replace(/<p><strong>In short:?\s*<\/strong>:?\s*(\w?)/g, (_, c) => '<p class="inshort"><strong>In short</strong>' + c.toUpperCase());   // plain-language lead, styled
  return window.DOMPurify ? DOMPurify.sanitize(html, { ADD_ATTR: ["target"] }) : html;
}
function sourcesBlock(list) {
  if (!list?.length) return "";
  return `<details class="sources"><summary>${list.length} sources used</summary><ul>${list.map(s =>
    `<li><span class="id">${esc(s.ref)}</span> <a href="${esc(s.url)}" target="_blank">${esc(s.title)}</a></li>`).join("")}</ul></details>`;
}
function spark(counts, w = 200, h = 44, hotFrom = 2) {
  if (!counts?.length) return "";
  const vals = counts.map(c => c.count), max = Math.max(1, ...vals), n = vals.length, bw = w / n;
  return `<svg width="${w}" height="${h + 14}" viewBox="0 0 ${w} ${h + 14}" role="img" aria-label="Papers per year">
    ${vals.map((v, i) => { const bh = Math.max(2, v / max * h), hot = i >= n - 1 - hotFrom && i < n - 1;
      return `<rect x="${i * bw + 2}" y="${h - bh}" width="${bw - 4}" height="${bh}" rx="2" style="fill:var(${i === n - 1 ? "--line2" : hot ? "--eosin" : "--hema"})"><title>${counts[i].year}: ${v}</title></rect>`; }).join("")}
    <text x="0" y="${h + 12}" font-size="9" style="fill:var(--faint)">${counts[0].year}</text>
    <text x="${w}" y="${h + 12}" font-size="9" style="fill:var(--faint)" text-anchor="end">${counts[n - 1].year} so far</text></svg>`;
}
// ---------------- themes ----------------
let THEMES = null, THEME = "auto";
function autoTheme(d = new Date()) {
  // the Bam colors most of the year; a seasonal palette only right around a holiday
  const m = d.getMonth() + 1, day = d.getDate();
  if (m === 10 && day >= 24) return "halloween";
  if (m === 11 && day >= 20) return "autumn";
  if ((m === 12 && day === 31) || (m === 1 && day <= 2)) return "newyear";
  if (m === 12 && day >= 15) return "christmas";
  if (m === 2 && day >= 10 && day <= 14) return "valentines";
  if (m === 3 && day >= 14 && day <= 17) return "stpatricks";
  return "bam";
}
const resolveTheme = n => n === "auto" ? autoTheme() : (THEMES?.themes[n] ? n : "bam");
const themeVals = n => ({ ...THEMES.themes.bam, ...THEMES.themes[resolveTheme(n)] });
const kebab = k => k.replace(/[A-Z]/g, c => "-" + c.toLowerCase());
const SKIP = new Set(["name", "note", "deco", "dark", "snow", "snowColor", "stripe"]);
function applyTheme(name) {
  THEME = name || "auto";
  try { localStorage.setItem("medr_theme", THEME); } catch (e) {}
  if (!THEMES) return;
  const t = themeVals(THEME), root = document.documentElement;
  for (const [k, v] of Object.entries(t)) if (!SKIP.has(k)) root.style.setProperty("--" + kebab(k), v);
  root.toggleAttribute("data-dark", !!t.dark);
  root.style.setProperty("--stripe", t.stripe ? `repeating-linear-gradient(-45deg,${t.stripe[0]} 0 9px,${t.stripe[1]} 9px 18px)` : "none");
  root.style.setProperty("--stripe-h", t.stripe ? "4px" : "0px");
  stopSnow();
  
}
let snow = null;
function startSnow(color) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  if (!snow) {
    const c = Object.assign(document.createElement("canvas"), { id: "snow" }); c.setAttribute("aria-hidden", "true");
    document.body.prepend(c);
    snow = { c, ctx: c.getContext("2d"), flakes: [], raf: 0 };
    const size = () => { c.width = innerWidth * devicePixelRatio; c.height = innerHeight * devicePixelRatio; };
    size(); addEventListener("resize", size);
    for (let i = 0; i < 70; i++) snow.flakes.push({ x: Math.random(), y: Math.random(), r: .8 + Math.random() * 2.6, v: .25 + Math.random() * .7, p: Math.random() * 6.28, w: .3 + Math.random() * .7 });
  }
  snow.color = color; cancelAnimationFrame(snow.raf);
  const tick = () => {
    const { c, ctx } = snow, W = c.width, H = c.height, k = devicePixelRatio;
    ctx.clearRect(0, 0, W, H); ctx.fillStyle = snow.color;
    for (const f of snow.flakes) {
      f.y += f.v * k / H * 1.6; f.p += .012; if (f.y > 1.02) { f.y = -.02; f.x = Math.random(); }
      ctx.beginPath(); ctx.arc((f.x + Math.sin(f.p) * .012 * f.w) * W, f.y * H, f.r * k, 0, 6.283); ctx.fill();
    }
    snow.raf = requestAnimationFrame(tick);
  };
  snow.c.style.display = ""; tick();
}
function stopSnow() { if (snow) { cancelAnimationFrame(snow.raf); snow.c.style.display = "none"; } }
async function loadThemes() {
  try { THEMES = await (await fetch("/static/themes.json")).json(); } catch (e) { return; }
  let t = "auto"; try { t = localStorage.getItem("medr_theme") || "auto"; } catch (e) {}
  applyTheme(t);
  api("/api/settings").then(s => { if (s.theme && s.theme !== THEME) applyTheme(s.theme); }).catch(() => {});
}
function swatch(key, label, sub) {
  const t = themeVals(key);
  return `<button class="sw ${THEME === key ? "on" : ""}" data-theme="${key}" aria-label="${esc(label)} theme">
    <div class="pv" style="background:${t.glass};background-image:${t.pattern.replaceAll("var(--line)", t.line)};background-size:${t.patternSize}">
      <b style="background:${t.bar}"></b><s style="background:${t.panel};border:1px solid ${t.line}"></s><u style="background:${t.hema}"></u><i style="background:${t.eosin}"></i></div>
    <div class="nm">${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ""}</div></button>`;
}
function themePicker() {
  if (!THEMES) return toast("Themes didn't load. Refresh the page.", true);
  const m = document.createElement("div"); m.className = "modal";
  const draw = () => {
    m.innerHTML = `<div class="panel themes"><div class="row"><h2 class="grow" style="margin:0">Pick a theme</h2><button class="btn ghost small" data-close>Done</button></div>
      <p class="hint" style="margin:6px 0 0">Your pick is saved. Auto uses the Medr colors, switching to a seasonal palette for a few days around holidays.</p>
      <h3>Automatic</h3><div class="swatches">${swatch("auto", "Auto", `Right now: ${THEMES.themes[autoTheme()].name}`)}</div>
      <h3>Everyday</h3><div class="swatches">${THEMES.order.everyday.map(k => swatch(k, THEMES.themes[k].name, THEMES.themes[k].note)).join("")}</div>
      <h3>Seasonal</h3><div class="swatches">${THEMES.order.holidays.map(k => swatch(k, THEMES.themes[k].name, THEMES.themes[k].note)).join("")}</div></div>`;
    $$(".sw", m).forEach(b => b.onclick = () => { applyTheme(b.dataset.theme); draw();
      api("/api/settings/theme", { method: "PUT", json: { theme: THEME } }).catch(e => toast(e.message, true)); });
  };
  draw();
  m.onclick = e => { if (e.target === m || e.target.closest("[data-close]")) m.remove(); };
  document.body.append(m);
}

const logo = `<svg class="mark" width="26" height="26" viewBox="0 0 64 64" aria-hidden="true">
  <path d="M10 50 V28 M10 34 a10 10 0 0 1 20 0 V50 M30 34 a10 10 0 0 1 20 0 V50" fill="none" stroke-width="6.5" stroke-linecap="round" stroke-linejoin="round" style="stroke:var(--bar-text)"/>
  <circle cx="20" cy="36" r="5" style="fill:var(--eosin)"/></svg>`;
// line icons for the tools
const IC = {
  feedback: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/></svg>`,
  admin: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></svg>`,
  measures: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V3h6v1"/><path d="M9 10h6M9 14h6M9 18h3"/></svg>`,
  news: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h13v14H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v8a2 2 0 0 1-2 2"/><path d="M8 9h5M8 13h5M8 16h3"/></svg>`,
  alerts: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>`,
  overview: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>`,
  board: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linejoin="round"><rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="10" y="4" width="5" height="11" rx="1.5"/><rect x="17" y="4" width="4" height="7" rx="1.5"/></svg>`,
  notes: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/></svg>`,
  trials: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M9 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L15 9V3"/><path d="M8 3h8"/><circle cx="10" cy="16" r="1"/><circle cx="14" cy="14" r="1"/></svg>`,
  activity: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-8 4 16 3-8h4"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>`,
  folder: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linejoin="round"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2.5h8.5A1.5 1.5 0 0 1 21 9v9.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"/></svg>`,
  chevron: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10l5 5 5-5"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>`,
  plans: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2l2.5 6.5L21 9l-5 4.5L17.5 21 12 17l-5.5 4L8 13.5 3 9l6.5-.5z"/></svg>`,
  ideas: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"/><path d="M7.5 15h9"/></svg>`,
  radar: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-6 4 3 5-8 4 5"/><path d="M3 21h18"/></svg>`,
  bridge: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="5" cy="12" r="3"/><circle cx="19" cy="12" r="3"/><circle cx="12" cy="5" r="2.5"/><path d="M7.5 10.5L10 7M14 7l2.5 3.5M8 12h8" stroke-dasharray="2 3"/></svg>`,
  gaps: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-4 10.5c.7.7 1 1.5 1 2.5h6c0-1 .3-1.8 1-2.5A6 6 0 0 0 12 3z"/></svg>`,
  brief: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M6 3h9l3 3v15H6z"/><path d="M9 11h6M9 15h6M9 7h3"/></svg>`,
  search: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>`,
  watch: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round"><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>`,
  ask: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 0 1-11.5 7.2L4 20l1-4.5A8 8 0 1 1 21 12z"/></svg>`,
  saved: `<svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linejoin="round"><path d="M6 3h12v18l-6-4-6 4z"/></svg>`,
};

// ---------------- app state ----------------
let PROJECTS = [], ACTIVE = null;
async function loadProjects() { PROJECTS = await api("/api/projects"); return PROJECTS; }
function rememberProject(pid) {
  if (ACTIVE === pid) return;
  ACTIVE = pid;
  try { localStorage.setItem("medr_project", pid); } catch (e) {}
  api("/api/settings/active_project", { method: "PUT", json: { value: String(pid) } }).catch(() => {});
}
async function startProject() {
  const s = await api("/api/settings").catch(() => ({}));
  let saved = +s.active_project || 0;
  try { saved = saved || +localStorage.getItem("medr_project") || 0; } catch (e) {}
  return PROJECTS.some(p => p.id === saved) ? saved : PROJECTS[0]?.id;
}

// Six plain sections; related pages live inside each one as tabs.
const SECTIONS = [
  ["home", "Home", "overview", [["overview", "Overview"], ["activity", "Activity"]]],
  ["ideas", "Ideas", "ideas", [["ideas", "Idea Lab"], ["board", "Pipeline"], ["explore", "Explore"]]],
  ["plans", "Plans", "plans", [["plans", "Plans"]]],
  ["research", "Research", "search", [["search", "Search"], ["saved", "Library"], ["trials", "Trials"], ["ask", "Ask"]]],
  ["news", "News", "news", [["news", "Top news"], ["alerts", "Alerts"], ["watch", "Tracked topics"]]],
  ["notes", "Notes", "notes", [["notes", "Notes"]]],
];
const EXPLORE = { measures: "Assessment Finder", radar: "Trends", bridge: "Connections", brief: "Evidence brief", gaps: "Opportunities" };
const sectionOf = tab => SECTIONS.find(s => s[3].some(t => t[0] === tab)) || (EXPLORE[tab] ? SECTIONS[1] : null);
const TITLE = { ...Object.fromEntries(SECTIONS.flatMap(s => s[3])), ...EXPLORE, settings: "Project settings" };
const KIND_LABEL = { measures: "Assessment Finder", news: "News", plan: "Research plan", ideas: "Idea Lab", radar: "Trends", bridge: "Connections", gaps: "Opportunities",
  brief: "Evidence brief", digest: "Monitor", note: "Note", idea: "Idea in progress", paper: "Paper", preprint: "Preprint", trial: "Trial" };

function planTeaser(mdText) {
  const m = (mdText || "").split(/\n## /).find(sec => /^Recommendation/i.test(sec));
  return (m || mdText || "").replace(/^Recommendation[^\n]*\n/i, "").replace(/[#*\[\]`]/g, "").replace(/\s+/g, " ").trim().slice(0, 260);
}
function navCounts(p) {
  const st = p.stats || {};
  return { ideas: st.idea || undefined, plans: st.plan || undefined, notes: st.note || undefined, news: ALERTS?.unread || undefined };
}
function sidebar(pid, tab, p) {
  const counts = navCounts(p), deco = THEMES ? themeVals(THEME).deco || "" : "";
  return `<aside class="sidebar">
    <a class="brand" href="#/">${logo}<b>Medr</b></a>
    <div class="switch">
      <button class="psw" id="sw" aria-haspopup="true" aria-expanded="false"><span class="sw-ic">${IC.folder}</span>
        <span class="sw-txt"><small>Project</small><b>${esc(p.name)}</b></span><span class="sw-chev">${IC.chevron}</span></button>
      <div class="sw-menu" id="swm" hidden>${PROJECTS.map(x => `<button class="sw-item ${x.id === pid ? "on" : ""}" data-pid="${x.id}">
          <span>${esc(x.name)}</span>${x.id === pid ? IC.check : `<small>${x.items}</small>`}</button>`).join("")}
        <div class="sw-sep"></div><button class="sw-item" id="sw-new">+ New project</button><a class="sw-item" href="#/">All projects</a></div>
    </div>
    <nav class="nav">${SECTIONS.map(([k, label, first, tabs]) => { const on = sectionOf(tab)?.[0] === k;
      return `<a class="nav-i ${on ? "on" : ""}" href="#/p/${pid}/${first}" data-nav="${k}">${IC[{ home: "overview", research: "search" }[k] || k]}<span>${label}</span>${counts[k] ? `<i class="${k === "news" ? "hot" : ""}">${counts[k]}</i>` : ""}</a>`; }).join("")}
    </nav>
    <div class="side-foot"><a class="nav-i ${tab === "settings" ? "on" : ""}" href="#/p/${pid}/settings">${IC.settings}<span>Settings</span></a>
      <button class="nav-i" id="themeb"><i class="th-dot" aria-hidden="true"></i><span>Theme</span></button>
      ${ME.hosted ? `<button class="nav-i" id="fbb">${IC.feedback}<span>Send feedback</span></button>` : ""}
      ${ME.user?.admin ? `<a class="nav-i" href="#/admin">${IC.admin}<span>Admin</span></a>` : ""}
      ${ME.hosted ? hostedFoot() : ""}
      <div class="by">Medr by Caiden</div></div>
  </aside>`;
}
async function newProjectFlow() {
  const name = prompt("Name your project (e.g. AI × Alzheimer's):"); if (!name) return null;
  const goal = prompt("What are you hoping to find or build? (optional)") || "";
  const r = await api("/api/projects", { json: { name, goal } });
  await loadProjects(); return r.id;
}
function bindShell(pid, tab) {
  const sw = $("#sw"), menu = $("#swm");
  sw.onclick = e => { e.stopPropagation(); menu.hidden = !menu.hidden; sw.setAttribute("aria-expanded", String(!menu.hidden)); };
  document.onclick = e => { if (!menu.hidden && !e.target.closest(".switch")) { menu.hidden = true; sw.setAttribute("aria-expanded", "false"); } };
  $$("[data-pid]", menu).forEach(b => b.onclick = () => {
    const to = +b.dataset.pid; menu.hidden = true;
    if (to !== pid) { rememberProject(to); location.hash = `#/p/${to}/${tab}`; toast(`Switched to ${PROJECTS.find(x => x.id === to)?.name}`); }
  });
  $("#sw-new").onclick = async () => { const id = await newProjectFlow(); if (id) { rememberProject(id); location.hash = `#/p/${id}/overview`; } };
  $("#themeb").onclick = themePicker;
  const fb = $("#fbb"); if (fb) fb.onclick = feedbackDialog;
}

// ---------------- router ----------------
async function route() {
  if (snow && !THEMES) stopSnow();
  const [path, qs] = (location.hash.slice(1) || "/").split("?");
  const parts = path.split("/").filter(Boolean), params = new URLSearchParams(qs || "");
  document.onclick = null; READONLY = false;
  try {
    if (ME.hosted) { ME = await api("/api/me"); if (!ME.user) return authView("in"); }
    if (parts[0] === "admin" && ME.user?.admin) return await adminView();
    await loadProjects();
    if (parts[0] === "p" && (PROJECTS.some(x => x.id === +parts[1]) || ME.user?.admin)) return await projectView(+parts[1], parts[2] || "overview", params);
    return projectsView(await startProject().catch(() => null));     // the app opens here
  } catch (e) { toast(e.message, true); }
}
window.addEventListener("hashchange", route);

// ---------------- all projects ----------------
function projectsView(lastPid) {
  const first = !PROJECTS.length;
  const sorted = [...PROJECTS].sort((x, y) => (y.id === lastPid) - (x.id === lastPid));
  app.innerHTML = `<div class="plain"><header class="plainbar"><a class="brand" href="#/">${logo}<b>Medr</b></a><div class="grow"></div>
      ${ME.hosted ? `<button class="btn link small" id="fbb2">Send feedback</button>${ME.user?.admin ? `<a class="btn link small" href="#/admin">Admin</a>` : ""}<button class="btn link small" id="out2">Sign out</button>` : ""}
      <button class="btn ghost small" id="themeb2">Theme</button></header>
    <div class="wrap home-wrap">
      <section class="intro">
        <h1>The research behind better care, in minutes.</h1>
        <p class="lede">Medr is a research aid for clinicians and care teams. Describe a patient group or a question, and it finds the validated assessments, the latest evidence and new approaches, in plain language with every source linked. It works alongside the tools your team already uses; it doesn't replace them.</p>
        <ul class="trust"><li>Public research only: PubMed, ClinicalTrials.gov, preprints</li><li>Never uses patient data</li><li>Every result graded and sourced</li></ul>
        <ol class="how3">
          <li><b>Create a project</b><span>One per patient group or challenge you care about, e.g. "teens with type 1 diabetes and depression". Everything you find is saved there.</span></li>
          <li><b>Pick a task</b><span>Choose assessments for the group, see what the research says, or put forward your own idea and develop it into a plan.</span></li>
          <li><b>Use the results</b><span>Each answer is graded A to D on evidence, cites its sources, and can be turned into a step-by-step plan.</span></li>
        </ol>
      </section>
      <h2 class="ph2">${first ? "Create your first project" : "Choose a project"}</h2>
      <div class="pgrid">${sorted.map(p => `<a class="pcard ${p.id === lastPid ? "last" : ""}" href="#/p/${p.id}/overview">
          ${p.id === lastPid ? `<span class="pc-tag">Last opened</span>` : ""}<div class="nm">${esc(p.name)}</div>
          ${p.goal ? `<div class="goal">${esc(p.goal)}</div>` : `<div class="goal muted">No goal set yet</div>`}
          <div class="meta"><span><b>${p.items}</b> saved items</span><span>Started ${ago(p.created)}</span></div>
          <span class="open">Open project</span></a>`).join("")}
        <form class="pcard new" id="np"><div class="nm">New project</div>
          <label class="small" for="npn">What do you want to work on?</label>
          <input id="npn" type="text" name="name" required placeholder="A patient group or question" aria-label="Project name">
          <div class="ex-row">${["Teens with T1D and depression", "Collaborative Care for depression", "Adults with anxiety"].map(e => `<button type="button" class="ex" data-ex="${e}">${e}</button>`).join("")}</div>
          <textarea name="goal" placeholder="Your goal (optional), e.g. find better ways to support kids' mental health" aria-label="Goal"></textarea>
          <button class="btn">Create project</button></form></div>
      <p class="restore">Have a backup file? <label class="btn link small"><input type="file" id="rf" accept=".json" hidden>Restore a project</label></p>
    </div></div>`;
  $("#themeb2").onclick = themePicker;
  if (ME.hosted) { $("#fbb2").onclick = feedbackDialog; $("#out2").onclick = signOut; }
  $$("#np .ex").forEach(b => b.onclick = () => { $("#npn").value = b.dataset.ex; $("#npn").focus(); });
  $("#np").onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    busy($("#np .btn"), "Creating…", async () => { const r = await api("/api/projects", { json: f }); rememberProject(r.id); location.hash = `#/p/${r.id}/overview`; }); };
  $("#rf").onchange = async e => {
    const file = e.target.files[0]; if (!file) return;
    try { const r = await api("/api/projects/restore", { json: JSON.parse(await file.text()) }); toast("Project restored"); location.hash = `#/p/${r.id}/overview`; }
    catch (err) { toast(err.message.includes("JSON") ? "That file isn't a Medr backup" : err.message, true); }
  };
}

// ---------------- project shell ----------------
async function projectView(pid, tab, params = new URLSearchParams()) {
  const [p] = await Promise.all([api(`/api/projects/${pid}`), refreshAlerts()]);
  READONLY = !!p.read_only;
  if (!READONLY) rememberProject(pid);
  app.innerHTML = `<div class="shell">${sidebar(pid, tab, p)}
    <main class="main"><header class="pagehead"><div class="grow"><div class="crumb">${esc(p.name)}</div><h1>${tab === "settings" ? "Project settings" : sectionOf(tab)?.[1] || "Home"}</h1></div>
      <div class="ph-actions" id="pha"></div></header>
      ${READONLY ? `<div class="ro-bar"><span>Viewing <b>${esc(p.owner_email)}</b>'s project. View only, nothing you click changes it.</span><a class="btn link small" href="#/admin">Back to Admin</a></div>` : ""}${subtabs(pid, tab)}<div id="pane"></div></main></div>`;
  bindShell(pid, tab);
  const views = { overview: overviewTab, board: boardTab, notes: notesTab, activity: activityTab, settings: settingsTab, trials: trialsTab,
    ideas: ideasTab, plans: plansTab, news: newsTab, alerts: alertsTab, explore: exploreTab, measures: measuresTab, radar: radarTab, bridge: bridgeTab, gaps: gapsTab, brief: briefTab, search: searchTab, watch: watchTab, ask: askTab, saved: savedTab };
  let run = null; try { run = params.get("run") ? JSON.parse(params.get("run")) : null; } catch (e) {}
  if (run) history.replaceState(null, "", `#/p/${pid}/${tab}`);     // don't re-run on refresh
  (views[tab] || overviewTab)($("#pane"), pid, p, run);
}

function subtabs(pid, tab) {
  const sec = sectionOf(tab); if (!sec || sec[3].length < 2) return "";
  const cur = EXPLORE[tab] ? "explore" : tab;
  return `<nav class="subtabs" aria-label="${esc(sec[1])}">${sec[3].map(([k, l]) => `<a href="#/p/${pid}/${k}" class="${k === cur ? "on" : ""}">${l}${k === "alerts" && ALERTS?.unread ? ` <i>${ALERTS.unread}</i>` : ""}</a>`).join("")}</nav>
    ${EXPLORE[tab] ? `<a class="back-link" href="#/p/${pid}/explore">← All explore tools</a>` : ""}`;
}
function exploreTab(pane, pid) {
  const tools = [["measures", "Assessment Finder", "Validated questionnaires and rating scales for a patient group, graded on evidence, with cutoffs and gaps."],
    ["radar", "Trends", "What's taking off in a field right now, and what's quietly rising."],
    ["bridge", "Connections", "Hidden links between two topics that researchers haven't connected yet."],
    ["brief", "Evidence brief", "Where the evidence stands on a question, in plain words."],
    ["gaps", "Opportunities", "Open questions in a topic, and where AI could help answer them."]];
  pane.innerHTML = `<p class="sub" style="margin-top:0">Other ways to look at a topic. Each one saves its results to this project.</p>
    <div class="explore-grid">${tools.map(([k, t, d]) => `<a class="ecard" href="#/p/${pid}/${k}"><span class="eic">${IC[k]}</span><b>${t}</b><span>${d}</span></a>`).join("")}</div>`;
}

// a runner = input + button + previous runs of one kind
function runner(pane, { title, sub, how, examples, fields, button, wait, run, kind, items, render, legend, auto, mdFirst, printable, bindRun }) {
  pane.innerHTML = `<form class="runner"><h2>${title}</h2><p class="sub">${sub}</p>
    <div class="row">${fields}<button class="btn">${button}</button></div>
    <div class="formfoot">${examples ? `<span class="exlist"><span class="muted">Examples:</span> ${examples.map(e => `<button type="button" class="ex" data-ex='${esc(JSON.stringify(e))}'>${esc(e._label || Object.values(e).join(" × "))}</button>`).join("")}</span>` : ""}
      ${how ? `<details class="howit"><summary>How it works</summary><ol>${how.map(h => `<li>${h}</li>`).join("")}</ol></details>` : ""}</div>
    <div id="work"></div></form>
    <div id="runs"></div>`;
  $$(".ex", pane).forEach(b => b.onclick = () => { const v = JSON.parse(b.dataset.ex); delete v._label; for (const [k, x] of Object.entries(v)) { const el = $(`[name="${k}"]`, pane); if (el) el.value = x; } });
  const runs = $("#runs", pane);
  const draw = list => {
    runs.innerHTML = list.length ? list.map(i => `<article class="report" data-id="${i.id}">
      <header><h3>${esc(i.title)}</h3><span class="when">${ago(i.created)}</span>
      ${printable ? `<button class="btn ghost small" data-print="${i.id}">Print / save PDF</button>` : ""}
      <button class="btn link small" data-del="${i.id}" aria-label="Delete">Delete</button></header>
      <div class="body">${mdFirst ? `<div class="md">${md(i.data.md)}</div>` : ""}${render(i)}${legend ? `<div class="legend">${legend}</div>` : ""}${mdFirst ? "" : `<div class="md">${md(i.data.md)}</div>`}${sourcesBlock(i.data.sources)}</div></article>`).join("")
      : `<div class="empty">Nothing here yet. Run one above.</div>`;
    $$("[data-print]", runs).forEach(b => b.onclick = () => {
      const art = b.closest(".report"); art.classList.add("printing"); document.body.classList.add("printing");
      art.querySelectorAll("details").forEach(d => d.open = true);
      setTimeout(() => { print(); art.classList.remove("printing"); document.body.classList.remove("printing"); }, 50);
    });
    if (bindRun) bindRun(runs, list);
    $$("[data-del]", runs).forEach(b => b.onclick = async () => {
      if (!confirm("Delete this?")) return; await api(`/api/items/${b.dataset.del}`, { method: "DELETE" });
      list = list.filter(x => x.id != b.dataset.del); draw(list); });
  };
  let list = items.filter(i => i.kind === kind); draw(list);
  $("form", pane).onsubmit = e => {
    e.preventDefault();
    const btn = $("form .btn", pane), work = $("#work", pane);
    work.innerHTML = `<div class="working"><span class="pulse"></span>${wait}</div>`;
    busy(btn, "Working…", async () => {
      const item = await run(Object.fromEntries(new FormData(e.target)));
      list = [item, ...list]; draw(list);
      runs.firstElementChild?.scrollIntoView({ behavior: "smooth", block: "start" });
    }).finally(() => work.innerHTML = "");
  };
  if (auto) {               // started from the hub: fill in and go
    for (const [k, x] of Object.entries(auto)) { const el = $(`[name="${k}"]`, pane); if (el) el.value = x; }
    $("form", pane).requestSubmit();
  }
}

const VERDICT = { "Unexplored": "v-new", "Emerging": "v-em", "Being tested": "v-test", "Well studied": "v-old" };
function ring(score) {
  const r = 26, c = 2 * Math.PI * r, f = Math.max(0, Math.min(100, score)) / 100;
  return `<svg class="ring" width="68" height="68" viewBox="0 0 68 68" role="img" aria-label="Promise score ${score} out of 100">
    <circle cx="34" cy="34" r="${r}" fill="none" stroke-width="7" style="stroke:var(--wash)"/>
    <circle cx="34" cy="34" r="${r}" fill="none" stroke-width="7" stroke-linecap="round" style="stroke:var(${score >= 60 ? "--eosin" : "--hema"})"
      stroke-dasharray="${c * f} ${c}" transform="rotate(-90 34 34)"/>
    <text x="34" y="39" text-anchor="middle" font-size="17" font-weight="700" style="fill:var(--ink);font-family:var(--display)">${score}</text></svg>`;
}
const GRADE = { A: "Strong evidence", B: "Promising", C: "Plausible", D: "Original idea" };
function gradeBox(g, why) {
  if (!g) return "";
  return `<div class="grade g${g}" title="Confidence grade: A strong evidence, B promising, C plausible, D original (little research yet)">
    <span class="gm">${g}</span><span class="gl">${GRADE[g] || ""}</span>${why ? `<span class="gw">${esc(why)}</span>` : ""}</div>`;
}
function originTag(x) {
  if (!x.origin) return "";
  return `<span class="origin">${x.origin === "Borrowed from another field" ? `Borrowed from ${esc(x.borrowed_from || "another field")}` : esc(x.origin)}</span>`;
}
function potWhy(m) {
  if (!m || m.co == null) return "";
  if ((m.trials || 0) >= 3) return `already being tested in ${fmt(m.trials)} trials`;
  if ((m.co || 0) >= 50) return `already well studied (${fmt(m.co)} papers)`;
  if (Math.min(m.link_i || 0, m.link_c || 0) < 5) return "little evidence linking it to the cause yet";
  if ((m.co || 0) <= 3) return `backed by related research, and only ${fmt(m.co)} papers on it here`;
  return `${fmt(m.co)} papers on it here so far`;
}
function potBar(v, m) {
  if (m && m.unscored) return `<div class="pot"><div class="pot-l"><span>Breakthrough potential</span><b>Not scored yet</b></div>
    <div class="pot-bar"><i style="width:0"></i></div><small class="pot-why">No linked research found to score it. That makes it fully original: check the steps below.</small></div>`;
  const n = Math.max(0, Math.min(100, Math.round(v))), lvl = n >= 60 ? "High" : n >= 30 ? "Medium" : "Low";
  let why = potWhy(m);
  if (m?.loosened?.length) why += `${why ? ". " : ""}Counted without the detail "${m.loosened.join('", "')}" (nothing matched it exactly)`;
  return `<div class="pot" title="Breakthrough potential: how much new ground this could break. High = supported by related research but barely studied for this condition.">
    <div class="pot-l"><span>Breakthrough potential</span><b class="num">${n}<small>/100</small> · ${lvl}</b></div>
    <div class="pot-bar"><i style="width:${n}%" class="p${lvl[0]}"></i></div>${why ? `<small class="pot-why">${esc(why[0].toUpperCase() + why.slice(1))}</small>` : ""}</div>`;
}
function stepsList(arr) { return (arr || []).slice(0, 3).map(t => `<li>${md(t).replace(/^<p>|<\/p>\s*$/g, "")}</li>`).join(""); }
function ideaCard(x, cond, k) {
  const m = x.m || {};
  const list = (arr) => (arr || []).map(t => `<li>${md(t).replace(/^<p>|<\/p>\s*$/g, "")}</li>`).join("");
  const grade = x.grade || ({ High: "B", Medium: "C", Low: "D" })[x.confidence];
  return `<article class="idea" data-k="${k}">
    <header class="idea-h"><div class="grow"><div class="idea-meta">${x.yours ? `<span class="yours">Your idea, sharpened</span>` : ""}<span class="kind">${esc(x.type)}</span>${originTag(x)}</div>
      <h4>${esc(x.title)}</h4></div>
      </header>
    ${x.plain ? `<p class="plainlead">${esc(x.plain)}</p>` : ""}
    ${potBar(m.promise ?? 0, m)}
    ${gradeBox(grade, x.grade_reason)}
    <div class="approach"><h5>${x.plain ? "How it works" : "The approach"}</h5><div class="md">${md(x.rationale)}</div>
      ${x.target ? `<p class="path"><span>${esc(cond)}</span> → <span>${esc(x.target)}</span> → <b>${esc(x.intervention)}</b></p>` : ""}</div>
    ${x.test_plan?.length ? `<div class="pursue"><h5>How to pursue it</h5><ol>${stepsList(x.test_plan)}</ol></div>` : ""}
    ${x.proof ? `<p class="proof"><b>What would prove it:</b> ${esc(x.proof)}</p>` : ""}
    <details class="evid"><summary>Evidence and risks</summary>
      <div class="metrics"><div><b class="num">${fmt(m.co)}</b>papers on this idea<small class="num">${fmt(m.co_recent)} in the last 3 years</small></div>
        <div><b class="num">${fmt(m.link_i)}</b>papers linking the approach to the target</div>
        <div><b class="num">${fmt(m.trials)}</b>matching clinical trials</div></div>
      ${x.chain?.length ? `<h5>Evidence chain</h5><ol>${list(x.chain)}</ol>` : ""}
      ${x.risks?.length ? `<h5>Why it might not work</h5><ul>${list(x.risks)}</ul>` : ""}
      ${x.question ? `<p class="openq"><b>Key open question:</b> ${esc(x.question)}</p>` : ""}</details>
    <div class="idea-foot"><button class="btn small" data-solve="${k}">Build a solution</button>
      <button class="btn link small" data-short="${k}" title="Keep it in your Pipeline without starting a plan yet">Save for later</button>
      ${x.links?.idea ? `<a class="btn link small" href="${x.links.idea}" target="_blank">Papers ↗</a>` : ""}
      ${x.links?.trials ? `<a class="btn link small" href="${x.links.trials}" target="_blank">Trials ↗</a>` : ""}</div></article>`;
}
function ideasTab(pane, pid, p, auto) {
  runner(pane, {
    auto, mdFirst: true, printable: true, title: "Idea Lab",
    sub: "Put your idea forward: how you'd use something (a tool, a medicine, a program, AI) to solve a specific problem for a specific group. Medr checks it against the research, sharpens it, and suggests strong variations and alternatives. A plain topic works too; an idea gets you further.",
    how: ["<b>Your idea first.</b> Medr reads the research around your idea and the problem it targets.",
          "<b>Sharpen and expand.</b> It refines your idea, then proposes variations and alternatives from related fields.",
          "<b>Check and grade.</b> Every idea is checked against PubMed and ClinicalTrials.gov, then graded A to D."],
    examples: [{ condition: "Flag patients whose PHQ-9 scores aren't improving so clinicians adjust treatment sooner", angle: "ai", _label: "Flag patients not improving on PHQ-9" },
      { condition: "A peer-mentor app to lower diabetes distress in teens", angle: "any", _label: "Peer-mentor app for teens with diabetes" },
      { condition: "Use smartphone voice recordings to catch early Parkinson's", angle: "diagnostics", _label: "Voice recordings to catch early Parkinson's" },
      { condition: "Repurpose an existing anti-inflammatory drug to ease long COVID fatigue", angle: "repurpose", _label: "Existing drug for long COVID fatigue" }],
    fields: `<input class="grow" type="text" name="condition" required placeholder="Your idea: use [what] to [do what] for [whom], e.g. school-nurse check-ins to catch burnout in teens with asthma" aria-label="Your idea">
      <select name="angle" aria-label="Focus" style="width:auto;min-width:210px">
        <option value="any">Any kind of idea</option><option value="repurpose">New uses for approved medicines</option>
        <option value="combination">Combining existing treatments</option><option value="ai">AI and digital tools</option>
        <option value="diagnostics">Diagnostics and early detection</option><option value="prevention">Prevention and lifestyle</option></select>`,
    button: "Develop idea", wait: "Reading the research around your idea, sharpening it, and checking each version against real data. About a minute.",
    run: f => api(`/api/projects/${pid}/ideas`, { json: f }), kind: "ideas", items: p.items,
    render: i => {
      const d = i.data, st = d.stats || {};
      const hasPlan = p.items.some(x => x.kind === "plan" && x.data.run_id === i.id);
      return `<p class="basis">Based on ${fmt(st.papers)} papers, ${fmt(st.preprints)} preprints and ${fmt(st.trials)} trials${d.targets?.length ? `, around ${d.targets.length} targets: ${d.targets.map(t => esc(t.name)).join(", ")}` : ""}. ${fmt(st.checks)} checks against PubMed and ClinicalTrials.gov.</p>
        <div class="actionline"><span>Turn the strongest leads into a research plan with a first task.</span>
          ${hasPlan ? `<a class="btn ghost small" href="#/p/${pid}/plans">View plan</a>` : `<button class="btn small" data-plan="${i.id}">Draft research plan</button>`}</div>
        <details class="howit scores"><summary>How the scores work</summary><p><b>Confidence (A to D)</b>: how strongly the evidence suggests it would help. A strong evidence, B promising, C plausible, D original with little research yet.</p>
          <p><b>Breakthrough potential (0 to 100)</b>: how much new ground it would break. High means supported by related research but barely studied for this exact problem.</p></details>
        <div class="ideas">${(d.ideas || []).map((x, k) => ideaCard(x, d.condition, k)).join("")}</div>`;
    },
    bindRun: (runs, list) => { $$("[data-plan]", runs).forEach(b => b.onclick = () => busy(b, "Writing plan… about a minute", async () => {
        await api(`/api/projects/${pid}/plan`, { json: { run_id: +b.dataset.plan } });
        toast("Research plan ready"); location.hash = `#/p/${pid}/plans`; }));
      $$("[data-solve]", runs).forEach(b => b.onclick = () => {
        const item = list.find(it => it.id == b.closest(".report").dataset.id), x = item.data.ideas[+b.dataset.solve];
        buildSolution(b, pid, x, item.data.condition); });
      $$("[data-short]", runs).forEach(b => b.onclick = async () => {
      const item = list.find(it => it.id == b.closest(".report").dataset.id), x = item.data.ideas[+b.dataset.short];
      await api(`/api/projects/${pid}/shortlist`, { json: { idea: x, condition: item.data.condition } }).catch(e => toast(e.message, true));
      b.textContent = "Saved"; b.disabled = true; toast("Saved to your Pipeline (Ideas › Pipeline)");
    }); },
  });
}

function buildSolution(btn, pid, idea, condition) {
  return busy(btn, "Setting up your first step…", async () => {
    await api(`/api/projects/${pid}/solution`, { json: { idea, condition } });
    toast("Moved to Plans. Task 1 is ready."); location.hash = `#/p/${pid}/plans`;
  });
}
function planLeads(mdText) {
  const sec = ("\n" + (mdText || "")).split(/\n## /).find(x => /^The leads/i.test(x)) || "";
  const leads = [...sec.matchAll(/^###\s+(.+)$/gm)].map(m => m[1].replace(/[*_`]/g, "").replace(/^\d+[.)]\s*/, "").trim());
  if (/\n## One idea of my own/i.test("\n" + mdText)) leads.push("Medr's own idea (from the plan)");
  return leads.slice(0, 6);
}
function resLink(r, pid) {
  const q = encodeURIComponent(r.query || r.label || "");
  const href = r.kind === "pubmed" ? `https://pubmed.ncbi.nlm.nih.gov/?term=${q}` : r.kind === "trials" ? `https://clinicaltrials.gov/search?term=${q}`
    : r.kind === "medr" ? `#/p/${pid}/search?run=${encodeURIComponent(JSON.stringify({ q: r.query || r.label, mode: "papers" }))}` : `https://www.google.com/search?q=${q}`;
  const where = { pubmed: "PubMed", trials: "ClinicalTrials.gov", medr: "Medr search", web: "Web" }[r.kind] || "Web";
  return `<a class="res" href="${href}" ${r.kind === "medr" ? "" : `target="_blank" rel="noopener"`}><b>${esc(r.label || r.query)}</b><span>${where}${r.query && r.query !== r.label ? `: ${esc(r.query)}` : ""}</span></a>`;
}
function missionView(i, pid) {
  const m = i.data.mission, leads = planLeads(i.data.md);
  if (!m) return `<section class="mission start" data-id="${i.id}">
      <div class="m-h"><span class="m-ico">${IC.plans}</span><div><h4>Execute this plan</h4>
        <p class="muted">Select a lead to pursue. Medr issues one task at a time with method, resources and completion criteria, then sets the next task based on the findings you record.</p></div></div>
      <div class="leadpick"><button class="lead on" data-lead="">Recommended lead</button>${leads.map(l => `<button class="lead" data-lead="${esc(l)}">${esc(l)}</button>`).join("")}</div>
      <div class="row"><input class="grow" type="text" data-own placeholder="Or specify a different direction (optional)" aria-label="Direction">
        <button class="btn" data-start="${i.id}">Begin</button></div></section>`;
  const steps = m.steps || [], cur = steps[steps.length - 1], done = steps.filter(s => s.status === "done");
  const road = m.roadmap || [], curIdx = Math.max(0, road.findIndex(r => r && cur?.milestone && (r.toLowerCase() === cur.milestone.toLowerCase())));
  const list = a => (a || []).map(t => `<li>${md(t).replace(/^<p>|<\/p>\s*$/g, "")}</li>`).join("");
  return `<section class="mission" data-id="${i.id}">
    <div class="m-h"><span class="m-ico">${IC.plans}</span><div class="grow"><small>Objective · ${esc(m.lead)}</small><h4>${esc(m.mission)}</h4></div>
      <div class="m-count"><b class="num">${done.length}</b><span>tasks completed</span></div></div>
    ${road.length ? `<ol class="road">${road.map((r, k) => `<li class="${k < curIdx ? "past" : k === curIdx ? "now" : ""}"><i></i><span>${esc(r)}</span></li>`).join("")}</ol>` : ""}
    ${m.feedback ? `<div class="coach"><b>Advisor review</b><div class="md">${md(m.feedback)}</div></div>` : ""}
    ${m.finished ? `<div class="coach done"><b>Objective reached</b><p>This lead has produced a concrete result. Open a new objective on another lead, or continue with the task below.</p>
      <button class="btn ghost small" data-reset="${i.id}">New objective</button></div>` : ""}
    ${cur ? `<article class="step">
      <div class="step-h"><span class="step-n">Task ${steps.filter(s => s.status !== "skipped").length}</span>${cur.time ? `<span>${esc(cur.time)}</span>` : ""}${cur.milestone ? `<span>${esc(cur.milestone)}</span>` : ""}</div>
      <h4>${esc(cur.title)}</h4>
      ${cur.why ? `<p class="why">${md(cur.why).replace(/^<p>|<\/p>\s*$/g, "")}</p>` : ""}
      <div class="step-b"><div><h5>Method</h5><ol class="how-l">${list(cur.how)}</ol></div>
        <div class="side-b">${cur.done_when ? `<div class="donewhen"><h5>Completion criteria</h5><p>${esc(cur.done_when)}</p></div>` : ""}
        ${cur.resources?.length ? `<div><h5>Resources</h5><div class="reslist">${cur.resources.map(r => resLink(r, pid)).join("")}</div></div>` : ""}
        ${cur.tips?.length ? `<div><h5>Notes</h5><ul class="tips">${list(cur.tips)}</ul></div>` : ""}</div></div>
      ${(cur.help || []).map(h => `<div class="helpqa"><div class="hq">${esc(h.q)}</div><div class="md">${md(h.a)}</div></div>`).join("")}
      <div class="step-act">
        <label class="lbl" for="note${i.id}">Findings <span class="muted">Record what you found or produced. The next task is based on it.</span></label>
        <textarea id="note${i.id}" data-note placeholder="e.g. 2 reviews identified; current screening relies on questionnaires; no studies use CGM data…">${esc(cur.note || "")}</textarea>
        <div class="row"><button class="btn" data-done="${i.id}">${IC.check} Record findings &amp; continue</button>
          <button class="btn ghost" data-stuck>Request guidance</button><button class="btn ghost" data-swap>Propose alternative</button>
          ${done.length ? `<button class="btn link small" data-undo="${i.id}">Reopen previous task</button>` : ""}</div>
        <div class="row ask hidden" data-askrow><input class="grow" type="text" data-q placeholder="What needs clarifying? (leave blank for a full walkthrough)">
          <button class="btn small" data-help="${i.id}">Get guidance</button></div>
        <div class="row ask hidden" data-swaprow><input class="grow" type="text" data-r placeholder="Reason, e.g. no access to that resource, already completed, scope too large">
          <button class="btn small" data-doswap="${i.id}">Replace task</button></div>
      </div></article>` : ""}
    ${done.length ? `<details class="donelist"><summary>Completed tasks (${done.length})</summary><ol>${done.map(s => `<li><b>${esc(s.title)}</b>
      ${s.note ? `<p>${esc(s.note)}</p>` : ""}<time>${ago(s.done_at)}</time></li>`).join("")}</ol></details>` : ""}
  </section>`;
}
function plansTab(pane, pid, p) {
  const plans = p.items.filter(i => i.kind === "plan");
  pane.innerHTML = `<p class="sub" style="margin-top:0">Each plan works through one idea a task at a time. Record your findings and Medr sets the next task.</p>
    ${plans.length ? plans.map(i => `<article class="report plan" data-id="${i.id}"><header><h3>${esc(i.title)}</h3><span class="when">${ago(i.created)}</span>
      <button class="btn ghost small" data-print="${i.id}">Print / save PDF</button><button class="btn link small" data-del="${i.id}">Delete</button></header>
      <div class="body">${missionView(i, pid)}
      <details class="fullplan" ${i.data.mission ? "" : "open"}><summary>The full plan</summary><div class="md">${md(i.data.md)}</div>${sourcesBlock(i.data.sources)}</details></div></article>`).join("")
      : `<div class="empty">No plans yet. Run <a href="#/p/${pid}/ideas">Idea Lab</a>, then press <b>Draft research plan</b>.</div>`}`;
  const redraw = async (fn, btn, label) => busy(btn, label, async () => {
    const it = await fn(); const k = p.items.findIndex(x => x.id === it.id); if (k >= 0) p.items[k] = it;
    const art = $(`.report[data-id="${it.id}"]`, pane); const open = !!it.data.mission;
    art.querySelector(".mission").outerHTML = missionView(it, pid); art.querySelector(".fullplan").open = !open; bind();
    art.querySelector(".mission").scrollIntoView({ behavior: "smooth", block: "start" }); });
  const bind = () => {
    $$(".leadpick", pane).forEach(lp => $$(".lead", lp).forEach(b => b.onclick = () => { $$(".lead", lp).forEach(x => x.classList.toggle("on", x === b)); }));
    $$("[data-start]", pane).forEach(b => b.onclick = () => { const sec = b.closest(".mission");
      const lead = $("[data-own]", sec).value.trim() || $(".lead.on", sec)?.dataset.lead || "";
      redraw(() => api(`/api/items/${b.dataset.start}/mission/start`, { json: { lead } }), b, "Preparing task 1…"); });
    $$("[data-done]", pane).forEach(b => b.onclick = () => { const note = $("[data-note]", b.closest(".step")).value;
      try { localStorage.removeItem("medr_note_" + b.dataset.done); } catch (e) {}
      redraw(() => api(`/api/items/${b.dataset.done}/mission/done`, { json: { note } }), b, "Preparing the next task…"); });
    $$("[data-stuck]", pane).forEach(b => b.onclick = () => { const r = $("[data-askrow]", b.closest(".step")); r.classList.toggle("hidden"); $("input", r).focus(); });
    $$("[data-swap]", pane).forEach(b => b.onclick = () => { const r = $("[data-swaprow]", b.closest(".step")); r.classList.toggle("hidden"); $("input", r).focus(); });
    $$("[data-help]", pane).forEach(b => b.onclick = () => { const question = $("[data-q]", b.closest(".step")).value;
      redraw(() => api(`/api/items/${b.dataset.help}/mission/help`, { json: { question } }), b, "Preparing guidance…"); });
    $$("[data-doswap]", pane).forEach(b => b.onclick = () => { const reason = $("[data-r]", b.closest(".step")).value;
      redraw(() => api(`/api/items/${b.dataset.doswap}/mission/swap`, { json: { reason } }), b, "Preparing an alternative…"); });
    $$("[data-undo]", pane).forEach(b => b.onclick = () => redraw(() => api(`/api/items/${b.dataset.undo}/mission/undo`, { method: "POST" }), b, "Undoing…"));
    $$("[data-reset]", pane).forEach(b => b.onclick = () => redraw(() => api(`/api/items/${b.dataset.reset}/mission/reset`, { method: "POST" }), b, "Resetting…"));
    $$("[data-note]", pane).forEach(t => t.oninput = () => { const id = t.closest(".mission").dataset.id;
      try { localStorage.setItem("medr_note_" + id, t.value); } catch (e) {} });
    $$("[data-note]", pane).forEach(t => { if (!t.value) try { t.value = localStorage.getItem("medr_note_" + t.closest(".mission").dataset.id) || ""; } catch (e) {} });
  };
  bind();
  $$("[data-print]", pane).forEach(b => b.onclick = () => {
    const art = b.closest(".report"); art.classList.add("printing"); document.body.classList.add("printing"); art.querySelector(".fullplan").open = true;
    setTimeout(() => { print(); art.classList.remove("printing"); document.body.classList.remove("printing"); }, 50); });
  $$("[data-del]", pane).forEach(b => b.onclick = async () => { if (!confirm("Delete this plan?")) return;
    await api(`/api/items/${b.dataset.del}`, { method: "DELETE" }); route(); });
}

function radarTab(pane, pid, p, auto) {
  runner(pane, {
    auto, title: "Trends",
    sub: "Find the ideas in a field that are taking off right now.",
    how: ["You type a field.", "Medr reads the newest papers and preprints and picks out the new ideas in them.", "It counts real papers per year for each idea and ranks them by growth."],
    examples: [{ field: "AI for early cancer detection" }, { field: "gut microbiome and mental health" }, { field: "wearables for heart disease" }],
    legend: `<b>How to read this:</b> the big number is growth. <b>2×</b> means twice as many papers in the last 2 full years as the 2 years before. Pink bars are those recent years. Small counts with big growth are early signals.`,
    fields: `<input class="grow" type="text" name="field" required placeholder="e.g. AI in drug repurposing, cancer immunotherapy, gut microbiome and mental health" aria-label="Field">`,
    button: "Scan field", wait: "Scanning recent research and measuring growth. This takes about a minute.",
    run: f => api(`/api/projects/${pid}/radar`, { json: f }), kind: "radar", items: p.items,
    render: i => `<div class="movers">${(i.data.topics || []).map(t => {
      const recent = t.counts.slice(-3, -1).reduce((a, c) => a + c.count, 0);
      return `<div class="mover ${t.growth < 1.15 ? "cool" : ""}"><div class="g num">${t.growth}×<small>${t.growth >= 1.15 ? "growth" : "flat"}</small></div>
        <div class="t">${esc(t.name)}</div><div class="w">${md(t.why || "").replace(/^<p>|<\/p>\s*$/g, "")}</div>
        ${spark(t.counts, 220, 40)}<div class="small" style="color:var(--muted)">${fmt(recent)} papers in the last 2 full years</div>
        <div class="q">${esc(t.query)}</div></div>`; }).join("")}</div>`,
  });
}

function bridgeTab(pane, pid, p, auto) {
  runner(pane, {
    auto, title: "Connections",
    sub: "Find untested ideas between two topics. Results are ideas worth checking, not proven facts.",
    how: ["You pick two topics, A and B.", "Medr finds things both connect to (C), like a process or a biomarker.", "It flags the Cs that are well studied with A and with B, but almost never with both."],
    examples: [{ a: "retinal imaging AI", b: "Alzheimer's disease" }, { a: "sleep", b: "migraine" }, { a: "gut microbiome", b: "depression" }],
    legend: `<b>How to read this:</b> each row is a possible link. "A + C" and "B + C" count papers on each pair. "A + B + C" counts papers on all three. A long pink bar means lots of research on each side but very little connecting them, which is where new ideas hide.`,
    fields: `<input class="grow" type="text" name="a" required placeholder="Topic A, e.g. deep learning retinal imaging" aria-label="Topic A">
      <span style="font:700 1.2rem var(--display);color:var(--faint)" aria-hidden="true">×</span>
      <input class="grow" type="text" name="b" required placeholder="Topic B, e.g. Alzheimer disease" aria-label="Topic B">`,
    button: "Find links", wait: "Mapping both literatures and testing bridges. This takes about a minute.",
    run: f => api(`/api/projects/${pid}/bridge`, { json: f }), kind: "bridge", items: p.items,
    render: i => {
      const d = i.data, c = d.counts || {}, links = d.links || [], top = Math.max(1, ...links.map(l => l.score));
      return `<div class="abc"><div class="node"><b>${esc(d.a)}</b><span class="num">${fmt(c.a)} papers</span></div>
        <div class="mid"><b class="num">${fmt(c.ab)}</b>papers on both<br><span class="num">${fmt(c.ab_recent)} in the last 3 years</span></div>
        <div class="node"><b>${esc(d.b)}</b><span class="num">${fmt(c.b)} papers</span></div></div>
        ${links.length ? `<table class="links"><thead><tr><th>Bridge concept (C)</th><th class="num">A + C</th><th class="num">B + C</th><th class="num">A + B + C</th><th>Hidden-link score</th></tr></thead>
        <tbody>${links.map((l, k) => `<tr class="${k < 3 ? "lead" : ""}"><td><b>${esc(l.name)}</b></td><td class="num">${fmt(l.ac)}</td>
          <td class="num">${fmt(l.bc)}</td><td class="num">${fmt(l.abc)}</td>
          <td><div class="bar"><i style="width:${Math.round(l.score / top * 100)}%"></i></div></td></tr>`).join("")}</tbody></table>
        <p class="hint">High score: lots of research on A + C and on B + C, but very little on all three together.</p>` : ""}`;
    },
  });
}

function measuresTab(pane, pid, p, auto) {
  runner(pane, {
    auto, mdFirst: true, printable: true, title: "Assessment Finder",
    sub: "Find the validated questionnaires and rating scales that fit a patient group, how strongly each is supported in that group, how scores are interpreted, and where measurement gaps remain. Built for measurement-based care and Collaborative Care programs.",
    how: ["<b>Define the group.</b> Describe the patients, the purpose (screening or tracking change) and the setting.",
          "<b>Check the evidence.</b> Each candidate instrument is counted against PubMed for use and validation in that exact group.",
          "<b>Recommend.</b> Instruments are graded A to D, with cutoffs, cadence and cautions, plus measurement gaps."],
    examples: [{ population: "adolescents with type 1 diabetes and depressive symptoms", purpose: "both", setting: "Pediatric primary care", _label: "Teens with T1D and depression" },
      { population: "adults with anxiety in collaborative care", purpose: "monitoring", setting: "Collaborative Care (CoCM)", _label: "Adult anxiety, Collaborative Care" },
      { population: "children 6-12 with ADHD", purpose: "monitoring", setting: "Outpatient behavioral health", _label: "Children with ADHD" },
      { population: "veterans with PTSD and substance use", purpose: "both", setting: "", _label: "Veterans, PTSD + substance use" }],
    fields: `<input class="grow" type="text" name="population" required placeholder="Patient group, e.g. adolescents with type 1 diabetes and depressive symptoms" aria-label="Patient group">
      <select name="purpose" aria-label="Purpose" style="width:auto;min-width:200px"><option value="both">Screening + tracking change</option>
        <option value="screening">Screening</option><option value="monitoring">Tracking change over time</option></select>
      <select name="setting" aria-label="Setting" style="width:auto;min-width:200px"><option value="">Any setting</option>
        <option>Collaborative Care (CoCM)</option><option>Primary care</option><option>Pediatric primary care</option>
        <option>Outpatient behavioral health</option><option>Specialty medical clinic</option><option>School-based</option><option>Inpatient / partial hospitalization</option></select>`,
    button: "Find assessments", wait: "Identifying instruments and checking each one against PubMed for this population. About 1 minute.",
    run: f => api(`/api/projects/${pid}/measures`, { json: f }), kind: "measures", items: p.items,
    render: i => {
      const d = i.data, ms = d.measures || [];
      const ctx = `<p class="muted small" style="margin:0 0 12px">${esc(d.population)} · ${esc({ both: "Screening + tracking change", screening: "Screening", monitoring: "Tracking change" }[d.purpose] || "")}${d.setting ? " · " + esc(d.setting) : ""}</p>`;
      return ctx + `<p class="howread"><b>How to read this:</b> the <b>fit grade</b> shows how well each questionnaire is backed for this group: <b>A</b> validated here by several studies, <b>B</b> some validation, <b>C</b> used here but little validation, <b>D</b> not validated here yet. Click a row for score cutoffs, how often to use it, and the key papers.</p>
        <div class="mtable"><div class="mrow mhead"><span>Instrument</span><span>Fit</span><span>Evidence in this group</span><span>Use</span></div>
        ${ms.map(m => `<details class="mrow-d"><summary class="mrow">
          <span><b>${esc(m.abbr || m.name)}</b><small>${esc(m.name)}${m.construct ? ` · ${esc(m.construct)}` : ""}</small></span>
          <span><i class="gchip g${m.fit_grade}" title="${GRADE[m.fit_grade] || ""}">${m.fit_grade}</i></span>
          <span class="num"><b>${fmt(m.n_val)}</b> validation · ${fmt(m.n_pop)} total</span>
          <span>${esc(m.use_for || "")}</span></summary>
          <div class="mdet">
            ${m.fit_reason ? `<p><b>Why this grade:</b> ${md(m.fit_reason).replace(/^<p>|<\/p>\s*$/g, "")}</p>` : ""}
            <div class="mgrid">
              <div><h5>Interpreting scores</h5><p>${m.thresholds ? md(m.thresholds).replace(/^<p>|<\/p>\s*$/g, "") : "Not established for this group."}</p></div>
              <div><h5>Cadence</h5><p>${esc(m.cadence || "n/a")}</p></div>
              <div><h5>Practical</h5><p>${esc(m.ages || "")}${m.length ? ` · ${esc(m.length)}` : ""}${m.respondent ? ` · ${esc(m.respondent)}` : ""}${m.access ? ` · ${esc(m.access)}` : ""}</p></div>
            </div>
            ${m.cautions ? `<p class="muted"><b>Caution:</b> ${esc(m.cautions)}</p>` : ""}
            ${m.loosened?.length ? `<p class="muted small">Counted without the detail "${esc(m.loosened.join('", "'))}" (no exact match).</p>` : ""}
            ${(m.key_papers || []).length ? `<h5>Key validation papers</h5><ul class="kp">${m.key_papers.map(k => `<li><a href="${k.url}" target="_blank">${esc(k.title)}</a> <span class="id">${k.year || ""}</span></li>`).join("")}</ul>` : ""}
            <a class="btn link small" href="${m.link}" target="_blank">All papers for this instrument in this group ↗</a>
          </div></details>`).join("")}</div>
        ${(d.gaps || []).length ? `<div class="mgaps"><h5>Measurement gaps</h5>${d.gaps.map(g => `<div class="mgap">${g.grade ? `<i class="gchip g${g.grade}">${g.grade}</i>` : ""}<div><b>${esc(g.gap)}</b><p>${esc(g.approach || "")}</p></div></div>`).join("")}</div>` : ""}`;
    },
  });
}

function gapsTab(pane, pid, p, auto) {
  runner(pane, {
    auto, title: "Opportunities",
    sub: "Find open questions, missing research, and where AI could help, plus project ideas you could actually start.",
    how: ["You type a topic.", "Medr reads current papers, preprints and trials on it.", "It lists what's still unanswered and where you could jump in."],
    examples: [{ topic: "early detection of pancreatic cancer" }, { topic: "long COVID" }, { topic: "AI in sepsis care" }],
    fields: `<input class="grow" type="text" name="topic" required placeholder="e.g. early detection of pancreatic cancer" aria-label="Topic">`,
    button: "Find opportunities", wait: "Reading papers, preprints and trials. About 30-60 seconds.",
    run: f => api(`/api/projects/${pid}/gaps`, { json: f }), kind: "gaps", items: p.items,
    render: i => i.data.trend?.length ? `<div class="trendbox">${spark(i.data.trend, 260, 40)}<div class="lbl"><b>Papers per year</b>last ${i.data.trend.length} years</div></div>` : "",
  });
}

function briefTab(pane, pid, p, auto) {
  runner(pane, {
    auto, title: "Evidence brief",
    sub: "Ask a question and get a short answer from real studies and trials. Every claim links to its source.",
    how: ["You ask a question.", "Medr pulls the most relevant papers, preprints and trials.", "It writes what's known, what's being tested, and what to read first."],
    examples: [{ topic: "Do GLP-1 drugs lower dementia risk?" }, { topic: "Can AI read X-rays as well as doctors?" }, { topic: "Is CAR-T working for solid tumors?" }],
    fields: `<input class="grow" type="text" name="topic" required placeholder="e.g. Do GLP-1 drugs reduce dementia risk?" aria-label="Question">`,
    button: "Write brief", wait: "Searching papers, preprints and trials, then writing. About 30-60 seconds.",
    run: f => api(`/api/projects/${pid}/brief`, { json: f }), kind: "brief", items: p.items,
    render: i => i.data.trend?.length ? `<div class="trendbox">${spark(i.data.trend, 260, 40)}<div class="lbl"><b>Papers per year</b>last ${i.data.trend.length} years</div></div>` : "",
  });
}

// ---------------- search ----------------
function readAs(it, mode) {
  if (!it) return "";
  const relaxed = (it.relaxed || []).filter(Boolean);
  return `<div class="readas"><div class="ra-h"><span class="ra-k">Medr read this as</span><span class="ra-m">${esc(it.meaning || "")}</span></div>
    <div class="ra-c">${(it.concepts || []).map(c => `<span class="concept ${relaxed.includes(c.name) ? "off" : ""}" title="${esc((c.terms || []).join(", "))}${c.mesh ? " | MeSH: " + esc(c.mesh) : ""}">
      ${esc(c.name)}<small>${esc((c.terms || []).slice(1, 4).join(", "))}</small></span>`).join("")}</div>
    ${relaxed.length ? `<p class="ra-note">Only <b>${fmt(it.exact_total)}</b> ${mode === "trials" ? "trials" : "papers"} match everything you asked, so Medr also shows close matches by loosening: <b>${relaxed.map(esc).join(", ")}</b>. Exact matches are listed first.</p>`
      : `<p class="ra-note ok"><b>${fmt(it.exact_total)}</b> results match every part of your search.</p>`}
    ${it.fallback ? `<p class="ra-note">Couldn't reach the AI reader, so this used a simple reading of your words.</p>` : ""}
    <details><summary>Exact query sent</summary><code>${esc(it.query || "")}</code></details></div>`;
}
function matchChip(x) {
  if (!x.match || !x.match.of) return "";
  return x.match.hit === x.match.of ? `<span class="chip exact">Matches everything</span>`
    : `<span class="chip partial" title="Missing: ${esc(x.match.missing.join(", "))}">Close match (missing ${esc(x.match.missing.join(", "))})</span>`;
}
function gapCard(g, q) {
  const aps = g.approaches || g.ideas || [];
  return `<section class="gap"><header class="gap-h"><div class="grow"><h3>Proposed approaches</h3>
      <p class="muted small">Little research answers this exactly, so these are original approaches built from the closest evidence, related fields and first principles. Each one is graded on confidence. Test them; don't trust them.</p></div></header>
    ${g.assessment ? `<div class="gap-a"><h5>What's known, and the gap</h5><div class="md">${md(g.assessment)}</div></div>` : ""}
    <div class="gap-ideas">${aps.map((x, k) => `<article class="gidea">
      <div class="idea-meta"><span class="kind">${esc(x.type)}</span>${originTag(x)}</div>
      <h4>${esc(x.title)}</h4>${x.plain ? `<p class="plainlead">${esc(x.plain)}</p>` : ""}${gradeBox(x.grade || "D", x.grade_reason)}
      ${x.plain ? `<h5>How it works</h5>` : ""}<div class="md">${md(x.approach || x.idea || "")}</div>
      ${(x.steps || (x.first_step ? [x.first_step] : [])).length ? `<h5>First steps</h5><ol>${stepsList(x.steps || [x.first_step])}</ol>` : ""}
      ${x.proof ? `<p class="proof"><b>What would prove it:</b> ${esc(x.proof)}</p>` : ""}
      <div class="idea-foot"><button class="btn small" data-gsolve="${k}">Build a solution</button>
        <button class="btn link small" data-gshort="${k}">Save for later</button>
        <button class="btn link small" data-glab="${k}">Explore in Idea Lab</button></div></article>`).join("")}</div>
    ${g.related_searches?.length ? `<div class="related"><h5>Try nearby searches</h5><div class="relrow">${g.related_searches.map(r =>
      `<button class="rel" data-rq="${esc(r.query)}" title="${esc(r.why || "")}">${esc(r.label)}</button>`).join("")}</div></div>` : ""}</section>`;
}
function searchTab(pane, pid, p, auto) {
  const savedRefs = new Set(p.items.map(i => i.kind + ":" + i.ref));
  let mode = auto?.mode || "papers";
  const ph = { papers: "Describe what you want, e.g. mental health screening in kids with type 1 diabetes",
    preprints: "Describe what you want, e.g. CGM data to predict diabetes distress", trials: "Describe the trials you want, e.g. depression treatment for teens with type 1 diabetes" };
  const extra = {
    papers: `<label class="check"><input type="checkbox" name="reviews" value="1"> Reviews only</label>
      <label class="check"><input type="checkbox" name="sort" value="new"> Newest first</label>
      <label class="check">Since <input type="number" name="min_year" min="1950" max="2100" style="width:90px;padding:4px 8px"></label>`,
    preprints: `<span class="hint" style="margin:0">Preprints are new and not peer reviewed yet.</span>`,
    trials: ["RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED"].map(s =>
        `<label class="check"><input type="checkbox" name="status" value="${s}"> ${s.replaceAll("_", " ").toLowerCase()}</label>`).join("")
      + `<span style="color:var(--line2)">|</span>` + ["PHASE1", "PHASE2", "PHASE3", "PHASE4"].map(s =>
        `<label class="check"><input type="checkbox" name="phase" value="${s}"> ${s.replace("PHASE", "Phase ")}</label>`).join(""),
  };
  pane.innerHTML = `<p class="sub" style="margin-top:0">Type what you want in plain words. Medr reads every part of it, searches for exactly that, and tells you how it read it. If the research doesn't exist yet, Medr proposes its own ideas.</p>
    <div class="seg" role="tablist">${["papers", "preprints", "trials"].map(m =>
    `<button data-m="${m}" class="${m === mode ? "on" : ""}">${m[0].toUpperCase() + m.slice(1)}</button>`).join("")}</div>
    <form class="runner" id="sf" style="margin-top:14px"><div class="row"><input class="grow" type="text" name="q" required aria-label="Search">
      <button class="btn">Search</button></div>
      <div class="row small" style="margin-top:8px" id="sx"></div></form>
    <div id="interp"></div><div id="gap"></div><div id="trend"></div><div id="res"></div>`;
  const qin = $("#sf [name=q]");
  const draw = () => { qin.placeholder = ph[mode]; $("#sx").innerHTML = extra[mode] + `<label class="check" title="Search your words exactly as typed, without the AI reader"><input type="checkbox" name="exact" value="1"> Exact words only</label>`;
    ["#res", "#trend", "#interp", "#gap"].forEach(x => $(x).innerHTML = ""); };
  $$(".seg button", pane).forEach(b => b.onclick = () => { mode = b.dataset.m; $$(".seg button", pane).forEach(x => x.classList.toggle("on", x === b)); draw(); });
  draw();
  const saveBtn = (kind, ref) => savedRefs.has(kind + ":" + ref) ? `<button class="btn small saved" disabled>Saved</button>`
    : `<button class="btn ghost small" data-save="${kind}:${esc(ref)}">Save</button>`;
  const abstract = x => x.abstract ? `<details><summary class="small" style="cursor:pointer;color:var(--muted);margin-top:8px">Abstract</summary><div class="abstract">${esc(x.abstract)}</div></details>` : "";

  const proposeBtn = (label) => `<div class="gapcta"><div class="grow"><b>${label}</b><span>Medr reasons from the closest research, related fields and first principles, then proposes graded approaches.</span></div>
    <button class="btn small" id="propose">Propose approaches</button></div>`;
  let last = null;
  const propose = (btn) => busy(btn, "Working out approaches…", async () => {
    const papers = (mode === "papers" ? last.r.results : []).slice(0, 8).map(x => ({ pmid: x.pmid, title: x.title, abstract: (x.abstract || "").slice(0, 900), journal: x.journal, year: x.year, types: x.types }));
    const g = await api("/api/search/ideas", { json: { q: last.q, meaning: last.r.interp?.meaning || "", exact_total: last.r.interp?.exact_total || 0, papers, pid } });
    if (!(g.approaches || g.ideas || []).length) throw new Error("No approaches came back. Press Propose approaches again.");
    $("#gap").innerHTML = gapCard(g, last.q);
    $("#gap").scrollIntoView({ behavior: "smooth", block: "start" });
    const aps = g.approaches || g.ideas || [];
    const asIdea = x => ({ title: x.title, type: x.type, intervention: x.title, target: "", plain: x.plain, rationale: x.approach || x.idea || "",
        test_plan: x.steps || [], proof: x.proof, grade: x.grade || "D", grade_reason: x.grade_reason, origin: x.origin, borrowed_from: x.borrowed_from,
        verdict: "Unexplored", source: "Medr approach from search", m: { promise: 0, co: 0, link_i: 0, trials: 0, unscored: true } });
    $$("[data-gsolve]").forEach(b => b.onclick = () => buildSolution(b, pid, asIdea(aps[+b.dataset.gsolve]), last.q));
    $$("[data-gshort]").forEach(b => b.onclick = async () => {
      await api(`/api/projects/${pid}/shortlist`, { json: { idea: asIdea(aps[+b.dataset.gshort]), condition: last.q } }).catch(e => toast(e.message, true));
      b.textContent = "Saved"; b.disabled = true; toast("Saved to your Pipeline (Ideas › Pipeline)"); });
    $$("[data-glab]").forEach(b => b.onclick = () => { location.hash = `#/p/${pid}/ideas?run=${encodeURIComponent(JSON.stringify({ condition: last.q, angle: "any" }))}`; });
    $$("[data-rq]").forEach(b => b.onclick = () => { qin.value = b.dataset.rq; $("#sf").requestSubmit(); });
  });

  const idOf = x => x.pmid || x.id || x.nct;
  const kindOf = () => mode === "papers" ? "paper" : mode === "preprints" ? "preprint" : "trial";
  const meter = (label, v, cls) => v ? `<span class="meter2 ${cls}" title="${label}: ${v} of 5"><small>${label}</small><span>${[1, 2, 3, 4, 5].map(k => `<i class="${k <= v ? "on" : ""}"></i>`).join("")}</span></span>` : "";
  const explainBlock = e => e ? `<div class="explain"><p><b>What they studied:</b> ${esc(e.premise || "")}</p><p><b>How it connects:</b> ${esc(e.connection || "")}</p></div>` : "";
  const itemHtml = (x, e) => {
    const head = mode === "papers"
      ? `<div class="m">${esc(x.journal)} ${esc(x.year)}. ${esc(x.authors.join(", "))}${x.authors.length >= 6 ? " et al." : ""} <span class="id"><abbr title="PubMed ID: this paper's ID number in PubMed">PMID</abbr> ${x.pmid}</span></div>
         <div class="chips">${matchChip(x)}${x.types.filter(t => t !== "Journal Article").map(t => `<span class="chip ${/Review|Meta/.test(t) ? "rev" : ""}">${esc(t)}</span>`).join("")}</div>`
      : mode === "preprints"
      ? `<div class="m">${esc(x.server)}, ${esc(x.date)}. ${esc(x.authors)}</div><div class="chips">${matchChip(x)}<span class="chip pp">Preprint, not peer reviewed</span></div>`
      : `<div class="m"><span class="id">${x.nct}</span> ${esc(x.sponsor)}${x.start ? `, started ${esc(x.start)}` : ""}${x.enrollment ? `, ${fmt(x.enrollment)} people` : ""}</div>
         <div class="chips"><span class="chip st-${esc(x.status)}">${esc(x.status.replaceAll("_", " ").toLowerCase())}</span>
         ${x.phases.map(ph => `<span class="chip">${esc(ph.replace("PHASE", "Phase ").replace("EARLY_", "Early "))}</span>`).join("")}
         ${x.interventions.slice(0, 3).map(iv => `<span class="chip">${esc(iv)}</span>`).join("")}</div>`;
    const text = mode === "trials" ? x.summary : x.abstract;
    return `<div class="result" data-rid="${esc(idOf(x))}"><div class="row" style="align-items:flex-start"><div class="grow">
        <div class="t"><a href="${x.url}" target="_blank">${esc(x.title)}</a></div>${head}</div>
        <div class="rside">${e ? `<div class="meters">${meter("Connection", e.relevance, "m-rel")}${meter("Breakthrough", e.breakthrough, "m-brk")}</div>` : ""}${saveBtn(kindOf(), idOf(x))}</div></div>
      ${explainBlock(e)}
      ${text ? `<details><summary class="small" style="cursor:pointer;color:var(--muted);margin-top:8px">${mode === "trials" ? "Trial summary" : "Abstract"}</summary><div class="abstract">${esc(text)}</div></details>` : ""}</div>`;
  };
  const score = (x, e) => e ? (e.relevance || 0) * 3 + (e.breakthrough || 0) + (x.match && x.match.hit === x.match.of ? 1 : 0) : -1;
  // the AI's rating can't overrule the facts: a study missing most of your search can't count as a close match
  const honest = (x, e) => {
    if (!e) return e;
    const share = x.match?.of ? x.match.hit / x.match.of : 1;
    return share < 0.5 ? { ...e, relevance: Math.min(e.relevance || 0, 2) } : e;
  };
  const drawList = (r, ex) => {
    let list = r.results.map(x => ({ x, e: honest(x, ex?.[idOf(x)]) }));
    if (ex) list.sort((a, b) => score(b.x, b.e) - score(a.x, a.e));
    const close = ex ? list.filter(o => !o.e || o.e.relevance >= 3) : list, far = ex ? list.filter(o => o.e && o.e.relevance < 3) : [];
    $("#rlist").innerHTML = (ex ? (close.length ? `<p class="hint rank-note">Ranked by how closely each one matches your search, then by breakthrough potential.</p>`
        : `<div class="gapcta"><div class="grow"><b>None of these fully match your search.</b><span>They each cover only part of it. Medr's approaches above are your best starting point.</span></div></div>`) : "")
      + close.map(o => itemHtml(o.x, o.e)).join("")
      + (far.length ? `<details class="farlist" ${close.length ? "" : "open"}><summary>${far.length} less related result${far.length > 1 ? "s" : ""} (only share part of your search)</summary>${far.map(o => itemHtml(o.x, o.e)).join("")}</details>` : "")
      + "";
    $$("[data-save]").forEach(b => b.onclick = async () => {
      const [kind, ref] = b.dataset.save.split(/:(.*)/s);
      const x = r.results.find(y => idOf(y) == ref), e = ex?.[ref];
      await api(`/api/projects/${pid}/items`, { json: { kind, ref, title: x.title, data: { ...x, explain: e || null } } }).catch(err => toast(err.message, true));
      savedRefs.add(kind + ":" + ref); $$(`[data-save="${b.dataset.save}"]`).forEach(z => z.outerHTML = `<button class="btn small saved" disabled>Saved</button>`); toast("Saved to project");
    });
  };

  $("#sf").onsubmit = e => {
    e.preventDefault();
    const fd = new FormData(e.target), btn = $("#sf .btn"), q = fd.get("q").trim();
    ["#gap", "#interp", "#trend"].forEach(x => $(x).innerHTML = "");
    $("#res").innerHTML = `<div class="working"><span class="pulse"></span>Reading your search and finding the best matches…</div>`;
    busy(btn, "Searching…", async () => {
      let r;
      const ex = fd.get("exact") || 0;
      try {
      if (mode === "papers") {
        const qs = new URLSearchParams({ q, reviews: fd.get("reviews") || 0, sort: fd.get("sort") || "relevance", exact: ex });
        if (fd.get("min_year")) qs.set("min_year", fd.get("min_year"));
        r = await api("/api/search/papers?" + qs);
        const tq = r.interp?.query || q;
        $("#trend").innerHTML = `<div class="trendbox"><span class="pulse"></span><div class="lbl">Measuring how this topic is trending…</div></div>`;
        api("/api/trend?q=" + encodeURIComponent(tq)).then(t => $("#trend").innerHTML =
          `<div class="trendbox">${spark(t, 300, 40)}<div class="lbl"><b>${fmt(r.total)} papers</b>per year, last ${t.length} years</div></div>`)
          .catch(() => $("#trend").innerHTML = "");
      } else if (mode === "preprints") {
        r = await api("/api/search/preprints?" + new URLSearchParams({ q, exact: ex }));
      } else {
        r = await api("/api/search/trials?" + new URLSearchParams({ q, exact: ex, status: fd.getAll("status").join(","), phase: fd.getAll("phase").join(",") }));
      }
      } catch (err) { $("#res").innerHTML = ""; throw err; }
      last = { q, r };
      $("#interp").innerHTML = readAs(r.interp, mode);
      const exact = r.interp?.exact_total ?? r.total, thin = exact < 10;
      $("#res").innerHTML = (thin ? proposeBtn(exact ? `Only ${fmt(exact)} ${mode === "trials" ? "trials" : "studies"} match exactly. That's a gap, and gaps are where breakthroughs come from.` : `Nothing matches this exactly yet. That's a gap, and gaps are where breakthroughs come from.`) : "")
        + `<p class="hint" style="margin:0 0 10px">${fmt(r.total)} found, showing ${r.results.length}</p>`
        + (r.results.length ? `<div class="working small" id="explaining"><span class="pulse"></span>Summarizing each study in plain words and ranking them by how well they match your search…</div>` : "")
        + `<div id="rlist"></div>`
        + (thin ? "" : proposeBtn("Want ideas beyond what's published?"));
      drawList(r, null);
      $("#propose").onclick = e2 => propose(e2.currentTarget);
      if (thin) propose($("#propose"));
      if (r.results.length) {
        const items = r.results.slice(0, 20).map(x => ({ id: idOf(x), kind: kindOf(), title: x.title,
          text: (mode === "trials" ? `${x.summary || ""} Conditions: ${(x.conditions || []).join(", ")}. Interventions: ${(x.interventions || []).join(", ")}.` : x.abstract || "").slice(0, 1200) }));
        api("/api/search/explain", { json: { q, meaning: r.interp?.meaning || "", items } })
          .then(ex => { if (last.r !== r) return;
            if (!Object.keys(ex.items || {}).length) { $("#explaining").innerHTML = "Couldn't write the summaries this time. The results are listed below; search again to retry."; return; }
            $("#explaining")?.remove(); drawList(r, ex.items); })
          .catch(() => { $("#explaining") && ($("#explaining").innerHTML = "Couldn't summarize these right now. The results are still listed below."); });
      }
    });
  };
  if (auto?.q) { qin.value = auto.q; $("#sf").requestSubmit(); }
}

// ---------------- watch ----------------
function watchTab(pane, pid, p) {
  const digests = p.items.filter(i => i.kind === "digest");
  pane.innerHTML = `<div class="side"><div>
      <h2>What's new</h2>${digests.length ? digests.map(i => `<article class="report"><header><h3>${esc(i.title)}</h3><span class="when">${ago(i.created)}</span></header>
        <div class="body"><div class="md">${md(i.data.md)}</div>${sourcesBlock(i.data.sources)}</div></article>`).join("")
        : `<div class="empty">Add a topic to watch, then hit "Check now". Medr finds new papers, preprints and trial updates since the last check and summarizes them.</div>`}</div>
    <div class="panel"><h2>Watching</h2>
      <div id="wl">${p.watches.map(w => `<div class="watch"><div class="grow"><b>${esc(w.topic)}</b><div class="small" style="color:var(--muted)">checked through ${esc(w.last_checked)}</div></div>
        <button class="btn small" data-check="${w.id}">Check now</button><button class="btn link small" data-rm="${w.id}" aria-label="Stop watching">Remove</button></div>`).join("")
        || `<p class="hint">Nothing watched yet.</p>`}</div>
      <form id="aw" style="margin-top:14px"><label for="wt">Watch a topic</label><input id="wt" type="text" name="topic" required placeholder="e.g. AI sepsis prediction">
      <button class="btn" style="width:100%;margin-top:10px">Start watching</button></form></div></div>`;
  $("#aw").onsubmit = e => { e.preventDefault(); busy($("#aw .btn"), "Adding…", async () => {
    await api(`/api/projects/${pid}/watches`, { json: { topic: e.target.topic.value } }); route(); }); };
  $$("[data-check]").forEach(b => b.onclick = () => busy(b, "Checking…", async () => {
    await api(`/api/watches/${b.dataset.check}/check`, { method: "POST" }); toast("Digest ready"); route(); }));
  $$("[data-rm]").forEach(b => b.onclick = async () => { await api(`/api/watches/${b.dataset.rm}`, { method: "DELETE" }); route(); });
}

// ---------------- ask ----------------
function askTab(pane, pid, p) {
  const n = p.items.filter(i => ["paper", "preprint", "trial"].includes(i.kind)).length;
  pane.innerHTML = `<p class="sub" style="margin-top:0">Ask anything about this project. Answers come from your ${n} saved sources plus your briefs, radar scans and crossovers, with citations.</p>
    <div class="chat" id="chat">${p.chat.map(m => `<div class="msg ${m.role}">${m.role === "user" ? esc(m.content) : `<div class="md">${md(m.content)}</div>`}</div>`).join("")}</div>
    <form id="af" class="row"><input class="grow" type="text" name="question" required placeholder="e.g. Which of these approaches is closest to the clinic?" aria-label="Question">
    <button class="btn">Ask</button>${p.chat.length ? `<button type="button" class="btn link" id="clr">Clear chat</button>` : ""}</form>`;
  $("#af").onsubmit = e => {
    e.preventDefault(); const qn = e.target.question.value; e.target.question.value = "";
    $("#chat").insertAdjacentHTML("beforeend", `<div class="msg user">${esc(qn)}</div><div class="msg assistant" id="pending"><span class="pulse" style="display:inline-block"></span></div>`);
    busy($("#af .btn"), "Preparing guidance…", async () => {
      const r = await api(`/api/projects/${pid}/ask`, { json: { question: qn } });
      $("#pending").outerHTML = `<div class="msg assistant"><div class="md">${md(r.answer)}</div></div>`;
    }).then(() => $("#pending")?.remove());
  };
  const clr = $("#clr"); if (clr) clr.onclick = async () => { await api(`/api/projects/${pid}/chat`, { method: "DELETE" }); route(); };
}

// ---------------- saved ----------------
function savedTab(pane, pid, p) {
  const groups = [["idea", "Ideas in progress"], ["paper", "Papers"], ["preprint", "Preprints"], ["trial", "Trials"], ["news", "News"], ["note", "Notes"]];
  pane.innerHTML = `<div class="side"><div>${groups.map(([k, label]) => {
      const list = p.items.filter(i => i.kind === k);
      return list.length ? `<h2>${label} <span class="small" style="color:var(--faint)">${list.length}</span></h2><div class="panel" style="margin-bottom:18px;padding:8px 18px">${list.map(i =>
        `<div class="item"><div class="grow">${k === "idea" ? `<b>${esc(i.title)}</b> <span class="badge type">${esc(i.data.type)}</span>
          <div class="small" style="color:var(--muted)">${esc(i.data.condition)}: ${esc(i.data.intervention)} via ${esc(i.data.target)}. Promise ${i.data.m?.promise ?? "–"}/100</div>` :
          k === "note" ? `<b>${esc(i.title)}</b><div class="small">${esc(i.data.text)}</div>` :
          k === "news" ? `<a href="${esc(i.data.url)}" target="_blank">${esc(i.title)}</a> <span class="small muted">${esc(i.data.source || "")}</span>` :
          `<a href="${esc(i.data.url)}" target="_blank">${esc(i.title)}</a> <span class="id">${esc(i.ref)}</span>${i.data.explain?.premise ? `<div class="small muted" style="margin-top:3px">${esc(i.data.explain.premise)}</div>` : ""}`}</div>
          <button class="btn link small" data-del="${i.id}">Remove</button></div>`).join("")}</div>` : ""; }).join("")
      || `<div class="empty">Nothing here yet. Save ideas from Idea Lab, and save papers, preprints and trials from Search. Ask uses everything here.</div>`}</div>
    <form class="panel" id="nf"><h2>Add a note</h2><label for="nt">Title</label><input id="nt" type="text" name="title" required>
      <label for="nx">Note</label><textarea id="nx" name="text" required></textarea><button class="btn" style="width:100%;margin-top:12px">Save note</button></form></div>`;
  $$("[data-del]").forEach(b => b.onclick = async () => { await api(`/api/items/${b.dataset.del}`, { method: "DELETE" }); route(); });
  $("#nf").onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    busy($("#nf .btn"), "Saving…", async () => { await api(`/api/projects/${pid}/items`, { json: { kind: "note", title: f.title, data: { text: f.text } } }); route(); }); };
}


// ---------------- Overview ----------------
const LAUNCH = [["ideas", "Idea Lab", "Generate and score new treatment ideas"], ["radar", "Trends", "What's taking off in a field"],
  ["brief", "Evidence brief", "Where the evidence stands"], ["gaps", "Opportunities", "Open questions and where AI could help"],
  ["measures", "Assessment Finder", "Validated questionnaires for a patient group"]];
const TASKS = [
  ["measures", "Choose assessments", "Validated questionnaires for a patient group, graded on evidence, with cutoffs.", "e.g. teens with type 1 diabetes and depression"],
  ["search", "See what the research says", "Studies on your question, summarized in plain words and ranked by relevance.", "e.g. does measurement-based care improve outcomes for teens?"],
  ["ideas", "Develop an idea", "Put your idea forward. Medr checks it against the research, sharpens it and suggests alternatives, each graded.", "Your idea, e.g. a peer-mentor app to lower diabetes distress in teens"],
  ["news", "Stay current", "The week's major health research news, and alerts when something big lands on your topics.", ""],
];
const STAGE_NAMES = { lead: "Lead", investigating: "Investigating", testing: "Testing", parked: "Parked" };

function overviewTab(pane, pid, p) {
  const st = p.stats || {}, items = p.items, ideas = items.filter(i => i.kind === "idea");
  const plan = items.find(i => i.kind === "plan" && i.data.mission) || items.find(i => i.kind === "plan"), lastRun = items.find(i => i.kind === "ideas");
  const mis = plan?.data?.mission, cur = mis?.steps?.[mis.steps.length - 1];
  const steps = [
    ["Run Idea Lab on your topic", !!st.ideas, "ideas", "Medr reads the research on your topic and proposes new ideas, each checked against real data."],
    ["Write a breakthrough plan", !!st.plan, lastRun ? "ideas" : "ideas", "Turn your best Idea Lab leads into a plan. Press Draft research plan under your Idea Lab results."],
    ["Begin the plan's first task", !!mis, "plans", "Select a lead; Medr issues one task at a time with method and completion criteria."],
    ["Build a solution from your best idea", !!st.idea, "ideas", "Select Build a solution on an idea. Medr moves it into Plans and issues the first task."],
  ];
  const nextSetup = steps.find(x => !x[1]), done = steps.filter(x => x[1]).length;
  const unread = (ALERTS?.alerts || []).filter(a => a.project_id === pid && !a.read);
  const hero = cur ? `<section class="hero"><small>Current task${mis.lead ? ` · ${esc(mis.lead)}` : ""}</small>
        <h2>${esc(cur.title)}</h2><p>${esc(cur.done_when || cur.why || "")}</p>
        <div class="row"><a class="btn" href="#/p/${pid}/plans">Open task</a><span class="muted small">${(mis.steps || []).filter(x => x.status === "done").length} tasks completed${cur.time ? ` · est. ${esc(cur.time)}` : ""}</span></div></section>`
    : "";
  pane.innerHTML = `
    <div class="goalline"><span class="muted">Goal:</span> ${esc(p.goal) || `<span class="muted">not set yet</span>`} <a class="btn link small" href="#/p/${pid}/settings">Edit</a></div>
    ${hero}
    <h2 class="tasks-h">What do you need?</h2>
    <div class="tasks">${TASKS.map(([k, t, d, ph]) => `<form class="task" data-task="${k}"><b>${t}</b><span>${d}</span>
      ${ph ? `<div class="row"><input class="grow" type="text" name="q" required placeholder="${esc(ph)}" value="${k === "measures" ? esc(p.name) : ""}" aria-label="${t}">
        <button class="btn small">Go</button></div>` : `<div class="row"><a class="btn ghost small" href="#/p/${pid}/news">Open News</a><a class="btn link small" href="#/p/${pid}/alerts">Alerts</a></div>`}</form>`).join("")}</div>
    <div class="ov-grid"><div class="stack">
      <section class="card"><div class="card-h"><h2>Alerts</h2><a class="btn link small" href="#/p/${pid}/alerts">${unread.length ? "See all" : "Open"}</a></div>
        ${unread.length ? unread.slice(0, 3).map(a => `<div class="mini-alert ${a.level}"><span>${a.level === "breakthrough" ? "Possible breakthrough" : "Notable"}</span><b>${esc(a.title)}</b></div>`).join("")
          : `<p class="muted">Nothing new. Medr keeps checking your topics for breakthroughs in the background.</p>`}</section>
      <section class="card"><div class="card-h"><h2>Ideas in progress</h2><a class="btn link small" href="#/p/${pid}/board">${ideas.length ? "Open" : ""}</a></div>
        ${ideas.length ? `<div class="mini-ideas">${ideas.slice(0, 4).map(i => `<div class="mini"><span class="pill st-${i.stage || "lead"}">${STAGE_NAMES[i.stage || "lead"]}</span>
          <b>${esc(i.title)}</b></div>`).join("")}</div>`
          : `<p class="muted">Ideas you build solutions for (or save for later) show up here.</p>`}</section>
    </div><div class="stack">
      <section class="card"><div class="card-h"><h2>Recent activity</h2><a class="btn link small" href="#/p/${pid}/activity">All</a></div>
        ${p.events.length ? `<ul class="feed-mini">${p.events.slice(0, 6).map(e => `<li><span>${esc(e.text)}</span><time>${ago(e.created)}</time></li>`).join("")}</ul>`
          : `<p class="muted">Everything you do in this project shows up here.</p>`}</section>
    </div></div>`;
  $$(".task[data-task]", pane).forEach(f => f.onsubmit = e => { e.preventDefault(); const t = f.dataset.task, v = f.q.value.trim();
    const run = t === "ideas" ? { condition: v, angle: "any" } : t === "search" ? { q: v, mode: "papers" } : t === "measures" ? { population: v, purpose: "both" } : { topic: v };
    location.hash = `#/p/${pid}/${t}?run=${encodeURIComponent(JSON.stringify(run))}`; });
}

// ---------------- Board ----------------
function boardTab(pane, pid, p) {
  const ideas = p.items.filter(i => i.kind === "idea");
  const order = Object.keys(STAGE_NAMES);
  pane.innerHTML = `<p class="sub" style="margin-top:0">Every idea you're building or saved for later, by stage. Drag a card to move it, or use the arrows. Every move is saved and logged.</p>
    ${ideas.length ? "" : `<div class="empty card">No ideas in your pipeline yet. In <a href="#/p/${pid}/ideas">Idea Lab</a>, press Build a solution on an idea and it lands in Lead.</div>`}
    <div class="board">${order.map(stg => `<section class="col" data-stage="${stg}"><header><span class="pill st-${stg}">${STAGE_NAMES[stg]}</span>
      <span class="muted small" data-count="${stg}"></span></header><div class="lane" data-stage="${stg}"></div></section>`).join("")}</div>`;
  const draw = () => {
    for (const stg of order) {
      const list = ideas.filter(i => (i.stage || "lead") === stg);
      $(`[data-count="${stg}"]`, pane).textContent = list.length || "";
      $(`.lane[data-stage="${stg}"]`, pane).innerHTML = list.map(i => {
        const k = order.indexOf(stg), d = i.data;
        return `<article class="bcard" draggable="true" data-id="${i.id}">
          <div class="bc-top"><span class="badge type">${esc(d.type || "Idea")}</span>${d.grade ? `<span class="bc-grade g${d.grade}" title="Confidence: ${GRADE[d.grade]}">${d.grade}</span>` : ""}</div>
          <b>${esc(i.title)}</b>${d.plain ? `<div class="small" style="margin:3px 0">${esc(d.plain)}</div>` : ""}<div class="muted small">${esc(d.condition || "")}${d.intervention ? `: ${esc(d.intervention)}` : ""}</div>
          <details><summary>Details</summary><div class="small">${md(d.rationale || "")}${(d.test_plan || []).length ? `<b>How to pursue it</b><ol>${d.test_plan.slice(0, 3).map(t => `<li>${esc(t)}</li>`).join("")}</ol>` : ""}</div></details>
          <div class="bc-move">${k > 0 ? `<button class="btn link small" data-mv="${order[k - 1]}" aria-label="Move to ${STAGE_NAMES[order[k - 1]]}">← ${STAGE_NAMES[order[k - 1]]}</button>` : "<span></span>"}
            ${k < order.length - 1 ? `<button class="btn link small" data-mv="${order[k + 1]}" aria-label="Move to ${STAGE_NAMES[order[k + 1]]}">${STAGE_NAMES[order[k + 1]]} →</button>` : ""}</div></article>`;
      }).join("") || `<div class="drop-empty">Drop here</div>`;
    }
    $$(".bcard", pane).forEach(c => {
      c.ondragstart = e => { e.dataTransfer.setData("text/plain", c.dataset.id); c.classList.add("dragging"); };
      c.ondragend = () => c.classList.remove("dragging");
      $$("[data-mv]", c).forEach(b => b.onclick = () => move(+c.dataset.id, b.dataset.mv));
    });
  };
  const move = async (id, stage) => {
    const it = ideas.find(i => i.id === id); if (!it || (it.stage || "lead") === stage) return;
    const before = it.stage; it.stage = stage; draw();
    try { await api(`/api/items/${id}`, { method: "PATCH", json: { stage } }); toast(`Moved to ${STAGE_NAMES[stage]}`); }
    catch (e) { it.stage = before; draw(); toast(e.message, true); }
  };
  $$(".lane", pane).forEach(d => {
    d.ondragover = e => { e.preventDefault(); d.classList.add("over"); };
    d.ondragleave = () => d.classList.remove("over");
    d.ondrop = e => { e.preventDefault(); d.classList.remove("over"); move(+e.dataTransfer.getData("text/plain"), d.dataset.stage); };
  });
  draw();
}

// ---------------- Notes ----------------
function notesTab(pane, pid, p) {
  let notes = p.items.filter(i => i.kind === "note").sort((a, b) => (b.updated || b.created) - (a.updated || a.created));
  let cur = notes[0]?.id, timer = null, preview = false;
  pane.innerHTML = `<div class="notes"><aside class="nlist"><button class="btn small" id="nn" style="width:100%">+ New note</button><div id="nl"></div></aside>
    <section class="neditor" id="ne"></section></div>`;
  const list = () => {
    $("#nl").innerHTML = notes.map(n => `<button class="nitem ${n.id === cur ? "on" : ""}" data-id="${n.id}"><b>${esc(n.title || "Untitled")}</b>
      <span>${esc((n.data.text || "").replace(/[#*`>\-]/g, "").slice(0, 70)) || "Empty note"}</span><time>${ago(n.updated || n.created)}</time></button>`).join("")
      || `<p class="muted small" style="padding:10px">No notes yet.</p>`;
    $$(".nitem").forEach(b => b.onclick = () => { cur = +b.dataset.id; list(); editor(); });
  };
  const editor = () => {
    const n = notes.find(x => x.id === cur);
    if (!n) { $("#ne").innerHTML = `<div class="empty">Write down mentor contacts, ideas, things to check. Notes save as you type, and Ask can read them.</div>`; return; }
    $("#ne").innerHTML = `<div class="ne-bar"><input id="nt" type="text" value="${esc(n.title)}" aria-label="Note title" placeholder="Title">
        <span class="muted small" id="ns">Saved</span><button class="btn ghost small" id="np">${preview ? "Edit" : "Preview"}</button>
        <button class="btn link small" id="nd">Delete</button></div>
      ${preview ? `<div class="md npreview">${md(n.data.text || "")}</div>` : `<textarea id="nx" aria-label="Note text" placeholder="Start writing… (markdown works: ## headings, - lists, **bold**)">${esc(n.data.text || "")}</textarea>`}`;
    const save = () => { clearTimeout(timer); $("#ns").textContent = "Saving…";
      timer = setTimeout(async () => {
        try { const r = await api(`/api/items/${n.id}`, { method: "PATCH", json: { title: n.title, data: { text: n.data.text } } });
          n.updated = r.updated; $("#ns").textContent = "Saved"; list(); }
        catch (e) { $("#ns").textContent = "Couldn't save"; toast(e.message, true); }
      }, 600); };
    $("#nt").oninput = e => { n.title = e.target.value; save(); };
    const tx = $("#nx"); if (tx) { tx.oninput = e => { n.data.text = e.target.value; save(); }; tx.focus(); }
    $("#np").onclick = () => { preview = !preview; editor(); };
    $("#nd").onclick = async () => { if (!confirm("Delete this note?")) return; await api(`/api/items/${n.id}`, { method: "DELETE" });
      notes = notes.filter(x => x.id !== n.id); cur = notes[0]?.id; list(); editor(); };
  };
  $("#nn").onclick = async () => {
    const r = await api(`/api/projects/${pid}/items`, { json: { kind: "note", title: "Untitled note", data: { text: "" } } });
    notes.unshift(r); cur = r.id; preview = false; list(); editor(); $("#nt").select();
  };
  list(); editor();
}

// ---------------- Trials ----------------
function trialsTab(pane, pid, p) {
  const trials = p.items.filter(i => i.kind === "trial");
  pane.innerHTML = `<div class="row" style="margin-bottom:14px"><p class="sub grow" style="margin:0">Trials you've saved from Search. Check for updates to pull the latest status, phase and enrollment from ClinicalTrials.gov.</p>
      ${trials.length ? `<button class="btn" id="tr">Check for updates</button>` : ""}<a class="btn ghost" href="#/p/${pid}/search">Find trials</a></div>
    <div id="trc"></div>
    ${trials.length ? `<div class="tablewrap"><table class="tbl"><thead><tr><th>Trial</th><th>Status</th><th>Phase</th><th class="num">People</th><th>Sponsor</th><th>Checked</th><th></th></tr></thead>
      <tbody>${trials.map(t => { const d = t.data; return `<tr><td><a href="${esc(d.url)}" target="_blank"><b>${esc(d.title || t.title)}</b></a><div class="id">${esc(d.nct || t.ref)}</div></td>
        <td><span class="chip st-${esc(d.status)}">${esc((d.status || "").replaceAll("_", " ").toLowerCase())}</span></td>
        <td>${esc((d.phases || []).map(x => x.replace("PHASE", "Phase ").replace("EARLY_", "Early ")).join(", ") || "–")}</td>
        <td class="num">${fmt(d.enrollment)}</td><td class="small">${esc(d.sponsor || "")}</td><td class="small muted">${esc(d.checked || "–")}</td>
        <td><button class="btn link small" data-del="${t.id}">Remove</button></td></tr>`; }).join("")}</tbody></table></div>`
      : `<div class="empty card">No trials yet. Go to <a href="#/p/${pid}/search">Search</a>, pick <b>Trials</b>, and save the ones you want to follow.</div>`}`;
  const btn = $("#tr"); if (btn) btn.onclick = () => busy(btn, "Checking…", async () => {
    const r = await api(`/api/projects/${pid}/trials/refresh`, { method: "POST" });
    sessionStorage.setItem("medr_trial_changes", JSON.stringify(r)); route();
  });
  const last = sessionStorage.getItem("medr_trial_changes");
  if (last) { sessionStorage.removeItem("medr_trial_changes"); const r = JSON.parse(last);
    $("#trc").innerHTML = `<div class="notice">${r.changes.length ? `<b>${r.changes.length} trial${r.changes.length > 1 ? "s" : ""} changed:</b><ul>${r.changes.map(c =>
      `<li><b>${esc(c.nct)}</b> ${esc(c.changes.join("; "))}</li>`).join("")}</ul>` : `<b>All ${r.checked} trials checked.</b> Nothing changed since last time.`}
      ${r.failed ? ` ${r.failed} couldn't be reached; try again later.` : ""}</div>`; }
  $$("[data-del]", pane).forEach(b => b.onclick = async () => { await api(`/api/items/${b.dataset.del}`, { method: "DELETE" }); route(); });
}

// ---------------- Activity ----------------
function activityTab(pane, pid, p) {
  const days = {};
  for (const e of p.events) { const d = new Date(e.created * 1000).toLocaleDateString(undefined, { weekday: "long", month: "short", day: "numeric" }); (days[d] = days[d] || []).push(e); }
  pane.innerHTML = Object.keys(days).length ? Object.entries(days).map(([d, list]) => `<section class="day"><h3>${esc(d)}</h3><ul class="timeline">${list.map(e =>
    `<li class="ev-${esc(e.kind)}"><span class="dot"></span><span class="grow">${esc(e.text)}</span><time>${new Date(e.created * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</time></li>`).join("")}</ul></section>`).join("")
    : `<div class="empty card">Nothing yet. Runs, saves, notes, board moves and trial updates all show up here.</div>`;
}

// ---------------- Settings ----------------
function settingsTab(pane, pid, p) {
  pane.innerHTML = `<div class="stack" style="max-width:720px">
    <form class="card" id="pf"><h2>Project</h2><label for="pn2">Name</label><input id="pn2" type="text" name="name" value="${esc(p.name)}" required>
      <label for="pg2">Goal</label><textarea id="pg2" name="goal" placeholder="What do you want to find or build?">${esc(p.goal)}</textarea>
      <button class="btn" style="margin-top:12px">Save changes</button></form>
    <section class="card"><h2>Your data</h2><p class="muted">${ME.hosted ? "Your projects are private to your account. Medr backs up the server every day. You can also download a copy of this project any time." : "Everything is stored on this computer, and Medr makes an automatic backup every day (the last 14 days are kept)."}</p>
      <div class="row"><a class="btn ghost" href="/api/projects/${pid}/backup">Download backup</a><a class="btn ghost" href="/api/projects/${pid}/export">Export notes (markdown)</a>
      <label class="btn ghost"><input type="file" id="rf" accept=".json" hidden>Restore a backup…</label></div>
      <p class="hint">A backup file holds this whole project: ideas, plans, notes, saved research, board, chat and history. Restoring creates a copy, so nothing gets overwritten.</p></section>
    ${ME.hosted ? accountCard() : ""}
    ${ME.hosted && !ME.user?.admin ? "" : `<section class="card" id="speed"><h2>Speed <span class="muted small" style="font-family:var(--body);font-weight:400">(all projects)</span></h2>
      <div class="speedopt"><label class="check"><input type="radio" name="spd" value="balanced"> <b>Balanced</b></label><span class="muted small">Strongest model for ideas, plans and approaches; fast model for summaries, grading and assessments.</span></div>
      <div class="speedopt"><label class="check"><input type="radio" name="spd" value="fast"> <b>Fastest</b></label><span class="muted small">Fast model for everything. Idea Lab and plans about 2 to 3 times faster, slightly less detailed.</span></div>
      <label for="ncbi">PubMed key <span class="muted" style="font-weight:400">(optional, free)</span></label>
      <div class="row"><input id="ncbi" class="grow" type="text" autocomplete="off" placeholder="Recommended: paste your free NCBI API key (PubMed checks about 3x faster)">
        <button class="btn ghost small" id="ncbis" type="button">Save key</button></div>
      <p class="hint" id="ncbih">Get one in 2 minutes: sign in at <a href="https://account.ncbi.nlm.nih.gov/settings/" target="_blank" rel="noopener">NCBI account settings</a>, scroll to "API Key Management" and press "Create an API Key".</p></section>`}
    <section class="card danger-zone"><h2>Delete project</h2><p class="muted">Deletes this project and everything in it. Download a backup first if you might want it back.</p>
      <button class="btn danger" id="del">Delete "${esc(p.name)}"</button></section></div>`;
  if (ME.hosted) bindAccount();
  if ($("#speed")) api("/api/settings/speed").then(sp => {
    $$("[name=spd]").forEach(r => { r.checked = r.value === sp.mode; r.onchange = () => api("/api/settings/speed", { method: "PUT", json: { mode: r.value } }).then(() => toast(r.value === "fast" ? "Speed: Fastest" : "Speed: Balanced")); });
    if (sp.ncbi_key) { $("#ncbi").placeholder = "Key saved. Paste a new one to replace it, or save empty to remove"; }
    $("#ncbis").onclick = () => api("/api/settings/speed", { method: "PUT", json: { mode: $("[name=spd]:checked")?.value || "balanced", ncbi_key: $("#ncbi").value } })
      .then(r => { $("#ncbi").value = ""; toast(r.ncbi_key ? "PubMed key saved: lookups are faster now" : "PubMed key removed"); settingsTab(pane, pid, p); });
  });
  $("#pf").onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    busy($("#pf .btn"), "Saving…", async () => { await api(`/api/projects/${pid}`, { method: "PUT", json: f }); toast("Saved"); route(); }); };
  $("#rf").onchange = async e => { const file = e.target.files[0]; if (!file) return;
    try { const r = await api("/api/projects/restore", { json: JSON.parse(await file.text()) }); toast("Restored as a new project"); location.hash = `#/p/${r.id}/overview`; }
    catch (err) { toast(err.message.includes("JSON") ? "That file isn't a Medr backup" : err.message, true); } };
  $("#del").onclick = async () => {
    if (prompt(`Type the project name to delete it:\n${p.name}`) !== p.name) return toast("Not deleted");
    await api(`/api/projects/${pid}`, { method: "DELETE" }); ACTIVE = null; toast("Project deleted"); location.hash = "#/";
  };
}

boot();


// ---------------- alerts: background polling + desktop pop-ups ----------------
let ALERTS = null;
async function refreshAlerts() {
  try { ALERTS = await api("/api/alerts"); } catch (e) { return ALERTS; }
  const b = $('.nav-i[data-nav="news"]');
  if (b) { let i = $("i", b); if (!ALERTS.unread) i?.remove(); else { if (!i) { i = document.createElement("i"); b.append(i); } i.className = "hot"; i.textContent = ALERTS.unread; } }
  let seen = 0; try { seen = +localStorage.getItem("medr_alert_seen") || 0; } catch (e) {}
  const fresh = ALERTS.alerts.filter(a => a.level === "breakthrough" && !a.read && a.id > seen);
  if (fresh.length) {
    try { localStorage.setItem("medr_alert_seen", Math.max(...fresh.map(a => a.id))); } catch (e) {}
    if (seen && "Notification" in window && Notification.permission === "granted")
      fresh.slice(0, 3).forEach(a => { const n = new Notification("Medr: possible breakthrough", { body: `${a.title}\n${a.project || ""}` }); n.onclick = () => { focus(); location.hash = `#/p/${a.project_id}/alerts`; }; });
    else if (seen) toast(`Possible breakthrough: ${fresh[0].title}`);
  }
  return ALERTS;
}
setInterval(refreshAlerts, 120000);

function alertsTab(pane, pid, p) {
  const A = ALERTS || { alerts: [], settings: { on: true, hours: 12 }, last_checked: {} };
  let scope = "project";
  const topics = [...new Set([...p.watches.map(w => w.topic), ...p.items.filter(i => i.kind === "ideas").map(i => i.data.condition)].filter(Boolean))];
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  const draw = () => {
    const last = A.last_checked?.[pid];
    const list = A.alerts.filter(a => scope === "all" || a.project_id === pid);
    pane.innerHTML = `<p class="sub" style="margin-top:0">Medr quietly checks everything you research for <b>breakthroughs</b>: approvals, decisive trial results, big meta-analyses, first-ever results, and trials you saved finishing. The bar is high on purpose, so most checks find nothing. When something clears it, you get a pop-up.</p>
      <div class="al-grid"><div>
        <div class="row" style="margin-bottom:12px"><div class="seg" role="tablist"><button data-sc="project" class="${scope === "project" ? "on" : ""}">This project</button><button data-sc="all" class="${scope === "all" ? "on" : ""}">All projects</button></div>
          <div class="grow"></div>${list.some(a => !a.read) ? `<button class="btn link small" id="readall">Mark all read</button>` : ""}</div>
        ${list.length ? list.map(a => `<article class="alert ${a.level} ${a.read ? "read" : ""}" data-id="${a.id}">
            <div class="al-h"><span class="al-lv">${a.level === "breakthrough" ? "Possible breakthrough" : "Notable"}</span>
              <span class="muted small">${esc(a.topic || "")}${scope === "all" && a.project ? ` · ${esc(a.project)}` : ""}</span><span class="grow"></span><time>${ago(a.created)}</time></div>
            <h3><a href="${esc(a.link || "#")}" target="_blank" rel="noopener">${esc(a.title)}</a></h3>
            <p>${esc(a.body || "")}</p>
            <div class="al-f"><span class="muted small">${esc(a.source || "")}</span><span class="grow"></span>
              ${a.read ? "" : `<button class="btn link small" data-read="${a.id}">Mark read</button>`}<button class="btn link small" data-rm="${a.id}">Dismiss</button></div></article>`).join("")
          : `<div class="empty">No alerts yet. That's normal: real breakthroughs are rare. Medr keeps checking in the background${last ? `, last time ${ago(last)}` : ""}.</div>`}
      </div><aside class="stack">
        <section class="card"><div class="card-h"><h2>What's being watched</h2></div>
          ${topics.length ? `<ul class="watched">${topics.slice(0, 5).map(t => `<li>${esc(t)}</li>`).join("")}<li class="muted">Trials you saved (${p.stats?.trial || 0})</li></ul>`
            : `<p class="muted small">Run Idea Lab or add a topic in Monitor, and Medr starts watching it.</p>`}
          <p class="muted small">Last checked: ${last ? ago(last) : "not yet"}</p>
          <button class="btn small" id="checknow">Check now</button></section>
        <section class="card"><div class="card-h"><h2>Settings</h2></div>
          ${ME.hosted && !ME.user?.admin ? `<p class="muted small" style="margin:0">Medr checks your topics about once a day while you're using it.</p>` : `<label class="check"><input type="checkbox" id="alon" ${A.settings.on ? "checked" : ""}> Check automatically while Medr is running</label>
          <label class="small" style="display:block;margin:10px 0 4px">How often</label>
          <select id="alh">${[[6, "Every 6 hours"], [12, "Every 12 hours"], [24, "Once a day"], [72, "Every 3 days"]].map(([v, l]) => `<option value="${v}" ${A.settings.hours == v ? "selected" : ""}>${l}</option>`).join("")}</select>`}
          <div class="popsetting">${perm === "granted" ? `<span class="pill st-investigating">Browser pop-ups on</span>`
            : perm === "denied" ? `<span class="muted small">Browser pop-ups are blocked for this page in Chrome's site settings. The tray icon still pops up alerts.</span>`
            : perm === "unsupported" ? "" : `<button class="btn ghost small" id="perm">Turn on browser pop-ups</button>`}
            ${ME.hosted ? "" : `<p class="muted small" style="margin:8px 0 0">The Medr tray icon by your clock also pops up breakthroughs, even with this tab closed.</p>`}</div></section>
      </aside></div>`;
    $$("[data-sc]", pane).forEach(b => b.onclick = () => { scope = b.dataset.sc; draw(); });
    $$("[data-read]", pane).forEach(b => b.onclick = async () => { await api(`/api/alerts/${b.dataset.read}/read`, { method: "POST" }); A.alerts.find(a => a.id == b.dataset.read).read = 1; await refreshAlerts(); draw(); });
    $$("[data-rm]", pane).forEach(b => b.onclick = async () => { await api(`/api/alerts/${b.dataset.rm}`, { method: "DELETE" }); A.alerts = A.alerts.filter(a => a.id != b.dataset.rm); await refreshAlerts(); draw(); });
    const ra = $("#readall"); if (ra) ra.onclick = async () => { await api("/api/alerts/read_all", { method: "POST" }); A.alerts.forEach(a => a.read = 1); await refreshAlerts(); draw(); };
    $("#checknow").onclick = e => busy(e.currentTarget, "Checking… about a minute", async () => {
      const r = await api("/api/alerts/check", { json: { pid } });
      toast(r.breakthroughs ? `${r.breakthroughs} possible breakthrough${r.breakthroughs > 1 ? "s" : ""} found` : r.new ? `${r.new} notable update${r.new > 1 ? "s" : ""}, no breakthroughs` : "Checked: nothing breakthrough-level right now");
      Object.assign(A, await refreshAlerts()); draw(); });
    const save = () => api("/api/settings/alerts", { method: "PUT", json: { on: $("#alon").checked, hours: +$("#alh").value } }).then(s => { A.settings = s; toast("Alert settings saved"); });
    if ($("#alon")) { $("#alon").onchange = save; $("#alh").onchange = save; }
    const pb = $("#perm"); if (pb) pb.onclick = () => Notification.requestPermission().then(() => alertsTab(pane, pid, p));
  };
  draw();
}

// ---------------- News ----------------
const NEWS_CAT = ["Approvals & policy", "Clinical trials", "Research", "Biotech & pharma", "Public health", "AI & tech"];
function newsWhen(t) { return t ? ago(t) : ""; }
function newsTab(pane, pid, p) {
  const savedRefs = new Set(p.items.filter(i => i.kind === "news").map(i => i.ref));
  let N = null, group = "All";
  const saveBtn = it => savedRefs.has(it.id) ? `<button class="btn small saved" disabled>Saved</button>` : `<button class="btn ghost small" data-nsave="${it.id}">Save</button>`;
  const row = it => `<li class="nrow"><div class="grow"><a href="${esc(it.link)}" target="_blank" rel="noopener">${esc(it.title)}</a>
      <div class="m">${esc(it.source)}${it.date ? ` · ${newsWhen(it.date)}` : ""}${it.topic ? ` · <span class="chip soft">${esc(it.topic)}</span>` : ""}</div></div>${saveBtn(it)}</li>`;
  pane.innerHTML = `<div id="nhead"></div><div id="ntop"><div class="working"><span class="pulse"></span>Gathering the latest from ${13} health and biomed sources, then picking the biggest stories…</div></div>
    <div class="news-grid"><section><div class="card-h"><h2>Latest</h2><div class="seg small" id="ngroups"></div></div><ul class="nlist" id="nlatest"></ul></section>
    <aside><div class="card-h"><h2>For this project</h2></div><div id="nproj"><div class="working small"><span class="pulse"></span>Finding news on your topics…</div></div></aside></div>`;
  const bindSave = () => $$("[data-nsave]", pane).forEach(b => b.onclick = async () => {
    const all = [...(N?.items || []), ...(N?.top || []), ...(PN?.items || [])]; const it = all.find(x => x.id === b.dataset.nsave);
    await api(`/api/projects/${pid}/items`, { json: { kind: "news", ref: it.id, title: it.title, data: { url: it.link, source: it.source, date: it.date, summary: it.summary } } }).catch(e => toast(e.message, true));
    savedRefs.add(it.id); $$(`[data-nsave="${it.id}"]`, pane).forEach(x => x.outerHTML = `<button class="btn small saved" disabled>Saved</button>`); toast("Saved to Library"); });
  const drawLatest = () => {
    const groups = ["All", ...new Set(N.items.map(i => i.group))];
    $("#ngroups").innerHTML = groups.map(g => `<button data-g="${esc(g)}" class="${g === group ? "on" : ""}">${esc(g)}</button>`).join("");
    $$("#ngroups button").forEach(b => b.onclick = () => { group = b.dataset.g; drawLatest(); });
    const list = N.items.filter(i => group === "All" || i.group === group).slice(0, 60);
    $("#nlatest").innerHTML = list.map(row).join("") || `<li class="muted">Nothing from these sources right now.</li>`; bindSave();
  };
  let PN = null;
  const load = async (refresh) => {
    N = await api("/api/news" + (refresh ? "?refresh=1" : ""));
    const failed = N.status.filter(s => !s.ok);
    $("#nhead").innerHTML = `<div class="nhead"><div class="grow"><p class="sub" style="margin:0">The biggest health, medicine and biomed stories of the week, picked and explained by Medr, plus the full feed and news on your project.</p>
        <span class="muted small">Updated ${newsWhen(N.fetched)} from ${N.sources_ok} of ${N.sources} sources${failed.length ? ` <abbr title="Didn't answer: ${esc(failed.map(f => f.name).join(", "))}">(${failed.length} didn't answer)</abbr>` : ""}</span></div>
      <button class="btn ghost small" id="nref">Refresh</button></div>`;
    $("#nref").onclick = e => busy(e.currentTarget, "Refreshing…", () => load(true));
    $("#ntop").innerHTML = N.top.length ? `${N.digest ? `<div class="ndigest"><b>This week</b><p>${esc(N.digest)}</p></div>` : ""}
      <ol class="topnews">${N.top.map((t, k) => `<li class="tstory ${k === 0 ? "lead" : ""}"><span class="rank num">${k + 1}</span><div class="grow">
        <div class="tmeta"><span class="cat c${NEWS_CAT.indexOf(t.category)}">${esc(t.category || "News")}</span>${t.importance >= 5 ? `<span class="chip exact">Historic</span>` : t.importance >= 4 ? `<span class="chip soft">Major</span>` : ""}</div>
        <h3><a href="${esc(t.link)}" target="_blank" rel="noopener">${esc(t.title)}</a></h3><p class="why">${esc(t.why || "")}</p>
        <div class="m">${esc(t.source)}${t.date ? ` · ${newsWhen(t.date)}` : ""}${t.also?.length ? ` · also: ${t.also.map(a => `<a href="${esc(a.link)}" target="_blank" rel="noopener">${esc(a.source)}</a>`).join(", ")}` : ""}</div></div>${saveBtn(t)}</li>`).join("")}</ol>`
      : N.items.length ? `<div class="empty">Couldn't rank today's stories (the AI didn't answer). The full feed is below.</div>`
      : `<div class="empty">None of the news sources answered. Check your internet connection, then press Refresh.</div>`;
    if (N.items.length) drawLatest(); else $("#nlatest").innerHTML = "";
    bindSave();
  };
  load(false).catch(e => { $("#ntop").innerHTML = `<div class="empty">${esc(e.message)}</div>`; });
  api(`/api/news/project/${pid}`).then(r => { PN = r;
    $("#nproj").innerHTML = (r.topics.length ? `<p class="muted small" style="margin:0 0 8px">Topics: ${r.topics.map(esc).join(", ")}</p>` : "")
      + (r.items.length ? `<ul class="nlist compact">${r.items.slice(0, 20).map(row).join("")}</ul>` : `<p class="muted small">No recent news on your topics. Breakthroughs will show up in Alerts.</p>`);
    bindSave(); }).catch(e => $("#nproj").innerHTML = `<p class="muted small">${esc(e.message)}</p>`);
}


// ======================= hosted mode: accounts, feedback, admin =======================
async function boot() {
  try { const r = await fetch("/api/me"); if (r.ok) ME = await r.json(); } catch (e) {}
  await loadThemes();
  if (ME.hosted && !ME.user) return authView(location.hash.includes("signup") ? "up" : "in");
  route();
}

const FIELDS = ["Medicine", "Nursing", "Behavioral health", "Public health", "Research or lab", "Engineering", "Student, health", "Student, other", "Other"];

function authView(mode = "in") {
  const up = mode === "up";
  app.innerHTML = `<div class="plain"><header class="plainbar"><a class="brand" href="#/">${logo}<b>Medr</b></a><div class="grow"></div>
      <button class="btn ghost small" id="themeb2">Theme</button></header>
    <div class="wrap auth-wrap">
      <section class="auth-intro">
        <h1>The research behind better care, in minutes.</h1>
        <p class="lede">Describe a patient group, a question or an idea. Medr finds the validated assessments, the latest evidence and new approaches, in plain language with every source linked.</p>
        <p class="muted small">Medr is in early testing. Your feedback shapes what gets built next.</p>
      </section>
      <form class="auth" id="af" novalidate>
        <div class="auth-tabs" role="tablist"><button type="button" role="tab" aria-selected="${!up}" class="${up ? "" : "on"}" data-mode="in">Sign in</button>
          <button type="button" role="tab" aria-selected="${up}" class="${up ? "on" : ""}" data-mode="up">Create account</button></div>
        <label for="ae">Email</label><input id="ae" name="email" type="email" autocomplete="email" required>
        <label for="ap">Password</label><input id="ap" name="password" type="password" autocomplete="${up ? "new-password" : "current-password"}" minlength="8" required>
        ${up ? `<p class="hint" style="margin-top:4px">At least 8 characters.</p>
        <label for="an">Name <span class="opt">optional</span></label><input id="an" name="name" type="text" autocomplete="name">
        <label for="af2">Your field <span class="opt">optional</span></label>
        <select id="af2" name="field"><option value="">Choose one</option>${FIELDS.map(f => `<option>${f}</option>`).join("")}</select>
        ${ME.invite ? `<label for="ai">Invite code</label><input id="ai" name="invite" type="text" autocomplete="off" required>` : ""}
        <div class="notice"><b>Before you start</b>
          <p>Medr only searches public research: PubMed, ClinicalTrials.gov and preprints. Don't enter patient names, records, dates of birth or anything else that could identify a person.</p>
          <p>Medr is a research aid, not medical advice. Check the linked sources before acting on anything.</p>
          <p>During testing, Caiden can view projects to see how Medr is working and improve it.</p>
          <label class="check"><input type="checkbox" name="agree" required> I understand, and I won't enter patient information.</label></div>` : ""}
        <p class="auth-err" id="aerr" role="alert" hidden></p>
        <button class="btn" type="submit">${up ? "Create account" : "Sign in"}</button>
        ${up ? "" : `<p class="muted small">Forgot your password? Message Caiden and he'll reset it.</p>`}
      </form>
    </div></div>`;
  $("#themeb2").onclick = themePicker;
  $$("[data-mode]").forEach(b => b.onclick = () => authView(b.dataset.mode));
  $("#af").onsubmit = e => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target)), err = $("#aerr");
    f.agree = !!f.agree; err.hidden = true;
    if (up && !f.agree) { err.textContent = "Please tick the box to confirm you won't enter patient information."; err.hidden = false; return; }
    busy($("#af .btn"), up ? "Creating…" : "Signing in…", async () => {
      try { await api(up ? "/api/auth/signup" : "/api/auth/login", { json: f }); }
      catch (x) { err.textContent = x.message; err.hidden = false; return; }
      ME = await api("/api/me");
      api("/api/settings").then(s => { if (s.theme && s.theme !== THEME) applyTheme(s.theme); }).catch(() => {});
      if (location.hash === "#/" || !location.hash) route(); else location.hash = "#/";
    });
  };
  ($("#ae")).focus();
}

async function signOut() {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  ME.user = null; PROJECTS = []; ACTIVE = null; authView("in");
}

function hostedFoot() {
  const u = ME.usage || {}, left = Math.max(0, (u.runs_cap || 0) - (u.runs || 0));
  return `<p class="side-note">Public research only. Don't enter patient information.</p>
    ${ME.user?.admin ? "" : `<p class="side-use">${left} of ${u.runs_cap} deep runs left today</p>`}`;
}

function feedbackDialog() {
  const m = document.createElement("div"); m.className = "modal";
  m.innerHTML = `<form class="panel fbp"><div class="row"><h2 class="grow" style="margin:0">Send feedback</h2><button type="button" class="btn link small" data-close>Close</button></div>
    <p class="hint" style="margin:4px 0 14px">Goes straight to Caiden. Anything helps: something broken, a confusing screen, a result that missed, an idea.</p>
    <div class="fbk" role="radiogroup" aria-label="Type">${[["bug", "Something broke"], ["confusing", "Confusing"], ["idea", "Idea"], ["other", "Other"]].map(([k, l], i) =>
      `<label><input type="radio" name="kind" value="${k}" ${i ? "" : "checked"}><span>${l}</span></label>`).join("")}</div>
    <textarea name="text" rows="6" required aria-label="Feedback" placeholder="What happened, or what would make Medr better?"></textarea>
    <p class="muted small" style="margin:6px 0 12px">Please don't include patient information.</p>
    <div class="row"><span class="grow"></span><button class="btn">Send feedback</button></div></form>`;
  m.onclick = e => { if (e.target === m || e.target.closest("[data-close]")) m.remove(); };
  document.body.append(m); $("textarea", m).focus();
  $("form", m).onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    busy($(".btn:not(.link)", m), "Sending…", async () => { await api("/api/feedback", { json: { ...f, page: location.hash } }); m.remove(); toast("Feedback sent. Thank you!"); }); };
}

function accountCard() {
  return `<section class="card" id="acct"><h2>Account</h2>
    <p class="muted">Signed in as <b>${esc(ME.user.email)}</b>${ME.user.field ? `, ${esc(ME.user.field)}` : ""}.</p>
    <div class="row"><button class="btn ghost" id="out" type="button">Sign out</button></div>
    <details class="acct-d"><summary>Change password</summary>
      <form id="pwf"><label for="pw0">Current password</label><input id="pw0" name="old" type="password" autocomplete="current-password" required>
        <label for="pw1">New password</label><input id="pw1" name="new" type="password" autocomplete="new-password" minlength="8" required>
        <button class="btn small" style="margin-top:10px">Change password</button></form></details>
    <details class="acct-d"><summary>Delete my account</summary>
      <p class="muted small">Deletes your account and every project in it. This can't be undone.</p>
      <form id="delf" class="row"><input name="password" type="password" autocomplete="current-password" placeholder="Your password" aria-label="Your password" required>
        <button class="btn danger small">Delete my account</button></form></details></section>`;
}
function bindAccount() {
  $("#out").onclick = signOut;
  $("#pwf").onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    busy($("#pwf .btn"), "Saving…", async () => { await api("/api/account/password", { json: f }); e.target.reset(); toast("Password changed"); }); };
  $("#delf").onsubmit = e => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    if (!confirm("Delete your account and all your projects for good?")) return;
    busy($("#delf .btn"), "Deleting…", async () => { await api("/api/account/delete", { json: f }); ME.user = null; authView("up"); toast("Account deleted"); }); };
}

const FEATURE = { "/api/projects/#/ideas": "Idea Lab", "/api/projects/#/measures": "Assessment Finder", "/api/projects/#/plan": "Research plan",
  "/api/projects/#/solution": "Build a solution", "/api/search/papers": "Search: papers", "/api/search/trials": "Search: trials",
  "/api/search/preprints": "Search: preprints", "/api/search/explain": "Study summaries", "/api/projects/#/ask": "Ask",
  "/api/items/#/mission/done": "Plan: task done", "/api/items/#/mission/help": "Plan: guidance", "/api/items/#/mission/swap": "Plan: alternative",
  "/api/items/#/mission/start": "Plan: start", "/api/projects/#/brief": "Evidence brief", "/api/projects/#/gaps": "Opportunities",
  "/api/projects/#/radar": "Trends", "/api/projects/#/bridge": "Connections", "/api/search/ideas": "Ideas from search", "/api/alerts/check": "Alert check" };
const GOAL = 200;

async function adminView() {
  const d = await api("/api/admin/overview");
  const wk = d.weeks[0], day = t => t ? new Date(t * 1000).toLocaleDateString([], { month: "short", day: "numeric" }) : "never";
  const open = d.feedback.filter(f => !f.resolved).length;
  app.innerHTML = `<div class="plain"><header class="plainbar"><a class="brand" href="#/">${logo}<b>Medr</b></a><div class="grow"></div>
      <a class="btn link small" href="#/">Back to projects</a></header>
    <div class="wrap admin">
      <h1>Testing</h1>
      <div class="goal"><div class="goal-t"><b>${d.total_users}</b> of ${GOAL} users <span class="muted">· goal for mid-February</span></div>
        <div class="goal-bar"><i style="width:${Math.min(100, d.total_users / GOAL * 100)}%"></i></div></div>
      <p class="admin-sum">This week: <b>${wk.signups}</b> new sign-up${wk.signups === 1 ? "" : "s"}, <b>${wk.active}</b> active user${wk.active === 1 ? "" : "s"}, <b>${wk.runs}</b> deep runs.
        Today: <b>${d.today_runs}</b> of ${d.limits.global_runs} deep runs allowed across everyone. Each person gets ${d.limits.runs} deep runs and ${d.limits.actions} searches a day.</p>

      <h2>Feedback ${open ? `<span class="muted">${open} open</span>` : ""}</h2>
      ${d.feedback.length ? `<ul class="fb-list">${d.feedback.map(f => `<li class="${f.resolved ? "done" : ""}">
          <div class="fb-h"><span class="fb-k k-${esc(f.kind)}">${esc({ bug: "Something broke", confusing: "Confusing", idea: "Idea", other: "Other" }[f.kind] || f.kind)}</span>
            <span class="muted small">${esc(f.email || "deleted user")} · ${day(f.created)}${f.page ? ` · ${esc(f.page)}` : ""}</span><span class="grow"></span>
            <button class="btn link small" data-res="${f.id}">${f.resolved ? "Reopen" : "Mark handled"}</button></div>
          <p>${esc(f.text)}</p></li>`).join("")}</ul>` : `<p class="muted">No feedback yet.</p>`}

      <div class="admin-cols">
        <section><h2>By week</h2><table class="tbl"><thead><tr><th>Week ending</th><th>Sign-ups</th><th>Active</th><th>Deep runs</th></tr></thead>
          <tbody>${d.weeks.map(w => `<tr><td>${day(w.start + 7 * 86400)}</td><td>${w.signups}</td><td>${w.active}</td><td>${w.runs}</td></tr>`).join("")}</tbody></table></section>
        <section><h2>Most used this week</h2>${d.popular.length ? `<table class="tbl"><tbody>${d.popular.map(x => `<tr><td>${esc(FEATURE[x.path] || x.path)}</td><td>${x.n}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">Nothing yet.</p>`}
          <h2>Fields</h2><table class="tbl"><tbody>${d.fields.map(x => `<tr><td>${esc(x.field)}</td><td>${x.n}</td></tr>`).join("")}</tbody></table></section>
      </div>

      <h2>People</h2>
      <div class="tbl-wrap"><table class="tbl"><thead><tr><th>Email</th><th>Field</th><th>Joined</th><th>Last seen</th><th>Projects</th><th>Uses this week</th><th></th></tr></thead>
        <tbody>${d.users.map(u => `<tr class="${u.disabled ? "off" : ""}"><td>${esc(u.email)}${u.name ? `<br><span class="muted small">${esc(u.name)}</span>` : ""}${u.admin ? ` <span class="muted small">admin</span>` : ""}</td>
          <td>${esc(u.field || "")}</td><td>${day(u.created)}</td><td>${u.last_seen ? ago(u.last_seen) : "never"}</td><td>${u.projects ? `<button class="btn link small" data-projs="${u.id}">${u.projects} · view</button>` : "0"}</td><td>${u.uses_week}</td>
          <td class="nowrap"><button class="btn link small" data-reset="${u.id}" data-em="${esc(u.email)}">Reset password</button>${u.admin ? "" : `<button class="btn link small" data-off="${u.id}">${u.disabled ? "Turn on" : "Turn off"}</button>`}</td></tr>`).join("")}</tbody></table></div>
    </div></div>`;
  $$("[data-projs]").forEach(b => b.onclick = () => userProjects(+b.dataset.projs));
  $$("[data-res]").forEach(b => b.onclick = async () => { await api(`/api/admin/feedback/${b.dataset.res}/resolve`, { method: "POST" }); adminView(); });
  $$("[data-off]").forEach(b => b.onclick = async () => { await api(`/api/admin/users/${b.dataset.off}/toggle`, { method: "POST" }); adminView(); });
  $$("[data-reset]").forEach(b => b.onclick = async () => {
    if (!confirm(`Reset the password for ${b.dataset.em}?`)) return;
    const r = await api(`/api/admin/users/${b.dataset.reset}/reset`, { method: "POST" });
    prompt(`Send this temporary password to ${b.dataset.em}. They can change it in Settings.`, r.temp_password); });
}

async function userProjects(id) {
  const d = await api(`/api/admin/users/${id}/projects`);
  const m = document.createElement("div"); m.className = "modal";
  m.innerHTML = `<div class="panel up-panel"><div class="row"><h2 class="grow" style="margin:0">${esc(d.user.email)}</h2><button class="btn link small" data-close>Close</button></div>
    <p class="hint" style="margin:4px 0 12px">Opens in view-only mode. You can't change anything in their projects.</p>
    <ul class="up-list">${d.projects.map(p => `<li><a href="#/p/${p.id}/overview"><b>${esc(p.name)}</b></a>
      <span class="muted small">${p.items} saved · last active ${p.last_activity ? ago(p.last_activity) : "never"}</span>
      ${p.goal ? `<p class="muted small">${esc(p.goal)}</p>` : ""}</li>`).join("")}</ul></div>`;
  m.onclick = e => { if (e.target === m || e.target.closest("[data-close]") || e.target.closest("a")) m.remove(); };
  document.body.append(m);
}
