"""Medr - biomedical research assistant. Run: uvicorn app:app --port 8100 (from this folder)."""
import os, re, json, sqlite3, threading, time
from urllib.parse import quote_plus, urlencode
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai
import news as newsmod
import smart
import sources as src
import hosted
from hosted import uid

BASE = Path(__file__).resolve().parent
DATA = Path(os.getenv("MEDR_DATA", BASE / "data"))
DATA.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / "medr.db"
APP_VERSION = 11

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, goal TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT, ref TEXT, title TEXT,
  data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY, project_id INTEGER, topic TEXT, last_checked TEXT, created REAL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chats(id INTEGER PRIMARY KEY, project_id INTEGER, role TEXT, content TEXT, created REAL);
"""


def db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def q(sql, args=(), one=False):
    con = db()
    try:
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()
    return (rows[0] if rows else None) if one else rows


def ex(sql, args=()):
    con = db()
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


_c = db(); _c.executescript(SCHEMA)
_c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, project_id INTEGER, kind TEXT, text TEXT, ref TEXT, created REAL)")
_c.execute("""CREATE TABLE IF NOT EXISTS alerts(id INTEGER PRIMARY KEY, project_id INTEGER, level TEXT, topic TEXT, title TEXT,
  body TEXT, link TEXT, source TEXT, key TEXT UNIQUE, created REAL, read INTEGER DEFAULT 0, popped INTEGER DEFAULT 0)""")
_cols = [r[1] for r in _c.execute("PRAGMA table_info(items)")]
if "stage" not in _cols:
    _c.execute("ALTER TABLE items ADD COLUMN stage TEXT")
if "updated" not in _cols:
    _c.execute("ALTER TABLE items ADD COLUMN updated REAL")
if not _c.execute("SELECT 1 FROM settings WHERE key = 'theme_v10'").fetchone():   # new Bam colors, once
    _c.execute("INSERT INTO settings(key, value) VALUES ('theme', 'auto') ON CONFLICT(key) DO UPDATE SET value = 'auto'")
    _c.execute("INSERT INTO settings(key, value) VALUES ('theme_v10', '1')")
_c.commit(); _c.close()


def backup_db(keep=14):
    """One automatic backup per day in data/backups (keeps the last two weeks)."""
    folder = DATA / "backups"
    folder.mkdir(exist_ok=True)
    dest = folder / f"medr-{time.strftime('%Y-%m-%d')}.db"
    if not dest.exists():
        src_con, dst_con = sqlite3.connect(DB_PATH), sqlite3.connect(dest)
        try:
            src_con.backup(dst_con)
        finally:
            src_con.close(); dst_con.close()
    for old in sorted(folder.glob("medr-*.db"))[:-keep]:
        old.unlink(missing_ok=True)


try:
    backup_db()
except Exception as e:
    print("backup failed:", e, flush=True)


def log(pid, kind, text, ref=""):
    try:
        ex("INSERT INTO events(project_id, kind, text, ref, created) VALUES (?,?,?,?,?)", (pid, kind, text, ref, time.time()))
    except Exception as e:
        print("activity log failed:", e, flush=True)
app = FastAPI(title="Medr")
hosted.install(app, q, ex, db)
HOSTED = hosted.HOSTED


def mine(alias="p"):
    """SQL filter for the signed-in user's projects (empty on the desktop app)."""
    u = uid()
    return (f" AND {alias}.user_id = ?", (u,)) if u is not None else ("", ())


def ukey(key):
    """Per-user settings key in hosted mode."""
    u = uid()
    return f"u{u}:{key}" if u is not None else key


@app.middleware("http")
async def no_stale_files(request: Request, call_next):
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.exception_handler(src.SourceError)
def source_error(request, exc):
    from fastapi.responses import JSONResponse
    print("SOURCE ERROR", request.url.path, exc, flush=True)
    return JSONResponse(status_code=502, content={"detail": f"{exc.source} didn't answer ({exc.reason}). "
                                                            "Wait a minute and try again."})


@app.exception_handler(src.requests.RequestException)
def upstream_error(request, exc):
    from fastapi.responses import JSONResponse
    print("REQUEST ERROR", request.url.path, repr(exc), flush=True)
    return JSONResponse(status_code=502, content={"detail": f"A data source didn't answer ({type(exc).__name__}). "
                                                            "Check your internet and try again."})


@app.exception_handler(Exception)
def any_error(request, exc):
    import traceback
    from fastapi.responses import JSONResponse
    print("ERROR", request.url.path, "\n" + traceback.format_exc(), flush=True)
    return JSONResponse(status_code=500, content={"detail": f"Something broke: {type(exc).__name__}: {str(exc)[:300]}"})


LOG_FILE = BASE.parent / "logs" / "medr.log"


@app.get("/api/logs")
def logs(request: Request, lines: int = 200):
    """Last lines of the app log, so problems can be checked from the browser (this computer only)."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "Local only")
    if not LOG_FILE.exists():
        return PlainTextResponse("(no log file yet)")
    return PlainTextResponse("\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))


@app.get("/api/diagnose")
def diagnose():
    """Checks each data source once. Open /api/diagnose in the browser to see what's blocked."""
    checks = {"PubMed": lambda: src.pubmed_count("asthma"),
              "Europe PMC (preprints)": lambda: src.preprints_search("asthma", n=1)[1],
              "ClinicalTrials.gov": lambda: src.trials_search(condition="asthma", n=1)[1]}
    out = {}
    for name, fn in checks.items():
        t = time.time()
        try:
            out[name] = {"ok": True, "result": fn(), "seconds": round(time.time() - t, 2)}
        except Exception as e:
            out[name] = {"ok": False, "error": str(e)[:300], "seconds": round(time.time() - t, 2)}
    out["Claude API key set"] = bool(os.getenv("ANTHROPIC_API_KEY"))
    return out


@app.post("/api/shutdown")
def shutdown(request: Request):
    """Lets the launcher / setup stop an older running copy so updates take effect."""
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(403, "Local only")
    threading.Timer(0.3, lambda: os._exit(0)).start()
    return {"ok": True}


@app.get("/api/version")
def version():
    return {"version": APP_VERSION, "app": "medr", "hosted": HOSTED}


# ---------------- settings (theme) ----------------
class ThemeIn(BaseModel):
    theme: str


@app.get("/api/settings")
def get_settings():
    if HOSTED:
        return {"theme": sget(ukey("theme"), "auto"), "active_project": sget(ukey("active_project"), "")}
    return {r["key"]: r["value"] for r in q("SELECT * FROM settings")} | {"theme": (q(
        "SELECT value FROM settings WHERE key = 'theme'", one=True) or {}).get("value", "auto")}


class SettingIn(BaseModel):
    value: str


@app.put("/api/settings/active_project")
def set_active(body: SettingIn):
    sset(ukey("active_project"), body.value)
    return {"ok": True}


@app.put("/api/settings/theme")
def set_theme(body: ThemeIn):
    names = set(json.loads((BASE / "static" / "themes.json").read_text(encoding="utf-8"))["themes"]) | {"auto"}
    if body.theme not in names:
        raise HTTPException(400, "Unknown theme")
    sset(ukey("theme"), body.theme)
    return {"theme": body.theme}


# ---------------- hub ----------------
@app.get("/api/hub")
def hub():
    w, a = mine()
    latest = [load_item(r) for r in q(
        "SELECT i.*, p.name AS project FROM items i JOIN projects p ON p.id = i.project_id "
        "WHERE i.kind IN ('ideas','plan','radar','bridge','gaps','brief','digest')" + w + " ORDER BY i.created DESC LIMIT 6", a)]
    for r in latest:   # the hub only needs a preview
        r["data"] = {"preview": (r["data"].get("md") or "")[:400]}
    watches = q("SELECT w.*, p.name AS project FROM watches w JOIN projects p ON p.id = w.project_id WHERE 1=1" + w + " ORDER BY w.created", a)
    plans = [load_item(r) for r in q("SELECT i.*, p.name AS project FROM items i JOIN projects p ON p.id = i.project_id "
                                     "WHERE i.kind = 'plan'" + w + " ORDER BY i.created DESC LIMIT 3", a)]
    return {"projects": list_projects(), "latest": latest, "watches": watches, "plans": plans}


# ---------------- projects ----------------
class ProjectIn(BaseModel):
    name: str
    goal: str = ""


def need_project(pid):
    p = q("SELECT * FROM projects WHERE id = ?", (pid,), one=True)
    if not p or (uid() is not None and p.get("user_id") != uid() and not hosted.admin_read()):
        raise HTTPException(404, "Project not found")
    return p


def load_item(r):
    r["data"] = json.loads(r["data"] or "{}")
    return r


@app.get("/api/projects")
def list_projects():
    return q("SELECT p.*, (SELECT COUNT(*) FROM items i WHERE i.project_id = p.id) AS items, "
             "(SELECT COUNT(*) FROM watches w WHERE w.project_id = p.id) AS watches "
             "FROM projects p WHERE 1=1" + mine()[0] + " ORDER BY created DESC", mine()[1])


@app.post("/api/projects")
def create_project(body: ProjectIn):
    if not body.name.strip():
        raise HTTPException(400, "Name the project")
    if HOSTED and q("SELECT COUNT(*) AS n FROM projects WHERE user_id = ?", (uid(),), one=True)["n"] >= 50:
        raise HTTPException(400, "You have 50 projects. Delete one to make another.")
    pid = ex("INSERT INTO projects(name, goal, created, user_id) VALUES (?,?,?,?)",
             (body.name.strip()[:200], body.goal.strip()[:2000], time.time(), uid()))
    log(pid, "project", f"Created the project")
    return {"id": pid}


