"""Shared route dependencies."""
from __future__ import annotations

from .. import db as dbmod


def get_conn():
    conn = dbmod.connect()
    try:
        yield conn
    finally:
        conn.close()
