"""Sign in with Google and the session cookie. Google itself is replaced by a fake."""
import time

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app import auth
from app.database import add_missing_columns


@pytest.fixture
def fake_google(monkeypatch):
    """Configure sign-in and make 'Google' accept tokens of the form 'good:<sub>'."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")

    def verify(credential, client_id):
        assert client_id == "test-client-id.apps.googleusercontent.com"
        if credential == "unverified":
            return {"sub": "x", "email": "x@example.com", "email_verified": False}
        if not credential.startswith("good:"):
            raise ValueError("bad token")
        sub = credential.split(":", 1)[1]
        return {"sub": sub, "email": f"{sub}@example.com", "name": sub.title(),
                "email_verified": True}

    monkeypatch.setattr(auth, "verify_google_token", verify)


def test_api_is_locked_without_signing_in(client):
    client.cookies.clear()
    assert client.get("/health").status_code == 200       # open
    assert client.get("/auth/config").status_code == 200  # open: the page needs it
    assert client.get("/exercises").status_code == 401
    assert client.get("/users").status_code == 401


def test_google_sign_in_sets_a_working_cookie(client, fake_google):
    client.cookies.clear()
    r = client.post("/auth/google", json={"credential": "good:alice"})
    assert r.status_code == 200
    assert r.json()["email"] == "alice@example.com"
    assert client.get("/auth/me").json()["email"] == "alice@example.com"
    assert client.get("/users").json() == []   # signed in, no profile yet

    client.post("/auth/logout")
    assert client.get("/users").status_code == 401


def test_bad_or_unverified_google_tokens_are_rejected(client, fake_google):
    client.cookies.clear()
    assert client.post("/auth/google", json={"credential": "forged"}).status_code == 401
    assert client.post("/auth/google", json={"credential": "unverified"}).status_code == 401
    assert client.get("/users").status_code == 401


def test_sign_in_refuses_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    assert client.get("/auth/config").json() == {"google_client_id": None}
    assert client.post("/auth/google", json={"credential": "good:x"}).status_code == 503


def test_tampered_cookie_is_rejected(client):
    cookie = auth.make_session({"sub": "alice", "email": "a@example.com", "name": "A"})
    data, sig = cookie.rsplit(".", 1)
    # Try to become someone else by editing the data but keeping the old signature.
    forged = auth.make_session({"sub": "bob", "email": "b@example.com", "name": "B"})
    client.cookies.set(auth.COOKIE, f"{forged.rsplit('.', 1)[0]}.{sig}")
    assert client.get("/users").status_code == 401


def test_expired_cookie_is_rejected(client):
    old = auth.make_session({"sub": "alice", "email": "", "name": ""},
                            now=time.time() - auth.MAX_AGE - 10)
    client.cookies.set(auth.COOKIE, old)
    assert client.get("/users").status_code == 401


def test_changing_session_secret_signs_everyone_out(client, monkeypatch):
    assert client.get("/users").status_code == 200
    monkeypatch.setenv("SESSION_SECRET", "a-new-secret")
    assert client.get("/users").status_code == 401


def test_migration_adds_google_columns_to_an_old_users_table():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    with engine.begin() as conn:  # a users table as it looked before Google sign-in
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name VARCHAR(100))"))
        conn.execute(text("INSERT INTO users (name) VALUES ('Old profile')"))

    add_missing_columns(engine)
    add_missing_columns(engine)   # running it twice is harmless

    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert {"google_sub", "email"} <= columns
    with engine.connect() as conn:  # old rows survive, owned by nobody
        assert conn.execute(text("SELECT name, google_sub FROM users")).all() == [("Old profile", None)]
