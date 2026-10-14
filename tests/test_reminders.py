"""Reminder notifications. The real push service is replaced by a fake that records messages."""
import datetime as dt

import pytest

from app import reminders
from app.models import PushSubscription

TODAY = dt.date(2026, 10, 14)
SECRET = "test-cron-secret"


@pytest.fixture
def push_env(monkeypatch):
    """Pretend the server is fully configured, freeze 'today', and capture sent messages."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "public")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "private")
    monkeypatch.setenv("CRON_SECRET", SECRET)
    monkeypatch.setattr(reminders, "local_today", lambda: TODAY)
    sent = []
    status = {"code": 201}

    def fake_send(sub, title, body, url="/"):
        sent.append({"endpoint": sub.endpoint, "title": title, "body": body})
        return status["code"]

    monkeypatch.setattr(reminders, "send", fake_send)
    return sent, status


def subscribe(client, user_id, endpoint="https://push.example.com/abc"):
    r = client.post(f"/users/{user_id}/push/subscribe",
                    json={"endpoint": endpoint, "keys": {"p256dh": "key", "auth": "secret"}})
    assert r.status_code == 201


def run_cron(client, kind):
    return client.get(f"/cron/{kind}", headers={"Authorization": f"Bearer {SECRET}"})


# --- Subscribing -----------------------------------------------------------------

def test_subscribing_twice_keeps_one_device(client, db_session, user, push_env):
    subscribe(client, user["id"])
    subscribe(client, user["id"])
    assert db_session.query(PushSubscription).count() == 1


def test_unsubscribe(client, db_session, user, push_env):
    subscribe(client, user["id"])
    r = client.post(f"/users/{user['id']}/push/unsubscribe",
                    json={"endpoint": "https://push.example.com/abc"})
    assert r.json()["removed"] is True
    assert db_session.query(PushSubscription).count() == 0


def test_subscription_must_be_https(client, user, push_env):
    r = client.post(f"/users/{user['id']}/push/subscribe",
                    json={"endpoint": "http://evil.example", "keys": {"p256dh": "k", "auth": "a"}})
    assert r.status_code == 422


# --- Scheduler security ------------------------------------------------------------

def test_cron_rejects_requests_without_the_secret(client, push_env):
    assert client.get("/cron/morning").status_code == 401
    assert client.get("/cron/morning",
                      headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_cron_refuses_to_run_when_no_secret_is_configured(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/cron/morning",
                      headers={"Authorization": "Bearer "}).status_code == 503


def test_cron_works_behind_the_password_gate(client, user, push_env, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw")   # no login cookie, but the secret is enough
    assert run_cron(client, "morning").status_code == 200


# --- Morning weigh-in reminder --------------------------------------------------------

def test_morning_reminder_when_not_weighed_in(client, user, push_env):
    sent, _ = push_env
    subscribe(client, user["id"])
    r = run_cron(client, "morning")
    assert r.json()["sent"] == 1
    assert "weigh" in sent[0]["body"]


def test_no_morning_reminder_after_weighing_in(client, user, push_env):
    sent, _ = push_env
    subscribe(client, user["id"])
    client.post(f"/users/{user['id']}/weigh-ins", json={"date": str(TODAY), "weight_kg": 80})
    r = run_cron(client, "morning")
    assert r.json()["skipped"] == 1
    assert sent == []


def test_users_without_devices_are_ignored(client, user, push_env):
    sent, _ = push_env
    assert run_cron(client, "morning").json()["users"] == 0
    assert sent == []


# --- Evening wrap-up -----------------------------------------------------------------

def test_evening_wrap_up_shows_totals_targets_and_missing_meals(client, user, push_env):
    sent, _ = push_env
    uid = user["id"]
    subscribe(client, uid)
    client.post(f"/users/{uid}/weigh-ins", json={"date": str(TODAY), "weight_kg": 80})
    client.post(f"/users/{uid}/food", json={
        "date": str(TODAY), "meal": "breakfast", "description": "oats",
        "calories": 400, "protein_g": 20, "carbs_g": 60, "fat_g": 8})

    run_cron(client, "evening")
    body = sent[0]["body"]
    assert "400 /" in body            # calories against the target
    assert "20 /" in body             # protein against the target
    assert "lunch, dinner" in body    # meals not logged yet
    assert "breakfast" not in body


def test_evening_wrap_up_when_nothing_logged(client, user, push_env):
    sent, _ = push_env
    subscribe(client, user["id"])
    run_cron(client, "evening")
    assert "Nothing logged" in sent[0]["body"]


# --- Devices that disappeared -----------------------------------------------------------

def test_dead_devices_are_removed(client, db_session, user, push_env):
    _, status = push_env
    subscribe(client, user["id"])
    status["code"] = 410   # "Gone": the user deleted the app or revoked permission
    run_cron(client, "evening")
    assert db_session.query(PushSubscription).count() == 0


def test_test_notification_reports_failure(client, user, push_env):
    _, status = push_env
    subscribe(client, user["id"])
    assert client.post(f"/users/{user['id']}/push/test").status_code == 200
    status["code"] = 500
    assert client.post(f"/users/{user['id']}/push/test").status_code == 502
