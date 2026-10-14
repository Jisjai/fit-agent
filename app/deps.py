"""Shared dependencies used by several routers."""
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def get_user(user_id: int, db: Session = Depends(get_db)) -> User:
    """Look up the user from the URL path, or stop the request with a 404.

    Any route with {user_id} in its path can depend on this and receive a
    real User object, so the 'does this user exist?' check lives in one place.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
