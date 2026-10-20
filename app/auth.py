"""Sign in with Google, and a signed session cookie that remembers who you are.

How it works:
  1. The web page shows Google's "Sign in with Google" button (Google's own script).
  2. After the user picks an account, Google hands the page an ID token: a small
     document signed by Google that says "this is user <sub>, email <email>".
  3. The page sends that token to POST /auth/google. We check Google's signature
     (so nobody can fake it) and that it was issued for OUR app (GOOGLE_CLIENT_ID).
  4. We then set our own session cookie, signed with SESSION_SECRET, so the browser
     doesn't need Google again for the next 30 days.

Every API request goes through `require_login` (middleware). It checks the cookie and
puts the signed-in identity on `request.state.identity`. Which DATA you may touch is
checked separately, in deps.get_user: you only ever get your own profile.

`sub` is Google's permanent ID for an account. We key users on it rather than on the
email address, because people can change the email on their Google account.
"""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

COOKIE = "fa_session"
MAX_AGE = 60 * 60 * 24 * 30  # stay signed in for 30 days
OPEN_PATHS = {"/", "/health", "/favicon.ico", "/manifest.webmanifest", "/sw.js",
              "/auth/config", "/auth/google", "/auth/logout"}
OPEN_PREFIXES = ("/icons/",  # phones fetch the icon and manifest without a cookie
                 "/cron/")   # the scheduler has no cookie; it proves itself with CRON_SECRET

router = APIRouter(prefix="/auth", tags=["auth"])


def google_client_id() -> str | None:
    return os.getenv("GOOGLE_CLIENT_ID") or None


def _secret() -> bytes | None:
    secret = os.getenv("SESSION_SECRET")
    return secret.encode() if secret else None


# --- Session cookie: "<base64 data>.<signature>" --------------------------------------
# The data is readable by anyone (it's just base64), but only we can produce a valid
# signature, so it can't be edited or forged without SESSION_SECRET.

def _sign(data: bytes, secret: bytes) -> str:
    return hmac.new(secret, data, hashlib.sha256).hexdigest()


def make_session(identity: dict, now: float | None = None) -> str:
    secret = _secret()
    if secret is None:
        raise RuntimeError("SESSION_SECRET is not set")
    payload = {**identity, "exp": int((now or time.time()) + MAX_AGE)}
    data = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    return f"{data.decode()}.{_sign(data, secret)}"


def read_session(cookie: str) -> dict | None:
    """Return the identity stored in a valid, unexpired cookie, else None."""
    secret = _secret()
    if not secret or "." not in cookie:
        return None
    data, signature = cookie.rsplit(".", 1)
    # compare_digest takes the same time whether the first or last character differs,
    # so attackers can't guess a signature one character at a time by timing responses.
    if not hmac.compare_digest(signature, _sign(data.encode(), secret)):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(data.encode()))
    except ValueError:
        return None
    if payload.get("exp", 0) < time.time() or not payload.get("sub"):
        return None
    return payload


async def require_login(request: Request, call_next):
    """Middleware: runs before every request and turns away anyone not signed in."""
    path = request.url.path
    if path in OPEN_PATHS or path.startswith(OPEN_PREFIXES):
        return await call_next(request)
    identity = read_session(request.cookies.get(COOKIE, ""))
    if identity is None:
        return JSONResponse({"detail": "Not signed in"}, status_code=401)
    request.state.identity = identity
    return await call_next(request)


# --- Google ------------------------------------------------------------------------

def verify_google_token(credential: str, client_id: str) -> dict:
    """Check the ID token's signature, expiry and audience with Google's library.

    Raises ValueError if anything is wrong. Kept as its own function so tests can
    replace it (they shouldn't need real Google accounts).
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token
    return id_token.verify_oauth2_token(credential, google_requests.Request(), client_id)


class GoogleLogin(BaseModel):
    credential: str


@router.get("/config")
def config():
    """The page needs the (public) client ID to show the Google button."""
    return {"google_client_id": google_client_id()}


@router.post("/google")
def google_login(payload: GoogleLogin, request: Request, response: Response):
    client_id = google_client_id()
    if client_id is None or _secret() is None:
        raise HTTPException(status_code=503,
                            detail="Sign-in isn't set up: GOOGLE_CLIENT_ID and SESSION_SECRET are needed.")
    try:
        info = verify_google_token(payload.credential, client_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Google sign-in failed. Please try again.")
    if not info.get("email_verified"):
        raise HTTPException(status_code=401, detail="Your Google email address isn't verified.")

    identity = {"sub": info["sub"], "email": info.get("email", ""), "name": info.get("name", "")}
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, make_session(identity), max_age=MAX_AGE,
                        httponly=True,   # page JavaScript can't read it (protects from XSS)
                        secure=https,    # only sent over HTTPS when online
                        samesite="lax")  # not sent on requests from other sites
    return {"email": identity["email"], "name": identity["name"]}


@router.get("/me")
def me(request: Request):
    identity = request.state.identity
    return {"email": identity.get("email"), "name": identity.get("name")}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}
