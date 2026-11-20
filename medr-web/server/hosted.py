"""Hosted (multi-user) mode for Medr. Turned on with MEDR_HOSTED=1.

Adds accounts, per-user data, daily usage caps, a feedback inbox and an admin page.
With MEDR_HOSTED unset, nothing here runs and Medr behaves like the desktop app.

Environment variables (all optional except the API key):
  ANTHROPIC_API_KEY      your Claude key (set on the server, never shown to users)
  MEDR_ADMIN_EMAIL       comma-separated emails that get the admin page
  MEDR_INVITE_CODE       if set, people need this code to sign up
  MEDR_DAILY_RUNS        deep runs per user per day (Idea Lab, Assessment Finder, plans...)  default 12
  MEDR_DAILY_ACTIONS     searches and quick AI actions per user per day                      default 150
  MEDR_GLOBAL_DAILY_RUNS deep runs across ALL users per day (protects your bill)              default 150
  MEDR_SECURE_COOKIE     1 (default) = cookies only over https. Set 0 for local testing.
"""
import contextvars, hashlib, hmac, json, os, re, secrets, threading, time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

HOSTED = os.getenv("MEDR_HOSTED") == "1"
_uid = contextvars.ContextVar("medr_uid", default=None)
_admin_read = contextvars.ContextVar("medr_admin_read", default=False)   # admin viewing someone else's project
COOKIE = "medr_session"
SESSION_DAYS = 30


def uid():
    """The signed-in user's id in hosted mode; None on the desktop app (no filtering)."""
    return _uid.get() if HOSTED else None


def admin_read():
    """True while an admin is reading (GET only) a project they don't own."""
    return HOSTED and _admin_read.get()


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def limits():
    return {"runs": env_int("MEDR_DAILY_RUNS", 12), "actions": env_int("MEDR_DAILY_ACTIONS", 150),
            "global_runs": env_int("MEDR_GLOBAL_DAILY_RUNS", 150)}


def admin_emails():
    return {e.strip().lower() for e in os.getenv("MEDR_ADMIN_EMAIL", "").split(",") if e.strip()}


# ---------------- passwords ----------------
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        _, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except Exception:
        return False


# ---------------- usage classes ----------------
# deep runs: the expensive, multi-step AI jobs
RUN_PATHS = [r"^/api/projects/\d+/(ideas|plan|solution|measures|brief|gaps|radar|bridge|discover)$", r"^/api/items/\d+/proposal$", r"^/api/search/ideas$",
             r"^/api/alerts/check$"]
# actions: searches and single AI calls
ACTION_PATHS = [r"^/api/search/(papers|preprints|trials|explain)$", r"^/api/projects/\d+/(ask|shortlist|trials/refresh)$",
                r"^/api/items/\d+/mission/(start|done|swap|help)$", r"^/api/trend$", r"^/api/watches/\d+/check$"]
# owned objects named in the URL
OWNED = [(re.compile(r"^/api/projects/(\d+)"), "project"), (re.compile(r"^/api/news/project/(\d+)"), "project"),
         (re.compile(r"^/api/items/(\d+)"), "item"), (re.compile(r"^/api/watches/(\d+)"), "watch"),
         (re.compile(r"^/api/alerts/(\d+)"), "alert")]
PUBLIC = {"/api/version", "/api/me", "/api/auth/signup", "/api/auth/login", "/api/auth/logout", "/api/auth/config"}
ADMIN_ONLY = [r"^/api/admin/", r"^/api/logs$", r"^/api/diagnose$", r"^/api/shutdown$"]
ADMIN_WRITES = [r"^/api/settings/speed$", r"^/api/settings/alerts$"]


def usage_class(method, path):
    if any(re.match(p, path) for p in RUN_PATHS) and method == "POST":
        return "run"
    if any(re.match(p, path) for p in ACTION_PATHS):
        return "action"
    return None


# ---------------- login rate limit ----------------
_tries, _tries_lock = {}, threading.Lock()


def too_many(key, limit=10, window=900):
    now = time.time()
    with _tries_lock:
        hits = [t for t in _tries.get(key, []) if now - t < window]
        _tries[key] = hits
        return len(hits) >= limit


def note_try(key):
    with _tries_lock:
        _tries.setdefault(key, []).append(time.time())


def client_ip(request):
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else None) or (request.client.host if request.client else "?")


