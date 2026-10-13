import secrets
import csv
import datetime as dt
import io
import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth, settings_store
from .config import COOKIE_SECURE, SECRET_KEY, SESSION_HOURS
from .db import execute, fetchall, fetchone
from .etl.models_config import MODELS
from .etl.runner import make_client, test_connection

BASE = Path(__file__).parent
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, max_age=SESSION_HOURS * 3600,
                   https_only=COOKIE_SECURE, same_site="strict")
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.filters["idr"] = lambda v: "-" if v is None else f"{float(v):,.0f}".replace(",", ".")
templates.env.filters["num"] = lambda v: "-" if v is None else (f"{float(v):,.2f}".rstrip("0").rstrip(".")).replace(",", "X").replace(".", ",").replace("X", ".")
templates.env.filters["plain"] = lambda v: "" if v is None else (f"{float(v):.4f}".rstrip("0").rstrip("."))
templates.env.filters["dt"] = lambda v: v.strftime("%d-%m-%Y %H:%M") if v else "-"


# ---------------------------------------------------------------- keamanan dasar
@app.middleware("http")
async def security_headers(request: Request, call_next):
    request.state.csp_nonce = secrets.token_urlsafe(16)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.url.path == "/ro" or request.url.path.startswith("/ro/"):
        # halaman form RO: Bootstrap CSS dari jsDelivr (versi dikunci di template), skrip inline hanya dengan nonce
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            f"script-src 'self' 'nonce-{request.state.csp_nonce}'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'")
    else:
        resp.headers["Content-Security-Policy"] = \
            "default-src 'self'; style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
    resp.headers["Cache-Control"] = "no-store"
    return resp


class NeedLogin(Exception):
    pass


class MustChange(Exception):
    pass


class Forbidden(Exception):
    pass


@app.exception_handler(NeedLogin)
async def _h1(request, exc):
    return RedirectResponse("/login", 303)


@app.exception_handler(MustChange)
async def _h2(request, exc):
    return RedirectResponse("/change-password", 303)


@app.exception_handler(Forbidden)
async def _h3(request, exc):
    return PlainTextResponse("Tidak punya akses.", status_code=403)


def _user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    return fetchone("SELECT id, username, role, must_change_password FROM app.users "
                    "WHERE id=%s AND is_active", (uid,))


def any_user(request: Request):
    u = _user(request)
    if not u:
        request.session.clear()
        raise NeedLogin
    return u


def logged_user(request: Request):
    u = any_user(request)
    if u["must_change_password"]:
        raise MustChange
    return u


def admin_user(request: Request):
    u = logged_user(request)
    if u["role"] != "admin":
        raise Forbidden
    return u


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


def check_csrf(request: Request, token: str):
    if not token or not secrets.compare_digest(token, request.session.get("csrf", "")):
        raise Forbidden


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = [kind, msg]


def render(request: Request, name: str, user=None, **ctx):
    f = request.session.pop("flash", None)
    try:
        env_label = settings_store.get_all()["env_label"]
    except Exception:
        env_label = ""
    return templates.TemplateResponse(request, name, {
        "user": user, "csrf": csrf_token(request), "flash": f, "env_label": env_label,
        "csp_nonce": getattr(request.state, "csp_nonce", ""), **ctx})


def back(url: str):
    return RedirectResponse(url, 303)


@app.get("/appearance")
def appearance_page(request: Request, user=Depends(logged_user)):
    return render(request, "appearance.html", user)


# ---------------------------------------------------------------- login
@app.get("/health")
def health():
    return PlainTextResponse("ok")


@app.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    u, err = auth.authenticate(username, password)
    if err:
        flash(request, err, "err")
        return back("/login")
    request.session.clear()
    request.session["uid"] = u["id"]
    return back("/change-password" if u["must_change_password"] else "/")


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.clear()
    return back("/login")


@app.get("/change-password")
def change_pw_page(request: Request, user=Depends(any_user)):
    return render(request, "change_password.html", user)