@app.put("/api/projects/{pid}")
def update_project(pid: int, body: ProjectIn):
    need_project(pid)
    old = need_project(pid)
    ex("UPDATE projects SET name = ?, goal = ? WHERE id = ?", (body.name.strip(), body.goal.strip(), pid))
    if old["goal"] != body.goal.strip():
        log(pid, "project", "Updated the goal")
    if old["name"] != body.name.strip():
        log(pid, "project", f"Renamed to {body.name.strip()}")
    return {"ok": True}


@app.get("/api/projects/{pid}")
def get_project(pid: int):
    p = need_project(pid)
    p["items"] = [load_item(r) for r in q("SELECT * FROM items WHERE project_id = ? ORDER BY created DESC", (pid,))]
    p["watches"] = q("SELECT * FROM watches WHERE project_id = ? ORDER BY created", (pid,))
    p["chat"] = q("SELECT role, content FROM chats WHERE project_id = ? ORDER BY id", (pid,))
    counts = {}
    for it in p["items"]:
        counts[it["kind"]] = counts.get(it["kind"], 0) + 1
    p["stats"] = counts
    p["events"] = q("SELECT kind, text, ref, created FROM events WHERE project_id = ? ORDER BY id DESC LIMIT 60", (pid,))
    if hosted.admin_read():
        p["owner_email"] = (q("SELECT email FROM users WHERE id = ?", (p.get("user_id"),), one=True) or {}).get("email", "")
        p["read_only"] = True
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: int):
    need_project(pid)
    for t in ("items", "watches", "chats", "events", "alerts"):
        ex(f"DELETE FROM {t} WHERE project_id = ?", (pid,))
    ex("DELETE FROM projects WHERE id = ?", (pid,))
    return {"ok": True}


# ---------------- saved items ----------------
class ItemIn(BaseModel):
    kind: str          # paper, preprint, trial, note
    ref: str = ""
    title: str = ""
    data: dict = {}


ITEM_LABEL = {'paper': 'Saved a paper', 'preprint': 'Saved a preprint', 'trial': 'Saved a trial', 'note': 'Wrote a note', 'idea': 'Added an idea to the pipeline', 'ideas': 'Ran Idea Lab', 'plan': 'Wrote a research plan', 'radar': 'Ran Trends', 'bridge': 'Ran Connections', 'gaps': 'Found opportunities', 'brief': 'Wrote an evidence brief', 'digest': "Checked what's new", 'measures': 'Ran Assessment Finder', 'discover': 'Mapped connections'}


def save_item(pid, kind, ref, title, data):
    if ref and q("SELECT id FROM items WHERE project_id = ? AND kind = ? AND ref = ?", (pid, kind, ref), one=True):
        return None  # already saved
    now = time.time()
    iid = ex("INSERT INTO items(project_id, kind, ref, title, data, created, updated, stage) VALUES (?,?,?,?,?,?,?,?)",
             (pid, kind, ref, title, json.dumps(data), now, now, "lead" if kind == "idea" else None))
    log(pid, kind, f"{ITEM_LABEL.get(kind, 'Saved')}: {title}", str(iid))
    return load_item(q("SELECT * FROM items WHERE id = ?", (iid,), one=True))


@app.post("/api/projects/{pid}/items")
def add_item(pid: int, body: ItemIn):
    need_project(pid)
    if body.kind not in ("paper", "preprint", "trial", "note", "plan", "news"):
        raise HTTPException(400, "Unknown item type")
    item = save_item(pid, body.kind, body.ref, body.title, body.data)
    return item or {"duplicate": True}


@app.delete("/api/items/{iid}")
def delete_item(iid: int):
    it = q("SELECT project_id, kind, title FROM items WHERE id = ?", (iid,), one=True)
    ex("DELETE FROM items WHERE id = ?", (iid,))
    if it:
        log(it["project_id"], "delete", f"Removed: {it['title']}")
    return {"ok": True}


STAGES = {"lead": "Lead", "investigating": "Investigating", "testing": "Testing", "parked": "Parked"}


class ItemPatch(BaseModel):
    title: Optional[str] = None
    stage: Optional[str] = None
    data: Optional[dict] = None      # merged into the item's data


@app.patch("/api/items/{iid}")
def patch_item(iid: int, body: ItemPatch):
    it = q("SELECT * FROM items WHERE id = ?", (iid,), one=True)
    if not it:
        raise HTTPException(404, "Item not found")
    data = json.loads(it["data"] or "{}")
    if body.data:
        data.update(body.data)
    title = body.title if body.title is not None else it["title"]
    stage = it["stage"]
    if body.stage is not None:
        if body.stage not in STAGES:
            raise HTTPException(400, "Unknown stage")
        if body.stage != stage:
            log(it["project_id"], "stage", f"Moved {title} to {STAGES[body.stage]}", str(iid))
        stage = body.stage
    ex("UPDATE items SET title = ?, data = ?, stage = ?, updated = ? WHERE id = ?",
       (title, json.dumps(data), stage, time.time(), iid))
    return load_item(q("SELECT * FROM items WHERE id = ?", (iid,), one=True))


def saved(pid, kind):
    return [r["data"] for r in get_project(pid)["items"] if r["kind"] == kind]


# ---------------- search ----------------
@app.get("/api/search/papers")
def search_papers(request: Request, n: int = 20, min_year: Optional[int] = None, reviews: int = 0,
                  sort: str = "relevance", exact: int = 0):
    term = request.query_params.get("q", "").strip()
    if not term:
        raise HTTPException(400, "Type something to search")
    res, total, interp = smart.search_papers(term, n=min(n, 50), sort="pub_date" if sort == "new" else "relevance",
                                             min_year=min_year, reviews_only=bool(reviews), exact_words=bool(exact))
    return {"results": res, "total": total, "interp": interp}


@app.get("/api/search/preprints")
def search_preprints(request: Request, n: int = 15, exact: int = 0):
    term = request.query_params.get("q", "").strip()
    if not term:
        raise HTTPException(400, "Type something to search")
    res, total, interp = smart.search_preprints(term, n=min(n, 40), exact_words=bool(exact))
    return {"results": res, "total": total, "interp": interp}


@app.get("/api/search/trials")
def search_trials(request: Request, condition: str = "", intervention: str = "", term: str = "", status: str = "",
                  phase: str = "", n: int = 25, exact: int = 0):
    text = request.query_params.get("q", "").strip()
    statuses = [s for s in status.split(",") if s] or None
    phases = [p for p in phase.split(",") if p] or None
    if text:
        res, total, interp = smart.search_trials(text, statuses, phases, n=min(n, 100), exact_words=bool(exact))
        return {"results": res, "total": total, "interp": interp}
    if not (condition or intervention or term):
        raise HTTPException(400, "Type something to search")
    res, total = src.trials_search(condition, intervention, term, statuses, phases, n=min(n, 100))
    return {"results": res, "total": total}


class GapIn(BaseModel):
    q: str
    meaning: str = ""
    exact_total: int = 0
    papers: list = []
    pid: Optional[int] = None


@app.post("/api/search/ideas")
def gap_ideas(body: GapIn):
    papers = [p for p in body.papers if isinstance(p, dict) and p.get("pmid")][:8]
    out = ai.propose_from_gap(body.q, body.meaning, papers, body.exact_total)
    if body.pid:
        log(body.pid, "idea", f"Medr proposed {len(out.get('ideas') or [])} ideas for a gap: {body.q}")
    return out


_trend_cache = {}


def get_trend(term):
    key = term.strip().lower()                    # only the cache key is lowercased: PubMed needs AND/OR in capitals
    hit = _trend_cache.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    data = src.pubmed_trend(smart.usable_query(term.strip()))
    _trend_cache[key] = (time.time(), data)
    return data


@app.get("/api/trend")
def trend(request: Request):
    term = request.query_params.get("q", "").strip()
    if not term:
        raise HTTPException(400, "Missing topic")
    return get_trend(term)


# ---------------- AI: brief, gaps, ask ----------------
class TopicIn(BaseModel):
    topic: str


def optional(fn, empty):
    """Preprints and trials add a lot, but a run shouldn't fail if just one of them is down."""
    try:
        return fn()
    except Exception as e:
        print("optional source failed:", e, flush=True)
        return empty


def gather(topic):
    (papers, _), (preprints, _), (trials, _) = src.together(
        lambda: smart.find_papers(topic, n=15),
        lambda: optional(lambda: smart.find_preprints(topic, n=8), ([], 0)),
        lambda: optional(lambda: smart.find_trials(topic, n=15), ([], 0)))
    try:
        tr = get_trend(topic)
    except Exception:
        tr = []
    return papers, trials, preprints, tr


def sources_list(papers, trials, preprints):
    return ([{"ref": f"PMID {p['pmid']}", "title": p["title"], "url": p["url"]} for p in papers] +
            [{"ref": f"PPR {p['id']}", "title": p["title"], "url": p["url"]} for p in preprints] +
            [{"ref": t["nct"], "title": t["title"], "url": t["url"]} for t in trials])


@app.post("/api/projects/{pid}/brief")
def make_brief(pid: int, body: TopicIn):
    p = need_project(pid)
    papers, trials, preprints, tr = gather(body.topic)
    md = ai.research_brief(body.topic, papers, trials, preprints, tr, p["goal"])
    return save_item(pid, "brief", "", body.topic, {"md": md, "trend": tr, "sources": sources_list(papers, trials, preprints)})


@app.post("/api/projects/{pid}/gaps")
def make_gaps(pid: int, body: TopicIn):
    p = need_project(pid)
    papers, trials, preprints, tr = gather(body.topic)
    md = ai.find_gaps(body.topic, papers, trials, preprints, tr, p["goal"])
    return save_item(pid, "gaps", "", body.topic, {"md": md, "trend": tr, "sources": sources_list(papers, trials, preprints)})


class MeasuresIn(BaseModel):
    population: str
    purpose: str = "both"
    setting: str = ""


