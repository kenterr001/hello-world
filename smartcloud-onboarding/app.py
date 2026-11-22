"""
Smart Cloud Sales & Onboarding Portal
Joint portal for Melita and Smart Technologies.

Single-file Flask application backed by SQLite. All data stays on the host
(DATA_DIR). See README.md for deployment on a Smart Cloud VPS.
"""
import argparse
import csv
import functools
import io
import json
import logging
import os
import secrets
import shutil
import smtplib
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "portal.db")
CATALOGUE_PATH = os.path.join(DATA_DIR, "catalogue.json")
SECRET_PATH = os.path.join(DATA_DIR, ".secret_key")

ALLOWED_DOMAINS = [d.strip().lower() for d in os.environ.get(
    "ALLOWED_EMAIL_DOMAINS", "melita.com,smarttech.com.mt").split(",") if d.strip()]
TECH_TEAM_EMAILS = [e.strip() for e in os.environ.get("TECH_TEAM_EMAILS", "").split(",") if e.strip()]
BASE_URL = os.environ.get("APP_BASE_URL", "http://localhost:8000").rstrip("/")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
CURRENCY = os.environ.get("CURRENCY_SYMBOL", "€")

STAGES = [
    ("presales", "Pre-Sales"),
    ("tech_review", "Technical Review"),
    ("onboarding", "Onboarding"),
    ("finalization", "Finalization"),
    ("networking", "Networking Call"),
    ("live", "Live"),
]
STAGE_LABEL = dict(STAGES + [("lost", "Lost")])
STAGE_ORDER = [s for s, _ in STAGES]
DELIVERY_STAGES = ["tech_review", "onboarding", "finalization", "networking"]
ROLES = {
    "admin": "Administrator",
    "sales": "Sales",
    "technical": "Technical",
    "viewer": "Viewer (read only)",
}