@app.post("/change-password")
def change_pw(request: Request, current: str = Form(""), new: str = Form(""),
              confirm: str = Form(""), csrf: str = Form(""), user=Depends(any_user)):
    check_csrf(request, csrf)
    row = fetchone("SELECT password_hash FROM app.users WHERE id=%s", (user["id"],))
    try:
        auth.ph.verify(row["password_hash"], current)
    except Exception:
        flash(request, "Password saat ini salah.", "err")
        return back("/change-password")
    if new != confirm:
        flash(request, "Konfirmasi password tidak sama.", "err")
        return back("/change-password")
    err = auth.validate_new_password(new, user["username"])
    if err:
        flash(request, err, "err")
        return back("/change-password")
    if new == current:
        flash(request, "Password baru harus berbeda dari yang lama.", "err")
        return back("/change-password")
    auth.set_password(user["id"], new, must_change=False)
    auth.audit(user["username"], "password_changed")
    flash(request, "Password berhasil diganti.")
    return back("/")


# ---------------------------------------------------------------- dashboard & laporan
@app.get("/")
def dashboard(request: Request, user=Depends(logged_user)):
    runs = fetchall("SELECT run_id, started_at, finished_at, status, details FROM etl.run_log "
                    "ORDER BY run_id DESC LIMIT 5")
    stats = fetchone("""SELECT
        (SELECT count(*) FROM dwh.fact_pos_order) AS orders,
        (SELECT count(*) FROM dwh.fact_pos_order_line) AS lines,
        (SELECT count(*) FROM dwh.dim_product) AS products,
        (SELECT count(*) FROM dwh.dim_store) AS stores,
        (SELECT max(full_date) FROM dwh.dim_date d
           JOIN dwh.fact_pos_order o ON o.date_key = d.date_key) AS last_sale_date,
        (SELECT max(snapshot_date) FROM dwh.snapshot_stock_daily) AS last_stock_snapshot""")
    s = settings_store.get_all()
    return render(request, "dashboard.html", user, runs=runs, stats=stats,
                  configured=bool(s["odoo_url"] and s["odoo_api_key"]))


@app.get("/reports")
def reports(request: Request, user=Depends(logged_user)):
    daily = fetchall("""
        SELECT d.full_date, coalesce(s.name, '(toko ' || o.store_id || ')') AS store,
               count(*) AS orders, sum(o.amount_total) AS total
        FROM dwh.fact_pos_order o
        JOIN dwh.dim_date d ON d.date_key = o.date_key
        LEFT JOIN dwh.dim_store s ON s.store_id = o.store_id
        WHERE o.state IN ('paid','done','invoiced')
          AND d.full_date >= (now() AT TIME ZONE 'Asia/Jakarta')::date - 14
        GROUP BY 1, 2 ORDER BY 1 DESC, 2""")
    top = fetchall("""
        SELECT coalesce(p.name, '(produk ' || l.product_id || ')') AS name,
               sum(l.qty) AS qty, sum(l.net_amount) AS net
        FROM dwh.fact_pos_order_line l
        JOIN dwh.fact_pos_order o ON o.order_id = l.order_id
             AND o.state IN ('paid','done','invoiced')
        LEFT JOIN dwh.dim_product p ON p.product_id = l.product_id
        WHERE l.date_key >= to_char((now() AT TIME ZONE 'Asia/Jakarta')::date - 7, 'YYYYMMDD')::int
        GROUP BY 1 ORDER BY net DESC NULLS LAST LIMIT 10""")
    return render(request, "reports.html", user, daily=daily, top=top)


# ---------------------------------------------------------------- laporan scrap
def _scrap_filter(frm: str, to: str, store: str):
    today = dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    try:
        d2 = dt.date.fromisoformat(to) if to else today
    except ValueError:
        d2 = today
    try:
        d1 = dt.date.fromisoformat(frm) if frm else d2 - dt.timedelta(days=6)
    except ValueError:
        d1 = d2 - dt.timedelta(days=6)
    if d1 > d2:
        d1, d2 = d2, d1
    if (d2 - d1).days > 366:
        d1 = d2 - dt.timedelta(days=366)
    sid = int(store) if store.isdigit() else None
    return d1, d2, sid


_SCRAP_WHERE = """f.state = 'done' AND f.date_key BETWEEN %s AND %s
                  AND (%s::int IS NULL OR f.store_id = %s::int)"""


def _scrap_detail(d1, d2, sid, limit):
    k1, k2 = int(d1.strftime("%Y%m%d")), int(d2.strftime("%Y%m%d"))
    return fetchall(f"""
        SELECT d.full_date, coalesce(s.name, '(Bukan toko)') AS store,
               coalesce(p.name, '(produk ' || f.product_id || ')') AS product,
               sum(f.qty) AS qty, sum(f.cost_amount_est) AS nilai
        FROM dwh.fact_scrap f
        JOIN dwh.dim_date d ON d.date_key = f.date_key
        LEFT JOIN dwh.dim_store s ON s.store_id = f.store_id
        LEFT JOIN dwh.dim_product p ON p.product_id = f.product_id
        WHERE {_SCRAP_WHERE}
        GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 5 DESC NULLS LAST
        LIMIT {int(limit)}""", (k1, k2, sid, sid))