VALIDATION = smart.concept(["validation", "validity", "psychometric", "reliability", "sensitivity and specificity",
                            "diagnostic accuracy"], 2, "validation")
INSTRUMENT = smart.concept(["questionnaire", "scale", "screening", "instrument", "measure", "assessment"], 1, "instrument")


@app.post("/api/projects/{pid}/measures")
def find_measures(pid: int, body: MeasuresIn):
    p = need_project(pid)
    pop = body.population.strip()
    if not pop:
        raise HTTPException(400, "Describe the patient group")
    Pc = [dict(c) for c in smart.interpret(pop)["concepts"]] or smart.fallback_interp(pop)["concepts"]
    def literature():
        _, pq, _ = smart.relaxed_count([Pc, [INSTRUMENT]])
        return src.pubmed_search(pq or smart.topic_query(pop), n=14)[0]
    papers, cands = src.together(literature, lambda: ai.measure_candidates(pop, body.purpose, body.setting, []))
    if not cands:
        raise HTTPException(502, "No instruments came back. Try again or describe the group differently.")

    def evidence(m):
        Mc = [smart.concept(m.get("terms") or [m.get("abbr"), m.get("name")], 3, m.get("abbr") or m.get("name"))]
        (n_pop, q_pop, loose), (n_val, q_val, _) = src.together(
            lambda: smart.relaxed_count([Mc, Pc]), lambda: smart.relaxed_count([Mc, Pc, [VALIDATION]]))
        m.update(n_pop=n_pop, n_val=n_val, loosened=loose,
                 link="https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(q_val or q_pop or ""))
        m["key_papers"] = src.pubmed_search(q_val, n=3)[0] if n_val and q_val else []
        return m
    cands = list(src.CHECK_POOL.map(evidence, cands))
    extra = [x for m in cands for x in m.get("key_papers", [])]
    allp = list({x["pmid"]: x for x in papers + extra}.values())
    rep = ai.measures_report(pop, body.purpose, body.setting, cands, allp)
    by = {str(r.get("abbr", "")).strip().lower(): r for r in rep.get("measures", []) if isinstance(r, dict)}
    for m in cands:
        r = by.get(str(m.get("abbr", "")).strip().lower()) or by.get(str(m.get("name", "")).strip().lower()) or {}
        m.update({k: r.get(k) for k in ("fit_grade", "fit_reason", "use_for", "thresholds", "cadence", "cautions")})
        if not m.get("fit_grade"):
            m["fit_grade"] = "C" if m["n_val"] else "D"
        if m["n_val"] == 0 and m["fit_grade"] in "AB":       # can't claim validation the data doesn't show
            m["fit_grade"] = "C"
        m["key_papers"] = [{"pmid": x["pmid"], "title": x["title"], "year": x.get("year"), "url": x["url"]} for x in m.get("key_papers", [])]
    cands.sort(key=lambda m: ("ABCD".index(m["fit_grade"]), -m["n_val"], -m["n_pop"]))
    data = {"md": rep.get("summary_md", ""), "population": pop, "purpose": body.purpose, "setting": body.setting,
            "measures": cands, "gaps": rep.get("gaps") or [], "sources": sources_list(allp, [], [])}
    return save_item(pid, "measures", "", f"Assessments: {pop}", data)


class AskIn(BaseModel):
    question: str


@app.post("/api/projects/{pid}/ask")
def ask(pid: int, body: AskIn):
    p = get_project(pid)
    notes = "\n\n".join(f"## {i['title']}\n{i['data'].get('md') or i['data'].get('text', '')}"
                        for i in p["items"] if i["kind"] in ("brief", "gaps", "note", "digest", "radar", "bridge", "ideas", "plan", "measures"))
    answer = ai.ask_project(body.question, p["chat"], saved(pid, "paper"), saved(pid, "trial"),
                            saved(pid, "preprint"), notes, p["goal"])
    ex("INSERT INTO chats(project_id, role, content, created) VALUES (?,?,?,?)", (pid, "user", body.question, time.time()))
    ex("INSERT INTO chats(project_id, role, content, created) VALUES (?,?,?,?)", (pid, "assistant", answer, time.time()))
    return {"answer": answer}


@app.delete("/api/projects/{pid}/chat")
def clear_chat(pid: int):
    ex("DELETE FROM chats WHERE project_id = ?", (pid,))
    return {"ok": True}


# ---------------- watches ("what's new") ----------------
@app.post("/api/projects/{pid}/watches")
def add_watch(pid: int, body: TopicIn):
    need_project(pid)
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))   # first check covers 30 days
    wid = ex("INSERT INTO watches(project_id, topic, last_checked, created) VALUES (?,?,?,?)",
             (pid, body.topic.strip(), since, time.time()))
    log(pid, "watch", f"Started monitoring: {body.topic.strip()}")
    return {"id": wid}


@app.delete("/api/watches/{wid}")
def delete_watch(wid: int):
    ex("DELETE FROM watches WHERE id = ?", (wid,))
    return {"ok": True}


@app.post("/api/watches/{wid}/check")
def check_watch(wid: int):
    w = q("SELECT * FROM watches WHERE id = ?", (wid,), one=True)
    if not w:
        raise HTTPException(404, "Watch not found")
    since = w["last_checked"]
    (papers, _), (preprints, _), (trials, _) = src.together(
        lambda: smart.find_papers(w["topic"], n=15, since=since),
        lambda: optional(lambda: smart.find_preprints(w["topic"], n=10, since=since), ([], 0)),
        lambda: optional(lambda: smart.find_trials(w["topic"], n=10, updated_since=since), ([], 0)))
    counts = {"papers": len(papers), "preprints": len(preprints), "trials": len(trials)}
    if not any(counts.values()):
        md = f"Nothing new on **{w['topic']}** since {since}."
    else:
        md = ai.whats_new(w["topic"], papers, trials, preprints, since)
    ex("UPDATE watches SET last_checked = ? WHERE id = ?", (time.strftime("%Y-%m-%d"), wid))
    item = save_item(w["project_id"], "digest", "", f"What's new: {w['topic']} (since {since})",
                     {"md": md, "counts": counts, "sources": sources_list(papers, trials, preprints)})
    return item


# ---------------- discovery: radar ----------------
class RadarIn(BaseModel):
    field: str


def growth_of(counts):
    """last 2 complete years vs the 2 before (1.0 = flat)."""
    full = counts[:-1]           # drop the current, incomplete year
    recent, before = sum(c["count"] for c in full[-2:]), sum(c["count"] for c in full[-4:-2])
    return round((recent + 1) / (before + 1), 2)


@app.post("/api/projects/{pid}/radar")
def radar(pid: int, body: RadarIn):
    p = need_project(pid)
    field = body.field.strip()
    (papers, _), (preprints, _) = src.together(lambda: smart.find_papers(field, n=25, sort="pub_date"),
                                               lambda: optional(lambda: smart.find_preprints(field, n=25), ([], 0)))
    topics = ai.radar_candidates(field, papers, preprints)
    this = time.localtime().tm_year
    ys = list(range(this - 5, this + 1))
    for t in topics:                                   # AI-written queries are checked and loosened if they find nothing
        t["query"] = smart.usable_query(t.get("query") or t.get("name", ""))
    allc = src.pmap(lambda ty: src.pubmed_count(ty[0], ty[1], ty[1]), [(t["query"], y) for t in topics for y in ys])
    for k, t in enumerate(topics):
        t["counts"] = [{"year": y, "count": allc[k * len(ys) + j]} for j, y in enumerate(ys)]
        t["growth"] = growth_of(t["counts"])
        t["recent"] = sum(c["count"] for c in t["counts"][-3:-1])
    topics.sort(key=lambda t: t["growth"], reverse=True)
    md = ai.radar_report(field, topics, p["goal"])
    return save_item(pid, "radar", "", f"Radar: {field}",
                     {"md": md, "topics": topics, "sources": sources_list(papers, [], preprints)})


# ---------------- discovery: crossovers (A-B-C hidden links) ----------------
class BridgeIn(BaseModel):
    a: str
    b: str


@app.post("/api/projects/{pid}/bridge")
def bridge(pid: int, body: BridgeIn):
    p = need_project(pid)
    a, b = body.a.strip(), body.b.strip()
    if not a or not b:
        raise HTTPException(400, "Enter both topics")
    qa, qb = src.together(lambda: smart.topic_query(a), lambda: smart.topic_query(b))
    A, B = f"({qa})", f"({qb})"
    this = time.localtime().tm_year
    ca, cb, cab, cabr, (papers_a, _), (papers_b, _), (papers_ab, _) = src.together(
        lambda: src.pubmed_count(A), lambda: src.pubmed_count(B), lambda: src.pubmed_count(f"{A} AND {B}"),
        lambda: src.pubmed_count(f"{A} AND {B}", this - 2, this), lambda: src.pubmed_search(qa, n=12),
        lambda: src.pubmed_search(qb, n=12), lambda: src.pubmed_search(f"{A} AND {B}", n=8))
    counts = {"a": ca, "b": cb, "ab": cab, "ab_recent": cabr}
    links = []
    cands = ai.bridge_candidates(a, b, papers_a, papers_b)
    for c in cands:
        c["query"] = smart.usable_query(c.get("query") or c.get("name", ""))
    qs = [q_ for c in cands for q_ in (f"{A} AND ({c['query']})", f"{B} AND ({c['query']})", f"{A} AND {B} AND ({c['query']})")]
    got = src.pmap(src.pubmed_count, qs)                 # all candidate checks at once
    for k, c in enumerate(cands):
        ac, bc, abc = got[3 * k: 3 * k + 3]
        # strong on both sides, rarely together = interesting
        score = round((min(ac, bc) ** 0.5) / (abc + 1), 2)
        links.append({"name": c["name"], "query": c["query"], "ac": ac, "bc": bc, "abc": abc, "score": score})
    links.sort(key=lambda l: l["score"], reverse=True)
    md = ai.bridge_report(a, b, counts, links, papers_ab, p["goal"])
    return save_item(pid, "bridge", "", f"Crossover: {a} × {b}",
                     {"md": md, "a": a, "b": b, "counts": counts, "links": links,
                      "sources": sources_list(papers_ab, [], [])})


