from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..deps import get_user
from ..models import FoodEntry, User
from ..schemas import FoodEntryCreate, FoodEntryOut

router = APIRouter(prefix="/users/{user_id}/food", tags=["food"])


@router.post("", response_model=FoodEntryOut, status_code=201)
def log_food(
    payload: FoodEntryCreate,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    return services.add_food_entry(db, user.id, **payload.model_dump())


@router.get("", response_model=list[FoodEntryOut])
def list_food(
    date: date | None = None,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    query = select(FoodEntry).where(FoodEntry.user_id == user.id)
    if date:
        query = query.where(FoodEntry.date == date)
    return db.scalars(query.order_by(FoodEntry.date, FoodEntry.id)).all()


@router.delete("/{entry_id}", status_code=204)
def delete_food(
    entry_id: int,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    # services.delete_food_entry also filters on user_id, so user A can't delete
    # user B's entry: it simply looks like it doesn't exist (404), which leaks nothing.
    if services.delete_food_entry(db, user.id, entry_id) is None:
        raise HTTPException(status_code=404, detail="Food entry not found")