@app.get("/reports/scrap")
def scrap_report(request: Request, frm: str = Query("", alias="from"), to: str = "",
                 store: str = "", user=Depends(logged_user)):
    d1, d2, sid = _scrap_filter(frm, to, store)
    k1, k2 = int(d1.strftime("%Y%m%d")), int(d2.strftime("%Y%m%d"))
    summary = fetchall(f"""
        SELECT coalesce(s.name, '(Bukan toko)') AS store, count(*) AS kejadian,
               sum(f.qty) AS qty, sum(f.cost_amount_est) AS nilai
        FROM dwh.fact_scrap f
        LEFT JOIN dwh.dim_store s ON s.store_id = f.store_id
        WHERE {_SCRAP_WHERE}
        GROUP BY 1 ORDER BY nilai DESC NULLS LAST""", (k1, k2, sid, sid))
    detail = _scrap_detail(d1, d2, sid, 2000)
    stores = fetchall("SELECT store_id, name FROM dwh.dim_store WHERE is_active ORDER BY name")
    mapped = fetchone("SELECT count(*) FILTER (WHERE location_id IS NOT NULL) AS m, count(*) AS n "
                      "FROM dwh.dim_store")
    return render(request, "scrap.html", user, d1=d1, d2=d2, sid=sid, stores=stores,
                  summary=summary, detail=detail, mapped=mapped,
                  total_qty=sum((r["qty"] or 0) for r in summary),
                  total_nilai=sum((r["nilai"] or 0) for r in summary))


def _csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@app.get("/reports/scrap.csv")
def scrap_csv(frm: str = Query("", alias="from"), to: str = "", store: str = "",
              user=Depends(logged_user)):
    d1, d2, sid = _scrap_filter(frm, to, store)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["tanggal", "toko", "produk", "qty", "nilai_estimasi"])
    for r in _scrap_detail(d1, d2, sid, 50000):
        wr.writerow([r["full_date"], _csv_safe(r["store"]), _csv_safe(r["product"]),
                     r["qty"], r["nilai"]])
    auth.audit(user["username"], "scrap_csv", f"{d1}..{d2} store={sid}")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="scrap_{d1}_{d2}.csv"'})


# ---------------------------------------------------------------- status ETL
@app.get("/runs")
def runs_page(request: Request, user=Depends(logged_user)):
    runs = fetchall("SELECT run_id, started_at, finished_at, status, details FROM etl.run_log "
                    "ORDER BY run_id DESC LIMIT 30")
    jobs = fetchall("SELECT * FROM app.jobs ORDER BY id DESC LIMIT 15")
    return render(request, "runs.html", user, runs=runs, jobs=jobs)


@app.post("/runs/start")
def runs_start(request: Request, mode: str = Form("incremental"), since: str = Form(""),
               only: str = Form(""), csrf: str = Form(""), user=Depends(admin_user)):
    check_csrf(request, csrf)
    params = {}
    if only.strip():
        wanted = [x.strip() for x in only.split(",") if x.strip()]
        known = {m["model"] for m in MODELS}
        bad = [x for x in wanted if x not in known]
        if bad:
            flash(request, "Model tidak dikenal: " + ", ".join(bad), "err")
            return back("/runs")
        params["only"] = wanted
    if mode == "backfill":
        try:
            dt.datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            flash(request, "Isi tanggal awal backfill (YYYY-MM-DD).", "err")
            return back("/runs")
        params["since"] = since
    if fetchone("SELECT 1 AS x FROM app.jobs WHERE status IN ('queued','running')"):
        flash(request, "Masih ada job yang antre atau berjalan.", "err")
        return back("/runs")
    execute("INSERT INTO app.jobs (params, requested_by) VALUES (%s, %s)",
            (json.dumps(params), user["username"]))
    auth.audit(user["username"], "job_queued", str(params))
    flash(request, "Job dimasukkan ke antrian; scheduler memulainya dalam beberapa detik.")
    return back("/runs")


