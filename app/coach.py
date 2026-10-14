"""The AI coach: Gemini with tools that read AND write the user's own data.

How it works (the "agent loop"):
  1. We send the model the conversation plus a list of TOOLS it may call.
  2. If it wants to look something up or log something, its response contains
     function_calls: which tool to call with which arguments.
  3. WE run that tool (a normal Python function over our database) and send
     the result back.
  4. Repeat until it answers in plain text.

The model never touches the database directly. It can only use the tools below,
every input is validated with the same Pydantic rules as the web forms, and
every number it reports comes from services.py.
"""
import json
import os
import time
import datetime as dt
from datetime import timedelta

from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from . import services
from .models import Meal, MuscleGroup, User
from .schemas import FoodEntryCreate

# "gemini-flash-latest" always points at Google's newest Flash model.
MODEL = os.getenv("COACH_MODEL", "gemini-flash-latest")
# Used when the main model is overloaded: smaller, cheaper, usually has capacity.
FALLBACK_MODEL = os.getenv("COACH_FALLBACK_MODEL", "gemini-flash-lite-latest")
# 429 = too many requests, 500/503 = Google's side is struggling. Worth retrying.
RETRYABLE_CODES = {429, 500, 503}
RETRY_WAITS = (0, 2, 4)  # seconds to wait before each attempt, per model
MAX_TOOL_ROUNDS = 10     # safety stop so a confused model can't loop forever
MAX_RANGE_DAYS = 90


def system_prompt(user: User, today: dt.date, exercises: list[str]) -> str:
    return f"""You are a supportive, evidence-based nutrition and strength coach inside a \
personal tracking app. You are talking with {user.name}. The day being logged is \
{today.isoformat()} ({today.strftime('%A')}).

LOGGING (your main job)
- When the user tells you what they ate, what they weigh, or how they trained, LOG IT with
  log_food / log_weight / log_workout. Don't ask permission first.
- Only log things they actually ate or did. Not plans ("I'm going to have pizza later"),
  not hypotheticals ("how many calories in a burger?").
- Food: estimate calories, protein, carbs and fat from typical portions. Make a sensible
  assumption rather than asking (e.g. "a bowl of pasta" = ~250 g cooked with sauce) and say
  what you assumed. Ask a short question only if the portion could plausibly be off by more
  than ~2x and you can't reasonably guess. One entry per dish or item they mention.
- Meal type: use what they say ("for lunch"), otherwise infer it from context; use "snack"
  when unclear. Log to a different date only if they clearly say so ("yesterday").
- Weight: in kg. Convert pounds (1 lb = 0.4536 kg).
- Workouts: use names from the exercise catalog when they match. Known exercises:
  {", ".join(exercises)}. If they did something not in the list, give it a clear standard
  name and the right muscle group. "3x8 at 60" means 3 sets of 8 reps at 60 kg. Use
  weight_kg 0 for bodyweight exercises. For cardio, log one set with reps = minutes.
- After logging, confirm briefly: one line per item with calories and protein for food.
  Then, if useful, one short coaching remark (e.g. how the day is tracking vs target).
- Corrections ("actually it was 3 eggs", "delete the coffee"): call get_food_entries to find
  the entry id, delete_food_entry the wrong one, then log the corrected one.

COACHING
- Base advice on the logged data. Use the tools to look it up instead of guessing,
  and mention the actual numbers you found.
- For calorie and protein targets, ALWAYS use get_targets. Never invent your own target
  and never recommend eating below it. Those targets include safety floors on purpose.
- Weight changes day to day from water and salt; judge progress on the 7-day average.
- Keep answers short and practical: a few sentences or a short list. Plain text, no
  markdown headings or tables.

BOUNDARIES
- You are not a doctor or dietitian. Don't diagnose. For pain, injuries, medical
  conditions, medications or pregnancy, recommend seeing a professional.
- If the user describes very restrictive eating, purging, compulsive exercise or
  distress about food or body, respond with care, don't give numeric targets in that
  reply, and gently encourage talking to a professional."""


