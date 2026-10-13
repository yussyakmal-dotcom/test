"""Aturan validasi RO Toko (tanpa dependensi pihak ketiga agar mudah diuji)."""
import csv
import datetime as dt
import io
import math
import re

STATUS = {1: "DRAFT", 2: "KIRIM DATA"}
MAX_QTY = 1_000_000


def min_kirim(tgl_pesan: dt.date) -> dt.date:
    return tgl_pesan + dt.timedelta(days=1)


def default_kirim(tgl_pesan: dt.date) -> dt.date:
    return tgl_pesan + dt.timedelta(days=2)


def validate_header(tgl_pesan: dt.date, tgl_kirim: dt.date, status: int) -> list[str]:
    errs = []
    if status not in STATUS:
        errs.append("STATUS tidak valid.")
    if tgl_kirim < min_kirim(tgl_pesan):
        errs.append(f"TGL KIRIM minimal TGL PESAN + 1 hari ({min_kirim(tgl_pesan).strftime('%d-%m-%Y')}).")
    return errs


def clean_items(items, status: int):
    """items: [(kode_odoo, qty)]. Return (errors, {kode: qty>0})."""
    errs, seen = [], {}
    for kode, qty in items:
        kode = (kode or "").strip()
        if not kode:
            errs.append("Ada baris tanpa KODE ODOO.")
            continue
        if qty is None or not math.isfinite(qty) or qty < 0 or qty > MAX_QTY:
            errs.append(f"QTY untuk {kode} tidak valid (0 sampai 1.000.000).")
            continue
        if kode in seen:
            errs.append(f"KODE ODOO {kode} muncul lebih dari sekali.")
            continue
        seen[kode] = round(float(qty), 4)
    kept = {k: v for k, v in seen.items() if v > 0}
    if status == 2 and not kept and not errs:
        errs.append("RO tanpa item (semua QTY 0) tidak bisa dikirim.")
    return errs, kept


# ---------------------------------------------------------------- master jadwal dan template
MAX_IMPORT_ROWS = 5000


def clean_name(name, maxlen: int = 60):
    """Nama master (jadwal): spasi dirapikan, huruf besar. Return (error|None, nama)."""
    s = re.sub(r"\s+", " ", name or "").strip().upper()
    if not s:
        return "Nama wajib diisi.", ""
    if len(s) > maxlen:
        return f"Nama maksimal {maxlen} karakter.", ""
    return None, s


def clean_template(nama, gink, kode, dapur):
    """Return (errors, {nama_produk, kode_gink, kode_odoo, kode_dapur})."""
    errs = []
    nama = re.sub(r"\s+", " ", nama or "").strip()
    kode = (kode or "").strip()
    dapur = (dapur or "").strip().upper()
    gink = (gink or "").strip() or None
    if not nama:
        errs.append("NAMA PRODUK wajib diisi.")
    elif len(nama) > 200:
        errs.append("NAMA PRODUK maksimal 200 karakter.")
    if not kode:
        errs.append("KODE ODOO wajib diisi.")
    elif len(kode) > 50:
        errs.append("KODE ODOO maksimal 50 karakter.")
    if not dapur:
        errs.append("KODE DAPUR wajib diisi.")
    if gink and len(gink) > 50:
        errs.append("KODE GINK maksimal 50 karakter.")
    return errs, {"nama_produk": nama, "kode_gink": gink, "kode_odoo": kode, "kode_dapur": dapur}


def parse_template_csv(text: str, valid_dapur):
    """Urai CSV master template (pemisah koma, titik koma, atau tab; BOM Excel diabaikan).
    Kolom wajib: NAMA_PRODUK, KODE_ODOO, KODE_DAPUR; opsional: KODE_GINK. Return (errors, rows)."""
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return ["File kosong."], []
    first = text.splitlines()[0]
    delim = max((",", ";", "\t"), key=first.count)
    rdr = csv.DictReader(io.StringIO(text), delimiter=delim)
    fields = {(f or "").strip().lower(): f for f in (rdr.fieldnames or [])}
    errs = [f"Kolom {c.upper()} tidak ada di baris judul." for c in ("nama_produk", "kode_odoo", "kode_dapur")
            if c not in fields]
    if errs:
        return errs, []
    valid = set(valid_dapur)
    rows, seen = [], {}
    for n, r in enumerate(rdr, start=2):
        def get(k):
            return (r.get(fields[k]) or "") if k in fields else ""
        vals = [get("nama_produk"), get("kode_gink"), get("kode_odoo"), get("kode_dapur")]
        if not any(v.strip() for v in vals):
            continue                                   # baris kosong
        e, c = clean_template(vals[0], vals[1], vals[2], vals[3])
        if not e and c["kode_dapur"] not in valid:
            e = [f"KODE DAPUR {c['kode_dapur']} tidak dikenal."]
        key = (c["kode_dapur"], c["kode_odoo"])
        if not e and key in seen:
            e = [f"KODE ODOO {c['kode_odoo']} dobel di dapur {c['kode_dapur']} (juga di baris {seen[key]})."]
        if e:
            errs += [f"Baris {n}: {x}" for x in e]
            continue
        seen[key] = n
        rows.append(c)
        if len(rows) > MAX_IMPORT_ROWS:
            return [f"Maksimal {MAX_IMPORT_ROWS} baris per impor."], []
    if not errs and not rows:
        errs.append("Tidak ada baris data.")
    return errs, rows
