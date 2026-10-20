from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import current_sub, get_user
from ..models import User
from ..schemas import UserCreate, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Create the profile for the signed-in Google account (one per account)."""
    sub = current_sub(request)
    if db.scalar(select(User).where(User.google_sub == sub)) is not None:
        raise HTTPException(status_code=409, detail="You already have a profile.")
    user = User(**payload.model_dump(), google_sub=sub,
                email=request.state.identity.get("email"))
    db.add(user)
    db.commit()
    return user


@router.get("", response_model=list[UserOut])
def list_users(request: Request, db: Session = Depends(get_db)):
    """Your own profile, as a list: empty until you've created it."""
    return db.scalars(select(User).where(User.google_sub == current_sub(request))).all()


@router.get("/{user_id}", response_model=UserOut)
def read_user(user: User = Depends(get_user)):
    return user
