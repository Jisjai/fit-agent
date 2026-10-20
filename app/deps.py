"""Shared dependencies used by several routers."""
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def current_sub(request: Request) -> str:
    """Google's permanent ID for the signed-in account (set by the auth middleware)."""
    return request.state.identity["sub"]


def get_user(user_id: int, request: Request, db: Session = Depends(get_db)) -> User:
    """Look up the user from the URL path, and make sure it's YOUR profile.

    Every route with {user_id} in its path depends on this, so the "is this yours?"
    check lives in one place. Someone else's profile gets the same 404 as one that
    doesn't exist, so the API doesn't even reveal which user ids are taken.
    """
    user = db.get(User, user_id)
    if user is None or user.google_sub != current_sub(request):
        raise HTTPException(status_code=404, detail="User not found")
    return user