# ---------------------------------------------------------------- pengaturan
def _validate(form: dict) -> str | None:
    if form["odoo_url"] and not form["odoo_url"].startswith("https://"):
        return "URL Odoo harus diawali https://"
    try:
        if not 1 <= int(form["lookback_days"]) <= 60:
            return "Lookback harus 1-60 hari."
        if not 100 <= int(form["batch_size"]) <= 5000:
            return "Batch size harus 100-5000."
        if not (0 <= int(form["schedule_hour"]) <= 23 and 0 <= int(form["schedule_minute"]) <= 59):
            return "Jam/menit jadwal tidak valid."
    except ValueError:
        return "Angka pada pengaturan tidak valid."
    return None


@app.get("/settings")
def settings_page(request: Request, user=Depends(admin_user)):
    return render(request, "settings.html", user, s=settings_store.get_all())


@app.post("/settings")
async def settings_save(request: Request, user=Depends(admin_user)):
    form = dict(await request.form())
    check_csrf(request, form.get("csrf", ""))
    data = {k: str(form.get(k, "")).strip() for k in settings_store.EDITABLE}
    data["require_all_closed"] = "true" if form.get("require_all_closed") else "false"
    data["schedule_enabled"] = "true" if form.get("schedule_enabled") else "false"
    data["odoo_url"] = data["odoo_url"].rstrip("/")
    data["env_label"] = data["env_label"][:20]
    err = _validate(data)
    if err:
        flash(request, err, "err")
        return back("/settings")
    changed = []
    for k, v in data.items():
        if k == "odoo_api_key" and not v:
            continue
        settings_store.set_value(k, v, user["username"])
        changed.append(k)
    auth.audit(user["username"], "settings_saved", ", ".join(changed))
    bound = (settings_store.get_all()["bound_odoo_url"] or "").lower()
    if bound and data["odoo_url"] and data["odoo_url"].lower() != bound:
        flash(request, f"Tersimpan, tetapi URL ini berbeda dari asal data BI ({bound}). ETL akan ditolak agar data tidak tercampur. "
                       "Kembalikan URL, atau kosongkan data lewat terminal: docker compose exec app python -m app.reset", "err")
        return back("/settings")
    flash(request, "Pengaturan disimpan.")
    return back("/settings")


@app.post("/settings/test")
async def settings_test(request: Request, user=Depends(admin_user)):
    form = dict(await request.form())
    check_csrf(request, form.get("csrf", ""))
    url = str(form.get("odoo_url", "")).strip().rstrip("/")
    key = str(form.get("odoo_api_key", "")).strip() or settings_store.get_all(reveal=True)["odoo_api_key"]
    if not url.startswith("https://") or not key:
        flash(request, "Isi URL (https://) dan API key dulu.", "err")
        return back("/settings")
    ok, msg = test_connection(url, key, str(form.get("odoo_db", "")).strip())
    auth.audit(user["username"], "test_connection", "ok" if ok else "gagal")
    flash(request, msg, "ok" if ok else "err")
    return back("/settings")


# ---------------------------------------------------------------- user
@app.get("/users")
def users_page(request: Request, user=Depends(admin_user)):
    users = fetchall("SELECT id, username, role, is_active, must_change_password, last_login "
                     "FROM app.users ORDER BY id")
    audit = fetchall("SELECT ts, username, action, detail FROM app.audit_log ORDER BY id DESC LIMIT 30")
    return render(request, "users.html", user, users=users, audit=audit)


@app.post("/users/create")
def users_create(request: Request, username: str = Form(""), role: str = Form("reporting"),
                 csrf: str = Form(""), user=Depends(admin_user)):
    check_csrf(request, csrf)
    username = username.strip()
    if not username.replace("_", "").replace(".", "").replace("-", "").isalnum() or len(username) < 3:
        flash(request, "Username minimal 3 karakter (huruf, angka, . _ -).", "err")
        return back("/users")
    if role not in ("admin", "reporting"):
        role = "reporting"
    if fetchone("SELECT 1 AS x FROM app.users WHERE lower(username)=lower(%s)", (username,)):
        flash(request, "Username sudah dipakai.", "err")
        return back("/users")
    pw = auth.random_password()
    auth.create_user(username, role, pw, must_change=True)
    auth.audit(user["username"], "user_created", f"{username} ({role})")
    flash(request, f"User {username} dibuat. Password sementara (tampil sekali): {pw}")
    return back("/users")


