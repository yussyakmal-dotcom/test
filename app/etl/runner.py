"""ETL: Odoo -> staging -> dwh. Konfigurasi dari app.settings (secret didekripsi)."""
import datetime as dt
import logging
from pathlib import Path

from psycopg2.extras import Json, execute_values

from .. import settings_store
from ..config import TZ_LOCAL
from ..db import connect
from .client import OdooClient
from .models_config import FULL, MODELS, STOCK_QUANT

log = logging.getLogger("etl")
SQL = Path(__file__).resolve().parent.parent / "sql"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def make_client(s: dict, retries: int = 5) -> OdooClient:
    if not s["odoo_url"] or not s["odoo_api_key"]:
        raise RuntimeError("Pengaturan Odoo (URL dan API key) belum diisi.")
    return OdooClient(s["odoo_url"], s["odoo_api_key"], s["odoo_db"] or None, retries=retries)


def test_connection(url: str, api_key: str, db: str | None):
    try:
        c = OdooClient(url, api_key, db or None, timeout=30, retries=1)
        rows = c.call("pos.config", "search_read", domain=[], fields=["name"], limit=50)
        return True, f"Koneksi berhasil. {len(rows)} konfigurasi POS terbaca: " + \
            ", ".join(str(r.get("name")) for r in rows[:5])
    except Exception as e:
        return False, f"Gagal: {str(e)[:300]}"


def upsert_staging(cur, table, rows):
    execute_values(
        cur,
        f"""INSERT INTO stg.{table} (odoo_id, write_date, payload) VALUES %s
            ON CONFLICT (odoo_id) DO UPDATE SET
              write_date = EXCLUDED.write_date, payload = EXCLUDED.payload, extracted_at = now()""",
        [(r["id"], r.get("write_date"), Json(r)) for r in rows], page_size=500)


def _since(conn, cfg, since, lookback):
    if since:
        return since
    with conn.cursor() as cur:
        cur.execute("SELECT last_success_at FROM etl.state WHERE model=%s", (cfg["model"],))
        row = cur.fetchone()
    if not row:
        raise RuntimeError(f"Belum ada watermark untuk {cfg['model']}. Jalankan backfill dulu "
                           "(Status ETL, pilih tanggal awal).")
    return row[0] - dt.timedelta(days=lookback)


