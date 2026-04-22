"""
Auth endpoints.

Passwordless email magic-link flow:

  POST /auth/request-link   body: {"email": "..."}
      -> 202 Accepted regardless of whether the email exists. Triggers a
         magic-link email via SMTP. Always returns 202 to avoid leaking
         which addresses are registered.

  GET /auth/verify?token=<raw>
      -> consumes the token, finds-or-creates the user, marks them
         verified, issues a session cookie, redirects to /account.

  POST /auth/logout
      -> revokes the current session and clears the cookie.

  GET /auth/me
      -> {"user": {...}, "subscription": {...|null}} for the current
         session; 401 if unauthenticated.

Session cookie: HttpOnly, SameSite=Lax, Secure (prod). Name ee_session.
"""
import os
from typing import Optional

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, field_validator

from edge_equation.auth.email import MagicLinkSender
from edge_equation.auth.sessions import COOKIE_NAME, Session, SessionStore
from edge_equation.auth.subscriptions import SubscriptionStore
from edge_equation.auth.tokens import AuthTokenStore
from edge_equation.auth.users import User, UserStore
from edge_equation.persistence.db import Database


router = APIRouter(prefix="/auth", tags=["auth"])


WEBSITE_BASE_URL_ENV = "WEBSITE_BASE_URL"


def _open_db():
    conn = Database.open(Database.resolve_path(None))
    Database.migrate(conn)
    return conn


def _cookie_secure_flag() -> bool:
    # In production you want Secure=True; local dev over http needs False.
    # Driven by explicit env var; default matches FastAPI convention.
    return os.environ.get("EE_COOKIE_SECURE", "true").lower() in ("1", "true", "yes")


def _website_base() -> str:
    return os.environ.get(WEBSITE_BASE_URL_ENV, "http://localhost:3000").rstrip("/")


def _set_session_cookie(response: Response, session: Session) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=session.session_id,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure_flag(),
        expires=session.expires_at,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=COOKIE_NAME, path="/")


def current_user_or_none(request: Request) -> Optional[User]:
    """FastAPI dependency: returns the User for the current session, or None."""
    session_id = request.cookies.get(COOKIE_NAME)
    if not session_id:
        return None
    conn = _open_db()
    try:
        sess = SessionStore.get_active(conn, session_id)
        if sess is None:
            return None
        return UserStore.get_by_id(conn, sess.user_id)
    finally:
        conn.close()


def require_user(request: Request) -> User:
    user = current_user_or_none(request)
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RequestLinkBody(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v


@router.post("/request-link")
def request_link(body: RequestLinkBody) -> JSONResponse:
    conn = _open_db()
    try:
        raw_token, _ = AuthTokenStore.mint(conn, email=body.email)
    finally:
        conn.close()

    # If SMTP isn't configured (local dev), skip the send. The raw token is
    # never leaked -- the operator can read it from auth_tokens manually.
    try:
        MagicLinkSender.send(
            to_email=body.email,
            raw_token=raw_token,
            base_url=_website_base(),
        )
    except RuntimeError:
        # SMTP misconfiguration: silently succeed to avoid leaking config
        # state. Operator sees a failsafe log or an empty inbox.
        pass
    return JSONResponse(
        status_code=202,
        content={"status": "ok", "message": "If that email is registered, a link has been sent."},
    )


@router.get("/verify")
def verify_token(token: Optional[str] = None) -> Response:
    if not token:
        raise HTTPException(status_code=400, detail="missing token")

    conn = _open_db()
    try:
        email = AuthTokenStore.consume(conn, raw_token=token)
        if email is None:
            raise HTTPException(status_code=400, detail="invalid or expired token")
        user = UserStore.find_or_create(conn, email=email)
        UserStore.mark_verified(conn, user_id=user.user_id)
        session = SessionStore.create(conn, user_id=user.user_id)
    finally:
        conn.close()

    # Redirect to the frontend account page; cookie drives subsequent requests.
    response = RedirectResponse(url=f"{_website_base()}/account", status_code=302)
    _set_session_cookie(response, session)
    return response


@router.post("/logout")
def logout(request: Request) -> JSONResponse:
    session_id = request.cookies.get(COOKIE_NAME)
    response = JSONResponse(content={"status": "ok"})
    if session_id:
        conn = _open_db()
        try:
            SessionStore.revoke(conn, session_id)
        finally:
            conn.close()
    _clear_session_cookie(response)
    return response


@router.get("/me")
def me(request: Request) -> dict:
    user = require_user(request)
    conn = _open_db()
    try:
        subs = SubscriptionStore.list_for_user(conn, user.user_id)
        active = next((s for s in subs if s.is_active()), None)
    finally:
        conn.close()
    return {
        "user": user.to_dict(),
        "subscription": active.to_dict() if active else None,
        "has_active_subscription": active is not None,
    }