# ---------------- Discover: the connection map ----------------
class DiscoverIn(BaseModel):
    goal: str
    as_of: int | None = None


STRONG, MODERATE, WEAK = 100, 20, 5


def link_level(n):
    return "strong" if n >= STRONG else "moderate" if n >= MODERATE else "weak" if n >= WEAK else "none"


def link_status(ac, n_a, n_c, best):
    """Already studied is relative: a small field with 40 papers on it is studied; a giant one isn't."""
    share = ac / max(1, min(n_a, n_c))
    if ac >= 300 or (ac >= 50 and share >= 0.05):
        return "known"
    if ac >= 30 or (ac >= 10 and share >= 0.02):
        return "some"
    return "lead" if best >= MODERATE else "weak"


@app.post("/api/projects/{pid}/discover")
def discover(pid: int, body: DiscoverIn):
    need_project(pid)
    goal = body.goal.strip()
    if not goal:
        raise HTTPException(400, "Describe what you want to solve or improve.")
    this = time.localtime().tm_year
    as_of = body.as_of if body.as_of and 1960 <= body.as_of < this else None
    yr = (1800, as_of) if as_of else (None, None)
    cnt = lambda term: src.pubmed_count(term, *yr)

    def fix(node):                        # a query PubMed can't use falls back to the safe search builder
        q_ = (node.get("query") or "").strip() or node["name"]
        if not src.pubmed_count(q_):
            q_ = smart.usable_query(node["name"])
        node["query"] = q_
        node["n"] = cnt(f"({q_})")
        return node

    # 1. the target, kept to the population the goal names
    target, mechs = ai.discover_frame(goal)
    if not mechs:
        raise HTTPException(502, "Medr couldn't map this problem just now. Try again, or word it as a condition and a group, e.g. 'anxiety in young teens'.")
    fix(target)
    A = f"({target['query']})"
    pop = (target.get("population_query") or "").strip()
    if pop and src.pubmed_count(f"{A} AND ({pop})") >= 50:
        A = f"{A} AND ({pop})"
        target["n"] = cnt(A)
    target["query"] = A
    src.pmap(fix, mechs)
    mechs = [x for x in mechs if x["n"] > 0]
    ab = src.pmap(lambda b: cnt(f"{A} AND ({b['query']})"), mechs)
    for b, n in zip(mechs, ab):
        b["ab"] = n
    mechs.sort(key=lambda b: b["ab"], reverse=True)
    top = [b for b in mechs if b["ab"] >= WEAK][:8]          # every real mechanism gets cross-checked

    links = []

    def evaluate(apprs):
        src.pmap(fix, apprs)
        apprs = [c for c in apprs if c["n"] > 0]
        pairs = [(b, c) for b in top for c in apprs]
        jobs = [lambda c=c: cnt(f"{A} AND ({c['query']})") for c in apprs] + \
               [lambda b=b, c=c: cnt(f"({b['query']}) AND ({c['query']})") for b, c in pairs]
        if as_of:
            jobs += [lambda c=c: src.pubmed_count(f"{A} AND ({c['query']})", as_of + 1, this) for c in apprs]
        got = src.pmap(lambda f: f(), jobs)
        ac, bc = got[:len(apprs)], got[len(apprs):len(apprs) + len(pairs)]
        after = got[len(apprs) + len(pairs):] if as_of else [None] * len(apprs)
        for (b, c), n in zip(pairs, bc):
            if n >= WEAK:
                links.append({"b": b["name"], "c": c["name"], "n": n})
        out = []
        for c, n_ac, n_after in zip(apprs, ac, after):
            paths = sorted(({"via": b["name"], "ab": b["ab"], "bc": next((l["n"] for l in links if l["b"] == b["name"] and l["c"] == c["name"]), 0)}
                            for b in top), key=lambda p: min(p["ab"], p["bc"]), reverse=True)
            paths = [p for p in paths if min(p["ab"], p["bc"]) >= WEAK][:3]
            best = min(paths[0]["ab"], paths[0]["bc"]) if paths else 0
            status = link_status(n_ac, target.get("n") or 0, c["n"], best)
            potential = round(100 * min(1, math.log10(best + 1) / 3) / (1 + n_ac / 8)) if status == "lead" else 0
            out.append({**c, "ac": n_ac, "after": n_after, "paths": paths, "status": status, "potential": potential})
        return out

    out_apprs = evaluate(ai.discover_approaches(goal, target["name"], mechs))
    if sum(1 for c in out_apprs if c["status"] == "lead") < 3 and top:     # widen: look further afield
        more = ai.discover_approaches(goal, target["name"], top[:5], n=8, exclude=[c["name"] for c in out_apprs], cross_only=True)
        out_apprs += evaluate(more)
    if not out_apprs:
        raise HTTPException(502, "Medr couldn't find approaches to check just now. Please try again.")
    rank = {"lead": 0, "some": 1, "known": 2, "weak": 3}
    out_apprs.sort(key=lambda c: (rank[c["status"]], -c["potential"], -c["ac"]))
    shown = {c["name"] for c in out_apprs}
    links = [l for l in links if l["c"] in shown]

    leads = [c for c in out_apprs if c["status"] == "lead"][:6]
    summary = ""
    try:
        ex_ = ai.discover_explain(goal, target["name"], leads, as_of) if leads else {}
        got_leads = [e for e in ex_.get("leads", []) if isinstance(e, dict)]
        by = {e.get("name", "").lower(): e for e in got_leads}
        for k, c in enumerate(leads):
            e = by.get(c["name"].lower()) or (got_leads[k] if k < len(got_leads) else {})
            c.update({"why": (e or {}).get("why", ""), "test": (e or {}).get("test", ""), "caution": (e or {}).get("caution", "")})
        summary = ex_.get("summary", "")
    except Exception as e:
        print("discover explain skipped:", e, flush=True)

    check = None
    if as_of:
        flagged = [c for c in out_apprs if c["status"] == "lead"]
        check = {"flagged": len(flagged), "grew": sum(1 for c in flagged if (c["after"] or 0) >= MODERATE),
                 "examples": [{"name": c["name"], "before": c["ac"], "after": c["after"]} for c in
                              sorted(flagged, key=lambda c: -(c["after"] or 0))[:5]]}
    data = {"goal": goal, "as_of": as_of, "target": target, "mechanisms": mechs, "approaches": out_apprs,
            "links": links, "summary": summary, "check": check,
            "thresholds": {"strong": STRONG, "moderate": MODERATE, "weak": WEAK}}
    title = f"Discover: {goal}" + (f" (as of {as_of})" if as_of else "")
    return save_item(pid, "discover", "", title, data)


# ---------------- Idea Lab ----------------
import math


class IdeaIn(BaseModel):
    condition: str
    angle: str = "any"
    origin: dict = {}


def resolve_origin(pid, origin):
    """A Discover lead, looked up from the saved map so its evidence travels with the idea."""
    if not origin or not origin.get("map"):
        return None
    it = q("SELECT * FROM items WHERE id = ? AND project_id = ? AND kind = 'discover'", (int(origin["map"]), pid), one=True)
    if not it:
        return None
    d = json.loads(it["data"] or "{}")
    c = next((a for a in d.get("approaches", []) if a.get("name") == origin.get("lead")), None)
    if not c:
        return None
    tname = d.get("target", {}).get("name", "")
    paths = "; ".join(f"{p['via']} ({p['ab']} papers link it to {tname}, {p['bc']} link it to {c['name']})" for p in c.get("paths", []))
    text = (f"Discover map for '{d.get('goal')}': {c['name']} ({c.get('kind', '')}) is strongly connected to {tname} through "
            f"{paths or 'a mechanism'}, but only {c.get('ac', 0)} papers study it for {tname} directly. "
            f"{c.get('why', '')} Suggested first check: {c.get('test', '')} Caution: {c.get('caution', '')}").strip()
    return {"map": it["id"], "goal": d.get("goal"), "target": tname, "lead": c["name"], "kind": c.get("kind"),
            "paths": c.get("paths", []), "ac": c.get("ac", 0), "why": c.get("why", ""), "test": c.get("test", ""), "text": text}


def cap_grade(g, m):
    """Keep confidence honest: no direct papers and no trials can't be better than C; almost nothing at all is D."""
    order = "ABCD"
    g = g if g in order else "C"
    cap = "A"
    if m.get("co", 0) == 0 and m.get("trials", 0) == 0:
        cap = "C" if m.get("link_i", 0) >= 20 else "D"
    elif m.get("co", 0) < 5 and m.get("trials", 0) == 0:
        cap = "B"
    return max(g, cap, key=order.index)


def promise_of(m):
    novelty = 1 / (1 + math.log10(1 + m["co"]))                       # few papers on this exact idea = novel
    support = min(1, math.log10(1 + m["link_i"]) / 3) * min(1, math.log10(1 + m["link_c"]) / 3)
    # support is required: an idea with no evidence behind its link scores near zero, however new it is
    score = 100 * (support ** 0.6) * (0.35 + 0.65 * novelty)
    if m["trials"] >= 3:
        score *= 0.65                                                 # already being tested a lot
    return round(score)