# --- Tool input shapes ---------------------------------------------------------
# Pydantic models double as (a) the JSON Schema we show the model and (b) validation of
# what the model sends back. One definition, no chance of the two drifting apart.

class FoodItem(BaseModel):
    meal: Meal
    description: str = Field(min_length=1, max_length=200,
                             description="What was eaten, with the assumed portion, "
                                         "e.g. 'Chicken shawarma in pita (~350 g)'")
    calories: float = Field(ge=0, le=10_000)
    protein_g: float = Field(ge=0, le=1_000)
    carbs_g: float = Field(ge=0, le=1_000)
    fat_g: float = Field(ge=0, le=1_000)
    date: dt.date | None = Field(default=None, description="YYYY-MM-DD. Omit for the current day.")


class LogFoodInput(BaseModel):
    items: list[FoodItem] = Field(min_length=1, max_length=20)


class LogWeightInput(BaseModel):
    weight_kg: float = Field(gt=20, lt=400)
    date: dt.date | None = Field(default=None, description="YYYY-MM-DD. Omit for the current day.")


class SetInput(BaseModel):
    reps: int = Field(gt=0, le=1_000, description="Reps, or minutes for cardio")
    weight_kg: float = Field(ge=0, le=1_000, description="0 for bodyweight or cardio")


class ExerciseInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    muscle_group: MuscleGroup
    sets: list[SetInput] = Field(min_length=1, max_length=50)


class LogWorkoutInput(BaseModel):
    exercises: list[ExerciseInput] = Field(min_length=1, max_length=30)
    notes: str | None = Field(default=None, max_length=500)
    date: dt.date | None = Field(default=None, description="YYYY-MM-DD. Omit for the current day.")


class DayInput(BaseModel):
    date: dt.date


class DaysInput(BaseModel):
    days: int = Field(ge=1, le=MAX_RANGE_DAYS)


class RangeInput(BaseModel):
    start: dt.date
    end: dt.date


class DeleteFoodInput(BaseModel):
    entry_id: int


class NoInput(BaseModel):
    pass


