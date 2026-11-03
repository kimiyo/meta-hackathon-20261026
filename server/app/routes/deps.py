"""Shared route dependencies: connection, login, ownership checks."""
from __future__ import annotations

import sqlite3

from fastapi import Depends, HTTPException, Request

from .. import auth as authmod
from .. import db as dbmod
from .. import design_nodes


def get_conn():
    conn = dbmod.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_author(request: Request,
                       conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    authz = request.headers.get("authorization", "")
    token = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
    author = authmod.resolve_token(conn, token)
    if not author:
        raise HTTPException(401, "login required")
    return authmod.public_author(author)


def require_work(conn: sqlite3.Connection, key: str,
                 author_id: int) -> dict:
    """The work if owned; foreign or missing keys are all 404."""
    w = dbmod.find_work(conn, key)
    if not w or w.get("author_id") != author_id:
        raise HTTPException(404, f"work {key} not found")
    return w


def require_design(conn: sqlite3.Connection, design_id: int,
                   author_id: int) -> dict:
    d = conn.execute("SELECT * FROM story_designs WHERE id=?",
                     (design_id,)).fetchone()
    if not d:
        raise HTTPException(404, f"design {design_id} not found")
    require_work(conn, d["work_id"], author_id)
    return dict(d)


def require_node(conn: sqlite3.Connection, node_id: str,
                 author_id: int) -> dict:
    n = design_nodes.get_node(conn, node_id)
    if not n:
        raise HTTPException(404, f"node {node_id} not found")
    require_design(conn, n["design_id"], author_id)
    return n


def require_thread(conn: sqlite3.Connection, thread_id: int,
                   author_id: int) -> dict:
    t = conn.execute("SELECT * FROM idea_threads WHERE id=?",
                     (thread_id,)).fetchone()
    if not t:
        raise HTTPException(404, f"thread {thread_id} not found")
    require_work(conn, t["work_id"], author_id)
    return dict(t)


def require_design_thread(conn: sqlite3.Connection, thread_id: int,
                            author_id: int) -> dict:
    t = conn.execute("SELECT * FROM design_threads WHERE id=?",
                     (thread_id,)).fetchone()
    if not t:
        raise HTTPException(404, f"design thread {thread_id} not found")
    require_design(conn, t["design_id"], author_id)
    return dict(t)


def require_design_message(conn: sqlite3.Connection, message_id: int,
                           author_id: int) -> dict:
    m = conn.execute("SELECT * FROM design_messages WHERE id=?",
                     (message_id,)).fetchone()
    if not m:
        raise HTTPException(404, f"design message {message_id} not found")
    require_design_thread(conn, m["thread_id"], author_id)
    return dict(m)


def require_episode_job(conn: sqlite3.Connection, work_id: int,
                        episode_no: int, author_id: int):
    """This work's episode job if owned; a foreign job reads as missing."""
    from .. import state_machine
    require_work(conn, work_id, author_id)
    job = state_machine.get_job(conn, work_id, episode_no)
    if job and job["design_id"]:
        require_design(conn, job["design_id"], author_id)
    return job