@app.post("/users/{uid}/reset")
def users_reset(uid: int, request: Request, csrf: str = Form(""), user=Depends(admin_user)):
    check_csrf(request, csrf)
    t = fetchone("SELECT username FROM app.users WHERE id=%s", (uid,))
    if not t:
        return back("/users")
    pw = auth.random_password()
    auth.set_password(uid, pw, must_change=True)
    auth.audit(user["username"], "password_reset", t["username"])
    flash(request, f"Password {t['username']} direset. Password sementara (tampil sekali): {pw}")
    return back("/users")


@app.post("/users/{uid}/toggle")
def users_toggle(uid: int, request: Request, csrf: str = Form(""), user=Depends(admin_user)):
    check_csrf(request, csrf)
    t = fetchone("SELECT username, role, is_active FROM app.users WHERE id=%s", (uid,))
    if not t:
        return back("/users")
    if uid == user["id"]:
        flash(request, "Tidak bisa menonaktifkan akun sendiri.", "err")
        return back("/users")
    execute("UPDATE app.users SET is_active = NOT is_active WHERE id=%s", (uid,))
    auth.audit(user["username"], "user_toggled", f"{t['username']} -> {'nonaktif' if t['is_active'] else 'aktif'}")
    return back("/users")


# ---------------------------------------------------------------- bahan baku (where-used) & kalkulator
from . import bom_tools as bt  # noqa: E402


def _freshness():
    r = fetchone("SELECT max(finished_at) AS t FROM etl.run_log WHERE status IN ('success','partial')")
    return r["t"] if r else None


def _where_used(cid: int):
    comp = fetchone("SELECT product_id, name, sku, standard_price FROM dwh.dim_product "
                    "WHERE product_id = %s", (cid,))
    rows = fetchall(bt.UP_SQL, (cid,)) if comp else []
    return comp, rows


@app.get("/bom/where-used")
def where_used(request: Request, q: str = "", cid: str = "", newprice: str = "",
               ratio: str = "", user=Depends(logged_user)):
    q = q.strip()[:80]
    matches, comp, rows, impact = None, None, [], []
    if cid.isdigit():
        comp, rows = _where_used(int(cid))
    elif q:
        toks = bt.tokens(q)
        if toks:
            cond, params = bt.like_where(toks)
            matches = fetchall(f"""
                SELECT p.product_id, p.name, p.sku, count(DISTINCT l.bom_id) AS boms
                FROM dwh.dim_product p JOIN dwh.dim_bom_line l ON l.component_id = p.product_id
                WHERE {cond} GROUP BY 1, 2, 3 ORDER BY p.name LIMIT 100""", params)
            if len(matches) == 1:
                comp, rows = _where_used(matches[0]["product_id"])
                matches = None
    sim = None
    if comp:
        np_ = bt.parse_num(newprice)
        rt = bt.parse_num(ratio, 1.0) or 1.0
        if np_ > 0:
            old = float(comp["standard_price"] or 0)
            new_cost = np_ * rt
            impact = []
            for r in fetchall(bt.IMPACT_SQL, (comp["product_id"],)):
                q_ = float(r["qty_per_unit"] or 0)
                hpp = float(r["hpp"] or 0)
                delta = q_ * (new_cost - old)
                impact.append(dict(name=r["root_name"], sku=r["root_sku"], qty=q_, hpp=hpp,
                                   hpp_baru=hpp + delta, delta=delta,
                                   pct=(delta / hpp * 100) if hpp else None))
            sim = dict(old=old, new=np_, ratio=rt, new_cost=new_cost)
    bom_ids = list({r["bom_id"] for r in rows})
    uom_warn = fetchone("SELECT count(*) AS n FROM dwh.v_bom_uom_check WHERE NOT uom_ok AND bom_id = ANY(%s)",
                        (bom_ids,))["n"] if bom_ids else 0
    summary = dict(
        direct=len({r["bom_id"] for r in rows if r["level"] == 1}),
        finished=len({(r["finished_name"], r["finished_sku"]) for r in rows}),
        inactive=len({r["bom_id"] for r in rows if not r["is_active"]}),
        indirect=len({r["bom_id"] for r in rows if r["level"] > 1}),
    ) if comp else None
    return render(request, "where_used.html", user, q=q, matches=matches, comp=comp, rows=rows,
                  summary=summary, sim=sim, impact=impact, newprice=newprice, ratio=ratio,
                  uom_warn=uom_warn, fresh=_freshness())


