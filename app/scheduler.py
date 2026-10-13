"""Worker: menjalankan job dari antrian dan jadwal harian."""
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from . import migrate, settings_store
from .config import TZ_LOCAL
from .db import execute, fetchone
from .etl.runner import run_etl

log = logging.getLogger("scheduler")


def execute_job(job_id: int, params: dict):
    try:
        since = dt.datetime.strptime(params["since"], "%Y-%m-%d") if params.get("since") else None
        only = params.get("only") or None
        status, details = run_etl(since=since, only=only)
        ok = status in ("success", "partial")
        msg = f"{status}: " + ", ".join(f"{k}={v}" for k, v in details.get("models", {}).items())[:900]
        if details.get("errors"):
            msg += " | ERROR: " + "; ".join(f"{k}: {v}" for k, v in details["errors"].items())[:600]
        execute("UPDATE app.jobs SET status=%s, message=%s, finished_at=now() WHERE id=%s",
                ("done" if ok else "failed", msg, job_id))
    except Exception as e:
        log.exception("job %s gagal", job_id)
        execute("UPDATE app.jobs SET status='failed', message=%s, finished_at=now() WHERE id=%s",
                (str(e)[:900], job_id))


def tick():
    job = fetchone(
        """UPDATE app.jobs SET status='running', started_at=now()
           WHERE id = (SELECT id FROM app.jobs WHERE status='queued' ORDER BY id
                       LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, params""")
    if job:
        log.info("menjalankan job %s %s", job["id"], job["params"])
        execute_job(job["id"], job["params"])
        return
    s = settings_store.get_all()
    if s["schedule_enabled"] != "true" or not s["odoo_url"]:
        return
    now = dt.datetime.now(ZoneInfo(TZ_LOCAL))
    if (now.hour, now.minute) >= (int(s["schedule_hour"]), int(s["schedule_minute"])) \
            and s["last_scheduled_date"] != now.date().isoformat():
        settings_store.set_value("last_scheduled_date", now.date().isoformat(), "scheduler")
        row = fetchone("INSERT INTO app.jobs (status, requested_by, started_at, params) "
                       "VALUES ('running','scheduler', now(), '{}') RETURNING id")
        log.info("jadwal harian dimulai (job %s)", row["id"])
        execute_job(row["id"], {})


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    migrate.run()
    execute("UPDATE app.jobs SET status='failed', message='Terputus (restart)', finished_at=now() "
            "WHERE status='running'")
    log.info("scheduler aktif")
    while True:
        try:
            tick()
        except Exception:
            log.exception("tick gagal")
        time.sleep(15)


if __name__ == "__main__":
    main()
