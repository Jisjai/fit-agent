"""Starter exercise catalog, added automatically when the app starts."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import Exercise, MuscleGroup

EXERCISES = [
    ("Bench Press", MuscleGroup.chest),
    ("Incline Dumbbell Press", MuscleGroup.chest),
    ("Push-up", MuscleGroup.chest),
    ("Deadlift", MuscleGroup.back),
    ("Pull-up", MuscleGroup.back),
    ("Barbell Row", MuscleGroup.back),
    ("Lat Pulldown", MuscleGroup.back),
    ("Back Squat", MuscleGroup.legs),
    ("Romanian Deadlift", MuscleGroup.legs),
    ("Leg Press", MuscleGroup.legs),
    ("Walking Lunge", MuscleGroup.legs),
    ("Overhead Press", MuscleGroup.shoulders),
    ("Lateral Raise", MuscleGroup.shoulders),
    ("Barbell Curl", MuscleGroup.arms),
    ("Triceps Pushdown", MuscleGroup.arms),
    ("Dips", MuscleGroup.arms),
    ("Plank", MuscleGroup.core),
    ("Hanging Leg Raise", MuscleGroup.core),
    ("Running", MuscleGroup.cardio),
    ("Rowing Machine", MuscleGroup.cardio),
]


def seed_exercises(db: Session) -> int:
    """Add any missing starter exercises. Safe to run every startup."""
    existing = set(db.scalars(select(Exercise.name)))
    new = [Exercise(name=n, muscle_group=g) for n, g in EXERCISES if n not in existing]
    db.add_all(new)
    try:
        db.commit()
    except IntegrityError:
        # Online, two server instances can start at the same moment and both try to
        # insert the same exercises. The unique name constraint stops the second one;
        # that's fine, the exercises exist either way.
        db.rollback()
        return 0
    return len(new)