@app.get("/bom/where-used.csv")
def where_used_csv(cid: int, user=Depends(logged_user)):
    comp, rows = _where_used(cid)
    if not comp:
        return PlainTextResponse("Bahan tidak ditemukan.", status_code=404)
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["bahan", "level", "produk_jadi", "sku", "kode_bom", "id_bom", "tipe_bom",
                 "aktif", "lewat_bahan", "qty_per_unit", "sudah_diganti"])
    for r in rows:
        wr.writerow([_csv_safe(comp["name"]), r["level"], _csv_safe(r["finished_name"]),
                     _csv_safe(r["finished_sku"]), _csv_safe(r["code"]), r["bom_id"], r["bom_type"],
                     "ya" if r["is_active"] else "arsip", _csv_safe(r["via_name"]),
                     r["qty_per_unit"], ""])
    auth.audit(user["username"], "where_used_csv", f"product {cid}")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="bahan_{cid}_dipakai_di.csv"'})


def _calc_rows(pid: int, mode: str):
    if mode == "direct":
        return fetchall("""
            SELECT l.component_id AS cid, p.name, p.sku,
                   sum(l.qty / NULLIF(b.product_qty, 0)) AS qty, p.standard_price AS price
            FROM dwh.v_product_bom v
            JOIN dwh.dim_bom b ON b.bom_id = v.bom_id
            JOIN dwh.dim_bom_line l ON l.bom_id = b.bom_id
            JOIN dwh.dim_product p ON p.product_id = l.component_id
            WHERE v.product_id = %s GROUP BY 1, 2, 3, 5 ORDER BY p.name""", (pid,))
    return fetchall("""
        SELECT component_id AS cid, component_name AS name, component_sku AS sku,
               sum(qty_per_unit) AS qty, component_unit_cost AS price
        FROM dwh.v_bom_exploded WHERE root_product_id = %s AND is_leaf
        GROUP BY 1, 2, 3, 5 ORDER BY component_name""", (pid,))


def _bom_uom_warn(pid):
    r = fetchone("SELECT count(*) AS n FROM dwh.v_bom_uom_check WHERE NOT uom_ok AND bom_id IN "
                 "(SELECT bom_id FROM dwh.v_product_bom WHERE product_id = %s)", (pid,))
    return r["n"] if r else 0


def _num_or_none(v):
    return None if v is None else float(v)


def _calc_ctx(rows, p):
    res = bt.calc_price(rows, p["extra"], p["margin"], p["ppn"], p["round_to"])
    for r in rows:
        r["line"] = (r["qty"] or 0) * (r["price"] or 0)
    return dict(rows=rows, res=res, p=p)


DEFAULT_P = dict(extra=0.0, margin=30.0, ppn=0.0, round_to=0.0)


@app.get("/calculator")
def calculator(request: Request, q: str = "", pid: str = "", mode: str = "full",
               new: str = "", user=Depends(logged_user)):
    q = q.strip()[:80]
    mode = "direct" if mode == "direct" else "full"
    ctx = dict(q=q, matches=None, product=None, mode=mode, is_new=False, fresh=_freshness(),
               rows=[], res=None, p=DEFAULT_P, uom_warn=0)
    if new:
        ctx.update(_calc_ctx([], DEFAULT_P), is_new=True)
    elif pid.isdigit():
        prod = fetchone("""SELECT p.product_id, p.name, p.sku, b.code, b.product_qty
                           FROM dwh.dim_product p
                           LEFT JOIN dwh.v_product_bom v ON v.product_id = p.product_id
                           LEFT JOIN dwh.dim_bom b ON b.bom_id = v.bom_id
                           WHERE p.product_id = %s""", (int(pid),))
        if prod:
            rows = [dict(cid=r["cid"], name=r["name"], sku=r["sku"],
                         qty=_num_or_none(r["qty"]) or 0.0, price=_num_or_none(r["price"]) or 0.0)
                    for r in _calc_rows(int(pid), mode)]
            ctx.update(_calc_ctx(rows, DEFAULT_P), product=prod, uom_warn=_bom_uom_warn(int(pid)))
    elif q:
        toks = bt.tokens(q)
        if toks:
            cond, params = bt.like_where(toks)
            ctx["matches"] = fetchall(f"""
                SELECT p.product_id, p.name, p.sku
                FROM dwh.dim_product p JOIN dwh.v_product_bom v ON v.product_id = p.product_id
                WHERE {cond} ORDER BY p.name LIMIT 100""", params)
    return render(request, "calculator.html", user, **ctx)