def extract_model(client, conn, cfg, since, lookback, batch, started_at, run_start=None):
    domain = list(cfg.get("domain", []))
    if cfg["mode"] != FULL:
        s = _since(conn, cfg, since, lookback)
        domain.append(["write_date", ">=", s.strftime("%Y-%m-%d %H:%M:%S")])
        log.info("%s: incremental sejak %s UTC", cfg["model"], s)
    else:
        log.info("%s: tarik penuh", cfg["model"])
    total = 0
    for rows in client.search_read_batches(cfg["model"], domain, cfg["fields"], batch=batch,
                                           context={"active_test": False}):
        with conn.cursor() as cur:
            upsert_staging(cur, cfg["table"], rows)
        conn.commit()
        total += len(rows)
    if cfg["mode"] == FULL and run_start is not None:     # hapus baris yang sudah tidak ada di Odoo
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM stg.{cfg['table']} WHERE extracted_at < %s", (run_start,))
        conn.commit()
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO etl.state (model, last_success_at) VALUES (%s,%s)
                       ON CONFLICT (model) DO UPDATE SET last_success_at = EXCLUDED.last_success_at""",
                    (cfg["model"], started_at))
    conn.commit()
    return total


def snapshot_stock(client, conn, batch):
    with conn.cursor() as cur:
        cur.execute("SELECT (now() AT TIME ZONE %s)::date", (TZ_LOCAL,))
        snap_date = cur.fetchone()[0]
    agg = {}
    for rows in client.search_read_batches(STOCK_QUANT["model"], STOCK_QUANT["domain"],
                                           STOCK_QUANT["fields"], batch=batch):
        for r in rows:
            a = agg.setdefault((r["product_id"], r["location_id"], r.get("lot_id") or 0), [0.0, 0.0])
            a[0] += r.get("quantity") or 0
            a[1] += r.get("reserved_quantity") or 0
    data = [(snap_date, p, l, lot, q, rq) for (p, l, lot), (q, rq) in agg.items()]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM dwh.snapshot_stock_daily WHERE snapshot_date=%s", (snap_date,))
        if data:
            execute_values(cur, """INSERT INTO dwh.snapshot_stock_daily
                (snapshot_date, product_id, location_id, lot_id, quantity, reserved_quantity)
                VALUES %s""", data, page_size=2000)
    conn.commit()
    return len(data)


def run_etl(since: dt.datetime | None = None, only: list[str] | None = None,
            skip_transform: bool = False):
    """Return (status, details). Raise RuntimeError untuk kesalahan konfigurasi."""
    s = settings_store.get_all(reveal=True)
    client = make_client(s)
    lookback, batch = int(s["lookback_days"]), int(s["batch_size"])
    cur_url = s["odoo_url"].strip().rstrip("/").lower()
    bound = (s.get("bound_odoo_url") or "").strip().lower()
    if bound and bound != cur_url:
        raise RuntimeError(
            f"URL Odoo sekarang ({cur_url}) berbeda dari asal data BI ({bound}). Data dari dua Odoo tidak boleh "
            "tercampur. Kembalikan URL ke yang lama, atau bila memang pindah Odoo kosongkan data BI lewat terminal: "
            "docker compose exec app python -m app.reset")
    if not bound:
        settings_store.set_value("bound_odoo_url", cur_url, "etl")
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(7002)")
            if not cur.fetchone()[0]:
                raise RuntimeError("ETL lain sedang berjalan.")
            cur.execute("INSERT INTO etl.run_log DEFAULT VALUES RETURNING run_id, started_at")
            run_id, run_start = cur.fetchone()
        conn.commit()
        started_at = utcnow()
        details = {"models": {}, "errors": {}}

        rows = client.call("pos.session", "search_read", domain=[["state", "!=", "closed"]],
                           fields=["name", "state"], limit=200)
        details["open_sessions"] = len(rows)
        if rows and s["require_all_closed"] == "true":
            details["errors"]["sessions"] = f"{len(rows)} sesi POS belum closed"
            with conn.cursor() as cur:
                cur.execute("UPDATE etl.run_log SET finished_at=now(), status='failed', details=%s "
                            "WHERE run_id=%s", (Json(details), run_id))
            conn.commit()
            return "failed", details

        for cfg in MODELS:
            if only and cfg["model"] not in only:
                continue
            try:
                details["models"][cfg["model"]] = extract_model(
                    client, conn, cfg, since, lookback, batch, started_at, run_start)
            except RuntimeError as e:
                conn.rollback()
                details["errors"][cfg["model"]] = str(e)[:500]
            except Exception as e:
                conn.rollback()
                log.exception("gagal: %s", cfg["model"])
                details["errors"][cfg["model"]] = str(e)[:500]
        try:
            details["stock_snapshot_rows"] = snapshot_stock(client, conn, batch)
        except Exception as e:
            conn.rollback()
            log.exception("gagal snapshot stok")
            details["errors"]["stock.quant"] = str(e)[:500]
        if not skip_transform:
            try:
                with conn.cursor() as cur:
                    cur.execute((SQL / "transform.sql").read_text(encoding="utf-8"),
                                {"run_start": run_start})
                conn.commit()
            except Exception as e:
                conn.rollback()
                log.exception("gagal transformasi")
                details["errors"]["transform"] = str(e)[:500]
        status = "success" if not details["errors"] else "partial"
        with conn.cursor() as cur:
            cur.execute("UPDATE etl.run_log SET finished_at=now(), status=%s, details=%s WHERE run_id=%s",
                        (status, Json(details), run_id))
        conn.commit()
        return status, details
    finally:
        conn.close()