@app.post("/api/projects/{pid}/ideas")
def idea_lab(pid: int, body: IdeaIn):
    need_project(pid)
    cond = body.condition.strip()
    if not cond:
        raise HTTPException(400, "Enter a condition")
    origin = resolve_origin(pid, body.origin)
    step = "starting"
    try:
        # 1. scan the landscape
        step = "reading your topic"
        interp = smart.interpret(cond)
        cq = smart.pubmed_query(interp["concepts"], tagged=False) or cond
        if cq != cond and not src.pubmed_count(cq):
            cq = cond
        tcond = interp.get("trial_condition") or cond
        step = "scanning research"

        def neighbours():                                         # runs alongside the main searches
            try:
                areas = ai.adjacent_topics(cond)
                got = src.pmap(lambda a: smart.find_papers(a.get("query") or a["name"], n=4, reviews_only=True)[0], areas)
                return [x for g in got for x in g][:12]
            except Exception as e:
                print("adjacent research skipped:", e, flush=True)
                return []
        (reviews, _), (recent, _), (preprints, _), (trials, trials_total), adjacent_all = src.together(      # all at once
            lambda: src.pubmed_search(cq, n=10, reviews_only=True),
            lambda: src.pubmed_search(cq, n=12, sort="pub_date"),
            lambda: optional(lambda: src.preprints_search(smart.epmc_query(interp["concepts"]) or cond, n=10), ([], 0)),
            lambda: optional(lambda: src.trials_search(condition=tcond, term=interp.get("trial_other") or "", n=20), ([], 0)),
            neighbours)
        papers = list({p["pmid"]: p for p in reviews + recent}.values())
        if len(papers) < 8 and len(interp["concepts"]) > 1:      # too narrow: loosen to the core of the topic
            core = sorted(interp["concepts"], key=lambda c: -c.get("importance", 2))[:max(1, len(interp["concepts"]) - 1)]
            wider, _ = src.pubmed_search(smart.pubmed_query(core, tagged=False), n=12, reviews_only=True)
            papers = list({p["pmid"]: p for p in papers + wider}.values())
        adjacent = adjacent_all if len(papers) < 12 else adjacent_all[:4]
        # 2+3. map targets and generate hypotheses in one step
        step = "generating ideas"
        mechs, ideas = ai.idea_generate_all(cond, body.angle, papers, trials, preprints, adjacent=adjacent,
                                            context=(origin or {}).get("text", ""))
        if not mechs:                                             # reason from first principles instead of stopping
            mechs = [{"name": "unmet needs in daily care", "why": "first-principles: where patients and families struggle most",
                      "terms": [cond]}]
        if not ideas:
            raise ValueError("no ideas came back")
        # 4. validate each idea against real data
        step = "validating ideas"
        this = time.localtime().tm_year
        Cc = [dict(c) for c in interp["concepts"]] or smart.fallback_interp(cond)["concepts"]

        def words(t):
            return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2}

        def target_concepts(name):
            best = max(mechs, key=lambda m: len(words(m["name"]) & words(name)), default=None)
            if best and words(best["name"]) & words(name):
                return smart.concepts_for(best["name"], [best.get("terms") or [best["name"]]])
            return smart.concepts_for(name or cond)

        def check(i):                       # every idea is checked at the same time
            Ic = smart.concepts_for(i["intervention"], i.get("intervention_terms"))
            Mc = target_concepts(i.get("target"))
            i["intervention_query"] = smart.pubmed_query(Ic, tagged=False)

            def trials_n():
                first = (Ic[0]["terms"][0] if Ic and Ic[0]["terms"] else i["intervention"])
                try:
                    return src.trials_search(condition=tcond, intervention=first, n=1)[1]
                except Exception:
                    return None
            (co, co_q, loosened), (link_i, _, _), (link_c, _, _), tcount = src.together(
                lambda: smart.relaxed_count([Cc, Ic]), lambda: smart.relaxed_count([Ic, Mc]),
                lambda: smart.relaxed_count([Cc, Mc]), trials_n)
            co_recent = src.pubmed_count(co_q, this - 2, this) if co else 0
            m = {"co": co, "co_recent": co_recent, "link_i": link_i, "link_c": link_c, "trials": tcount or 0,
                 "loosened": loosened, "unscored": not (co or link_i or link_c)}
            m["promise"] = promise_of(m)
            i["m"] = m
            i["links"] = {"idea": "https://pubmed.ncbi.nlm.nih.gov/?term=" + quote_plus(co_q),
                          "trials": "https://clinicaltrials.gov/search?" + urlencode({"cond": tcond, "intr": i["intervention"]})}
        list(src.CHECK_POOL.map(check, ideas))
        checks = 5 * len(ideas)
        ideas.sort(key=lambda i: (bool(i.get("yours")), i["m"]["promise"]), reverse=True)   # your own idea stays first
        # 5. dossier
        step = "writing the dossier"
        dossier = ai.idea_dossier(cond, ideas, "PubMed counts and ClinicalTrials.gov matches")
        for d in dossier.get("ideas", []):
            k = d.get("k")
            k = k - 1 if isinstance(k, int) else None          # the dossier numbers ideas from 1
            if isinstance(k, int) and 0 <= k < len(ideas):
                ideas[k].update({x: d.get(x) for x in ("verdict", "grade", "grade_reason", "chain", "test_plan", "proof", "risks", "question")})
        for i in ideas:                                           # the grade can't claim more than the data shows
            i["grade"] = cap_grade(i.get("grade") or "C", i["m"])

    except HTTPException:
        raise
    except src.SourceError:
        raise
    except Exception as e:
        import traceback
        print(f"IDEA LAB FAILED while {step}:\n" + traceback.format_exc(), flush=True)
        raise HTTPException(500, f"Idea Lab failed while {step}: {type(e).__name__}: {str(e)[:250]}")
    stats = {"papers": len(papers), "preprints": len(preprints), "trials": len(trials), "trials_total": trials_total,
             "targets": len(mechs), "ideas": len(ideas), "checks": checks + len(ideas)}
    return save_item(pid, "ideas", "", f"Idea Lab: {cond}",
                     {"condition": cond, "angle": body.angle, "md": dossier.get("summary_md", ""), "targets": mechs,
                      "read_as": interp.get("meaning", ""), "query": cq,
                      "ideas": ideas, "stats": stats, "sources": sources_list(papers, trials, preprints), "origin": origin})


class PlanIn(BaseModel):
    run_id: int


@app.post("/api/projects/{pid}/plan")
def make_plan(pid: int, body: PlanIn):
    p = need_project(pid)
    run = q("SELECT * FROM items WHERE id = ? AND project_id = ? AND kind = 'ideas'", (body.run_id, pid), one=True)
    if not run:
        raise HTTPException(404, "Idea Lab run not found")
    d = json.loads(run["data"])
    ideas = d.get("ideas", [])
    extra = []
    for i in ideas[:3]:                      # a little more evidence for the leading ideas
        try:
            found, _ = src.pubmed_search(f"({smart.pubmed_query(smart.concepts_for(d['condition']), tagged=False) or d['condition']}) AND ({i.get('intervention_query') or i.get('intervention', '')})", n=4)
            extra += found
        except Exception as e:
            print("plan evidence search failed:", e, flush=True)
    extra = list({x["pmid"]: x for x in extra}.values())[:10]
    md = ai.breakthrough_plan(d.get("condition", ""), p["goal"], ideas, extra)
    return save_item(pid, "plan", f"run{body.run_id}", f"Breakthrough plan: {d.get('condition', '')}",
                     {"md": md, "run_id": body.run_id, "sources": sources_list(extra, [], [])})


# ---------------- plan missions: one step at a time ----------------
def need_plan(iid):
    it = q("SELECT * FROM items WHERE id = ? AND kind = 'plan'", (iid,), one=True)
    if not it:
        raise HTTPException(404, "Plan not found")
    d = json.loads(it["data"] or "{}")
    p = need_project(it["project_id"])
    run = q("SELECT data FROM items WHERE id = ?", (d.get("run_id"),), one=True) if d.get("run_id") else None
    cond = d.get("condition") or (json.loads(run["data"]).get("condition", "") if run else
                                  it["title"].replace("Breakthrough plan: ", "").replace("Solution: ", ""))
    return it, d, p, cond


def save_plan(it, d):
    ex("UPDATE items SET data = ?, updated = ? WHERE id = ?", (json.dumps(d), time.time(), it["id"]))
    return load_item(q("SELECT * FROM items WHERE id = ?", (it["id"],), one=True))


def new_step(step, milestone=""):
    return {**step, "id": int(time.time() * 1000), "status": "doing", "note": "", "help": [], "started": time.time(),
            "milestone": milestone}


class LeadIn(BaseModel):
    lead: str = ""


@app.post("/api/items/{iid}/mission/start")
def mission_start(iid: int, body: LeadIn):
    it, d, p, cond = need_plan(iid)
    lead = body.lead.strip() or "the lead the plan recommends pursuing first"
    out = ai.mission_start(cond, p["goal"], d.get("md", ""), lead)
    if not out.get("step"):
        raise HTTPException(500, "The coach didn't return a first step. Try again.")
    roadmap = out.get("roadmap") or []
    d["mission"] = {"lead": out.get("lead") or lead, "mission": out.get("mission", ""), "roadmap": roadmap,
                    "steps": [new_step(out["step"], roadmap[0] if roadmap else "")], "feedback": "", "finished": False,
                    "started": time.time()}
    log(it["project_id"], "plan", f"Started a mission: {d['mission']['lead']}", str(iid))
    return save_plan(it, d)


class DoneIn(BaseModel):
    note: str = ""


@app.post("/api/items/{iid}/mission/done")
def mission_done(iid: int, body: DoneIn):
    it, d, p, cond = need_plan(iid)
    m = d.get("mission")
    if not m or not m.get("steps"):
        raise HTTPException(400, "Start the plan first")
    cur = m["steps"][-1]
    cur.update(status="done", note=body.note.strip(), done_at=time.time())
    log(it["project_id"], "plan", f"Finished step: {cur.get('title')}", str(iid))
    out = ai.mission_next(cond, p["goal"], d.get("md", ""), m, m["steps"])
    m["feedback"] = out.get("feedback", "")
    m["roadmap"] = out.get("roadmap") or m.get("roadmap")
    m["finished"] = bool(out.get("finished"))
    if out.get("step"):
        m["steps"].append(new_step(out["step"], out.get("milestone", "")))
    return save_plan(it, d)