@app.post("/calculator")
async def calculator_post(request: Request, user=Depends(logged_user)):
    form = await request.form()
    check_csrf(request, str(form.get("csrf", "")))
    names, qtys, prices = form.getlist("name"), form.getlist("qty"), form.getlist("price")
    rows = []
    for i, nm in enumerate(names[:80]):
        nm = str(nm).strip()[:120]
        qt = bt.parse_num(qtys[i] if i < len(qtys) else "")
        pr = bt.parse_num(prices[i] if i < len(prices) else "")
        if nm or qt or pr:
            rows.append(dict(cid=None, name=nm or "(tanpa nama)", sku=None, qty=qt, price=pr))
    p = dict(extra=bt.parse_num(form.get("extra")), margin=bt.parse_num(form.get("margin"), 30.0),
             ppn=bt.parse_num(form.get("ppn")), round_to=bt.parse_num(form.get("round_to")))
    pid = str(form.get("pid", ""))
    prod = None
    if pid.isdigit():
        prod = fetchone("""SELECT p.product_id, p.name, p.sku, b.code, b.product_qty
                           FROM dwh.dim_product p
                           LEFT JOIN dwh.v_product_bom v ON v.product_id = p.product_id
                           LEFT JOIN dwh.dim_bom b ON b.bom_id = v.bom_id
                           WHERE p.product_id = %s""", (int(pid),))
    mode = "direct" if form.get("mode") == "direct" else "full"
    return render(request, "calculator.html", user, q="", matches=None, product=prod, mode=mode,
                  is_new=not prod, fresh=_freshness(), uom_warn=_bom_uom_warn(prod["product_id"]) if prod else 0,
                  **_calc_ctx(rows, p))


# ---------------------------------------------------------------- harga beli (dari faktur supplier)
from . import price_tools as pt  # noqa: E402


def _price_params(frm, to, mode, min_n, min_pct, direction, sort):
    today = dt.datetime.now(ZoneInfo("Asia/Jakarta")).date()
    try:
        d2 = dt.date.fromisoformat(to) if to else today
    except ValueError:
        d2 = today
    try:
        d1 = dt.date.fromisoformat(frm) if frm else d2 - dt.timedelta(days=29)
    except ValueError:
        d1 = d2 - dt.timedelta(days=29)
    if d1 > d2:
        d1, d2 = d2, d1
    if (d2 - d1).days > 730:
        d1 = d2 - dt.timedelta(days=730)
    span = (d2 - d1).days + 1
    p2 = d1 - dt.timedelta(days=1)
    p1 = p2 - dt.timedelta(days=span - 1)
    mode = mode if mode in pt.MODES else "awal_akhir"
    n = max(1, int(bt.parse_num(min_n, 2)))
    pct = max(0.0, bt.parse_num(min_pct, 0.0))
    direction = direction if direction in ("semua", "naik", "turun") else "semua"
    sort = sort if sort in ("pct", "nilai", "dampak") else "pct"
    return dict(d1=d1, d2=d2, p1=p1, p2=p2, mode=mode, min_n=n, min_pct=pct, direction=direction, sort=sort)


