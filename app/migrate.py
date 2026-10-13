"""Siapkan skema dan buat admin awal. Aman dijalankan berulang (dengan advisory lock)."""
import logging
import os
import time
from pathlib import Path

import psycopg2

from . import auth
from .db import connect

log = logging.getLogger("migrate")
SQL = Path(__file__).parent / "sql"


def run():
    conn = None
    for _ in range(30):                              # tunggu database siap
        try:
            conn = connect()
            break
        except psycopg2.OperationalError:
            time.sleep(2)
    if conn is None:
        raise RuntimeError("Database tidak bisa dihubungi")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(7001)")
            for name in ("schema.sql", "app_schema.sql", "ro_schema.sql"):
                cur.execute((SQL / name).read_text(encoding="utf-8"))
            conn.commit()
            cur.execute("SELECT count(*) FROM app.users")
            if cur.fetchone()[0] == 0:
                pw = os.getenv("ADMIN_INITIAL_PASSWORD") or auth.random_password()
                cur.execute(
                    "INSERT INTO app.users (username, password_hash, role, must_change_password) "
                    "VALUES ('admin', %s, 'admin', true)", (auth.hash_password(pw),))
                conn.commit()
                if os.getenv("ADMIN_INITIAL_PASSWORD"):
                    log.warning("Admin awal dibuat (password dari ADMIN_INITIAL_PASSWORD).")
                else:
                    banner = (
                        "\n" + "=" * 56 +
                        "\n  ADMIN AWAL DIBUAT\n  username : admin"
                        f"\n  PASSWORD : {pw}\n  (sekali pakai, wajib diganti saat login pertama)\n"
                        + "=" * 56)
                    print(banner, flush=True)
            cur.execute("SELECT pg_advisory_unlock(7001)")
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