class SwapIn(BaseModel):
    reason: str = ""


@app.post("/api/items/{iid}/mission/swap")
def mission_swap(iid: int, body: SwapIn):
    it, d, p, cond = need_plan(iid)
    m = d.get("mission")
    if not m or not m.get("steps"):
        raise HTTPException(400, "Start the plan first")
    cur = m["steps"][-1]
    cur.update(status="skipped", note=f"Swapped: {body.reason.strip() or 'wanted a different step'}")
    out = ai.mission_next(cond, p["goal"], d.get("md", ""), m, m["steps"], swap_reason=body.reason or "wanted a different step")
    m["steps"].append(new_step(out["step"], out.get("milestone", "")))
    m["roadmap"] = out.get("roadmap") or m.get("roadmap")
    m["feedback"] = ""
    return save_plan(it, d)


@app.post("/api/items/{iid}/mission/undo")
def mission_undo(iid: int):
    """Reopen the last finished step (drops the step that was generated after it)."""
    it, d, p, cond = need_plan(iid)
    m = d.get("mission") or {}
    steps = m.get("steps") or []
    if len(steps) < 2:
        raise HTTPException(400, "Nothing to undo")
    steps.pop()
    steps[-1].update(status="doing")
    m["feedback"], m["finished"] = "", False
    return save_plan(it, d)


class HelpIn(BaseModel):
    question: str = ""


@app.post("/api/items/{iid}/mission/help")
def mission_help(iid: int, body: HelpIn):
    it, d, p, cond = need_plan(iid)
    m = d.get("mission")
    if not m or not m.get("steps"):
        raise HTTPException(400, "Start the plan first")
    cur = m["steps"][-1]
    ans = ai.mission_help(cond, m, cur, body.question.strip())
    cur.setdefault("help", []).append({"q": body.question.strip() or "I'm stuck. Walk me through it.", "a": ans})
    return save_plan(it, d)


@app.post("/api/items/{iid}/mission/reset")
def mission_reset(iid: int):
    it, d, p, cond = need_plan(iid)
    d.pop("mission", None)
    log(it["project_id"], "plan", "Reset a plan's mission", str(iid))
    return save_plan(it, d)


@app.post("/api/projects/{pid}/trials/refresh")
def refresh_trials(pid: int):
    need_project(pid)
    rows = q("SELECT * FROM items WHERE project_id = ? AND kind = 'trial'", (pid,))
    changes, failed = [], 0
    for r in rows:
        old = json.loads(r["data"] or "{}")
        try:
            new = src.trial_by_id(r["ref"] or old.get("nct"))
        except Exception as e:
            print("trial refresh failed:", r["ref"], e, flush=True)
            failed += 1
            continue
        diff = []
        if new.get("status") != old.get("status"):
            diff.append(f"status {old.get('status') or '?'} → {new.get('status')}")
        if new.get("phases") != old.get("phases"):
            diff.append(f"phase {', '.join(old.get('phases') or []) or '?'} → {', '.join(new.get('phases') or [])}")
        if new.get("enrollment") != old.get("enrollment") and old.get("enrollment") is not None:
            diff.append(f"enrollment {old.get('enrollment')} → {new.get('enrollment')}")
        new["checked"] = time.strftime("%Y-%m-%d")
        ex("UPDATE items SET data = ?, updated = ? WHERE id = ?", (json.dumps({**old, **new}), time.time(), r["id"]))
        if diff:
            changes.append({"nct": new["nct"], "title": new["title"], "changes": diff})
            log(pid, "trial", f"{new['nct']} changed: {'; '.join(diff)}", r["ref"])
    log(pid, "trial", f"Checked {len(rows) - failed} saved trials for updates ({len(changes)} changed)")
    return {"checked": len(rows) - failed, "failed": failed, "changes": changes}


@app.get("/api/projects/{pid}/backup")
def project_backup(pid: int):
    p = need_project(pid)
    data = {"medr_project": 1, "exported": time.strftime("%Y-%m-%d %H:%M"),
            "project": {"name": p["name"], "goal": p["goal"], "created": p["created"]},
            "items": q("SELECT kind, ref, title, data, created, updated, stage FROM items WHERE project_id = ?", (pid,)),
            "watches": q("SELECT topic, last_checked, created FROM watches WHERE project_id = ?", (pid,)),
            "chats": q("SELECT role, content, created FROM chats WHERE project_id = ? ORDER BY id", (pid,)),
            "events": q("SELECT kind, text, ref, created FROM events WHERE project_id = ? ORDER BY id", (pid,))}
    name = "".join(c if c.isalnum() else "-" for c in p["name"]).strip("-") or "project"
    from fastapi.responses import JSONResponse
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{name}.medr.json"'})


@app.post("/api/projects/restore")
def project_restore(body: dict):
    if body.get("medr_project") != 1 or "project" not in body:
        raise HTTPException(400, "That file isn't a Medr project backup")
    pr = body["project"]
    pid = ex("INSERT INTO projects(name, goal, created, user_id) VALUES (?,?,?,?)",
             (f"{pr.get('name', 'Project')} (restored)", pr.get("goal", ""), pr.get("created") or time.time(), uid()))
    for it in body.get("items", []):
        ex("INSERT INTO items(project_id, kind, ref, title, data, created, updated, stage) VALUES (?,?,?,?,?,?,?,?)",
           (pid, it.get("kind"), it.get("ref", ""), it.get("title", ""),
            it["data"] if isinstance(it.get("data"), str) else json.dumps(it.get("data") or {}),
            it.get("created") or time.time(), it.get("updated"), it.get("stage")))
    for w in body.get("watches", []):
        ex("INSERT INTO watches(project_id, topic, last_checked, created) VALUES (?,?,?,?)",
           (pid, w.get("topic"), w.get("last_checked"), w.get("created") or time.time()))
    for c in body.get("chats", []):
        ex("INSERT INTO chats(project_id, role, content, created) VALUES (?,?,?,?)",
           (pid, c.get("role"), c.get("content"), c.get("created") or time.time()))
    for e in body.get("events", []):
        ex("INSERT INTO events(project_id, kind, text, ref, created) VALUES (?,?,?,?,?)",
           (pid, e.get("kind"), e.get("text"), e.get("ref", ""), e.get("created") or time.time()))
    log(pid, "project", "Restored from a backup file")
    return {"id": pid}


class ShortlistIn(BaseModel):
    idea: dict
    condition: str


@app.post("/api/projects/{pid}/shortlist")
def shortlist(pid: int, body: ShortlistIn):
    need_project(pid)
    item = save_item(pid, "idea", f"{body.condition}|{body.idea.get('title')}", body.idea.get("title", "Idea"),
                     {**body.idea, "condition": body.condition})
    return item or {"duplicate": True}


# ---------------- turn one idea into a solution plan, and start it ----------------
class SolutionIn(BaseModel):
    idea: dict
    condition: str = ""
    origin: dict | None = None


def idea_plan_md(x):
    m = x.get("m") or {}
    lines = [f"## {x.get('title', 'Solution')}", ""]
    if x.get("plain"):
        lines += [f"**In short:** {x['plain']}", ""]
    if x.get("rationale") or x.get("approach"):
        lines += [f"**The bet:** {x.get('rationale') or x.get('approach')}", ""]
    if x.get("grade"):
        lines += [f"**Confidence: {x['grade']}** {x.get('grade_reason') or ''}".strip(), ""]
    steps = x.get("test_plan") or x.get("steps") or []
    if steps:
        lines += ["**Approach:**"] + [f"{k + 1}. {t}" for k, t in enumerate(steps[:3])] + [""]
    if x.get("proof"):
        lines += [f"**Proof point:** {x['proof']}", ""]
    if x.get("risks"):
        lines += ["**Why it might not work:**"] + [f"- {r}" for r in x["risks"]] + [""]
    if m.get("co") is not None and not m.get("unscored"):
        lines += [f"_Evidence: {m.get('co', 0)} papers on this idea, {m.get('link_i', 0)} linking the approach to the cause, "
                  f"{m.get('trials', 0)} matching trials._"]
    return "\n".join(lines)


@app.post("/api/projects/{pid}/solution")
def start_solution(pid: int, body: SolutionIn):
    p = need_project(pid)
    x, cond = body.idea, body.condition or body.idea.get("condition", "")
    idea = save_item(pid, "idea", f"{cond}|{x.get('title')}", x.get("title", "Idea"), {**x, "condition": cond})
    ex("UPDATE items SET stage = 'investigating' WHERE project_id = ? AND kind = 'idea' AND ref = ?", (pid, f"{cond}|{x.get('title')}"))
    md = idea_plan_md(x)
    origin = body.origin if isinstance(body.origin, dict) and body.origin.get("text") else None
    if origin:                               # the Discover evidence path rides along into every task
        md += "\n\n## Where this came from\n" + origin["text"]
    out = ai.mission_start(cond, p["goal"], md, x.get("title", "this idea"))
    roadmap = out.get("roadmap") or []
    data = {"md": md, "condition": cond, "from_idea": x.get("title"), "sources": [], "origin": origin,
            "idea": {k: x.get(k) for k in ("title", "plain", "grade", "grade_reason", "type", "intervention", "potential") if x.get(k) is not None},
            "mission": {"lead": out.get("lead") or x.get("title"), "mission": out.get("mission", ""), "roadmap": roadmap,
                        "steps": [new_step(out["step"], roadmap[0] if roadmap else "")], "feedback": "", "finished": False,
                        "started": time.time()}}
    plan = save_item(pid, "plan", "", f"Solution: {x.get('title', 'idea')}", data)
    log(pid, "plan", f"Started building a solution: {x.get('title')}", str(plan["id"]) if plan else "")
    return plan


