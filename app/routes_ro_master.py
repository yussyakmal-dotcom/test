"""CRUD master template RO dan master jadwal (khusus admin).

Dipasang dari web.py, di bawah router RO:
    from .routes_ro_master import make_router as make_master_router
    app.include_router(make_master_router(admin_user, check_csrf, render))
"""
import csv
import io

import psycopg2.errors
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from psycopg2.extras import execute_values
from pydantic import BaseModel, Field

from . import auth
from .bom_tools import tokens
from .db import cursor, fetchall, fetchone
from .ro_logic import clean_name, clean_template, parse_template_csv


class JadwalIn(BaseModel):
    kode_jadwal: int | None = None
    jadwal: str = Field(max_length=200)


class JadwalEdit(BaseModel):
    jadwal: str = Field(max_length=200)


class TemplateIn(BaseModel):
    nama_produk: str = Field(max_length=500)
    kode_gink: str | None = Field(default=None, max_length=200)
    kode_odoo: str = Field(max_length=200)
    kode_dapur: str = Field(max_length=50)


class ImportIn(BaseModel):
    text: str = Field(max_length=3_000_000)
    dry_run: bool = True


def _csv_safe(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _tpl_where(dapur: str, q: str):
    conds, params = [], []
    if dapur:
        conds.append("t.kode_dapur = %s")
        params.append(dapur)
    for tk in tokens(q or ""):
        conds.append("(t.nama_produk ILIKE %s OR t.kode_odoo ILIKE %s OR coalesce(t.kode_gink, '') ILIKE %s)")
        params += [f"%{tk}%"] * 3
    return (" AND ".join(conds) or "TRUE"), params


def make_router(admin_user, check_csrf, render) -> APIRouter:
    router = APIRouter()

    def csrf(request: Request):
        check_csrf(request, request.headers.get("X-CSRF-Token", ""))

    # ---------------------------------------------------------------- halaman
    @router.get("/ro/master/jadwal")
    def page_jadwal(request: Request, user=Depends(admin_user)):
        return render(request, "ro_master_jadwal.html", user, active="jadwal")

    @router.get("/ro/master/template")
    def page_template(request: Request, user=Depends(admin_user)):
        return render(request, "ro_master_template.html", user, active="template")

    # ================================================================ MASTER JADWAL
    @router.get("/api/ro/master/jadwal")
    def jadwal_list(user=Depends(admin_user)):
        return fetchall("""
            SELECT j.kode_jadwal, j.jadwal,
                   (SELECT count(*) FROM app.ro_header h WHERE h.kode_jadwal = j.kode_jadwal) AS dipakai
            FROM app.master_jadwal j ORDER BY j.kode_jadwal""")

    @router.post("/api/ro/master/jadwal")
    def jadwal_create(request: Request, p: JadwalIn, user=Depends(admin_user)):
        csrf(request)
        err, nama = clean_name(p.jadwal)
        if err:
            raise HTTPException(422, err)
        if p.kode_jadwal is not None and not 1 <= p.kode_jadwal <= 9999:
            raise HTTPException(422, "KODE JADWAL harus 1 sampai 9999 (kosongkan untuk otomatis).")
        try:
            with cursor() as cur:
                cur.execute("SELECT 1 FROM app.master_jadwal WHERE upper(jadwal) = %s", (nama,))
                if cur.fetchone():
                    raise HTTPException(409, f"Jadwal {nama} sudah ada.")
                kode = p.kode_jadwal
                if kode is None:
                    cur.execute("SELECT coalesce(max(kode_jadwal), 0) + 1 AS k FROM app.master_jadwal")
                    kode = cur.fetchone()["k"]
                cur.execute("INSERT INTO app.master_jadwal (kode_jadwal, jadwal) VALUES (%s, %s)", (kode, nama))
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(409, "KODE JADWAL sudah dipakai.")
        auth.audit(user["username"], "master_jadwal_create", f"{kode} {nama}")
        return {"kode_jadwal": kode, "jadwal": nama}

    @router.put("/api/ro/master/jadwal/{kode}")
    def jadwal_update(kode: int, request: Request, p: JadwalEdit, user=Depends(admin_user)):
        csrf(request)
        err, nama = clean_name(p.jadwal)
        if err:
            raise HTTPException(422, err)
        with cursor() as cur:
            cur.execute("SELECT 1 FROM app.master_jadwal WHERE upper(jadwal) = %s AND kode_jadwal <> %s",
                        (nama, kode))
            if cur.fetchone():
                raise HTTPException(409, f"Jadwal {nama} sudah ada.")
            cur.execute("UPDATE app.master_jadwal SET jadwal = %s WHERE kode_jadwal = %s RETURNING kode_jadwal",
                        (nama, kode))
            if not cur.fetchone():
                raise HTTPException(404, "Jadwal tidak ditemukan.")
        auth.audit(user["username"], "master_jadwal_update", f"{kode} -> {nama}")
        return {"kode_jadwal": kode, "jadwal": nama}

    @router.delete("/api/ro/master/jadwal/{kode}")
    def jadwal_delete(kode: int, request: Request, user=Depends(admin_user)):
        csrf(request)
        try:
            with cursor() as cur:
                cur.execute("SELECT count(*) AS n FROM app.ro_header WHERE kode_jadwal = %s", (kode,))
                n = cur.fetchone()["n"]
                if n:
                    raise HTTPException(409, f"Jadwal ini dipakai oleh {n} RO, jadi tidak bisa dihapus. "
                                             "Ubah namanya bila perlu.")
                cur.execute("DELETE FROM app.master_jadwal WHERE kode_jadwal = %s RETURNING jadwal", (kode,))
                row = cur.fetchone()
                if not row:
                    raise HTTPException(404, "Jadwal tidak ditemukan.")
        except psycopg2.errors.ForeignKeyViolation:
            raise HTTPException(409, "Jadwal ini masih dipakai RO, jadi tidak bisa dihapus.")
        auth.audit(user["username"], "master_jadwal_delete", f"{kode} {row['jadwal']}")
        return {"ok": True}

    # ================================================================ MASTER TEMPLATE
    @router.get("/api/ro/master/template")
    def template_list(dapur: str = "", q: str = "", limit: int = 100, offset: int = 0,
                      user=Depends(admin_user)):
        limit, offset = max(1, min(limit, 500)), max(0, offset)
        where, params = _tpl_where(dapur, q)
        total = fetchone(f"SELECT count(*) AS n FROM app.master_template_ro t WHERE {where}", params)["n"]
        rows = fetchall(f"""
            SELECT t.id, t.nama_produk, t.kode_gink, t.kode_odoo, t.kode_dapur, d.dapur,
                   CASE WHEN o.n = 0 THEN NULL
                        ELSE EXISTS (SELECT 1 FROM dwh.dim_product p WHERE p.sku = t.kode_odoo) END AS odoo_ok
            FROM app.master_template_ro t
            JOIN app.master_dapur d ON d.kode_dapur = t.kode_dapur
            CROSS JOIN (SELECT count(*) AS n FROM dwh.dim_product) o
            WHERE {where} ORDER BY t.id LIMIT %s OFFSET %s""", params + [limit, offset])
        return {"total": total, "rows": rows}

    @router.get("/api/ro/master/template.csv")
    def template_csv(dapur: str = "", q: str = "", user=Depends(admin_user)):
        where, params = _tpl_where(dapur, q)
        rows = fetchall(f"""SELECT t.nama_produk, t.kode_gink, t.kode_odoo, t.kode_dapur
                            FROM app.master_template_ro t WHERE {where} ORDER BY t.id""", params)
        buf = io.StringIO()
        wr = csv.writer(buf)
        wr.writerow(["NAMA_PRODUK", "KODE_GINK", "KODE_ODOO", "KODE_DAPUR"])
        for r in rows:
            wr.writerow([_csv_safe(r["nama_produk"]), _csv_safe(r["kode_gink"]),
                         _csv_safe(r["kode_odoo"]), r["kode_dapur"]])
        auth.audit(user["username"], "master_template_csv", f"dapur={dapur or '*'} q={q}")
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="master_template_ro.csv"'})

    @router.post("/api/ro/master/template/import")
    def template_import(request: Request, p: ImportIn, user=Depends(admin_user)):
        csrf(request)
        valid = {r["kode_dapur"] for r in fetchall("SELECT kode_dapur FROM app.master_dapur")}
        errs, rows = parse_template_csv(p.text, valid)
        if errs:
            return {"ok": False, "errors": errs[:50], "total_errors": len(errs)}
        keys = tuple((r["kode_dapur"], r["kode_odoo"]) for r in rows)
        with cursor() as cur:
            cur.execute("SELECT kode_dapur, kode_odoo FROM app.master_template_ro "
                        "WHERE (kode_dapur, kode_odoo) IN %s", (keys,))
            existing = {(r["kode_dapur"], r["kode_odoo"]) for r in cur.fetchall()}
            baru = sum(1 for k in keys if k not in existing)
            res = {"ok": True, "dry_run": p.dry_run, "total": len(rows), "baru": baru, "ubah": len(rows) - baru}
            if not p.dry_run:
                # KODE GINK yang dikosongkan di CSV tidak menghapus nilai lama
                execute_values(cur, """
                    INSERT INTO app.master_template_ro (nama_produk, kode_gink, kode_odoo, kode_dapur) VALUES %s
                    ON CONFLICT (kode_dapur, kode_odoo) DO UPDATE SET
                      nama_produk = EXCLUDED.nama_produk,
                      kode_gink = COALESCE(EXCLUDED.kode_gink, app.master_template_ro.kode_gink)""",
                               [(r["nama_produk"], r["kode_gink"], r["kode_odoo"], r["kode_dapur"]) for r in rows],
                               page_size=500)
        if not p.dry_run:
            auth.audit(user["username"], "master_template_import", f"{len(rows)} baris (baru {baru})")
        return res

    @router.post("/api/ro/master/template")
    def template_create(request: Request, p: TemplateIn, user=Depends(admin_user)):
        csrf(request)
        errs, c = clean_template(p.nama_produk, p.kode_gink, p.kode_odoo, p.kode_dapur)
        if errs:
            raise HTTPException(422, " ".join(errs))
        try:
            with cursor() as cur:
                cur.execute("SELECT 1 FROM app.master_dapur WHERE kode_dapur = %s", (c["kode_dapur"],))
                if not cur.fetchone():
                    raise HTTPException(422, "DAPUR tidak dikenal.")
                cur.execute("""INSERT INTO app.master_template_ro (nama_produk, kode_gink, kode_odoo, kode_dapur)
                               VALUES (%s, %s, %s, %s) RETURNING id""",
                            (c["nama_produk"], c["kode_gink"], c["kode_odoo"], c["kode_dapur"]))
                new_id = cur.fetchone()["id"]
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(409, f"KODE ODOO {c['kode_odoo']} sudah ada di dapur {c['kode_dapur']}.")
        auth.audit(user["username"], "master_template_create", f"#{new_id} {c['kode_dapur']} {c['kode_odoo']}")
        return {"id": new_id}

    @router.put("/api/ro/master/template/{tid}")
    def template_update(tid: int, request: Request, p: TemplateIn, user=Depends(admin_user)):
        csrf(request)
        errs, c = clean_template(p.nama_produk, p.kode_gink, p.kode_odoo, p.kode_dapur)
        if errs:
            raise HTTPException(422, " ".join(errs))
        try:
            with cursor() as cur:
                cur.execute("SELECT 1 FROM app.master_dapur WHERE kode_dapur = %s", (c["kode_dapur"],))
                if not cur.fetchone():
                    raise HTTPException(422, "DAPUR tidak dikenal.")
                cur.execute("""UPDATE app.master_template_ro SET nama_produk=%s, kode_gink=%s, kode_odoo=%s,
                               kode_dapur=%s WHERE id=%s RETURNING id""",
                            (c["nama_produk"], c["kode_gink"], c["kode_odoo"], c["kode_dapur"], tid))
                if not cur.fetchone():
                    raise HTTPException(404, "Produk tidak ditemukan.")
        except psycopg2.errors.UniqueViolation:
            raise HTTPException(409, f"KODE ODOO {c['kode_odoo']} sudah ada di dapur {c['kode_dapur']}.")
        auth.audit(user["username"], "master_template_update", f"#{tid} {c['kode_dapur']} {c['kode_odoo']}")
        return {"id": tid}

    @router.delete("/api/ro/master/template/{tid}")
    def template_delete(tid: int, request: Request, user=Depends(admin_user)):
        csrf(request)
        with cursor() as cur:
            cur.execute("DELETE FROM app.master_template_ro WHERE id = %s RETURNING kode_dapur, kode_odoo", (tid,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Produk tidak ditemukan.")
        auth.audit(user["username"], "master_template_delete", f"#{tid} {row['kode_dapur']} {row['kode_odoo']}")
        return {"ok": True}

    return router
