"""Business logic: calculations that both the API routes and the AI coach use.

Keeping this separate from the routers means the coach can call the exact
same functions the web page uses, so the numbers always agree.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    ActivityLevel, Exercise, FoodEntry, Goal, MuscleGroup, Sex, User, WeighIn, Workout,
    WorkoutSet,
)

# --- Safety limits -----------------------------------------------------------
# These live in code, not in the AI prompt, so the coach can never talk its way
# past them. Any calorie target it gives comes from calorie_targets() below.
MIN_CALORIES = {Sex.male: 1500, Sex.female: 1200}
MAX_DAILY_DEFICIT = 500   # ~0.5 kg/week: a sustainable pace
DAILY_SURPLUS = 300       # lean-gain pace
PROTEIN_G_PER_KG = 1.6

ACTIVITY_MULTIPLIER = {
    ActivityLevel.sedentary: 1.2,
    ActivityLevel.light: 1.375,
    ActivityLevel.moderate: 1.55,
    ActivityLevel.active: 1.725,
    ActivityLevel.very_active: 1.9,
}


# --- Daily summary -----------------------------------------------------------

def latest_weight(db: Session, user_id: int, on_or_before: date) -> float | None:
    return db.scalar(
        select(WeighIn.weight_kg)
        .where(WeighIn.user_id == user_id, WeighIn.date <= on_or_before)
        .order_by(WeighIn.date.desc())
        .limit(1)
    )


def daily_summary(db: Session, user_id: int, day: date) -> dict:
    # Let the database add things up. coalesce(..., 0) turns "no rows" into 0.
    food = db.execute(
        select(
            func.coalesce(func.sum(FoodEntry.calories), 0),
            func.coalesce(func.sum(FoodEntry.protein_g), 0),
            func.coalesce(func.sum(FoodEntry.carbs_g), 0),
            func.coalesce(func.sum(FoodEntry.fat_g), 0),
            func.count(FoodEntry.id),
        ).where(FoodEntry.user_id == user_id, FoodEntry.date == day)
    ).one()

    workouts = db.scalar(
        select(func.count(Workout.id)).where(Workout.user_id == user_id, Workout.date == day)
    )
    volume = db.scalar(
        select(func.coalesce(func.sum(WorkoutSet.reps * WorkoutSet.weight_kg), 0))
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .where(Workout.user_id == user_id, Workout.date == day)
    )

    return {
        "date": day,
        "calories": round(food[0], 1),
        "protein_g": round(food[1], 1),
        "carbs_g": round(food[2], 1),
        "fat_g": round(food[3], 1),
        "food_entries": food[4],
        "latest_weight_kg": latest_weight(db, user_id, day),
        "workouts": workouts,
        "workout_volume_kg": round(volume, 1),
    }


# --- Weight trend --------------------------------------------------------------

def weight_trend(db: Session, user_id: int, end: date, days: int = 14) -> list[dict]:
    """Each weigh-in in the last `days`, plus the average of the 7 days ending that day.

    Day-to-day weight jumps around with water and salt; the 7-day average is
    the number that actually shows whether you're losing, gaining or holding.
    """
    start = end - timedelta(days=days - 1)
    # Fetch 6 extra days before `start` so the first points have a full window.
    rows = db.execute(
        select(WeighIn.date, WeighIn.weight_kg)
        .where(
            WeighIn.user_id == user_id,
            WeighIn.date >= start - timedelta(days=6),
            WeighIn.date <= end,
        )
        .order_by(WeighIn.date)
    ).all()

    result = []
    for d, w in rows:
        if d < start:
            continue
        window = [wt for dd, wt in rows if d - timedelta(days=6) <= dd <= d]
        result.append({
            "date": d,
            "weight_kg": w,
            "avg_7d": round(sum(window) / len(window), 2),
        })
    return result


# --- Calorie & protein targets -----------------------------------------------

def calorie_targets(db: Session, user: User, today: date) -> dict:
    """Estimate maintenance calories and a goal-based target, with safety floors."""
    weight = latest_weight(db, user.id, today)
    if weight is None:
        return {"available": False, "reason": "Log a weigh-in first so targets can be calculated."}

    age = today.year - user.birth_year
    # Mifflin-St Jeor equation for resting energy use (BMR).
    bmr = 10 * weight + 6.25 * user.height_cm - 5 * age + (5 if user.sex == Sex.male else -161)
    maintenance = bmr * ACTIVITY_MULTIPLIER[user.activity_level]

    if user.goal == Goal.lose:
        target = maintenance - MAX_DAILY_DEFICIT
    elif user.goal == Goal.gain:
        target = maintenance + DAILY_SURPLUS
    else:
        target = maintenance

    floor = MIN_CALORIES[user.sex]
    floor_applied = target < floor
    target = max(target, floor)

    return {
        "available": True,
        "weight_kg_used": weight,
        "maintenance_calories": round(maintenance),
        "target_calories": round(target),
        "protein_target_g": round(weight * PROTEIN_G_PER_KG),
        "goal": user.goal.value,
        "floor_applied": floor_applied,
    }


# --- Writing data (used by both the API routes and the agent) ----------------

def add_food_entry(db: Session, user_id: int, **fields) -> FoodEntry:
    """Save one food entry. fields = date, meal, description, calories, protein_g, carbs_g, fat_g."""
    entry = FoodEntry(user_id=user_id, **fields)
    db.add(entry)
    db.commit()
    return entry


def food_entries_on(db: Session, user_id: int, day: date) -> list[FoodEntry]:
    return list(db.scalars(
        select(FoodEntry)
        .where(FoodEntry.user_id == user_id, FoodEntry.date == day)
        .order_by(FoodEntry.id)
    ))


def delete_food_entry(db: Session, user_id: int, entry_id: int) -> FoodEntry | None:
    """Delete an entry if it belongs to this user. Returns the deleted entry, or None."""
    entry = db.scalar(
        select(FoodEntry).where(FoodEntry.id == entry_id, FoodEntry.user_id == user_id)
    )
    if entry is not None:
        db.delete(entry)
        db.commit()
    return entry


def log_weigh_in(db: Session, user_id: int, day: date, weight_kg: float) -> tuple[WeighIn, bool]:
    """Save a weigh-in. A second one on the same day replaces the first.

    Returns (weigh_in, created). created is False when an existing one was replaced.
    Design choice: people re-weigh to fix a typo, so 'last value wins' fits real use.
    """
    existing = db.scalar(
        select(WeighIn).where(WeighIn.user_id == user_id, WeighIn.date == day)
    )
    if existing:
        existing.weight_kg = weight_kg
        db.commit()
        return existing, False
    weigh_in = WeighIn(user_id=user_id, date=day, weight_kg=weight_kg)
    db.add(weigh_in)
    db.commit()
    return weigh_in, True


def find_or_create_exercise(db: Session, name: str, muscle_group: MuscleGroup) -> Exercise:
    """Match an exercise by name (ignoring upper/lower case), or add it to the catalog."""
    name = " ".join(name.split())  # tidy extra spaces
    exercise = db.scalar(select(Exercise).where(func.lower(Exercise.name) == name.lower()))
    if exercise is None:
        exercise = Exercise(name=name, muscle_group=muscle_group)
        db.add(exercise)
        db.flush()  # sends the INSERT now so exercise.id exists, without committing yet
    return exercise


def create_workout(
    db: Session, user_id: int, day: date, notes: str | None,
    sets: list[tuple[int, int, float]],
) -> Workout:
    """Save a workout and all its sets in ONE commit: all of it is saved, or none of it.

    sets = [(exercise_id, reps, weight_kg), ...] in the order they were done.
    """
    workout = Workout(user_id=user_id, date=day, notes=notes)
    for number, (exercise_id, reps, weight_kg) in enumerate(sets, start=1):
        workout.sets.append(WorkoutSet(
            exercise_id=exercise_id, set_number=number, reps=reps, weight_kg=weight_kg,
        ))
    db.add(workout)
    db.commit()
    db.refresh(workout)
    return workout


def exercise_names(db: Session) -> list[str]:
    return list(db.scalars(select(Exercise.name).order_by(Exercise.name)))


# --- Ranges (used mainly by the coach) ----------------------------------------

def food_by_day(db: Session, user_id: int, start: date, end: date) -> list[dict]:
    """Daily calorie and macro totals for every logged day in the range."""
    rows = db.execute(
        select(
            FoodEntry.date,
            func.sum(FoodEntry.calories),
            func.sum(FoodEntry.protein_g),
            func.sum(FoodEntry.carbs_g),
            func.sum(FoodEntry.fat_g),
        )
        .where(FoodEntry.user_id == user_id, FoodEntry.date.between(start, end))
        .group_by(FoodEntry.date)
        .order_by(FoodEntry.date)
    ).all()
    return [
        {"date": d, "calories": round(c), "protein_g": round(p), "carbs_g": round(cb), "fat_g": round(f)}
        for d, c, p, cb, f in rows
    ]


def workouts_in_range(db: Session, user_id: int, start: date, end: date) -> list[Workout]:
    return list(db.scalars(
        select(Workout)
        .where(Workout.user_id == user_id, Workout.date.between(start, end))
        .order_by(Workout.date, Workout.id)
    ))


def volume_by_muscle(db: Session, user_id: int, start: date, end: date) -> dict:
    """Sets and volume per muscle group: shows which areas are being neglected."""
    rows = db.execute(
        select(
            Exercise.muscle_group,
            func.count(WorkoutSet.id),
            func.coalesce(func.sum(WorkoutSet.reps * WorkoutSet.weight_kg), 0),
        )
        .join(WorkoutSet, WorkoutSet.exercise_id == Exercise.id)
        .join(Workout, WorkoutSet.workout_id == Workout.id)
        .where(Workout.user_id == user_id, Workout.date.between(start, end))
        .group_by(Exercise.muscle_group)
    ).all()
    result = defaultdict(lambda: {"sets": 0, "volume_kg": 0})
    for group, sets, vol in rows:
        result[group.value] = {"sets": sets, "volume_kg": round(vol)}
    return dict(result)
