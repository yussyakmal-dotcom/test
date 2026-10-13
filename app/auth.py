import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .config import LOCK_MINUTES, MAX_FAILED, MIN_PASSWORD
from .db import execute, fetchone

ph = PasswordHasher()
_DUMMY = ph.hash("dummy-password-for-timing")
GENERIC_ERR = "Username atau password salah."


def hash_password(pw: str) -> str:
    return ph.hash(pw)


def random_password() -> str:
    return secrets.token_urlsafe(12)


def validate_new_password(pw: str, username: str) -> str | None:
    if len(pw) < MIN_PASSWORD:
        return f"Password minimal {MIN_PASSWORD} karakter."
    if pw.lower() == username.lower():
        return "Password tidak boleh sama dengan username."
    if pw.isdigit() or pw.isalpha():
        return "Password harus berisi kombinasi huruf dan angka/simbol."
    return None


def audit(username: str | None, action: str, detail: str = ""):
    execute("INSERT INTO app.audit_log (username, action, detail) VALUES (%s,%s,%s)",
            (username, action, detail[:1000]))


def authenticate(username: str, password: str):
    """Return (user_row, error_message)."""
    u = fetchone(
        "SELECT *, (locked_until IS NOT NULL AND locked_until > now()) AS locked "
        "FROM app.users WHERE lower(username) = lower(%s)", (username.strip(),))
    if not u:
        try:
            ph.verify(_DUMMY, password)           # samakan waktu respons
        except Exception:
            pass
        return None, GENERIC_ERR
    if u["locked"]:
        return None, f"Akun terkunci sementara. Coba lagi dalam {LOCK_MINUTES} menit."
    try:
        ph.verify(u["password_hash"], password)
    except (VerifyMismatchError, InvalidHashError):
        execute(
            """UPDATE app.users SET failed_count = failed_count + 1,
                 locked_until = CASE WHEN failed_count + 1 >= %s
                                THEN now() + make_interval(mins => %s) ELSE locked_until END,
                 failed_count = CASE WHEN failed_count + 1 >= %s THEN 0 ELSE failed_count + 1 END
               WHERE id = %s""", (MAX_FAILED, LOCK_MINUTES, MAX_FAILED, u["id"]))
        audit(u["username"], "login_failed")
        return None, GENERIC_ERR
    if not u["is_active"]:
        return None, "Akun dinonaktifkan. Hubungi admin."
    new_hash = ph.hash(password) if ph.check_needs_rehash(u["password_hash"]) else u["password_hash"]
    execute("UPDATE app.users SET failed_count=0, locked_until=NULL, last_login=now(), password_hash=%s "
            "WHERE id=%s", (new_hash, u["id"]))
    audit(u["username"], "login")
    return u, None


def create_user(username: str, role: str, password: str, must_change: bool = True):
    execute("INSERT INTO app.users (username, password_hash, role, must_change_password) "
            "VALUES (%s,%s,%s,%s)", (username, hash_password(password), role, must_change))


def set_password(user_id: int, password: str, must_change: bool):
    execute("UPDATE app.users SET password_hash=%s, must_change_password=%s, failed_count=0, "
            "locked_until=NULL WHERE id=%s", (hash_password(password), must_change, user_id))
