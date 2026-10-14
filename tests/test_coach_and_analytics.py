import json
from types import SimpleNamespace as NS

from google.genai import errors as genai_errors
from google.genai import types

import app.coach as coach_module

from app.main import app
from app.routers.coach import get_ai_client


# --- Analytics ---------------------------------------------------------------

def test_weight_trend_rolling_average(client, user):
    uid = user["id"]
    weights = [80, 81, 79, 80, 80, 80, 81]  # Oct 1..7
    for day, w in enumerate(weights, start=1):
        client.post(f"/users/{uid}/weigh-ins", json={"date": f"2026-10-0{day}", "weight_kg": w})

    points = client.get(f"/users/{uid}/weigh-ins/trend",
                        params={"days": 3, "end": "2026-10-07"}).json()
    assert [p["date"] for p in points] == ["2026-10-05", "2026-10-06", "2026-10-07"]
    # Oct 7's average covers all 7 days, including ones before the 3-day window.
    assert points[-1]["avg_7d"] == round(sum(weights) / 7, 2)


def test_targets_need_a_weigh_in(client, user):
    t = client.get(f"/users/{user['id']}/targets", params={"date": "2026-10-14"}).json()
    assert t["available"] is False


def test_targets_never_below_safety_floor(client):
    uid = client.post("/users", json={
        "name": "Small", "height_cm": 150, "birth_year": 1960,
        "sex": "female", "goal": "lose", "activity_level": "sedentary",
    }).json()["id"]
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-14", "weight_kg": 45})

    t = client.get(f"/users/{uid}/targets", params={"date": "2026-10-14"}).json()
    # Raw math gives ~576 kcal, which would be unsafe. The floor must kick in.
    assert t["target_calories"] == 1200
    assert t["floor_applied"] is True


def test_list_workouts_in_range(client, user, exercises):
    uid = user["id"]
    for day in ["2026-10-10", "2026-10-12", "2026-10-20"]:
        client.post(f"/users/{uid}/workouts", json={
            "date": day,
            "sets": [{"exercise_id": exercises["Back Squat"], "reps": 5, "weight_kg": 100}],
        })
    got = client.get(f"/users/{uid}/workouts",
                     params={"start": "2026-10-09", "end": "2026-10-14"}).json()
    assert [w["date"] for w in got] == ["2026-10-10", "2026-10-12"]


# --- Coach (with a fake Gemini, so tests never call the real API) -------------

