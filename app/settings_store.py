"""Pengaturan aplikasi di tabel app.settings. Secret dienkripsi dengan MASTER_KEY."""
from .crypto import decrypt, encrypt
from .db import execute, fetchall

DEFAULTS = {
    "odoo_url": "",
    "odoo_api_key": "",
    "odoo_db": "",
    "lookback_days": "7",
    "batch_size": "1000",
    "require_all_closed": "false",
    "schedule_enabled": "true",
    "schedule_hour": "1",
    "schedule_minute": "30",
    "last_scheduled_date": "",
    "env_label": "",
    "bound_odoo_url": "",
}
SECRET_KEYS = {"odoo_api_key"}
EDITABLE = [k for k in DEFAULTS if k not in ("last_scheduled_date", "bound_odoo_url")]


def get_all(reveal: bool = False) -> dict:
    """reveal=True mengembalikan secret apa adanya (hanya untuk ETL/uji koneksi).
    reveal=False: secret diganti penanda 'is_set' agar tidak pernah sampai ke browser."""
    out = dict(DEFAULTS)
    for r in fetchall("SELECT key, value, is_secret FROM app.settings"):
        v = r["value"] or ""
        if r["is_secret"] and v:
            v = decrypt(v) if reveal else "********"
        out[r["key"]] = v
    return out


def set_value(key: str, value: str, user: str = "system"):
    if key not in DEFAULTS:
        raise KeyError(key)
    secret = key in SECRET_KEYS
    if secret and value == "":
        return  # kosong = tidak diubah
    stored = encrypt(value) if secret and value else value
    execute(
        """INSERT INTO app.settings (key, value, is_secret, updated_by)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
             is_secret = EXCLUDED.is_secret, updated_at = now(), updated_by = EXCLUDED.updated_by""",
        (key, stored, secret, user),
    )
