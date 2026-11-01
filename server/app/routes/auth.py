"""Author account routes: register, login, logout, me, status."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import auth as authmod
from .deps import get_conn, get_current_author

router = APIRouter(prefix="/api/v1", tags=["auth"])


def _bearer(request: Request) -> str:
    authz = request.headers.get("authorization", "")
    if authz.lower().startswith("bearer "):
        return authz[7:].strip()
    return ""


class RegisterIn(BaseModel):
    login_id: str
    pen_name: str
    password: str


@router.post("/auth/register")
def register(body: RegisterIn,
             conn: sqlite3.Connection = Depends(get_conn)):
    try:
        author = authmod.register(conn, login_id=body.login_id,
                                  pen_name=body.pen_name,
                                  password=body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(409, str(e))
    token = authmod.issue_token(conn, author["id"])
    return {"author": authmod.public_author(author), "token": token}


class LoginIn(BaseModel):
    login_id: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        author, token = authmod.authenticate(conn, body.login_id,
                                             body.password)
    except KeyError:
        raise HTTPException(401, "invalid login_id or password")
    return {"author": authmod.public_author(author), "token": token}


@router.post("/auth/logout")
def logout(request: Request,
           conn: sqlite3.Connection = Depends(get_conn),
           author: dict = Depends(get_current_author)):
    authmod.logout(conn, _bearer(request))
    return {"ok": True}


@router.get("/auth/me")
def me(author: dict = Depends(get_current_author)):
    return author


class RenameIn(BaseModel):
    pen_name: str
    password: str


@router.patch("/auth/me")
def rename_me(body: RenameIn,
              conn: sqlite3.Connection = Depends(get_conn),
              author: dict = Depends(get_current_author)):
    try:
        author = authmod.change_pen_name(conn, author["id"],
                                         pen_name=body.pen_name,
                                         password=body.password)
    except KeyError:
        raise HTTPException(401, "password does not match")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return authmod.public_author(author)


@router.get("/auth/status")
def status(request: Request,
           conn: sqlite3.Connection = Depends(get_conn)):
    a = authmod.resolve_token(conn, _bearer(request))
    n = conn.execute("SELECT COUNT(*) c FROM authors").fetchone()["c"]
    return {"authors_exist": n > 0, "logged_in": a is not None,
            "login_id": a["login_id"] if a else None,
            "pen_name": a["pen_name"] if a else None}
