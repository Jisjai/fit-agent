from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..deps import get_user
from ..models import User
from ..schemas import DailySummary, Targets

router = APIRouter(prefix="/users/{user_id}", tags=["summary"])


@router.get("/summary", response_model=DailySummary)
def daily_summary(
    day: date = Query(alias="date"),
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    # alias="date": the URL says ?date=..., but in Python we call it `day`
    # so it doesn't clash with the `date` type we imported.
    return services.daily_summary(db, user.id, day)


@router.get("/targets", response_model=Targets)
def targets(
    day: date | None = Query(default=None, alias="date"),
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    return services.calorie_targets(db, user, day or date.today())
