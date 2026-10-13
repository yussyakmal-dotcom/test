from cryptography.fernet import Fernet

from .config import MASTER_KEY

try:
    _f = Fernet(MASTER_KEY.encode())
except Exception as e:  # kunci salah format
    raise RuntimeError("MASTER_KEY tidak valid. Buat ulang dengan ./setup.sh") from e


def encrypt(text: str) -> str:
    return _f.encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _f.decrypt(token.encode()).decode()
