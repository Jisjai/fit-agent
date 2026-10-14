"""Push notifications: saving devices, building reminder messages, and sending them.

How web push works, in short:
  1. The phone asks the user for permission, then gets a "subscription" from its
     push service (Apple, Google or Mozilla) and the app sends it to us to store.
  2. To notify, we encrypt a small message and POST it to that subscription's URL.
     The push service delivers it to the phone, even when the app is closed.
  3. Our VAPID key pair proves to the push service that the message really
     comes from this app. The public half is given to the phone, the private half
     stays secret on the server.
"""
import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .models import FoodEntry, Meal, PushSubscription, User, WeighIn

TIMEZONE = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Jerusalem"))


def local_today() -> dt.date:
    """'Today' where the user lives, not on the server (which runs on UTC time)."""
    return dt.datetime.now(TIMEZONE).date()


def push_configured() -> bool:
    return bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY"))


# --- Storing devices ------------------------------------------------------------

def save_subscription(db: Session, user_id: int, endpoint: str, p256dh: str, auth: str):
    """Add a device, or update it if we already know that endpoint."""
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=endpoint)
        db.add(sub)
    sub.user_id, sub.p256dh, sub.auth = user_id, p256dh, auth
    db.commit()
    return sub


def delete_subscription(db: Session, user_id: int, endpoint: str) -> bool:
    sub = db.scalar(select(PushSubscription).where(
        PushSubscription.endpoint == endpoint, PushSubscription.user_id == user_id))
    if sub is None:
        return False
    db.delete(sub)
    db.commit()
    return True


# --- Sending --------------------------------------------------------------------

def send(sub: PushSubscription, title: str, body: str, url: str = "/") -> int:
    """Send one notification. Returns the push service's HTTP status code.

    404 or 410 means the device unsubscribed (app deleted, permission revoked...).
    """
    from pywebpush import WebPushException, webpush

    try:
        response = webpush(
            subscription_info={"endpoint": sub.endpoint,
                               "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"],
            # A fresh dict each time: the library adds fields to it.
            vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:admin@example.com")},
            ttl=4 * 3600,  # if the phone is offline, still deliver within 4 hours
        )
        return response.status_code
    except WebPushException as e:
        return e.response.status_code if e.response is not None else 0


def notify_user(db: Session, user: User, title: str, body: str, url: str = "/") -> int:
    """Send to all of a user's devices, cleaning up ones that are gone. Returns # delivered."""
    delivered = 0
    for sub in list(user.push_subscriptions):
        status = send(sub, title, body, url)
        if status in (404, 410):
            db.delete(sub)
        elif 200 <= status < 300:
            delivered += 1
    db.commit()
    return delivered


# --- What the reminders say ---------------------------------------------------------

def morning_message(db: Session, user: User, today: dt.date) -> tuple[str, str] | None:
    """Ask for a weigh-in, unless it's already logged today."""
    logged = db.scalar(select(WeighIn.id).where(WeighIn.user_id == user.id, WeighIn.date == today))
    if logged:
        return None
    return ("Good morning! ⚖️", f"What do you weigh today, {user.name}? Tap to log it.")


def evening_message(db: Session, user: User, today: dt.date) -> tuple[str, str]:
    """Today's totals against the targets, plus any main meal that wasn't logged."""
    s = services.daily_summary(db, user.id, today)
    if s["food_entries"] == 0:
        return ("Evening check-in 🌙",
                "Nothing logged today yet. Tell me what you ate and I'll add it up.")

    t = services.calorie_targets(db, user, today)
    if t["available"]:
        line = (f"{round(s['calories']):,} / {t['target_calories']:,} kcal · "
                f"{round(s['protein_g'])} / {t['protein_target_g']} g protein")
    else:
        line = f"{round(s['calories']):,} kcal · {round(s['protein_g'])} g protein"

    logged_meals = set(db.scalars(select(FoodEntry.meal).where(
        FoodEntry.user_id == user.id, FoodEntry.date == today)))
    missing = [m.value for m in (Meal.breakfast, Meal.lunch, Meal.dinner) if m not in logged_meals]
    if missing:
        line += f". Not logged yet: {', '.join(missing)}."
    if s["workouts"]:
        line += f" Trained today 💪"
    return ("Today's wrap-up 🌙", line)


def run_reminder(db: Session, kind: str) -> dict:
    """Called by the scheduler. Sends `kind` ('morning' or 'evening') to everyone subscribed."""
    today = local_today()
    users = db.scalars(select(User).where(User.push_subscriptions.any())).all()
    sent, skipped = 0, 0
    for user in users:
        message = morning_message(db, user, today) if kind == "morning" \
            else evening_message(db, user, today)
        if message is None:
            skipped += 1
            continue
        sent += notify_user(db, user, *message)
    return {"kind": kind, "date": today, "users": len(users), "sent": sent, "skipped": skipped}