@app.post("/api/items/{iid}/proposal")
def write_proposal(iid: int):
    it, d, p, cond = need_plan(iid)
    steps = (d.get("mission") or {}).get("steps") or []
    origin = d.get("origin") or {}
    md = ai.proposal({"origin_text": origin.get("text", "")}, d.get("md", ""), steps)
    d["proposal"] = {"md": md, "created": time.time(), "tasks_done": sum(1 for s_ in steps if s_.get("status") == "done")}
    log(it["project_id"], "plan", f"Wrote the proposal: {it['title']}", str(iid))
    return save_plan(it, d)


# ---------------- explain + rank search results ----------------
class ExplainIn(BaseModel):
    q: str
    meaning: str = ""
    items: list = []


_explain_cache = {}


@app.post("/api/search/explain")
def explain(body: ExplainIn):
    items = [i for i in body.items if isinstance(i, dict) and i.get("id")][:20]
    for i in items:
        i["id"] = str(i["id"])
    key = lambda i: f"{body.q.strip().lower()}|{i['id']}"
    todo = [i for i in items if key(i) not in _explain_cache]

    def score(v):
        try:
            return max(1, min(5, int(round(float(v)))))
        except (TypeError, ValueError):
            return None

    def run(chunk):
        try:
            got = ai.explain_results(body.q, body.meaning, chunk)
        except Exception as e:
            print("explain failed:", e, flush=True)
            return
        by_id = {i["id"]: i for i in chunk}
        matched = 0
        for k, e in enumerate(got):
            if not isinstance(e, dict):
                continue
            rid = str(e.get("id") or "").strip().replace("id=", "")
            it = by_id.get(rid)
            if not it and len(got) == len(chunk):        # no usable id: replies come back in the order sent
                it = chunk[k]
            if not it:                                   # last resort: the reply names the study's title
                t = str(e.get("title") or "").lower()[:40]
                it = next((c for c in chunk if t and c.get("title", "").lower().startswith(t)), None)
            if it:
                _explain_cache[key(it)] = {"premise": e.get("premise") or "", "connection": e.get("connection") or "",
                                           "relevance": score(e.get("relevance")), "breakthrough": score(e.get("breakthrough"))}
                matched += 1
        if matched < len(chunk):
            print(f"explain: matched {matched}/{len(chunk)}; sample reply: {str(got[:1])[:300]}", flush=True)
    chunks = [todo[k:k + 7] for k in range(0, len(todo), 7)]
    list(src.CHECK_POOL.map(run, chunks))               # all batches at once
    return {"items": {i["id"]: _explain_cache.get(key(i)) for i in items if _explain_cache.get(key(i))}}


