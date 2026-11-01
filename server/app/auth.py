"""Author accounts: pbkdf2 passwords, DB-backed bearer tokens."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .db import now_iso

PBKDF2_ITERS = 210_000
SESSION_DAYS = 30
LOGIN_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
# Pre-auth works (author_id NULL) are claimed when this id registers.
LEGACY_OWNER_LOGIN_ID = "kimiyo"


def _row(conn, sql, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def public_author(a: dict) -> dict:
    return {"id": a["id"], "login_id": a["login_id"],
            "pen_name": a["pen_name"], "created_at": a["created_at"]}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt,
                             PBKDF2_ITERS)
    return f"pbkdf2${PBKDF2_ITERS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt_hex, dk_hex = (stored or "").split("$")
        dk = hashlib.pbkdf2_hmac("sha256", (password or "").encode(),
                                 bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(dk.hex(), dk_hex)
    except (ValueError, TypeError):
        return False


def validate_pen_name(pen_name) -> str:
    pen_name = (pen_name or "").strip()
    if not pen_name or len(pen_name) > 40:
        raise ValueError("pen_name must be 1..40 chars")
    return pen_name


def validate_registration(login_id, pen_name,
                          password) -> tuple[str, str, str]:
    login_id = (login_id or "").strip()
    password = password or ""
    if not LOGIN_RE.match(login_id):
        raise ValueError(
            "login_id must be 3..32 chars of letters/digits/_/-")
    if not 4 <= len(password) <= 128:
        raise ValueError("password must be 4..128 chars")
    return login_id, validate_pen_name(pen_name), password


def register(conn: sqlite3.Connection, *, login_id: str, pen_name: str,
             password: str) -> dict:
    login_id, pen_name, password = validate_registration(
        login_id, pen_name, password)
    try:
        cur = conn.execute(
            "INSERT INTO authors(login_id, pen_name, password_hash,"
            " created_at) VALUES(?,?,?,?)",
            (login_id, pen_name, hash_password(password), now_iso()))
    except sqlite3.IntegrityError:
        raise KeyError(f"login_id {login_id} already taken")
    author = _row(conn, "SELECT * FROM authors WHERE id=?",
                  (cur.lastrowid,))
    if login_id == LEGACY_OWNER_LOGIN_ID:
        conn.execute("UPDATE works SET author_id=? WHERE author_id IS NULL",
                     (author["id"],))
    conn.commit()
    return author


def change_pen_name(conn: sqlite3.Connection, author_id: int, *,
                    pen_name: str, password: str) -> dict:
    """Rename after re-checking the password. Wrong password → KeyError."""
    a = _row(conn, "SELECT * FROM authors WHERE id=?", (author_id,))
    if not a or not verify_password(password or "", a["password_hash"]):
        raise KeyError("password does not match")
    pen_name = validate_pen_name(pen_name)
    conn.execute("UPDATE authors SET pen_name=? WHERE id=?",
                 (pen_name, author_id))
    conn.commit()
    return _row(conn, "SELECT * FROM authors WHERE id=?", (author_id,))


def issue_token(conn: sqlite3.Connection, author_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        "INSERT INTO sessions(token_hash, author_id, created_at,"
        " expires_at) VALUES(?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), author_id,
         now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()))
    conn.commit()
    return token


def authenticate(conn: sqlite3.Connection, login_id: str,
                 password: str) -> tuple[dict, str]:
    a = _row(conn, "SELECT * FROM authors WHERE login_id=?",
             ((login_id or "").strip(),))
    if not a or not verify_password(password or "", a["password_hash"]):
        raise KeyError("invalid login_id or password")
    return a, issue_token(conn, a["id"])


def resolve_token(conn: sqlite3.Connection, token: str) -> dict | None:
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    s = _row(conn, "SELECT * FROM sessions WHERE token_hash=?", (th,))
    if not s:
        return None
    if s["expires_at"] < now_iso():
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        conn.commit()
        return None
    return _row(conn, "SELECT * FROM authors WHERE id=?",
                (s["author_id"],))


def logout(conn: sqlite3.Connection, token: str) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash=?",
                     (hashlib.sha256(token.encode()).hexdigest(),))
        conn.commit()
