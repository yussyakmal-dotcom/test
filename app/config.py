import os

PG = dict(
    host=os.getenv("POSTGRES_HOST", "db"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.environ["POSTGRES_DB"],
    user=os.environ["POSTGRES_USER"],
    password=os.environ["POSTGRES_PASSWORD"],
)
MASTER_KEY = os.environ["MASTER_KEY"]      # kunci Fernet (enkripsi secret di database)
SECRET_KEY = os.environ["SECRET_KEY"]      # kunci tanda tangan cookie sesi
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
TZ_LOCAL = "Asia/Jakarta"
SESSION_HOURS = 8
LOCK_MINUTES = 15
MAX_FAILED = 5
MIN_PASSWORD = 10