# ---------------- export ----------------
@app.get("/api/projects/{pid}/export")
def export(pid: int):
    p = get_project(pid)
    out = [f"# {p['name']}", f"_Goal: {p['goal'] or 'n/a'}_", f"_Exported from Medr {time.strftime('%Y-%m-%d')}_", ""]
    labels = {"plan": "Plans", "ideas": "Idea Lab runs", "idea": "Ideas in progress", "brief": "Research briefs", "gaps": "Opportunities", "digest": "What's new", "note": "Notes",
              "radar": "Radar scans", "bridge": "Crossovers",
              "paper": "Saved papers", "preprint": "Saved preprints", "trial": "Saved trials"}
    for kind, label in labels.items():
        items = [i for i in p["items"] if i["kind"] == kind]
        if not items:
            continue
        out.append(f"## {label}\n")
        for i in items:
            d = i["data"]
            if kind == "idea":
                m = d.get("m", {})
                out.append(f"### {d.get('title')}  ({d.get('type')}, promise {m.get('promise')}/100)\n\n"
                           f"For: {d.get('condition')}. Intervention: {d.get('intervention')}. Target: {d.get('target')}.\n\n"
                           f"{d.get('rationale', '')}\n\nTest plan:\n" + "\n".join(f"- {t}" for t in d.get("test_plan") or []) + "\n")
            elif kind == "ideas":
                out.append(f"### {i['title']}\n\n{d.get('md', '')}\n\n" + "\n".join(
                    f"- **{x['title']}** ({x.get('type')}, promise {x['m']['promise']}/100, {x.get('verdict', '')})" for x in d.get("ideas", [])) + "\n")
            elif kind in ("brief", "gaps", "digest", "radar", "bridge", "plan"):
                out.append(f"### {i['title']}\n\n{d.get('md', '')}\n")
            elif kind == "note":
                out.append(f"### {i['title']}\n\n{d.get('text', '')}\n")
            else:
                out.append(f"- [{i['title']}]({d.get('url', '')}) ({i['ref']})")
        out.append("")
    name = "".join(c if c.isalnum() else "-" for c in p["name"]).strip("-") or "project"
    return PlainTextResponse("\n".join(out), media_type="text/markdown",
                             headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


# ---------------- frontend ----------------
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/sw.js")
def service_worker():          # served from the root so it can make the whole site installable
    return FileResponse(BASE / "static" / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(BASE / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")



# ======================= settings helpers =======================
def sget(key, default=None):
    r = q("SELECT value FROM settings WHERE key = ?", (key,), one=True)
    return r["value"] if r else default


def sset(key, value):
    ex("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def project_topics(pid, limit=5):
    """What this project is researching: monitored topics, Idea Lab conditions, mission leads."""
    out = [w["topic"] for w in q("SELECT topic FROM watches WHERE project_id = ? ORDER BY id DESC", (pid,))]
    for r in q("SELECT kind, data FROM items WHERE project_id = ? AND kind IN ('ideas','plan') ORDER BY created DESC", (pid,)):
        d = json.loads(r["data"] or "{}")
        if r["kind"] == "ideas" and d.get("condition"):
            out.append(d["condition"])
    seen, keep = set(), []
    for t in out:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k); keep.append(t.strip())
    if not keep:
        p = q("SELECT name, goal FROM projects WHERE id = ?", (pid,), one=True)
        if p:
            keep.append(p["goal"] or p["name"])
    return keep[:limit]


def concept_hit(text, concepts):
    """True when the core of a topic shows up in the text."""
    if not concepts:
        return False
    hit = smart.matches(text, concepts)
    core = [h for c, h in zip(concepts, hit) if c.get("importance", 2) >= 3] or hit[:1]
    return all(core) and sum(hit) >= max(1, (len(concepts) + 1) // 2)


# ======================= News =======================
_news = {"t": 0, "items": [], "status": [], "top": [], "digest": ""}
_news_lock = threading.Lock()
_topic_news = {}


_news_refreshing = threading.Event()


def refresh_news_bg():
    """Refresh the news in the background; whoever asks meanwhile gets the last copy instantly."""
    if _news_refreshing.is_set():
        return
    _news_refreshing.set()

    def run():
        try:
            load_news(force=True, _bg=True)
        except Exception as e:
            print("background news refresh failed:", e, flush=True)
        finally:
            _news_refreshing.clear()
    threading.Thread(target=run, daemon=True).start()


def load_news(force=False, _bg=False):
    if not force and _news["items"]:
        if time.time() - _news["t"] > 2 * 3600 and not _bg:
            refresh_news_bg()                       # stale: show what we have, update behind the scenes
        return _news
    with _news_lock:
        if not force and _news["items"]:
            return _news
        items, status = newsmod.fetch_all()
        top, digest = [], ""
        if items:
            pool = items[:140]
            try:
                r = ai.news_top(pool)
                digest = r.get("digest", "")
                for t in r.get("top", []):
                    k = t.get("k")
                    if isinstance(k, int) and 0 <= k < len(pool):
                        also = [pool[a] for a in (t.get("also") or []) if isinstance(a, int) and 0 <= a < len(pool) and a != k]
                        top.append({**pool[k], "category": t.get("category"), "why": t.get("why"),
                                    "importance": t.get("importance"), "also": also[:3]})
            except Exception as e:
                print("news ranking failed:", e, flush=True)
        _news.update(t=time.time(), items=items, status=status, top=top, digest=digest)
        return _news


@app.get("/api/news")
def get_news(refresh: int = 0):
    n = load_news(force=bool(refresh))
    ok = sum(1 for s in n["status"] if s["ok"])
    return {"items": n["items"][:200], "status": n["status"], "top": n["top"], "digest": n["digest"], "fetched": n["t"],
            "sources_ok": ok, "sources": len(n["status"])}


@app.get("/api/news/project/{pid}")
def project_news(pid: int):
    need_project(pid)
    topics = project_topics(pid, 3)
    n = load_news()
    out, seen = [], set()
    for t in topics:
        cs = smart.interpret(t)["concepts"]
        for it in n["items"]:
            if it["id"] not in seen and concept_hit(it["title"] + " " + it.get("summary", ""), cs):
                seen.add(it["id"]); out.append({**it, "topic": t})
        hit = _topic_news.get(t.lower())
        if not hit or time.time() - hit[0] > 2 * 3600:
            try:
                hit = (time.time(), newsmod.topic_news(t))
                _topic_news[t.lower()] = hit
            except Exception as e:
                print("topic news failed:", t, e, flush=True)
                hit = (time.time(), [])
        for it in hit[1]:
            if it["id"] not in seen:
                seen.add(it["id"]); out.append({**it, "topic": t})
    out.sort(key=lambda x: -(x.get("date") or 0))
    return {"topics": topics, "items": out[:40]}


# ======================= Breakthrough alerts =======================
HIGH_TYPES = {"Randomized Controlled Trial", "Meta-Analysis", "Clinical Trial, Phase III", "Clinical Trial, Phase II",
              "Practice Guideline", "Guideline", "Systematic Review", "Multicenter Study"}
TOP_JOURNALS = ["n engl j med", "lancet", "jama", "nature", "science", "cell", "bmj", "diabetes care", "pediatrics",
                "circulation", "j clin oncol", "ann intern med", "plos med", "diabetologia", "lancet diabetes"]
_alert_lock = threading.Lock()


def check_project(pid):
    """Look for breakthrough-level news on everything this project researches. Returns new alerts."""
    with _alert_lock:
        last = float(sget(f"alerts_last_{pid}", 0) or 0)
        since_ts = last or time.time() - 14 * 86400
        since = time.strftime("%Y-%m-%d", time.localtime(since_ts - 86400))
        topics = project_topics(pid)
        cands = []
        for t in topics:
            interp = smart.interpret(t)
            query = smart.pubmed_query(interp["concepts"], tagged=False) or t
            try:
                papers, _ = src.pubmed_search(query, n=25, since=since)
            except Exception as e:
                print("alert paper check failed:", e, flush=True)
                papers = []
            for pp in papers:
                types = set(pp.get("types") or [])
                top_j = any(j in (pp.get("journal") or "").lower() for j in TOP_JOURNALS)
                if types & HIGH_TYPES or top_j:
                    cands.append({"cid": "p" + pp["pmid"], "kind": "paper", "title": pp["title"], "topic": t,
                                  "meta": f"{pp.get('journal')} {pp.get('year')}; types: {', '.join(types)}",
                                  "text": pp.get("abstract", ""), "link": pp["url"], "source": pp.get("journal", "PubMed")})
            n = _news if _news["items"] else {"items": []}
            for it in n["items"]:
                if (it.get("date") or 0) >= since_ts and concept_hit(it["title"] + " " + it.get("summary", ""), interp["concepts"]):
                    cands.append({"cid": "n" + it["id"], "kind": "news", "title": it["title"], "topic": t,
                                  "meta": it["source"], "text": it.get("summary", ""), "link": it["link"], "source": it["source"]})
        for r in q("SELECT * FROM items WHERE project_id = ? AND kind = 'trial'", (pid,)):
            old = json.loads(r["data"] or "{}")
            try:
                new = src.trial_by_id(r["ref"] or old.get("nct"))
            except Exception:
                continue
            if new.get("status") == "COMPLETED" and old.get("status") != "COMPLETED" or \
               (new.get("phases") or []) != (old.get("phases") or []) and old.get("phases"):
                cands.append({"cid": "t" + new["nct"], "kind": "trial", "title": new["title"], "topic": "a trial you saved",
                              "meta": f"status {old.get('status')} → {new.get('status')}; phase {old.get('phases')} → {new.get('phases')}",
                              "text": new.get("summary", ""), "link": new.get("url"), "source": "ClinicalTrials.gov"})
                ex("UPDATE items SET data = ?, updated = ? WHERE id = ?", (json.dumps({**old, **new}), time.time(), r["id"]))
        have = {r["key"] for r in q("SELECT key FROM alerts WHERE project_id = ?", (pid,))}
        reviewed = json.loads(sget(f"alerts_seen_{pid}", "[]") or "[]")
        have |= {f"{pid}:{c}" for c in reviewed}
        uniq = {}
        for c in cands:
            if f"{pid}:{c['cid']}" not in have:
                uniq.setdefault(c["cid"], c)
        cands = list(uniq.values())[:40]
        made = []
        if cands:
            byid = {c["cid"]: c for c in cands}
            for a in ai.judge_breakthroughs(topics + ["saved clinical trials"], cands):
                c = byid.get(str(a.get("id", "")).strip())
                if not c or a.get("level") not in ("breakthrough", "notable"):
                    continue
                try:
                    aid = ex("INSERT INTO alerts(project_id, level, topic, title, body, link, source, key, created) VALUES (?,?,?,?,?,?,?,?,?)",
                             (pid, a["level"], a.get("topic") or c["topic"], a.get("headline") or c["title"], a.get("why", ""),
                              c["link"], c["source"], f"{pid}:{c['cid']}", time.time()))
                    made.append(aid)
                except sqlite3.IntegrityError:
                    pass
        sset(f"alerts_seen_{pid}", json.dumps((reviewed + [c["cid"] for c in cands])[-3000:]))
        sset(f"alerts_last_{pid}", time.time())
        nb = sum(1 for i in made if q("SELECT level FROM alerts WHERE id = ?", (i,), one=True)["level"] == "breakthrough")
        log(pid, "alert", f"Checked {len(topics)} topic{'s' if len(topics) != 1 else ''} for breakthroughs: {len(cands)} new items reviewed, "
                          f"{nb} breakthrough{'s' if nb != 1 else ''}, {len(made) - nb} notable")
        return {"topics": topics, "reviewed": len(cands), "new": len(made), "breakthroughs": nb}


def alerts_settings():
    return {"on": sget("alerts_on", "1") == "1", "hours": int(sget("alerts_hours", "24" if HOSTED else "12") or 12)}


def alert_loop():
    time.sleep(float(os.getenv("MEDR_ALERT_DELAY", "120")))
    while True:
        try:
            st = alerts_settings()
            if st["on"]:
                rows = q("SELECT p.id FROM projects p JOIN users u ON u.id = p.user_id WHERE u.last_seen > ? AND u.disabled = 0",
                         (time.time() - 7 * 86400,)) if HOSTED else q("SELECT id FROM projects")
                for p in rows:
                    last = float(sget(f"alerts_last_{p['id']}", 0) or 0)
                    if time.time() - last > st["hours"] * 3600:
                        try:
                            load_news()
                        except Exception as e:
                            print("news refresh failed:", e, flush=True)
                        print("alert check:", p["id"], check_project(p["id"]), flush=True)
        except Exception as e:
            print("alert loop error:", e, flush=True)
        time.sleep(900)


@app.on_event("startup")
def start_alerts():
    apply_speed_settings()
    if os.getenv("MEDR_NO_ALERTS") != "1":
        threading.Thread(target=alert_loop, daemon=True).start()
    if os.getenv("MEDR_NO_WARM") != "1":
        refresh_news_bg()                           # News is ready before you open it


# ======================= speed settings =======================
_ENV_NCBI = os.getenv("NCBI_API_KEY", "").strip()   # a key set on the server stays on


def apply_speed_settings():
    ai.FAST_MODE = sget("ai_speed", "balanced") == "fast"
    key = (sget("ncbi_key", "") or "").strip() or _ENV_NCBI
    if key:
        os.environ["NCBI_API_KEY"] = key
    else:
        os.environ.pop("NCBI_API_KEY", None)


@app.get("/api/settings/speed")
def get_speed():
    return {"mode": sget("ai_speed", "balanced"), "ncbi_key": bool((sget("ncbi_key", "") or "").strip())}


class SpeedIn(BaseModel):
    mode: str = "balanced"
    ncbi_key: Optional[str] = None


@app.put("/api/settings/speed")
def set_speed(body: SpeedIn):
    sset("ai_speed", "fast" if body.mode == "fast" else "balanced")
    if body.ncbi_key is not None:
        sset("ncbi_key", body.ncbi_key.strip())
    apply_speed_settings()
    return get_speed()


def alert_rows(where="1=1", args=()):
    w, a = mine()
    where, args = f"({where})" + w, tuple(args) + a
    return q(f"""SELECT a.*, p.name AS project FROM alerts a LEFT JOIN projects p ON p.id = a.project_id
                 WHERE {where} ORDER BY a.read, (a.level = 'breakthrough') DESC, a.created DESC LIMIT 200""", args)


@app.get("/api/alerts")
def list_alerts():
    rows = alert_rows()
    last = {p["id"]: float(sget(f"alerts_last_{p['id']}", 0) or 0) for p in q("SELECT id FROM projects p WHERE 1=1" + mine()[0], mine()[1])}
    return {"alerts": rows, "unread": sum(1 for r in rows if not r["read"]),
            "unread_breakthroughs": sum(1 for r in rows if not r["read"] and r["level"] == "breakthrough"),
            "settings": alerts_settings(), "last_checked": last}


@app.post("/api/alerts/check")
def run_check(body: dict = None):
    pid = (body or {}).get("pid")
    if pid:
        need_project(int(pid))
    ids = [int(pid)] if pid else [p["id"] for p in q("SELECT id FROM projects p WHERE 1=1" + mine()[0], mine()[1])]
    try:
        load_news()
    except Exception as e:
        print("news refresh failed:", e, flush=True)
    res = {i: check_project(i) for i in ids}
    return {"results": res, "new": sum(r["new"] for r in res.values()), "breakthroughs": sum(r["breakthroughs"] for r in res.values())}


@app.post("/api/alerts/{aid}/read")
def read_alert(aid: int):
    ex("UPDATE alerts SET read = 1 WHERE id = ?", (aid,))
    return {"ok": True}


@app.post("/api/alerts/read_all")
def read_all_alerts():
    w, a = mine()
    ex("UPDATE alerts SET read = 1 WHERE project_id IN (SELECT p.id FROM projects p WHERE 1=1" + w + ")", a)
    return {"ok": True}


@app.delete("/api/alerts/{aid}")
def delete_alert(aid: int):
    ex("DELETE FROM alerts WHERE id = ?", (aid,))
    return {"ok": True}


class AlertSettingsIn(BaseModel):
    on: bool = True
    hours: int = 12


@app.put("/api/settings/alerts")
def set_alert_settings(body: AlertSettingsIn):
    sset("alerts_on", "1" if body.on else "0")
    sset("alerts_hours", max(1, min(168, body.hours)))
    return alerts_settings()


def pending_popups():
    """For the tray: breakthrough alerts that haven't popped up on the desktop yet."""
    rows = alert_rows("a.level = 'breakthrough' AND a.popped = 0 AND a.read = 0")
    if rows:
        ex(f"UPDATE alerts SET popped = 1 WHERE id IN ({','.join(str(r['id']) for r in rows)})")
    return rows
