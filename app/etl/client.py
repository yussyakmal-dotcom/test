"""Klien ringan untuk Odoo 19 External API (/json/2) dengan API key."""
import logging
import time

import requests

log = logging.getLogger("odoo")

BOOL_FIELDS = {"active", "sale_ok", "purchase_ok", "available_in_pos"}


class OdooError(Exception):
    pass


class _Retryable(Exception):
    pass


def normalize(row: dict) -> dict:
    """Seragamkan hasil API:
    - many2one [id, 'nama'] -> id
    - False -> None (kecuali field boolean sungguhan)
    """
    out = {}
    for k, v in row.items():
        if isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], int) and isinstance(v[1], str):
            v = v[0]
        elif v is False and k not in BOOL_FIELDS:
            v = None
        out[k] = v
    return out


class OdooClient:
    def __init__(self, base_url, api_key, db=None, timeout=120, retries=5):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        })
        if db:
            self.s.headers["X-Odoo-Database"] = db

    def call(self, model: str, method: str, **params):
        url = f"{self.base}/json/2/{model}/{method}"
        last_err = None
        for attempt in range(self.retries):
            try:
                r = self.s.post(url, json=params, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise _Retryable(f"HTTP {r.status_code}")
                if r.status_code >= 400:
                    raise OdooError(f"{model}.{method} -> HTTP {r.status_code}: {r.text[:500]}")
                return r.json()
            except (requests.ConnectionError, requests.Timeout, _Retryable) as e:
                last_err = e
                wait = 2 ** attempt
                log.warning("%s.%s gagal (%s), coba lagi dalam %ss", model, method, e, wait)
                time.sleep(wait)
        raise OdooError(f"{model}.{method} gagal setelah {self.retries} percobaan: {last_err}")

    def search_read_batches(self, model, domain, fields, batch=1000, context=None, pause=0.2):
        """Generator batch baris. Paginasi keyset (id > last_id) agar stabil
        meski data berubah selama penarikan."""
        last_id = 0
        while True:
            rows = self.call(
                model, "search_read",
                domain=list(domain) + [["id", ">", last_id]],
                fields=fields, order="id asc", limit=batch,
                context=context or {},
            )
            if not rows:
                return
            yield [normalize(r) for r in rows]
            last_id = rows[-1]["id"]
            if len(rows) < batch:
                return
            time.sleep(pause)
