import contextlib

import psycopg2
from psycopg2.extras import RealDictCursor

from .config import PG


def connect():
    return psycopg2.connect(**PG)


@contextlib.contextmanager
def cursor():
    conn = connect()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetchall(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetchone(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql, params=None):
    with cursor() as cur:
        cur.execute(sql, params)