def _price_rows(P):
    raw = fetchall(pt.AGG_SQL, dict(d1=P["d1"], d2=P["d2"], p1=P["p1"], p2=P["p2"]))
    for r in raw:
        for k in ("avg_price", "pmin", "pmax", "qty_total", "first_price", "last_price", "avg_prev"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return pt.compute(raw, P["mode"], P["min_n"], P["min_pct"], P["direction"], P["sort"])


@app.get("/purchase-price")
def purchase_price(request: Request, frm: str = Query("", alias="from"), to: str = "",
                   mode: str = "awal_akhir", min_n: str = "2", min_pct: str = "0",
                   direction: str = "semua", sort: str = "pct", user=Depends(logged_user)):
    P = _price_params(frm, to, mode, min_n, min_pct, direction, sort)
    rows = _price_rows(P)
    skipped = fetchone(pt.SKIPPED_SQL, (P["d1"], P["d2"]))
    return render(request, "purchase_price.html", user, P=P, rows=rows[:300], total=len(rows),
                  modes=pt.MODES, labels=pt.MODE_LABELS[P["mode"]], skipped=skipped, fresh=_freshness())


@app.get("/purchase-price.csv")
def purchase_price_csv(frm: str = Query("", alias="from"), to: str = "", mode: str = "awal_akhir",
                       min_n: str = "2", min_pct: str = "0", direction: str = "semua",
                       sort: str = "pct", user=Depends(logged_user)):
    P = _price_params(frm, to, mode, min_n, min_pct, direction, sort)
    lb = pt.MODE_LABELS[P["mode"]]
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow(["produk", "sku", "satuan_kecil", "jumlah_pembelian", "jumlah_supplier", lb[0], lb[1],
                 "selisih_rp", "selisih_persen", "qty_periode", "dampak_rp", "pembelian_terakhir"])
    for r in _price_rows(P):
        wr.writerow([_csv_safe(r["name"]), _csv_safe(r["sku"]), _csv_safe(r["uom"]), r["n"], r["n_sup"],
                     round(r["base"], 4), round(r["now"], 4), round(r["delta"], 4), round(r["pct"], 2),
                     r["qty_total"], round(r["dampak"], 2), r["last_date"]])
    auth.audit(user["username"], "purchase_price_csv", f"{P['d1']}..{P['d2']} {P['mode']}")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="harga_beli_{P["d1"]}_{P["d2"]}.csv"'})


@app.get("/purchase-price/product")
def purchase_price_product(request: Request, pid: int, frm: str = Query("", alias="from"),
                           to: str = "", user=Depends(logged_user)):
    P = _price_params(frm, to, "awal_akhir", "2", "0", "semua", "pct")
    prod = fetchone("""SELECT p.product_id, p.name, p.sku, uo.name AS uom
                       FROM dwh.dim_product p LEFT JOIN dwh.dim_uom uo ON uo.uom_id = p.uom_id
                       WHERE p.product_id = %s""", (pid,))
    if not prod:
        return PlainTextResponse("Produk tidak ditemukan.", status_code=404)
    hist = fetchall(pt.HISTORY_SQL, (pid, P["d1"], P["d2"]))
    ok = [h for h in hist if h["usable"]]
    prices = [float(h["price_small"]) for h in ok]
    stats = None
    if prices:
        stats = dict(n=len(prices), pmin=min(prices), pmax=max(prices), first=prices[0], last=prices[-1],
                     delta=prices[-1] - prices[0],
                     pct=((prices[-1] - prices[0]) / prices[0] * 100) if prices[0] else None)
    chart = pt.svg_line([(h["invoice_date"], h["price_small"]) for h in ok])
    return render(request, "purchase_price_product.html", user, P=P, prod=prod, hist=hist, stats=stats,
                  chart=chart, fresh=_freshness())


# ---------------------------------------------------------------- inspeksi struktur Odoo (admin, hanya baca)
@app.get("/admin/inspect")
def inspect_page(request: Request, model: str = "", q: str = "", sample: str = "",
                 user=Depends(admin_user)):
    model = model.strip()
    ctx = dict(model=model, q=q.strip(), fields=None, sample_json=None, error=None, sample=bool(sample))
    if model:
        if not re.fullmatch(r"[a-z][a-z0-9_.]{1,60}", model):
            ctx["error"] = "Nama model tidak valid (contoh: uom.uom)."
        else:
            try:
                c = make_client(settings_store.get_all(reveal=True), retries=1)
                fg = c.call(model, "fields_get", attributes=["string", "type", "relation", "store"])
                needle = ctx["q"].lower()
                ctx["fields"] = [dict(name=k, **v) for k, v in sorted(fg.items())
                                 if not needle or needle in k.lower() or needle in str(v.get("string", "")).lower()]
                if sample:
                    recs = c.call(model, "search_read", domain=[], limit=3)
                    txt = json.dumps(recs, ensure_ascii=False, indent=1, default=str)
                    ctx["sample_json"] = txt[:20000] + ("\n... (dipotong)" if len(txt) > 20000 else "")
                auth.audit(user["username"], "inspect", f"{model} q={needle} sample={bool(sample)}")
            except Exception as e:
                ctx["error"] = str(e)[:500]
    return render(request, "inspect.html", user, **ctx)


# ---------------------------------------------------------------- modul Request Order (RO) Toko
from .routes_ro import make_router  # noqa: E402

app.include_router(make_router(logged_user, check_csrf, render))

from .routes_ro_master import make_router as make_master_router  # noqa: E402

app.include_router(make_master_router(admin_user, check_csrf, render))
