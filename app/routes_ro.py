"""Modul Request Order (RO) Toko: halaman form dan API.

Dipasang dari web.py (di bagian paling bawah):
    from .routes_ro import make_router
    app.include_router(make_router(logged_user, check_csrf, render))
"""
import datetime as dt
from zoneinfo import ZoneInfo

import psycopg2.errors
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg2.extras import execute_values
from pydantic import BaseModel, Field

from . import auth
from .config import TZ_LOCAL
from .db import cursor, fetchall, fetchone
from .ro_logic import STATUS, clean_items, validate_header

MAX_ITEMS = 2000


class ItemIn(BaseModel):
    kode_odoo: str = Field(max_length=50)
    qty: float


class ROIn(BaseModel):
    id: int | None = None
    kode_toko: int
    tgl_pesan: dt.date
    tgl_kirim: dt.date
    kode_dapur: str = Field(max_length=20)
    kode_jadwal: int
    status: int = 1
    items: list[ItemIn] = Field(default_factory=list)


def make_router(logged_user, check_csrf, render) -> APIRouter:
    router = APIRouter()

    # ---------------------------------------------------------------- halaman
    @router.get("/ro")
    def ro_page(request: Request, id: int | None = None, user=Depends(logged_user)):
        return render(request, "ro_form.html", user, ro_id=id)

    # ---------------------------------------------------------------- GET master
    @router.get("/api/ro/master")
    def ro_master(user=Depends(logged_user)):
        today = dt.datetime.now(ZoneInfo(TZ_LOCAL)).date()
        return {
            "toko": fetchall("SELECT kode_toko, toko FROM app.master_toko ORDER BY kode_toko"),
            "dapur": fetchall("SELECT kode_dapur, dapur FROM app.master_dapur ORDER BY dapur"),
            "jadwal": fetchall("SELECT kode_jadwal, jadwal FROM app.master_jadwal ORDER BY kode_jadwal"),
            "status": [{"kode": k, "nama": v} for k, v in STATUS.items()],
            "today": today.isoformat(),
        }

    # ---------------------------------------------------------------- GET produk per dapur
    @router.get("/api/ro/products")
    def ro_products(kode_dapur: str, user=Depends(logged_user)):
        if not fetchone("SELECT 1 AS x FROM app.master_dapur WHERE kode_dapur = %s", (kode_dapur,)):
            raise HTTPException(404, "Dapur tidak dikenal.")
        return fetchall("""SELECT kode_odoo, nama_produk, kode_gink FROM app.master_template_ro
                           WHERE kode_dapur = %s ORDER BY id""", (kode_dapur,))

    # ---------------------------------------------------------------- daftar RO
    @router.get("/api/ro")
    def ro_list(limit: int = 30, user=Depends(logged_user)):
        limit = max(1, min(limit, 200))
        return fetchall("""
            SELECT h.id, h.kode_toko, t.toko, h.tgl_pesan, h.tgl_kirim, h.kode_dapur, d.dapur,
                   h.kode_jadwal, j.jadwal, h.status,
                   (SELECT count(*) FROM app.ro_detail x WHERE x.ro_id = h.id) AS jumlah_item
            FROM app.ro_header h
            JOIN app.master_toko t ON t.kode_toko = h.kode_toko
            JOIN app.master_dapur d ON d.kode_dapur = h.kode_dapur
            JOIN app.master_jadwal j ON j.kode_jadwal = h.kode_jadwal
            ORDER BY h.id DESC LIMIT %s""", (limit,))

    # ---------------------------------------------------------------- satu RO
    @router.get("/api/ro/{ro_id}")
    def ro_get(ro_id: int, user=Depends(logged_user)):
        h = fetchone("""SELECT id, kode_toko, tgl_pesan, tgl_kirim, kode_dapur, kode_jadwal, status,
                               created_at, created_by FROM app.ro_header WHERE id = %s""", (ro_id,))
        if not h:
            raise HTTPException(404, "RO tidak ditemukan.")
        rows = fetchall("SELECT kode_odoo, nama_produk, qty FROM app.ro_detail WHERE ro_id = %s ORDER BY id",
                        (ro_id,))
        h["items"] = [{"kode_odoo": r["kode_odoo"], "nama_produk": r["nama_produk"], "qty": float(r["qty"])}
                      for r in rows]
        h["status_nama"] = STATUS.get(h["status"], "?")
        return h

    # ---------------------------------------------------------------- POST simpan (baru atau ubah)
    @router.post("/api/ro")
    def ro_save(request: Request, p: ROIn, user=Depends(logged_user)):
        check_csrf(request, request.headers.get("X-CSRF-Token", ""))
        errs = validate_header(p.tgl_pesan, p.tgl_kirim, p.status)
        if len(p.items) > MAX_ITEMS:
            errs.append("Terlalu banyak item.")
        e2, items = clean_items([(i.kode_odoo, i.qty) for i in p.items], p.status)
        errs += e2
        if errs:
            raise HTTPException(422, " ".join(errs))
        who = user["username"]
        try:
            with cursor() as cur:                     # satu transaksi: gagal di mana pun = batal semua
                cur.execute("""SELECT
                    (SELECT count(*) FROM app.master_toko   WHERE kode_toko   = %s) AS t,
                    (SELECT count(*) FROM app.master_dapur  WHERE kode_dapur  = %s) AS d,
                    (SELECT count(*) FROM app.master_jadwal WHERE kode_jadwal = %s) AS j""",
                            (p.kode_toko, p.kode_dapur, p.kode_jadwal))
                m = cur.fetchone()
                if not m["t"]:
                    raise HTTPException(422, "TOKO tidak dikenal.")
                if not m["d"]:
                    raise HTTPException(422, "DAPUR tidak dikenal.")
                if not m["j"]:
                    raise HTTPException(422, "JADWAL tidak dikenal.")

                names = {}
                if items:                              # nama produk diambil dari master, bukan dari klien
                    cur.execute("""SELECT kode_odoo, nama_produk FROM app.master_template_ro
                                   WHERE kode_dapur = %s AND kode_odoo = ANY(%s)""",
                                (p.kode_dapur, list(items)))
                    names = {r["kode_odoo"]: r["nama_produk"] for r in cur.fetchall()}
                    missing = [k for k in items if k not in names]
                    if missing:
                        raise HTTPException(422, "Produk bukan bagian dari dapur ini: " + ", ".join(missing[:10]))

                if p.id:
                    cur.execute("SELECT status FROM app.ro_header WHERE id = %s FOR UPDATE", (p.id,))
                    row = cur.fetchone()
                    if not row:
                        raise HTTPException(404, "RO tidak ditemukan.")
                    if row["status"] == 2:
                        raise HTTPException(409, f"RO #{p.id} sudah KIRIM DATA dan terkunci; tidak bisa diubah.")
                    cur.execute("""UPDATE app.ro_header SET kode_toko=%s, tgl_pesan=%s, tgl_kirim=%s,
                                   kode_dapur=%s, kode_jadwal=%s, status=%s, updated_at=now(), updated_by=%s
                                   WHERE id=%s""",
                                (p.kode_toko, p.tgl_pesan, p.tgl_kirim, p.kode_dapur, p.kode_jadwal,
                                 p.status, who, p.id))
                    cur.execute("DELETE FROM app.ro_detail WHERE ro_id = %s", (p.id,))
                    ro_id = p.id
                else:
                    cur.execute("""INSERT INTO app.ro_header (kode_toko, tgl_pesan, tgl_kirim, kode_dapur,
                                   kode_jadwal, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                                (p.kode_toko, p.tgl_pesan, p.tgl_kirim, p.kode_dapur, p.kode_jadwal,
                                 p.status, who))
                    ro_id = cur.fetchone()["id"]
                if items:
                    execute_values(cur, "INSERT INTO app.ro_detail (ro_id, kode_odoo, nama_produk, qty) VALUES %s",
                                   [(ro_id, k, names[k], q) for k, q in items.items()])
        except psycopg2.errors.UniqueViolation:
            ex = fetchone("""SELECT id, status FROM app.ro_header WHERE kode_toko=%s AND tgl_kirim=%s
                             AND kode_dapur=%s AND kode_jadwal=%s""",
                          (p.kode_toko, p.tgl_kirim, p.kode_dapur, p.kode_jadwal))
            hint = f" Sudah ada RO #{ex['id']} ({STATUS.get(ex['status'])}); buka dari Daftar RO." if ex else ""
            raise HTTPException(409, "RO untuk TOKO, TGL KIRIM, DAPUR, dan JADWAL yang sama sudah ada." + hint)
        auth.audit(who, "ro_saved", f"RO #{ro_id} status={p.status} item={len(items)}")
        return {"id": ro_id, "status": p.status, "status_nama": STATUS[p.status],
                "jumlah_item": len(items), "locked": p.status == 2}

    return router