log = logging.getLogger("portal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# --------------------------------------------------------------------------- setup
def _secret_key():
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "w") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(SECRET_PATH, 0o600)
    with open(SECRET_PATH) as fh:
        return fh.read().strip()


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SECRET_KEY=_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    SESSION_COOKIE_NAME="sc_portal",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=10),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    company TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    password_hash TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    invite_token TEXT,
    invite_expires TEXT,
    last_login TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deals (
    id INTEGER PRIMARY KEY,
    customer TEXT NOT NULL,
    title TEXT,
    contact_name TEXT, contact_email TEXT, contact_phone TEXT,
    service_type TEXT NOT NULL,
    partner TEXT NOT NULL,
    lead_source TEXT,
    owner_id INTEGER REFERENCES users(id),
    probability INTEGER NOT NULL DEFAULT 10,
    value_mrc REAL NOT NULL DEFAULT 0,
    value_otc REAL NOT NULL DEFAULT 0,
    term_months INTEGER NOT NULL DEFAULT 12,
    expected_close TEXT,
    next_step TEXT,
    notes TEXT,
    stage TEXT NOT NULL DEFAULT 'presales',
    stage_changed_at TEXT NOT NULL,
    lost_reason TEXT,
    networking_call_at TEXT,
    networking_notes TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sheets (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER UNIQUE NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    data TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'draft',
    version INTEGER NOT NULL DEFAULT 0,
    submitted_by INTEGER, submitted_at TEXT,
    reviewed_by INTEGER, reviewed_at TEXT, review_notes TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    title TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    done_by INTEGER, done_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER REFERENCES deals(id) ON DELETE CASCADE,
    user_id INTEGER,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deals_stage ON deals(stage);
CREATE INDEX IF NOT EXISTS idx_activity_deal ON activity(deal_id);
CREATE INDEX IF NOT EXISTS idx_tasks_deal ON tasks(deal_id);
"""


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    if not os.path.exists(CATALOGUE_PATH):
        shutil.copy(os.path.join(BASE_DIR, "catalogue.default.json"), CATALOGUE_PATH)


def catalogue():
    with open(CATALOGUE_PATH) as fh:
        return json.load(fh)


def log_activity(deal_id, kind, message, user_id=None):
    uid = user_id if user_id is not None else (g.user["id"] if g.get("user") else None)
    get_db().execute(
        "INSERT INTO activity (deal_id, user_id, kind, message, created_at) VALUES (?,?,?,?,?)",
        (deal_id, uid, kind, message, now()))


# --------------------------------------------------------------------------- email
def send_mail(recipients, subject, body):
    recipients = [r for r in recipients if r]
    host = os.environ.get("SMTP_HOST")
    if not recipients:
        return False
    if not host:
        log.info("SMTP not configured; would email %s: %s", recipients, subject)
        return False
    msg = EmailMessage()
    msg["From"] = os.environ.get("SMTP_FROM", "smartcloud-portal@localhost")
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        with smtplib.SMTP(host, port, timeout=15) as s:
            if os.environ.get("SMTP_STARTTLS", "1") == "1":
                s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception as exc:  # never break a workflow because mail failed
        log.error("Email to %s failed: %s", recipients, exc)
        return False


# --------------------------------------------------------------------------- security
_login_attempts = defaultdict(list)


def rate_limited(key, limit=8, window=900):
    t = time.time()
    _login_attempts[key] = [x for x in _login_attempts[key] if t - x < window]
    if len(_login_attempts[key]) >= limit:
        return True
    _login_attempts[key].append(t)
    return False


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("uid")
    if uid:
        row = get_db().execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
        if row and session.get("pwv") == (row["password_hash"] or "")[-12:]:
            g.user = row
        else:
            session.clear()
    if request.method == "POST":
        sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing form token. Reload the page and try again.")


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    return resp


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def role_required(*roles):
    def deco(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] not in roles and g.user["role"] != "admin":
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def can(action):
    if not g.get("user"):
        return False
    r = g.user["role"]
    if r == "admin":
        return True
    return {
        "edit_deal": r == "sales",
        "fill_sheet": r == "sales",
        "review": r == "technical",
        "delivery": r == "technical",
        "comment": r in ("sales", "technical"),
    }.get(action, False)


app.jinja_env.globals.update(can=can, STAGES=STAGES, STAGE_LABEL=STAGE_LABEL, ROLES=ROLES,
                             CURRENCY=CURRENCY, DELIVERY_STAGES=DELIVERY_STAGES)


def email_allowed(email):
    return "@" in email and email.rsplit("@", 1)[1].lower() in ALLOWED_DOMAINS


# --------------------------------------------------------------------------- filters
@app.template_filter("money")
def money(v):
    try:
        return f"{CURRENCY}{float(v or 0):,.0f}"
    except (TypeError, ValueError):
        return f"{CURRENCY}0"


@app.template_filter("dt")
def fmt_dt(v, with_time=True):
    if not v:
        return "—"
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        return v
    return d.strftime("%d %b %Y %H:%M" if with_time and (d.hour or d.minute) else "%d %b %Y")


@app.template_filter("days_since")
def days_since(v):
    if not v:
        return 0
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - d).days)


def deal_tcv(d):
    return (d["value_mrc"] or 0) * (d["term_months"] or 0) + (d["value_otc"] or 0)


app.jinja_env.globals["tcv"] = deal_tcv


def slug(s):
    return "".join(ch if ch.isalnum() else "-" for ch in (s or "").lower()).strip("-")


app.jinja_env.filters["slug"] = slug


def deal_ref(d):
    return f"SC-{d['created_at'][:4]}-{d['id']:04d}"


app.jinja_env.globals["ref"] = deal_ref
app.jinja_env.globals["has_any"] = lambda data, fields: any(data.get(k) for k, _ in fields)


# --------------------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw = request.form.get("password", "")
        if rate_limited(request.remote_addr or "?") or rate_limited("u:" + email, limit=6):
            flash("Too many attempts. Please wait 15 minutes.", "error")
            return render_template("login.html"), 429
        row = get_db().execute("SELECT * FROM users WHERE email=? AND active=1", (email,)).fetchone()
        if row and row["password_hash"] and email_allowed(email) and check_password_hash(row["password_hash"], pw):
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            session["pwv"] = row["password_hash"][-12:]
            get_db().execute("UPDATE users SET last_login=? WHERE id=?", (now(), row["id"]))
            get_db().commit()
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/invite/<token>", methods=["GET", "POST"])
def accept_invite(token):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE invite_token=? AND active=1", (token,)).fetchone()
    if not row or not row["invite_expires"] or datetime.fromisoformat(row["invite_expires"]) < datetime.now(timezone.utc):
        return render_template("message.html", title="Link expired",
                               message="This secure link is invalid or has expired. Ask a portal administrator for a new one."), 410
    if request.method == "POST":
        pw = request.form.get("password", "")
        if len(pw) < 12:
            flash("Password must be at least 12 characters.", "error")
        elif pw != request.form.get("password2"):
            flash("Passwords do not match.", "error")
        else:
            h = generate_password_hash(pw)
            db.execute("UPDATE users SET password_hash=?, invite_token=NULL, invite_expires=NULL WHERE id=?", (h, row["id"]))
            db.commit()
            session.clear()
            session["uid"] = row["id"]
            session["pwv"] = h[-12:]
            flash("Welcome! Your account is ready.", "ok")
            return redirect(url_for("dashboard"))
    return render_template("invite.html", u=row)


def make_invite(db, user_id):
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc) + timedelta(hours=72)).replace(microsecond=0).isoformat()
    db.execute("UPDATE users SET invite_token=?, invite_expires=? WHERE id=?", (token, exp, user_id))
    return f"{BASE_URL}/invite/{token}"


# --------------------------------------------------------------------------- dashboard
def all_deals(include_lost=False):
    q = """SELECT d.*, u.name AS owner_name,
                  s.status AS sheet_status,
                  (SELECT COUNT(*) FROM tasks t WHERE t.deal_id=d.id AND t.stage=d.stage) AS tasks_total,
                  (SELECT COUNT(*) FROM tasks t WHERE t.deal_id=d.id AND t.stage=d.stage AND t.done=1) AS tasks_done
           FROM deals d LEFT JOIN users u ON u.id=d.owner_id
           LEFT JOIN sheets s ON s.deal_id=d.id"""
    if not include_lost:
        q += " WHERE d.stage != 'lost'"
    q += " ORDER BY d.updated_at DESC"
    return get_db().execute(q).fetchall()


@app.route("/")
@login_required
def dashboard():
    cat = catalogue()
    deals = all_deals(include_lost=True)
    f = {k: request.args.get(k, "") for k in ("stage", "service", "partner", "owner", "q")}

    def match(d):
        if f["stage"] and d["stage"] != f["stage"]:
            return False
        if not f["stage"] and d["stage"] == "lost":
            return False
        if f["service"] and d["service_type"] != f["service"]:
            return False
        if f["partner"] and d["partner"] != f["partner"]:
            return False
        if f["owner"] and str(d["owner_id"]) != f["owner"]:
            return False
        if f["q"]:
            hay = " ".join(str(d[k] or "") for k in ("customer", "title", "contact_name", "notes", "next_step")).lower()
            if f["q"].lower() not in hay and f["q"].lower() not in deal_ref(d).lower():
                return False
        return True

    active = [d for d in deals if d["stage"] != "lost"]
    filtered = [d for d in deals if match(d)]
    presales = [d for d in active if d["stage"] == "presales"]
    won = [d for d in active if d["stage"] != "presales"]

    stage_stats = []
    for key, label in STAGES:
        ds = [d for d in active if d["stage"] == key]
        stage_stats.append({"key": key, "label": label, "count": len(ds),
                            "tcv": sum(deal_tcv(d) for d in ds), "mrc": sum(d["value_mrc"] for d in ds)})

    service_stats = []
    for st in cat["service_types"]:
        ds = [d for d in presales if d["service_type"] == st]
        service_stats.append({"label": st, "count": len(ds), "tcv": sum(deal_tcv(d) for d in ds),
                              "weighted": sum(deal_tcv(d) * d["probability"] / 100 for d in ds)})
    partner_stats = []
    for p in cat["partners"]:
        ds = [d for d in active if d["partner"] == p]
        partner_stats.append({"label": p, "count": len(ds), "tcv": sum(deal_tcv(d) for d in ds)})

    kpi = {
        "pipeline_tcv": sum(deal_tcv(d) for d in presales),
        "weighted": sum(deal_tcv(d) * d["probability"] / 100 for d in presales),
        "pipeline_count": len(presales),
        "pipeline_mrc": sum(d["value_mrc"] for d in presales),
        "won_mrc": sum(d["value_mrc"] for d in won),
        "in_delivery": len([d for d in active if d["stage"] in DELIVERY_STAGES]),
        "live": len([d for d in active if d["stage"] == "live"]),
        "awaiting_review": len([d for d in active if d["stage"] == "tech_review"]),
        "avg_prob": round(sum(d["probability"] for d in presales) / len(presales)) if presales else 0,
    }
    owners = get_db().execute("SELECT id, name FROM users WHERE role IN ('sales','admin') ORDER BY name").fetchall()
    return render_template("dashboard.html", deals=filtered, kpi=kpi, stage_stats=stage_stats,
                           service_stats=service_stats, partner_stats=partner_stats, f=f, cat=cat,
                           owners=owners, delivery=[d for d in active if d["stage"] in DELIVERY_STAGES],
                           filtering=any(f.values()))


@app.route("/board")
@login_required
def board():
    deals = all_deals()
    cols = [(k, l, [d for d in deals if d["stage"] == k]) for k, l in STAGES]
    return render_template("board.html", cols=cols)


@app.route("/export.csv")
@login_required
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Ref", "Customer", "Opportunity", "Service", "Partner", "Owner", "Stage", "Probability %",
                "MRC", "One-off", "Term (months)", "TCV", "Weighted TCV", "Expected close", "Next step",
                "Contact", "Contact email", "Lead source", "Created", "Updated"])
    for d in all_deals(include_lost=True):
        w.writerow([deal_ref(d), d["customer"], d["title"], d["service_type"], d["partner"], d["owner_name"],
                    STAGE_LABEL.get(d["stage"]), d["probability"], d["value_mrc"], d["value_otc"],
                    d["term_months"], deal_tcv(d), round(deal_tcv(d) * d["probability"] / 100, 2),
                    d["expected_close"], d["next_step"], d["contact_name"], d["contact_email"],
                    d["lead_source"], d["created_at"], d["updated_at"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=smartcloud-pipeline-{datetime.now():%Y%m%d}.csv"})


# --------------------------------------------------------------------------- deals
def get_deal(deal_id):
    d = get_db().execute("""SELECT d.*, u.name AS owner_name, u.email AS owner_email
                            FROM deals d LEFT JOIN users u ON u.id=d.owner_id WHERE d.id=?""", (deal_id,)).fetchone()
    if not d:
        abort(404)
    return d


def _deal_form(cat):
    fm = request.form
    errors = []
    data = {
        "customer": fm.get("customer", "").strip(),
        "title": fm.get("title", "").strip(),
        "contact_name": fm.get("contact_name", "").strip(),
        "contact_email": fm.get("contact_email", "").strip(),
        "contact_phone": fm.get("contact_phone", "").strip(),
        "service_type": fm.get("service_type", ""),
        "partner": fm.get("partner", ""),
        "lead_source": fm.get("lead_source", ""),
        "owner_id": fm.get("owner_id") or None,
        "expected_close": fm.get("expected_close") or None,
        "next_step": fm.get("next_step", "").strip(),
        "notes": fm.get("notes", "").strip(),
    }
    for k, lo, hi, typ in (("probability", 0, 100, int), ("value_mrc", 0, 1e9, float),
                           ("value_otc", 0, 1e9, float), ("term_months", 1, 120, int)):
        try:
            v = typ(fm.get(k) or 0)
            if not lo <= v <= hi:
                raise ValueError
            data[k] = v
        except ValueError:
            errors.append(f"{k.replace('_', ' ').title()} is not valid.")
    if not data["customer"]:
        errors.append("Customer name is required.")
    if data["service_type"] not in cat["service_types"]:
        errors.append("Select a service type.")
    if data["partner"] not in cat["partners"]:
        errors.append("Select the originating partner.")
    return data, errors


@app.route("/deals/new", methods=["GET", "POST"])
@role_required("sales")
def deal_new():
    cat = catalogue()
    users = get_db().execute("SELECT id, name, company FROM users WHERE active=1 AND role IN ('sales','admin') ORDER BY name").fetchall()
    if request.method == "POST":
        data, errors = _deal_form(cat)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("deal_form.html", d=data, cat=cat, users=users, new=True)
        db = get_db()
        ts = now()
        cur = db.execute(f"""INSERT INTO deals ({','.join(data)}, stage, stage_changed_at, created_by, created_at, updated_at)
                             VALUES ({','.join('?' * len(data))}, 'presales', ?, ?, ?, ?)""",
                         (*data.values(), ts, g.user["id"], ts, ts))
        log_activity(cur.lastrowid, "created", f"Lead created ({data['service_type']}, {data['probability']}%)")
        db.commit()
        flash("Lead created.", "ok")
        return redirect(url_for("deal_view", deal_id=cur.lastrowid))
    blank = {"probability": 20, "term_months": 12, "owner_id": g.user["id"],
             "partner": g.user["company"] if g.user["company"] in cat["partners"] else ""}
    return render_template("deal_form.html", d=blank, cat=cat, users=users, new=True)


@app.route("/deals/<int:deal_id>/edit", methods=["GET", "POST"])
@role_required("sales")
def deal_edit(deal_id):
    cat = catalogue()
    d = get_deal(deal_id)
    users = get_db().execute("SELECT id, name, company FROM users WHERE active=1 AND role IN ('sales','admin') ORDER BY name").fetchall()
    if request.method == "POST":
        data, errors = _deal_form(cat)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("deal_form.html", d={**dict(d), **data}, cat=cat, users=users, new=False)
        changes = []
        for k in ("probability", "value_mrc", "value_otc", "service_type", "expected_close", "term_months"):
            if str(d[k] or "") != str(data[k] or ""):
                changes.append(f"{k.replace('_', ' ')}: {d[k]} → {data[k]}")
        db = get_db()
        db.execute(f"UPDATE deals SET {', '.join(k + '=?' for k in data)}, updated_at=? WHERE id=?",
                   (*data.values(), now(), deal_id))
        log_activity(deal_id, "updated", "Details updated" + (": " + "; ".join(changes) if changes else ""))
        db.commit()
        flash("Saved.", "ok")
        return redirect(url_for("deal_view", deal_id=deal_id))
    return render_template("deal_form.html", d=d, cat=cat, users=users, new=False)


@app.route("/deals/<int:deal_id>")
@login_required
def deal_view(deal_id):
    d = get_deal(deal_id)
    db = get_db()
    sheet = db.execute("SELECT * FROM sheets WHERE deal_id=?", (deal_id,)).fetchone()
    tasks = db.execute("""SELECT t.*, u.name AS done_by_name FROM tasks t LEFT JOIN users u ON u.id=t.done_by
                          WHERE deal_id=? ORDER BY position""", (deal_id,)).fetchall()
    acts = db.execute("""SELECT a.*, u.name AS user_name FROM activity a LEFT JOIN users u ON u.id=a.user_id
                         WHERE deal_id=? ORDER BY a.id DESC""", (deal_id,)).fetchall()
    grouped = [(k, STAGE_LABEL[k], [t for t in tasks if t["stage"] == k]) for k in ("onboarding", "finalization", "networking")]
    return render_template("deal_view.html", d=d, sheet=sheet, grouped=grouped, acts=acts,
                           open_tasks=[t for t in tasks if t["stage"] == d["stage"] and not t["done"]])


@app.route("/deals/<int:deal_id>/comment", methods=["POST"])
@login_required
def deal_comment(deal_id):
    if not can("comment"):
        abort(403)
    get_deal(deal_id)
    msg = request.form.get("message", "").strip()
    if msg:
        log_activity(deal_id, "comment", msg[:4000])
        get_db().execute("UPDATE deals SET updated_at=? WHERE id=?", (now(), deal_id))
        get_db().commit()
    return redirect(url_for("deal_view", deal_id=deal_id) + "#activity")


@app.route("/deals/<int:deal_id>/lost", methods=["POST"])
@role_required("sales")
def deal_lost(deal_id):
    d = get_deal(deal_id)
    reason = request.form.get("reason", "").strip() or "No reason given"
    db = get_db()
    if d["stage"] == "lost":
        db.execute("UPDATE deals SET stage='presales', lost_reason=NULL, stage_changed_at=?, updated_at=? WHERE id=?", (now(), now(), deal_id))
        log_activity(deal_id, "stage", "Lead re-opened to Pre-Sales")
    else:
        db.execute("UPDATE deals SET stage='lost', lost_reason=?, stage_changed_at=?, updated_at=? WHERE id=?", (reason, now(), now(), deal_id))
        log_activity(deal_id, "stage", f"Marked as lost: {reason}")
    db.commit()
    return redirect(url_for("deal_view", deal_id=deal_id))


def _ensure_tasks(db, deal_id, stage):
    if db.execute("SELECT 1 FROM tasks WHERE deal_id=? AND stage=?", (deal_id, stage)).fetchone():
        return
    for i, title in enumerate(catalogue()["checklists"].get(stage, [])):
        db.execute("INSERT INTO tasks (deal_id, stage, title, position) VALUES (?,?,?,?)",
                   (deal_id, stage, title, STAGE_ORDER.index(stage) * 100 + i))


def _set_stage(db, deal_id, stage, msg):
    db.execute("UPDATE deals SET stage=?, stage_changed_at=?, updated_at=? WHERE id=?", (stage, now(), now(), deal_id))
    if stage in ("onboarding", "finalization", "networking"):
        _ensure_tasks(db, deal_id, stage)
    log_activity(deal_id, "stage", msg)


@app.route("/deals/<int:deal_id>/advance", methods=["POST"])
@role_required("technical")
def deal_advance(deal_id):
    d = get_deal(deal_id)
    db = get_db()
    nxt = {"onboarding": "finalization", "finalization": "networking", "networking": "live"}.get(d["stage"])
    if not nxt:
        abort(400)
    open_n = db.execute("SELECT COUNT(*) FROM tasks WHERE deal_id=? AND stage=? AND done=0", (deal_id, d["stage"])).fetchone()[0]
    if open_n and not (g.user["role"] == "admin" and request.form.get("force") == "1"):
        flash(f"{open_n} checklist item(s) still open for {STAGE_LABEL[d['stage']]}. Complete them first.", "error")
        return redirect(url_for("deal_view", deal_id=deal_id))
    if nxt == "networking":
        when = request.form.get("networking_call_at")
        if when:
            db.execute("UPDATE deals SET networking_call_at=? WHERE id=?", (when, deal_id))
    if d["stage"] == "networking":
        notes = request.form.get("networking_notes", "").strip()
        if notes:
            db.execute("UPDATE deals SET networking_notes=? WHERE id=?", (notes, deal_id))
    _set_stage(db, deal_id, nxt, f"Moved {STAGE_LABEL[d['stage']]} → {STAGE_LABEL[nxt]}"
               + (" (admin override, open items skipped)" if open_n else ""))
    db.commit()
    if d["owner_email"]:
        send_mail([d["owner_email"]], f"[{deal_ref(d)}] {d['customer']} is now at {STAGE_LABEL[nxt]}",
                  f"{d['customer']} moved to {STAGE_LABEL[nxt]}.\n\n{BASE_URL}/deals/{deal_id}")
    flash(f"Moved to {STAGE_LABEL[nxt]}.", "ok")
    return redirect(url_for("deal_view", deal_id=deal_id))


@app.route("/deals/<int:deal_id>/networking", methods=["POST"])
@login_required
def deal_networking(deal_id):
    if not (can("delivery") or can("edit_deal")):
        abort(403)
    get_deal(deal_id)
    db = get_db()
    db.execute("UPDATE deals SET networking_call_at=?, networking_notes=?, updated_at=? WHERE id=?",
               (request.form.get("networking_call_at") or None, request.form.get("networking_notes", "").strip(), now(), deal_id))
    log_activity(deal_id, "updated", f"Networking call details updated ({request.form.get('networking_call_at') or 'no date'})")
    db.commit()
    return redirect(url_for("deal_view", deal_id=deal_id))


@app.route("/tasks/<int:task_id>/toggle", methods=["POST"])
@role_required("technical")
def task_toggle(task_id):
    db = get_db()
    t = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        abort(404)
    done = 0 if t["done"] else 1
    db.execute("UPDATE tasks SET done=?, done_by=?, done_at=? WHERE id=?",
               (done, g.user["id"] if done else None, now() if done else None, task_id))
    db.execute("UPDATE deals SET updated_at=? WHERE id=?", (now(), t["deal_id"]))
    log_activity(t["deal_id"], "task", f"{'✓ Completed' if done else '↺ Re-opened'}: {t['title']}")
    db.commit()
    return redirect(url_for("deal_view", deal_id=t["deal_id"]) + "#checklist")


@app.route("/deals/<int:deal_id>/tasks", methods=["POST"])
@role_required("technical")
def task_add(deal_id):
    d = get_deal(deal_id)
    title = request.form.get("title", "").strip()
    if title and d["stage"] in ("onboarding", "finalization", "networking"):
        db = get_db()
        pos = db.execute("SELECT COALESCE(MAX(position),0)+1 FROM tasks WHERE deal_id=?", (deal_id,)).fetchone()[0]
        db.execute("INSERT INTO tasks (deal_id, stage, title, position) VALUES (?,?,?,?)", (deal_id, d["stage"], title[:300], pos))
        log_activity(deal_id, "task", f"Checklist item added: {title[:300]}")
        db.commit()
    return redirect(url_for("deal_view", deal_id=deal_id) + "#checklist")


# --------------------------------------------------------------------------- onboarding sheet
SHEET_TEXT_FIELDS = [
    # customer
    "legal_name", "company_reg", "vat_no", "billing_address", "billing_name", "billing_email", "billing_phone",
    "tech_name", "tech_email", "tech_phone", "alt_name", "alt_email", "alt_phone", "po_number",
    "requested_start", "contract_term", "sla_level", "pricing_ref", "agreed_mrc", "agreed_otc",
    # vps
    "vps_management", "vps_public_ipv4", "vps_ipv6", "vps_private_network", "vps_monitoring", "vps_av",
    "vps_snapshots", "vps_notes",
    # backup
    "bk_product", "bk_source", "bk_protected_count", "bk_quota_tb", "bk_frequency", "bk_retention",
    "bk_window", "bk_encryption", "bk_immutable", "bk_restore_test", "bk_rpo", "bk_rto", "bk_notes",
    # networking
    "net_connectivity", "net_customer_ips", "net_ports", "net_firewall_managed", "net_vpn_peer",
    "net_subnets", "net_bandwidth", "net_dns", "net_notes", "net_call_windows",
    # bespoke / compliance
    "bespoke", "compliance", "data_residency",
    # handover
    "portal_users", "handover_method", "go_live_dependencies",
]
VPS_ROW_FIELDS = ["hostname", "package", "vcpu", "ram_gb", "storage_gb", "storage_tier", "os", "licence", "qty", "role"]


def parse_sheet_form():
    fm = request.form
    data = {k: fm.get(k, "").strip()[:5000] for k in SHEET_TEXT_FIELDS}
    data["services"] = [s for s in fm.getlist("services") if s in ("vps", "backup", "bespoke")]
    rows = []
    cols = {f: fm.getlist("vps_" + f) for f in VPS_ROW_FIELDS}
    for i in range(len(cols["hostname"])):
        row = {f: (cols[f][i] if i < len(cols[f]) else "").strip()[:200] for f in VPS_ROW_FIELDS}
        if any(v for k, v in row.items() if k not in ("qty", "storage_tier", "licence")):
            rows.append(row)
    data["vps"] = rows
    return data


def validate_sheet(data):
    errs = []
    req = {"legal_name": "Legal company name", "billing_email": "Billing contact email",
           "tech_name": "Technical contact name", "tech_email": "Technical contact email",
           "tech_phone": "Technical contact phone", "requested_start": "Requested start date",
           "contract_term": "Contract term", "net_connectivity": "Connectivity requirement"}
    for k, label in req.items():
        if not data.get(k):
            errs.append(f"{label} is required.")
    if not data["services"]:
        errs.append("Select at least one service (VPS, Backup or Bespoke).")
    if "vps" in data["services"] and not data["vps"]:
        errs.append("Add at least one VPS specification.")
    if "backup" in data["services"]:
        for k, label in (("bk_product", "Backup product"), ("bk_retention", "Backup retention"), ("bk_quota_tb", "Backup storage quota")):
            if not data.get(k):
                errs.append(f"{label} is required.")
    if "bespoke" in data["services"] and not data.get("bespoke"):
        errs.append("Describe the bespoke requirements.")
    return errs


@app.route("/deals/<int:deal_id>/sheet", methods=["GET", "POST"])
@role_required("sales")
def sheet_edit(deal_id):
    d = get_deal(deal_id)
    cat = catalogue()
    db = get_db()
    sheet = db.execute("SELECT * FROM sheets WHERE deal_id=?", (deal_id,)).fetchone()
    if d["stage"] not in ("presales", "tech_review") or (sheet and sheet["status"] in ("submitted", "approved")):
        flash("The technical sheet is locked while it is with the technical team or after approval.", "error")
        return redirect(url_for("sheet_view", deal_id=deal_id))
    if request.method == "POST":
        data = parse_sheet_form()
        action = request.form.get("action", "save")
        ts = now()
        if not sheet:
            db.execute("INSERT INTO sheets (deal_id, data, updated_at) VALUES (?,?,?)", (deal_id, json.dumps(data), ts))
        else:
            db.execute("UPDATE sheets SET data=?, updated_at=? WHERE deal_id=?", (json.dumps(data), ts, deal_id))
        if action == "submit":
            errs = validate_sheet(data)
            if errs:
                db.commit()
                for e in errs:
                    flash(e, "error")
                return render_template("sheet_form.html", d=d, s=data, cat=cat, sheet=sheet)
            db.execute("""UPDATE sheets SET status='submitted', version=version+1, submitted_by=?, submitted_at=?,
                          review_notes=NULL WHERE deal_id=?""", (g.user["id"], ts, deal_id))
            db.execute("UPDATE deals SET probability=100 WHERE id=?", (deal_id,))
            _set_stage(db, deal_id, "tech_review", "Deal confirmed – technical onboarding sheet submitted to technical team")
            db.commit()
            sent = send_mail(TECH_TEAM_EMAILS + [u["email"] for u in db.execute("SELECT email FROM users WHERE role='technical' AND active=1")],
                             f"[{deal_ref(d)}] New onboarding sheet for review: {d['customer']}",
                             f"A new technical onboarding sheet has been submitted by {g.user['name']} for {d['customer']} "
                             f"({d['service_type']}).\n\nReview it here: {BASE_URL}/deals/{deal_id}/sheet/view\n")
            flash("Onboarding sheet submitted to the technical team for review." + ("" if sent else " (Email notifications are not configured – the sheet is in the Technical Review queue.)"), "ok")
            return redirect(url_for("deal_view", deal_id=deal_id))
        db.execute("UPDATE deals SET updated_at=? WHERE id=?", (ts, deal_id))
        db.commit()
        flash("Draft saved.", "ok")
        return redirect(url_for("sheet_edit", deal_id=deal_id))
    data = json.loads(sheet["data"]) if sheet else {}
    if not sheet:  # prefill from the deal
        data = {"legal_name": d["customer"], "tech_name": d["contact_name"], "tech_email": d["contact_email"],
                "tech_phone": d["contact_phone"], "contract_term": str(d["term_months"]),
                "agreed_mrc": f"{d['value_mrc']:.2f}", "agreed_otc": f"{d['value_otc']:.2f}",
                "services": {"VPS": ["vps"], "Backup": ["backup"], "VPS + Backup": ["vps", "backup"],
                             "Bespoke": ["bespoke"]}.get(d["service_type"], []), "vps": []}
    return render_template("sheet_form.html", d=d, s=data, cat=cat, sheet=sheet)


@app.route("/deals/<int:deal_id>/sheet/view")
@login_required
def sheet_view(deal_id):
    d = get_deal(deal_id)
    db = get_db()
    sheet = db.execute("""SELECT s.*, su.name AS submitted_name, ru.name AS reviewed_name FROM sheets s
                          LEFT JOIN users su ON su.id=s.submitted_by LEFT JOIN users ru ON ru.id=s.reviewed_by
                          WHERE deal_id=?""", (deal_id,)).fetchone()
    if not sheet:
        flash("No onboarding sheet has been started for this deal yet.", "error")
        return redirect(url_for("deal_view", deal_id=deal_id))
    data = json.loads(sheet["data"])
    def num(v):
        try:
            return float(v or 0)
        except ValueError:
            return 0
    totals = {k: sum(num(r.get(k)) * (num(r.get("qty")) or 1) for r in data.get("vps", []))
              for k in ("vcpu", "ram_gb", "storage_gb")}
    totals["servers"] = sum(num(r.get("qty")) or 1 for r in data.get("vps", []))
    return render_template("sheet_view.html", d=d, sheet=sheet, s=data, totals=totals)


@app.route("/sheet/blank")
@login_required
def sheet_blank():
    return render_template("sheet_blank.html", cat=catalogue())


@app.route("/deals/<int:deal_id>/review", methods=["POST"])
@role_required("technical")
def sheet_review(deal_id):
    d = get_deal(deal_id)
    db = get_db()
    sheet = db.execute("SELECT * FROM sheets WHERE deal_id=?", (deal_id,)).fetchone()
    if not sheet or sheet["status"] != "submitted":
        abort(400)
    decision = request.form.get("decision")
    notes = request.form.get("notes", "").strip()
    ts = now()
    if decision == "approve":
        db.execute("UPDATE sheets SET status='approved', reviewed_by=?, reviewed_at=?, review_notes=? WHERE deal_id=?",
                   (g.user["id"], ts, notes, deal_id))
        _set_stage(db, deal_id, "onboarding", "Technical sheet approved – onboarding started" + (f". Notes: {notes}" if notes else ""))
        subj = f"[{deal_ref(d)}] Technical sheet approved – onboarding started for {d['customer']}"
    elif decision == "changes":
        if not notes:
            flash("Please explain what needs to change.", "error")
            return redirect(url_for("sheet_view", deal_id=deal_id))
        db.execute("UPDATE sheets SET status='changes_requested', reviewed_by=?, reviewed_at=?, review_notes=? WHERE deal_id=?",
                   (g.user["id"], ts, notes, deal_id))
        db.execute("UPDATE deals SET updated_at=? WHERE id=?", (ts, deal_id))
        log_activity(deal_id, "review", f"Changes requested on technical sheet: {notes}")
        subj = f"[{deal_ref(d)}] Changes requested on onboarding sheet for {d['customer']}"
    else:
        abort(400)
    db.commit()
    send_mail([d["owner_email"]], subj, f"{subj}\n\n{notes}\n\n{BASE_URL}/deals/{deal_id}")
    flash("Review recorded.", "ok")
    return redirect(url_for("deal_view", deal_id=deal_id))


@app.route("/review")
@login_required
def review_queue():
    rows = get_db().execute("""SELECT d.*, s.status AS sheet_status, s.submitted_at, s.version, u.name AS owner_name
                               FROM deals d JOIN sheets s ON s.deal_id=d.id LEFT JOIN users u ON u.id=d.owner_id
                               WHERE d.stage='tech_review' ORDER BY s.submitted_at""").fetchall()
    return render_template("review.html", rows=rows)


# --------------------------------------------------------------------------- admin
@app.route("/admin/users", methods=["GET", "POST"])
@role_required("admin")
def admin_users():
    db = get_db()
    invite_link = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            email = request.form.get("email", "").strip().lower()
            name = request.form.get("name", "").strip()
            company = request.form.get("company", "")
            role = request.form.get("role", "viewer")
            if not email_allowed(email):
                flash(f"Only these email domains may be invited: {', '.join(ALLOWED_DOMAINS)}", "error")
            elif not name or role not in ROLES or company not in catalogue()["partners"]:
                flash("Name, company and role are required.", "error")
            elif db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                flash("A user with that email already exists.", "error")
            else:
                cur = db.execute("INSERT INTO users (email, name, company, role, created_at) VALUES (?,?,?,?,?)",
                                 (email, name, company, role, now()))
                invite_link = make_invite(db, cur.lastrowid)
                db.commit()
                send_mail([email], "Your Smart Cloud onboarding portal access",
                          f"Hello {name},\n\nYou have been given access to the Smart Cloud sales & onboarding portal.\n"
                          f"Set your password using this secure link (valid 72 hours):\n\n{invite_link}\n")
                flash(f"User created. Send them the secure link below (valid 72 hours).", "ok")
        elif action in ("reset", "toggle", "role"):
            uid = int(request.form.get("user_id", 0))
            if uid == g.user["id"] and action != "reset":
                flash("You cannot change your own role or disable yourself.", "error")
            elif action == "reset":
                invite_link = make_invite(db, uid)
                db.execute("UPDATE users SET password_hash=NULL WHERE id=?", (uid,))
                db.commit()
                flash("Password reset. Send the user the new secure link below.", "ok")
            elif action == "toggle":
                db.execute("UPDATE users SET active=1-active WHERE id=?", (uid,))
                db.commit()
            elif action == "role" and request.form.get("role") in ROLES:
                db.execute("UPDATE users SET role=? WHERE id=?", (request.form["role"], uid))
                db.commit()
    users = db.execute("SELECT * FROM users ORDER BY company, name").fetchall()
    return render_template("admin_users.html", users=users, invite_link=invite_link,
                           domains=ALLOWED_DOMAINS, partners=catalogue()["partners"])


@app.route("/admin/catalogue", methods=["GET", "POST"])
@role_required("admin")
def admin_catalogue():
    if request.method == "POST":
        raw = request.form.get("catalogue", "")
        try:
            parsed = json.loads(raw)
            for k in ("partners", "service_types", "vps_packages", "operating_systems", "backup_products", "checklists"):
                if k not in parsed:
                    raise ValueError(f"missing key '{k}'")
            shutil.copy(CATALOGUE_PATH, CATALOGUE_PATH + f".{datetime.now():%Y%m%d%H%M%S}.bak")
            with open(CATALOGUE_PATH, "w") as fh:
                json.dump(parsed, fh, indent=2, ensure_ascii=False)
            flash("Catalogue saved. A backup of the previous version was kept on the server.", "ok")
            return redirect(url_for("admin_catalogue"))
        except ValueError as exc:
            flash(f"Not saved – invalid catalogue: {exc}", "error")
            return render_template("admin_catalogue.html", raw=raw)
    with open(CATALOGUE_PATH) as fh:
        return render_template("admin_catalogue.html", raw=fh.read())


@app.route("/process")
@login_required
def process():
    return render_template("process.html", cat=catalogue())


@app.route("/healthz")
def healthz():
    get_db().execute("SELECT 1")
    return "ok"


@app.errorhandler(403)
def forbidden(_e):
    return render_template("message.html", title="Not permitted",
                           message="Your role does not allow this action. Contact a portal administrator."), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("message.html", title="Not found", message="That page does not exist."), 404


init_db()


# --------------------------------------------------------------------------- CLI
def cli():
    p = argparse.ArgumentParser(description="Smart Cloud onboarding portal management")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="create an administrator and print a secure set-password link")
    a.add_argument("--email", required=True)
    a.add_argument("--name", required=True)
    a.add_argument("--company", required=True, choices=["Melita", "Smart Technologies"])
    sub.add_parser("seed-demo", help="load fictional demo deals for review/training")
    r = sub.add_parser("run", help="development server (use gunicorn in production)")
    r.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    with app.app_context():
        db = get_db()
        if args.cmd == "create-admin":
            email = args.email.strip().lower()
            if not email_allowed(email):
                raise SystemExit(f"Email domain not in ALLOWED_EMAIL_DOMAINS ({', '.join(ALLOWED_DOMAINS)})")
            row = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            uid = row["id"] if row else db.execute(
                "INSERT INTO users (email, name, company, role, created_at) VALUES (?,?,?,?,?)",
                (email, args.name, args.company, "admin", now())).lastrowid
            db.execute("UPDATE users SET role='admin', active=1 WHERE id=?", (uid,))
            link = make_invite(db, uid)
            db.commit()
            print(f"\nAdministrator ready. Open this secure link within 72 hours to set the password:\n\n  {link}\n")
        elif args.cmd == "seed-demo":
            from seed_demo import seed
            seed(db, now, _ensure_tasks)
            db.commit()
            print("Demo data loaded.")
        elif args.cmd == "run":
            app.config["SESSION_COOKIE_SECURE"] = False
            app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    cli()
