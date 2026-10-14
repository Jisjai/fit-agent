"""A simple password gate for a personal app.

If the APP_PASSWORD environment variable is set, every API request needs a
login cookie. Logging in with the right password sets that cookie.
If APP_PASSWORD is not set (e.g. on your laptop), the app is open.

The cookie holds an HMAC "signature" derived from the password, not the
password itself. Changing APP_PASSWORD invalidates every existing login.

This is enough for a one-person app. With real users you'd want proper
accounts (one row per user with a hashed password), which is a later step.
"""
import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

COOKIE = "fa_session"
MAX_AGE = 60 * 60 * 24 * 90  # stay logged in for 90 days
OPEN_PATHS = {"/", "/login", "/logout", "/health", "/favicon.ico"}

router = APIRouter(tags=["auth"])


def _password() -> str | None:
    return os.getenv("APP_PASSWORD") or None


def _token(password: str) -> str:
    return hmac.new(password.encode(), b"fit-agent-session", hashlib.sha256).hexdigest()


async def require_login(request: Request, call_next):
    """Middleware: runs before every request and turns away anyone without the cookie."""
    password = _password()
    if password and request.url.path not in OPEN_PATHS:
        cookie = request.cookies.get(COOKIE, "")
        # compare_digest takes the same time whether the first or last character differs,
        # so attackers can't guess the token one character at a time by timing responses.
        if not hmac.compare_digest(cookie, _token(password)):
            return JSONResponse({"detail": "Not logged in"}, status_code=401)
    return await call_next(request)


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    password = _password()
    if password is None:
        return {"ok": True}  # no password configured: nothing to do
    if not hmac.compare_digest(payload.password.encode(), password.encode()):
        time.sleep(1)  # slows down anyone trying many passwords
        return JSONResponse({"detail": "Wrong password"}, status_code=401)

    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, _token(password), max_age=MAX_AGE,
                        httponly=True,   # page JavaScript can't read it (protects from XSS)
                        secure=https,    # only sent over HTTPS when online
                        samesite="lax")  # not sent on requests from other sites
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}
