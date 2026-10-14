"""SQLAlchemy ORM models: how data is stored in the database."""
import enum
import datetime as dt

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


# --- Enums -----------------------------------------------------------------
# Inheriting from str makes them JSON-friendly and comparable to plain strings.

class Sex(str, enum.Enum):
    male = "male"
    female = "female"


class Goal(str, enum.Enum):
    lose = "lose"
    maintain = "maintain"
    gain = "gain"


class ActivityLevel(str, enum.Enum):
    sedentary = "sedentary"
    light = "light"
    moderate = "moderate"
    active = "active"
    very_active = "very_active"


class Meal(str, enum.Enum):
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"
    snack = "snack"


class MuscleGroup(str, enum.Enum):
    chest = "chest"
    back = "back"
    legs = "legs"
    shoulders = "shoulders"
    arms = "arms"
    core = "core"
    cardio = "cardio"


# --- Tables ----------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    height_cm: Mapped[float]
    birth_year: Mapped[int]
    sex: Mapped[Sex]
    goal: Mapped[Goal]
    activity_level: Mapped[ActivityLevel]

    # Deleting a user deletes everything they logged.
    weigh_ins: Mapped[list["WeighIn"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    food_entries: Mapped[list["FoodEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    workouts: Mapped[list["Workout"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    push_subscriptions: Mapped[list["PushSubscription"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WeighIn(Base):
    __tablename__ = "weigh_ins"
    # The database itself guarantees one weigh-in per user per day.
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_weighin_user_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[dt.date]
    weight_kg: Mapped[float]

    user: Mapped[User] = relationship(back_populates="weigh_ins")


class FoodEntry(Base):
    __tablename__ = "food_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    meal: Mapped[Meal]
    description: Mapped[str] = mapped_column(String(200))
    calories: Mapped[float]
    protein_g: Mapped[float]
    carbs_g: Mapped[float]
    fat_g: Mapped[float]

    user: Mapped[User] = relationship(back_populates="food_entries")


class Exercise(Base):
    """Shared catalog of exercises (not per user)."""
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    muscle_group: Mapped[MuscleGroup]


class Workout(Base):
    __tablename__ = "workouts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    date: Mapped[dt.date] = mapped_column(index=True)
    notes: Mapped[str | None] = mapped_column(String(500))

    user: Mapped[User] = relationship(back_populates="workouts")
    sets: Mapped[list["WorkoutSet"]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        order_by="WorkoutSet.set_number",
    )

    @property
    def total_volume_kg(self) -> float:
        """Volume = sum of reps x weight over every set."""
        return sum(s.reps * s.weight_kg for s in self.sets)


class WorkoutSet(Base):
    __tablename__ = "workout_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    workout_id: Mapped[int] = mapped_column(ForeignKey("workouts.id"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"))
    set_number: Mapped[int]
    reps: Mapped[int]
    weight_kg: Mapped[float]  # 0 for bodyweight exercises

    workout: Mapped[Workout] = relationship(back_populates="sets")
    exercise: Mapped[Exercise] = relationship()

    @property
    def exercise_name(self) -> str:
        return self.exercise.name


class PushSubscription(Base):
    """One device that agreed to receive notifications (a phone, a laptop browser...).

    The browser hands us these three values when the user allows notifications.
    endpoint is a unique URL at Apple/Google/Mozilla's push service for that device;
    p256dh and auth are keys used to encrypt the message so only that device can read it.
    """
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))

    user: Mapped[User] = relationship(back_populates="push_subscriptions")
