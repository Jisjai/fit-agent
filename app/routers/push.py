import hmac
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import reminders
from ..database import get_db
from ..deps import get_user
from ..models import User

router = APIRouter(tags=["reminders"])


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class SubscriptionIn(BaseModel):
    """Exactly the shape the browser's PushSubscription.toJSON() produces."""
    endpoint: str = Field(max_length=1000, pattern=r"^https://")
    keys: SubscriptionKeys


class Unsubscribe(BaseModel):
    endpoint: str


@router.get("/push/public-key")
def public_key():
    if not reminders.push_configured():
        raise HTTPException(status_code=503, detail="Reminders aren't set up on the server yet.")
    return {"key": os.environ["VAPID_PUBLIC_KEY"]}


@router.post("/users/{user_id}/push/subscribe", status_code=201)
def subscribe(payload: SubscriptionIn, user: User = Depends(get_user),
              db: Session = Depends(get_db)):
    reminders.save_subscription(db, user.id, payload.endpoint,
                                payload.keys.p256dh, payload.keys.auth)
    return {"ok": True}


@router.post("/users/{user_id}/push/unsubscribe")
def unsubscribe(payload: Unsubscribe, user: User = Depends(get_user),
                db: Session = Depends(get_db)):
    return {"removed": reminders.delete_subscription(db, user.id, payload.endpoint)}


@router.post("/users/{user_id}/push/test")
def test_notification(user: User = Depends(get_user), db: Session = Depends(get_db)):
    if not reminders.push_configured():
        raise HTTPException(status_code=503, detail="Reminders aren't set up on the server yet.")
    delivered = reminders.notify_user(db, user, "Fit Agent 🔔",
                                      "Notifications work! You'll get your reminders here.")
    if delivered == 0:
        raise HTTPException(status_code=502, detail="Couldn't reach your device. Try turning reminders off and on.")
    return {"delivered": delivered}


# --- Called by Vercel's scheduler (see "crons" in vercel.json) ------------------------

def require_cron_secret(authorization: str | None = Header(default=None)):
    """Only Vercel's scheduler knows CRON_SECRET; it sends it as 'Bearer <secret>'.

    Without this, anyone who found the URL could spam you with notifications.
    """
    secret = os.getenv("CRON_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not set")
    if not hmac.compare_digest(authorization or "", f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Not allowed")


@router.get("/cron/{kind}", dependencies=[Depends(require_cron_secret)], include_in_schema=False)
def cron(kind: str, db: Session = Depends(get_db)):
    if kind not in ("morning", "evening"):
        raise HTTPException(status_code=404)
    if not reminders.push_configured():
        raise HTTPException(status_code=503, detail="VAPID keys are not set")
    return reminders.run_reminder(db, kind)
