from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..deps import get_user
from ..models import User, WeighIn
from ..schemas import TrendPoint, WeighInCreate, WeighInOut

router = APIRouter(prefix="/users/{user_id}/weigh-ins", tags=["weight"])


@router.post("", response_model=WeighInOut, status_code=201)
def log_weigh_in(
    payload: WeighInCreate,
    response: Response,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """Log a weigh-in. A second weigh-in on the same day REPLACES the first.

    Returns 201 for a new record and 200 when an existing one was replaced.
    """
    weigh_in, created = services.log_weigh_in(db, user.id, payload.date, payload.weight_kg)
    if not created:
        response.status_code = 200
    return weigh_in


@router.get("/trend", response_model=list[TrendPoint])
def weight_trend(
    days: int = Query(default=14, ge=1, le=365),
    end: date | None = None,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """Weigh-ins over the last `days`, each with its 7-day rolling average."""
    return services.weight_trend(db, user.id, end or date.today(), days)


@router.get("", response_model=list[WeighInOut])
def list_weigh_ins(
    start: date | None = None,
    end: date | None = None,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    query = select(WeighIn).where(WeighIn.user_id == user.id)
    if start:
        query = query.where(WeighIn.date >= start)
    if end:
        query = query.where(WeighIn.date <= end)
    return db.scalars(query.order_by(WeighIn.date)).all()