# ---------------- install ----------------
def install(app, q, ex, db):
    """Called by app.py. Creates the tables and, in hosted mode, the routes and the guard middleware."""
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, pw TEXT NOT NULL, name TEXT DEFAULT '',
      field TEXT DEFAULT '', created REAL, last_seen REAL, agreed REAL, admin INTEGER DEFAULT 0, disabled INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER, created REAL, expires REAL);
    CREATE TABLE IF NOT EXISTS usage(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, path TEXT, day TEXT, created REAL);
    CREATE INDEX IF NOT EXISTS usage_day ON usage(day, user_id);
    CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, text TEXT, page TEXT,
      created REAL, resolved INTEGER DEFAULT 0);
    """)
    cols = [r[1] for r in con.execute("PRAGMA table_info(projects)")]
    if "user_id" not in cols:
        con.execute("ALTER TABLE projects ADD COLUMN user_id INTEGER")
    if HOSTED:
        con.execute("PRAGMA journal_mode=WAL")
    con.commit(); con.close()
    if not HOSTED:
        return

    today = lambda: time.strftime("%Y-%m-%d", time.gmtime())

    def user_by_token(token):
        if not token:
            return None
        u = q("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND s.expires > ?",
              (hashlib.sha256(token.encode()).hexdigest(), time.time()), one=True)
        return u if u and not u["disabled"] else None

    def used(user_id):
        rows = q("SELECT kind, COUNT(*) AS n FROM usage WHERE day = ? AND user_id = ? GROUP BY kind", (today(), user_id))
        d = {r["kind"]: r["n"] for r in rows}
        return {"runs": d.get("run", 0), "actions": d.get("action", 0)}

    def owner_of(kind, oid):
        sql = {"project": "SELECT user_id FROM projects WHERE id = ?",
               "item": "SELECT p.user_id FROM items i JOIN projects p ON p.id = i.project_id WHERE i.id = ?",
               "watch": "SELECT p.user_id FROM watches w JOIN projects p ON p.id = w.project_id WHERE w.id = ?",
               "alert": "SELECT p.user_id FROM alerts a JOIN projects p ON p.id = a.project_id WHERE a.id = ?"}[kind]
        r = q(sql, (oid,), one=True)
        return r["user_id"] if r else None

    def deny(code, msg):
        return JSONResponse(status_code=code, content={"detail": msg})

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path, method = request.url.path, request.method
        if not path.startswith("/api/") or path in PUBLIC:
            return await call_next(request)
        if method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-medr") != "1":
            return deny(403, "Blocked: requests must come from the Medr app.")     # stops cross-site form posts
        user = user_by_token(request.cookies.get(COOKIE))
        if not user:
            return deny(401, "Please sign in.")
        is_admin = bool(user["admin"])
        if (any(re.match(p, path) for p in ADMIN_ONLY) or
                (method != "GET" and any(re.match(p, path) for p in ADMIN_WRITES))) and not is_admin:
            return deny(403, "Admins only.")
        reading_other = False
        for rx, kind in OWNED:
            m = rx.match(path)
            if m and path != "/api/projects/restore":
                owner = owner_of(kind, int(m.group(1)))
                if owner != user["id"]:
                    if is_admin and method == "GET" and owner is not None:
                        reading_other = True          # admins can look, never change
                    else:
                        return deny(404, "Not found.")
        cls = usage_class(method, path)
        if cls and not is_admin:
            lim, u = limits(), used(user["id"])
            if cls == "run":
                if u["runs"] >= lim["runs"]:
                    return deny(429, f"You've used today's {lim['runs']} deep runs (Idea Lab, Assessment Finder, plans). "
                                     "They reset at 7pm Eastern. Use Send feedback if you need more.")
                g = q("SELECT COUNT(*) AS n FROM usage WHERE day = ? AND kind = 'run'", (today(),), one=True)["n"]
                if g >= lim["global_runs"]:
                    return deny(429, "Medr is at its daily limit for everyone. Please try again tomorrow.")
            elif u["actions"] >= lim["actions"]:
                return deny(429, f"You've used today's {lim['actions']} searches. They reset at 7pm Eastern.")
        if time.time() - (user["last_seen"] or 0) > 300:
            ex("UPDATE users SET last_seen = ? WHERE id = ?", (time.time(), user["id"]))
        tok, rtok = _uid.set(user["id"]), _admin_read.set(reading_other)
        request.state.user = user
        try:
            resp = await call_next(request)
        finally:
            _uid.reset(tok); _admin_read.reset(rtok)
        if cls and resp.status_code < 400:
            ex("INSERT INTO usage(user_id, kind, path, day, created) VALUES (?,?,?,?,?)",
               (user["id"], cls, re.sub(r"\d+", "#", path), today(), time.time()))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        if os.getenv("MEDR_SECURE_COOKIE", "1") == "1":
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    # ---------------- auth ----------------
    class SignupIn(BaseModel):
        email: str
        password: str
        name: str = ""
        field: str = ""
        agree: bool = False
        invite: str = ""

    class LoginIn(BaseModel):
        email: str
        password: str

    def start_session(user_id):
        token = secrets.token_urlsafe(32)
        ex("INSERT INTO sessions(token, user_id, created, expires) VALUES (?,?,?,?)",
           (hashlib.sha256(token.encode()).hexdigest(), user_id, time.time(), time.time() + SESSION_DAYS * 86400))
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                        secure=os.getenv("MEDR_SECURE_COOKIE", "1") == "1", path="/")
        return resp

    @app.get("/api/auth/config")
    def auth_config():
        return {"hosted": True, "invite": bool(os.getenv("MEDR_INVITE_CODE"))}

    @app.post("/api/auth/signup")
    def signup(body: SignupIn, request: Request):
        ip = client_ip(request)
        if too_many("signup:" + ip, limit=8, window=3600):
            raise HTTPException(429, "Too many sign-ups from here. Try again in an hour.")
        note_try("signup:" + ip)
        email = body.email.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 200:
            raise HTTPException(400, "Enter a valid email.")
        if len(body.password) < 8:
            raise HTTPException(400, "Use a password with at least 8 characters.")
        if not body.agree:
            raise HTTPException(400, "Please confirm you won't enter patient information.")
        code = os.getenv("MEDR_INVITE_CODE", "")
        if code and not hmac.compare_digest(body.invite.strip().lower(), code.strip().lower()):
            raise HTTPException(400, "That invite code isn't right.")
        if q("SELECT id FROM users WHERE email = ?", (email,), one=True):
            raise HTTPException(400, "There's already an account with that email. Sign in instead.")
        now = time.time()
        user_id = ex("INSERT INTO users(email, pw, name, field, created, last_seen, agreed, admin) VALUES (?,?,?,?,?,?,?,?)",
                     (email, hash_pw(body.password), body.name.strip()[:80], body.field.strip()[:60], now, now, now,
                      1 if email in admin_emails() else 0))
        return start_session(user_id)

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request):
        email, ip = body.email.strip().lower(), client_ip(request)
        if too_many("login:" + ip) or too_many("login:" + email):
            raise HTTPException(429, "Too many tries. Wait 15 minutes and try again.")
        u = q("SELECT * FROM users WHERE email = ?", (email,), one=True)
        if not u or not check_pw(body.password, u["pw"]):
            note_try("login:" + ip); note_try("login:" + email)
            raise HTTPException(400, "Email or password is wrong.")
        if u["disabled"]:
            raise HTTPException(403, "This account is turned off. Contact Caiden.")
        if email in admin_emails() and not u["admin"]:
            ex("UPDATE users SET admin = 1 WHERE id = ?", (u["id"],))
        return start_session(u["id"])

    @app.post("/api/auth/logout")
    def logout(request: Request):
        token = request.cookies.get(COOKIE)
        if token:
            ex("DELETE FROM sessions WHERE token = ?", (hashlib.sha256(token.encode()).hexdigest(),))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/me")
    def me(request: Request):
        u = user_by_token(request.cookies.get(COOKIE))
        if not u:
            return {"hosted": True, "user": None, "invite": bool(os.getenv("MEDR_INVITE_CODE"))}
        lim = limits()
        return {"hosted": True, "user": {"id": u["id"], "email": u["email"], "name": u["name"], "field": u["field"], "admin": bool(u["admin"])},
                "usage": {**used(u["id"]), "runs_cap": lim["runs"], "actions_cap": lim["actions"]}}

    class PwIn(BaseModel):
        old: str
        new: str

    @app.post("/api/account/password")
    def change_pw(body: PwIn, request: Request):
        u = request.state.user
        if not check_pw(body.old, u["pw"]):
            raise HTTPException(400, "Your current password is wrong.")
        if len(body.new) < 8:
            raise HTTPException(400, "Use at least 8 characters.")
        ex("UPDATE users SET pw = ? WHERE id = ?", (hash_pw(body.new), u["id"]))
        return {"ok": True}

    class DeleteMeIn(BaseModel):
        password: str

    @app.post("/api/account/delete")
    def delete_me(body: DeleteMeIn, request: Request):
        u = request.state.user
        if not check_pw(body.password, u["pw"]):
            raise HTTPException(400, "Password is wrong.")
        wipe_user(u["id"])
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    def wipe_user(user_id):
        pids = [r["id"] for r in q("SELECT id FROM projects WHERE user_id = ?", (user_id,))]
        for pid in pids:
            for t in ("items", "watches", "chats", "events", "alerts"):
                ex(f"DELETE FROM {t} WHERE project_id = ?", (pid,))
            ex("DELETE FROM settings WHERE key LIKE ?", (f"alerts_%_{pid}",))
        ex("DELETE FROM projects WHERE user_id = ?", (user_id,))
        ex("DELETE FROM settings WHERE key LIKE ?", (f"u{user_id}:%",))
        for t in ("sessions", "usage"):
            ex(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
        ex("UPDATE feedback SET user_id = NULL WHERE user_id = ?", (user_id,))
        ex("DELETE FROM users WHERE id = ?", (user_id,))

    # ---------------- feedback ----------------
    class FeedbackIn(BaseModel):
        kind: str = "other"
        text: str
        page: str = ""

    @app.post("/api/feedback")
    def send_feedback(body: FeedbackIn, request: Request):
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Write a little about what happened.")
        if too_many(f"fb:{request.state.user['id']}", limit=20, window=3600):
            raise HTTPException(429, "That's a lot of feedback for one hour. Thank you! Try again later.")
        note_try(f"fb:{request.state.user['id']}")
        kind = body.kind if body.kind in ("bug", "confusing", "idea", "other") else "other"
        ex("INSERT INTO feedback(user_id, kind, text, page, created) VALUES (?,?,?,?,?)",
           (request.state.user["id"], kind, text[:4000], body.page[:200], time.time()))
        return {"ok": True}

    # ---------------- admin ----------------
    @app.get("/api/admin/overview")
    def admin_overview():
        now = time.time()
        week = 7 * 86400
        weeks = []
        for i in range(8):
            end = now - i * week
            start = end - week
            weeks.append({"start": start,
                          "signups": q("SELECT COUNT(*) AS n FROM users WHERE created >= ? AND created < ?", (start, end), one=True)["n"],
                          "active": q("SELECT COUNT(DISTINCT user_id) AS n FROM usage WHERE created >= ? AND created < ?",
                                      (start, end), one=True)["n"],
                          "runs": q("SELECT COUNT(*) AS n FROM usage WHERE kind = 'run' AND created >= ? AND created < ?",
                                    (start, end), one=True)["n"]})
        users = q("""SELECT u.id, u.email, u.name, u.field, u.created, u.last_seen, u.admin, u.disabled,
                       (SELECT COUNT(*) FROM projects p WHERE p.user_id = u.id) AS projects,
                       (SELECT COUNT(*) FROM usage x WHERE x.user_id = u.id AND x.kind = 'run' AND x.created > ?) AS runs_week,
                       (SELECT COUNT(*) FROM usage x WHERE x.user_id = u.id AND x.created > ?) AS uses_week
                     FROM users u ORDER BY u.created DESC""", (now - week, now - week))
        fb = q("""SELECT f.*, u.email FROM feedback f LEFT JOIN users u ON u.id = f.user_id
                  ORDER BY f.resolved, f.created DESC LIMIT 300""")
        popular = q("SELECT path, COUNT(*) AS n FROM usage WHERE created > ? GROUP BY path ORDER BY n DESC LIMIT 12", (now - week,))
        fields = q("SELECT COALESCE(NULLIF(field, ''), 'Not given') AS field, COUNT(*) AS n FROM users GROUP BY 1 ORDER BY n DESC")
        g = q("SELECT COUNT(*) AS n FROM usage WHERE day = ? AND kind = 'run'", (today(),), one=True)["n"]
        return {"total_users": len(users), "weeks": weeks, "users": users, "feedback": fb, "popular": popular,
                "fields": fields, "today_runs": g, "limits": limits()}

    @app.get("/api/admin/users/{user_id}/projects")
    def admin_user_projects(user_id: int):
        u = q("SELECT id, email, name FROM users WHERE id = ?", (user_id,), one=True)
        if not u:
            raise HTTPException(404, "No such user.")
        projects = q("""SELECT p.id, p.name, p.goal, p.created,
                          (SELECT COUNT(*) FROM items i WHERE i.project_id = p.id) AS items,
                          (SELECT MAX(created) FROM events e WHERE e.project_id = p.id) AS last_activity
                        FROM projects p WHERE p.user_id = ? ORDER BY p.created DESC""", (user_id,))
        return {"user": u, "projects": projects}

    @app.post("/api/admin/feedback/{fid}/resolve")
    def admin_resolve(fid: int):
        ex("UPDATE feedback SET resolved = 1 - resolved WHERE id = ?", (fid,))
        return {"ok": True}

    @app.post("/api/admin/users/{user_id}/reset")
    def admin_reset(user_id: int):
        temp = "-".join(secrets.token_hex(3) for _ in range(3))
        ex("UPDATE users SET pw = ? WHERE id = ?", (hash_pw(temp), user_id))
        ex("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return {"temp_password": temp}

    @app.post("/api/admin/users/{user_id}/toggle")
    def admin_toggle(user_id: int, request: Request):
        if user_id == request.state.user["id"]:
            raise HTTPException(400, "You can't turn off your own account.")
        ex("UPDATE users SET disabled = 1 - disabled WHERE id = ?", (user_id,))
        ex("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return {"ok": True}