class FakeGemini:
    """Pretends to be genai.Client(): returns pre-scripted responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def models(self):  # so fake.models.generate_content(...) works like the real SDK
        return self

    def generate_content(self, **kwargs):
        # Copy the list: the coach keeps appending to the same one between calls.
        self.calls.append({**kwargs, "contents": list(kwargs["contents"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):  # lets a test simulate Google errors
            raise response
        return response


def _tool_call(name, args):
    call = NS(id="call_1", name=name, args=args)
    return NS(function_calls=[call], text=None,
              candidates=[NS(content=types.Content(role="model", parts=[
                  types.Part.from_function_call(name=name, args=args)]))])


def _text(t):
    return NS(function_calls=None, text=t, candidates=[])


def _tool_result(fake, call_index=1):
    """What our code sent back to the model as the first tool result."""
    tool_turn = fake.calls[call_index]["contents"][-1]
    assert tool_turn.role == "user"
    assert tool_turn.parts[0].function_response is not None
    return tool_turn.parts[0].function_response.response["result"]


def use(fake):
    app.dependency_overrides[get_ai_client] = lambda: fake


def test_coach_runs_tool_then_answers(client, user):
    uid = user["id"]
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-14", "weight_kg": 80})
    fake = FakeGemini([_tool_call("get_targets", {}), _text("Aim for about 2,700 kcal.")])
    use(fake)

    r = client.post(f"/users/{uid}/coach",
                    json={"message": "How much should I eat?", "date": "2026-10-14"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Aim for about 2,700 kcal."
    assert _tool_result(fake)["available"] is True


def test_coach_without_api_key_returns_503(client, user, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 503


def test_chat_history_is_passed_to_the_model(client, user):
    fake = FakeGemini([_text("ok")])
    use(fake)
    client.post(f"/users/{user['id']}/coach", json={
        "message": "and now?",
        "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    })
    roles = [c.role for c in fake.calls[0]["contents"]]
    assert roles == ["user", "model", "user"]


# --- Coach logging -------------------------------------------------------------

def test_coach_logs_food_from_chat(client, user):
    uid = user["id"]
    use(FakeGemini([
        _tool_call("log_food", {"items": [
            {"meal": "breakfast", "description": "2 fried eggs", "calories": 180,
             "protein_g": 12, "carbs_g": 1, "fat_g": 14},
            {"meal": "breakfast", "description": "Toast with avocado", "calories": 250,
             "protein_g": 6, "carbs_g": 28, "fat_g": 13},
        ]}),
        _text("Logged breakfast."),
    ]))

    r = client.post(f"/users/{uid}/coach",
                    json={"message": "2 eggs and avocado toast", "date": "2026-10-14"})
    assert r.status_code == 200
    assert len(r.json()["actions"]) == 2

    # It really landed in the database, on the day the page was showing.
    s = client.get(f"/users/{uid}/summary", params={"date": "2026-10-14"}).json()
    assert s["calories"] == 430
    assert s["food_entries"] == 2


def test_coach_logs_workout_and_creates_new_exercise(client, user):
    uid = user["id"]
    client.post("/exercises", json={"name": "Bench Press", "muscle_group": "chest"})
    use(FakeGemini([
        _tool_call("log_workout", {"exercises": [
            {"name": "bench press", "muscle_group": "chest",   # existing, different case
             "sets": [{"reps": 8, "weight_kg": 60}] * 3},
            {"name": "Cable Fly", "muscle_group": "chest",      # new exercise
             "sets": [{"reps": 12, "weight_kg": 15}]},
        ]}),
        _text("Nice session."),
    ]))

    r = client.post(f"/users/{uid}/coach", json={"message": "bench 3x8 at 60, then flyes",
                                                 "date": "2026-10-14"})
    assert r.status_code == 200

    names = [e["name"] for e in client.get("/exercises").json()]
    assert names.count("Bench Press") == 1   # matched case-insensitively, not duplicated
    assert "Cable Fly" in names

    s = client.get(f"/users/{uid}/summary", params={"date": "2026-10-14"}).json()
    assert s["workout_volume_kg"] == 3 * 8 * 60 + 12 * 15


def test_coach_bad_tool_input_is_reported_not_saved(client, user):
    uid = user["id"]
    fake = FakeGemini([
        _tool_call("log_food", {"items": [{"meal": "lunch", "description": "x",
                                            "calories": -50, "protein_g": 0,
                                            "carbs_g": 0, "fat_g": 0}]}),
        _text("Sorry, let me fix that."),
    ])
    use(fake)

    r = client.post(f"/users/{uid}/coach", json={"message": "lunch", "date": "2026-10-14"})
    assert r.status_code == 200
    assert r.json()["actions"] == []
    assert _tool_result(fake)["error"] == "Invalid input"   # the model was told what was wrong
    assert client.get(f"/users/{uid}/food").json() == []    # ...and nothing was saved


def test_tool_schemas_have_no_refs():
    from app.coach import TOOLS
    assert "$ref" not in json.dumps(TOOLS)


# --- When Google is overloaded ---------------------------------------------------

def _overloaded():
    return genai_errors.ServerError(503, {"error": {
        "code": 503, "message": "high demand", "status": "UNAVAILABLE"}})


def test_coach_retries_when_overloaded(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)  # don't actually wait
    fake = FakeGemini([_overloaded(), _overloaded(), _text("Hi!")])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Hi!"
    assert len(fake.calls) == 3


def test_coach_falls_back_to_lighter_model(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    fake = FakeGemini([_overloaded()] * 3 + [_text("From the backup model.")])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 200
    assert fake.calls[0]["model"] == coach_module.MODEL
    assert fake.calls[3]["model"] == coach_module.FALLBACK_MODEL


def test_coach_gives_friendly_error_when_everything_is_busy(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    use(FakeGemini([_overloaded()] * 6))
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 503
    assert "try again" in r.json()["detail"]


def test_coach_does_not_retry_a_bad_api_key(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    bad_key = genai_errors.ClientError(400, {"error": {
        "code": 400, "message": "API key not valid", "status": "INVALID_ARGUMENT"}})
    fake = FakeGemini([bad_key])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 502
    assert len(fake.calls) == 1   # gave up immediately, no pointless retries