def _inline_refs(node, defs):
    """Pydantic puts nested models under $defs and points to them with $ref.
    Replace each $ref with the real definition so the model gets one plain schema."""
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline_refs(defs[node["$ref"].split("/")[-1]], defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _tool(name: str, description: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return {"name": name, "description": description, "input_schema": _inline_refs(schema, defs)}


TOOL_INPUTS: dict[str, type[BaseModel]] = {
    "log_food": LogFoodInput,
    "log_weight": LogWeightInput,
    "log_workout": LogWorkoutInput,
    "get_food_entries": DayInput,
    "delete_food_entry": DeleteFoodInput,
    "get_profile": NoInput,
    "get_targets": NoInput,
    "get_daily_summary": DayInput,
    "get_weight_trend": DaysInput,
    "get_food_by_day": RangeInput,
    "get_workouts": RangeInput,
    "get_volume_by_muscle": RangeInput,
}

TOOLS = [
    _tool("log_food", "Save one or more food items the user ATE, with your estimated calories "
          "and macros. Returns the saved entries and the day's new totals.", LogFoodInput),
    _tool("log_weight", "Save the user's body weight in kg for a day. Replaces any weigh-in "
          "already saved for that day.", LogWeightInput),
    _tool("log_workout", "Save a workout the user DID: each exercise with its sets. Unknown "
          "exercise names are added to the catalog with the given muscle group.", LogWorkoutInput),
    _tool("get_food_entries", "List the individual food entries for one day, with their ids. "
          "Use before deleting or correcting an entry.", DayInput),
    _tool("delete_food_entry", "Delete one food entry by id (to fix a mistake).", DeleteFoodInput),
    _tool("get_profile", "The user's profile: height, birth year, sex, goal (lose/maintain/gain) "
          "and activity level.", NoInput),
    _tool("get_targets", "Calculated maintenance calories, goal calorie target and protein "
          "target, from the latest weigh-in. Includes safety floors. Use for any question "
          "about how much to eat.", NoInput),
    _tool("get_daily_summary", "Totals for one day: calories, protein, carbs, fat, number of "
          "food entries, latest weight, number of workouts and training volume.", DayInput),
    _tool("get_weight_trend", "Weigh-ins over the last N days, each with its 7-day rolling "
          "average. Use the rolling average to judge real progress.", DaysInput),
    _tool("get_food_by_day", "Daily calorie and macro totals for each logged day between two "
          "dates. Days with nothing logged are absent.", RangeInput),
    _tool("get_workouts", "Workouts between two dates with every set: exercise, reps, weight.",
          RangeInput),
    _tool("get_volume_by_muscle", "Number of sets and total volume per muscle group between two "
          "dates. Good for spotting neglected muscle groups.", RangeInput),
]


def _clamp_range(r: RangeInput) -> tuple[dt.date, dt.date]:
    start, end = sorted((r.start, r.end))
    # Cap the range so one tool call can't pull years of data into the prompt.
    return max(start, end - timedelta(days=MAX_RANGE_DAYS - 1)), end


def run_tool(db: Session, user: User, today: dt.date, name: str,
             raw_args: dict) -> tuple[str, list[str]]:
    """Run one tool. Returns (result as JSON for the model, human-readable actions taken)."""
    actions: list[str] = []

    if name not in TOOL_INPUTS:
        return json.dumps({"error": f"Unknown tool: {name}"}), actions
    try:
        args = TOOL_INPUTS[name].model_validate(raw_args)
    except ValidationError as e:
        # Bad arguments: tell the model exactly what was wrong so it can fix and retry.
        return json.dumps({"error": "Invalid input", "details": e.errors(include_url=False)},
                          default=str), actions

    # --- writing tools ---
    if name == "log_food":
        saved = []
        for item in args.items:
            data = FoodEntryCreate(**{**item.model_dump(), "date": item.date or today})
            entry = services.add_food_entry(db, user.id, **data.model_dump())
            saved.append({"id": entry.id, "date": entry.date, "meal": entry.meal.value,
                          "description": entry.description, "calories": entry.calories,
                          "protein_g": entry.protein_g})
            actions.append(f"Food · {entry.description} · {round(entry.calories)} kcal")
        days = sorted({s["date"] for s in saved})
        result = {"saved": saved,
                  "day_totals": [services.daily_summary(db, user.id, d) for d in days]}

    elif name == "log_weight":
        day = args.date or today
        _, created = services.log_weigh_in(db, user.id, day, args.weight_kg)
        result = {"saved": True, "date": day, "weight_kg": args.weight_kg,
                  "replaced_existing": not created}
        actions.append(f"Weight · {args.weight_kg:g} kg")

    elif name == "log_workout":
        day = args.date or today
        sets = []
        for ex in args.exercises:
            exercise = services.find_or_create_exercise(db, ex.name, ex.muscle_group)
            sets += [(exercise.id, s.reps, s.weight_kg) for s in ex.sets]
        workout = services.create_workout(db, user.id, day, args.notes, sets)
        result = {"saved": True, "workout_id": workout.id, "date": day,
                  "sets": len(workout.sets), "total_volume_kg": workout.total_volume_kg}
        names = ", ".join(ex.name for ex in args.exercises)
        actions.append(f"Workout · {names} · {len(workout.sets)} sets")

    elif name == "get_food_entries":
        result = [{"id": e.id, "meal": e.meal.value, "description": e.description,
                   "calories": e.calories, "protein_g": e.protein_g, "carbs_g": e.carbs_g,
                   "fat_g": e.fat_g} for e in services.food_entries_on(db, user.id, args.date)]

    elif name == "delete_food_entry":
        entry = services.delete_food_entry(db, user.id, args.entry_id)
        if entry is None:
            result = {"error": "No food entry with that id for this user."}
        else:
            result = {"deleted": True, "description": entry.description}
            actions.append(f"Removed · {entry.description}")

    # --- reading tools ---
    elif name == "get_profile":
        result = {"name": user.name, "height_cm": user.height_cm,
                  "birth_year": user.birth_year, "sex": user.sex.value,
                  "goal": user.goal.value, "activity_level": user.activity_level.value}
    elif name == "get_targets":
        result = services.calorie_targets(db, user, today)
    elif name == "get_daily_summary":
        result = services.daily_summary(db, user.id, args.date)
    elif name == "get_weight_trend":
        result = services.weight_trend(db, user.id, today, args.days)
    elif name == "get_food_by_day":
        result = services.food_by_day(db, user.id, *_clamp_range(args))
    elif name == "get_workouts":
        result = [
            {"date": w.date, "notes": w.notes, "total_volume_kg": w.total_volume_kg,
             "sets": [{"exercise": s.exercise_name, "reps": s.reps, "weight_kg": s.weight_kg}
                      for s in w.sets]}
            for w in services.workouts_in_range(db, user.id, *_clamp_range(args))
        ]
    else:  # get_volume_by_muscle
        result = services.volume_by_muscle(db, user.id, *_clamp_range(args))

    return json.dumps(result, default=str), actions  # default=str turns dates into strings


def _to_gemini_history(history: list[dict]) -> list[types.Content]:
    """The web page sends [{"role": "user"|"assistant", "content": "..."}].
    Gemini calls the assistant side "model" and wants text wrapped in Parts."""
    return [
        types.Content(role="model" if turn["role"] == "assistant" else "user",
                      parts=[types.Part.from_text(text=turn["content"])])
        for turn in history
    ]


class CoachBusy(Exception):
    """Every model we tried was overloaded. The user should simply try again later."""


def _generate(client, contents, config):
    """Call Gemini, retrying when it's overloaded and then falling back to a lighter model.

    Errors that retrying can't fix (bad API key, invalid request) are raised right away.
    """
    for model in (MODEL, FALLBACK_MODEL):
        for wait in RETRY_WAITS:
            time.sleep(wait)
            try:
                return client.models.generate_content(model=model, contents=contents, config=config)
            except genai_errors.APIError as e:
                if e.code not in RETRYABLE_CODES:
                    raise
    raise CoachBusy()


def ask_coach(db: Session, user: User, message: str, history: list[dict], client,
              today: dt.date | None = None) -> tuple[str, list[str]]:
    """Run the agent loop. Returns (final text reply, list of things it logged)."""
    today = today or dt.date.today()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt(user, today, services.exercise_names(db)),
        tools=[types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t["name"], description=t["description"],
                                      parameters_json_schema=t["input_schema"])
            for t in TOOLS
        ])],
        # We run the tools ourselves, so we can validate input and track what was logged.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [*_to_gemini_history(history),
                types.Content(role="user", parts=[types.Part.from_text(text=message)])]
    actions: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = _generate(client, contents, config)

        calls = response.function_calls or []
        if not calls:
            return (response.text or "").strip() or "Sorry, I didn't get that. Could you rephrase?", actions

        # Keep the model's turn (its tool requests) in the conversation...
        contents.append(response.candidates[0].content)
        # ...run every tool it asked for, and send all results back in one turn.
        parts = []
        for call in calls:
            output, done = run_tool(db, user, today, call.name, dict(call.args or {}))
            actions += done
            parts.append(types.Part.from_function_response(
                name=call.name, response={"result": json.loads(output)}))
        contents.append(types.Content(role="tool", parts=parts))

    fallback = "Sorry, I got stuck partway. Check the log on the left and tell me what's missing."
    return fallback, actions
