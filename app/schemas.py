"""Pydantic schemas: what the API accepts (…Create) and returns (…Out).

Kept separate from the ORM models so the API shape and the database shape
can change independently, and so we never leak internal fields by accident.
"""
import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import ActivityLevel, Goal, Meal, MuscleGroup, Sex


class ORMOut(BaseModel):
    # Lets Pydantic read attributes off SQLAlchemy objects (obj.name, not obj["name"]).
    model_config = ConfigDict(from_attributes=True)


# --- Users -----------------------------------------------------------------

class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    height_cm: float = Field(gt=50, lt=272)
    birth_year: int = Field(ge=1900, le=2015)
    sex: Sex
    goal: Goal
    activity_level: ActivityLevel


class UserOut(ORMOut, UserCreate):
    id: int


# --- Weigh-ins -------------------------------------------------------------

class WeighInCreate(BaseModel):
    date: dt.date
    weight_kg: float = Field(gt=20, lt=400)


class WeighInOut(ORMOut):
    id: int
    date: dt.date
    weight_kg: float


# --- Food ------------------------------------------------------------------

class FoodEntryCreate(BaseModel):
    date: dt.date
    meal: Meal
    description: str = Field(min_length=1, max_length=200)
    calories: float = Field(ge=0, le=10_000)
    protein_g: float = Field(ge=0, le=1_000)
    carbs_g: float = Field(ge=0, le=1_000)
    fat_g: float = Field(ge=0, le=1_000)


class FoodEntryOut(ORMOut, FoodEntryCreate):
    id: int


# --- Exercises & workouts --------------------------------------------------

class ExerciseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    muscle_group: MuscleGroup


class ExerciseOut(ORMOut, ExerciseCreate):
    id: int


class SetCreate(BaseModel):
    exercise_id: int
    reps: int = Field(gt=0, le=1_000)
    weight_kg: float = Field(ge=0, le=1_000)


class WorkoutCreate(BaseModel):
    date: dt.date
    notes: str | None = Field(default=None, max_length=500)
    sets: list[SetCreate] = Field(min_length=1)  # a workout with no sets makes no sense


class SetOut(ORMOut):
    set_number: int
    exercise_id: int
    exercise_name: str  # comes from the @property on WorkoutSet
    reps: int
    weight_kg: float


class WorkoutOut(ORMOut):
    id: int
    date: dt.date
    notes: str | None
    sets: list[SetOut]
    total_volume_kg: float  # comes from the @property on Workout


# --- Summary ---------------------------------------------------------------

class DailySummary(BaseModel):
    date: dt.date
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float
    food_entries: int
    latest_weight_kg: float | None  # most recent weigh-in on or before this date
    workouts: int
    workout_volume_kg: float


class TrendPoint(BaseModel):
    date: dt.date
    weight_kg: float
    avg_7d: float


class Targets(BaseModel):
    available: bool
    reason: str | None = None
    weight_kg_used: float | None = None
    maintenance_calories: int | None = None
    target_calories: int | None = None
    protein_target_g: int | None = None
    goal: Goal | None = None
    floor_applied: bool | None = None


# --- Coach -------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8_000)


class CoachRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2_000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)
    # The day the user is looking at in the app; things get logged there by default.
    date: dt.date | None = None


class CoachReply(BaseModel):
    reply: str
    # Short descriptions of what the agent saved, e.g. "Food · Oatmeal with banana · 380 kcal".
    actions: list[str] = Field(default_factory=list)
    # Coach messages the user has left today (see COACH_DAILY_LIMIT).
    messages_left: int | None = None
