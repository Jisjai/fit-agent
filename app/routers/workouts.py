from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..deps import get_user
from ..models import Exercise, User, Workout
from ..schemas import ExerciseCreate, ExerciseOut, WorkoutCreate, WorkoutOut

exercises_router = APIRouter(prefix="/exercises", tags=["exercises"])
router = APIRouter(prefix="/users/{user_id}/workouts", tags=["workouts"])


# --- Exercise catalog ------------------------------------------------------

@exercises_router.post("", response_model=ExerciseOut, status_code=201)
def create_exercise(payload: ExerciseCreate, db: Session = Depends(get_db)):
    exercise = Exercise(**payload.model_dump())
    db.add(exercise)
    try:
        db.commit()
    except IntegrityError:
        # The unique constraint on name fired: let the DB be the source of truth.
        db.rollback()
        raise HTTPException(status_code=409, detail="Exercise already exists")
    return exercise


@exercises_router.get("", response_model=list[ExerciseOut])
def list_exercises(db: Session = Depends(get_db)):
    return db.scalars(select(Exercise).order_by(Exercise.name)).all()


# --- Workouts --------------------------------------------------------------

@router.post("", response_model=WorkoutOut, status_code=201)
def log_workout(
    payload: WorkoutCreate,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    # 1. Validate everything BEFORE writing anything.
    requested_ids = {s.exercise_id for s in payload.sets}
    found_ids = set(db.scalars(select(Exercise.id).where(Exercise.id.in_(requested_ids))))
    missing = requested_ids - found_ids
    if missing:
        raise HTTPException(
            status_code=400, detail=f"Unknown exercise id(s): {sorted(missing)}"
        )

    # 2. Save the workout and all its sets in one commit (see services.create_workout).
    return services.create_workout(
        db, user.id, payload.date, payload.notes,
        [(s.exercise_id, s.reps, s.weight_kg) for s in payload.sets],
    )


@router.get("", response_model=list[WorkoutOut])
def list_workouts(
    start: date,
    end: date,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    return services.workouts_in_range(db, user.id, start, end)


@router.get("/{workout_id}", response_model=WorkoutOut)
def read_workout(
    workout_id: int,
    user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    workout = db.scalar(
        select(Workout).where(Workout.id == workout_id, Workout.user_id == user.id)
    )
    if workout is None:
        raise HTTPException(status_code=404, detail="Workout not found")
    return workout
