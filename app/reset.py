"""Kosongkan data BI (staging, warehouse, watermark) agar bisa dihubungkan ke Odoo lain.
Pemakaian:  docker compose exec app python -m app.reset
Tidak menyentuh user, pengaturan, dan kunci enkripsi. Snapshot stok dan harga pokok harian IKUT terhapus."""
import sys

from .db import connect


def main() -> int:
    print("PERINGATAN: seluruh data hasil tarikan (schema stg dan dwh, kecuali dim_date) dan watermark ETL")
    print("akan dihapus, termasuk riwayat snapshot stok dan harga pokok harian. Tidak bisa dibatalkan.")
    if input("Ketik RESET untuk melanjutkan: ").strip() != "RESET":
        print("Dibatalkan.")
        return 1
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT table_schema, table_name FROM information_schema.tables
                           WHERE table_type = 'BASE TABLE'
                             AND (table_schema = 'stg' OR (table_schema = 'dwh' AND table_name <> 'dim_date'))""")
            tabs = cur.fetchall()
            if tabs:
                cur.execute("TRUNCATE " + ", ".join(f'"{s}"."{t}"' for s, t in tabs))
            cur.execute("TRUNCATE etl.state")
            cur.execute("DELETE FROM app.settings WHERE key IN ('bound_odoo_url', 'last_scheduled_date')")
            cur.execute("INSERT INTO app.audit_log (username, action, detail) VALUES ('terminal', 'reset_data', %s)",
                        (f"{len(tabs)} tabel dikosongkan",))
        conn.commit()
        print(f"Selesai: {len(tabs)} tabel dikosongkan. Jalankan backfill dari halaman Status ETL.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
