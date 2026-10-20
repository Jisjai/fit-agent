import json
from types import SimpleNamespace as NS

import anthropic
import httpx

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


# --- Coach (with a fake Claude client, so tests never call the real API) ------

class FakeClaude:
    """Pretends to be anthropic.Anthropic(): returns pre-scripted responses in order."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    @property
    def messages(self):  # so fake.messages.create(...) works like the real SDK
        return self

    def create(self, **kwargs):
        # Copy the list: the coach keeps appending to the same one between calls.
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        response = self.responses.pop(0)
        if isinstance(response, Exception):  # lets a test simulate API errors
            raise response
        return response


def _tool_call(name, args):
    block = NS(type="tool_use", id="toolu_1", name=name, input=args)
    return NS(content=[block], stop_reason="tool_use")


def _text(t):
    return NS(content=[NS(type="text", text=t)], stop_reason="end_turn")


def _tool_result(fake, call_index=1):
    """What our code sent back to the model as the first tool result."""
    tool_turn = fake.calls[call_index]["messages"][-1]
    assert tool_turn["role"] == "user"
    first = tool_turn["content"][0]
    assert first["type"] == "tool_result"
    assert first["tool_use_id"] == "toolu_1"   # linked to the request it answers
    return json.loads(first["content"])


def use(fake):
    app.dependency_overrides[get_ai_client] = lambda: fake


def test_coach_runs_tool_then_answers(client, user):
    uid = user["id"]
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-14", "weight_kg": 80})
    fake = FakeClaude([_tool_call("get_targets", {}), _text("Aim for about 2,700 kcal.")])
    use(fake)

    r = client.post(f"/users/{uid}/coach",
                    json={"message": "How much should I eat?", "date": "2026-10-14"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Aim for about 2,700 kcal."
    assert _tool_result(fake)["available"] is True


def test_coach_without_api_key_returns_503(client, user, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 503


def test_chat_history_is_passed_to_the_model(client, user):
    fake = FakeClaude([_text("ok")])
    use(fake)
    client.post(f"/users/{user['id']}/coach", json={
        "message": "and now?",
        "history": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
    })
    roles = [m["role"] for m in fake.calls[0]["messages"]]
    assert roles == ["user", "assistant", "user"]


# --- Coach logging -------------------------------------------------------------

def test_coach_logs_food_from_chat(client, user):
    uid = user["id"]
    use(FakeClaude([
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
    use(FakeClaude([
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
    fake = FakeClaude([
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


# --- When Anthropic is overloaded ------------------------------------------------

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _overloaded():
    return anthropic.InternalServerError(
        "Overloaded", response=httpx.Response(529, request=_REQUEST),
        body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})


def test_coach_retries_when_overloaded(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)  # don't actually wait
    fake = FakeClaude([_overloaded(), _overloaded(), _text("Hi!")])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 200
    assert r.json()["reply"] == "Hi!"
    assert len(fake.calls) == 3


def test_coach_falls_back_to_lighter_model(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    fake = FakeClaude([_overloaded()] * 3 + [_text("From the backup model.")])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 200
    assert fake.calls[0]["model"] == coach_module.MODEL
    assert fake.calls[3]["model"] == coach_module.FALLBACK_MODEL


def test_coach_gives_friendly_error_when_everything_is_busy(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    use(FakeClaude([_overloaded()] * 6))
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 503
    assert "try again" in r.json()["detail"]


def test_coach_does_not_retry_a_bad_api_key(client, user, monkeypatch):
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    bad_key = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=_REQUEST),
        body={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    fake = FakeClaude([bad_key])
    use(fake)
    r = client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert r.status_code == 502
    assert len(fake.calls) == 1   # gave up immediately, no pointless retries


# --- Daily coach limit -----------------------------------------------------------

def test_coach_daily_limit(client, user, monkeypatch):
    monkeypatch.setenv("COACH_DAILY_LIMIT", "2")
    fake = FakeClaude([_text("one"), _text("two")])
    use(fake)
    uid = user["id"]

    first = client.post(f"/users/{uid}/coach", json={"message": "hi"})
    assert first.json()["messages_left"] == 1
    assert client.post(f"/users/{uid}/coach", json={"message": "hi"}).json()["messages_left"] == 0

    third = client.post(f"/users/{uid}/coach", json={"message": "hi"})
    assert third.status_code == 429
    assert "reset" in third.json()["detail"]
    assert len(fake.calls) == 2   # the third message never reached the (paid) API


def test_failed_coach_calls_dont_use_up_the_limit(client, user, monkeypatch):
    monkeypatch.setenv("COACH_DAILY_LIMIT", "1")
    monkeypatch.setattr(coach_module.time, "sleep", lambda s: None)
    use(FakeClaude([_overloaded()] * 6 + [_text("finally")]))
    uid = user["id"]
    assert client.post(f"/users/{uid}/coach", json={"message": "hi"}).status_code == 503
    assert client.post(f"/users/{uid}/coach", json={"message": "hi"}).status_code == 200


def test_coach_limit_is_per_user(client, user, monkeypatch):
    from conftest import sign_in
    monkeypatch.setenv("COACH_DAILY_LIMIT", "1")
    use(FakeClaude([_text("a"), _text("b")]))
    assert client.post(f"/users/{user['id']}/coach", json={"message": "hi"}).status_code == 200

    sign_in(client, sub="google-user-2", email="two@example.com")
    other = client.post("/users", json={
        "name": "Other", "height_cm": 165, "birth_year": 2000,
        "sex": "female", "goal": "lose", "activity_level": "light",
    }).json()["id"]
    assert client.post(f"/users/{other}/coach", json={"message": "hi"}).status_code == 200


# --- Fixing mistakes across days ---------------------------------------------------

def test_coach_can_find_and_delete_a_duplicate_workout(client, user, exercises):
    uid = user["id"]
    for day in ["2026-10-19", "2026-10-20"]:   # the same session logged twice
        client.post(f"/users/{uid}/workouts", json={
            "date": day, "sets": [{"exercise_id": exercises["Bench Press"], "reps": 8, "weight_kg": 60}],
        })
    dup_id = client.get(f"/users/{uid}/workouts",
                        params={"start": "2026-10-20", "end": "2026-10-20"}).json()[0]["id"]

    fake = FakeClaude([
        _tool_call("get_workouts", {"start": "2026-10-19", "end": "2026-10-20"}),
        _tool_call("delete_workout", {"workout_id": dup_id}),
        _text("Removed the duplicate."),
    ])
    use(fake)
    r = client.post(f"/users/{uid}/coach", json={"message": "remove the duplicate workout",
                                                  "date": "2026-10-20"})
    assert r.status_code == 200
    assert all("id" in w for w in _tool_result(fake, 1))      # ids are visible to the coach
    assert r.json()["actions"] == ["Removed · workout"]
    left = client.get(f"/users/{uid}/workouts", params={"start": "2026-10-19", "end": "2026-10-20"})
    assert [w["date"] for w in left.json()] == ["2026-10-19"]   # the original survives


def test_actions_name_the_day_when_its_not_the_one_on_screen(client, user):
    use(FakeClaude([
        _tool_call("log_food", {"items": [
            {"meal": "dinner", "description": "Pasta", "calories": 600,
             "protein_g": 20, "carbs_g": 90, "fat_g": 15, "date": "2026-10-19"}]}),
        _text("Logged yesterday's dinner."),
    ]))
    r = client.post(f"/users/{user['id']}/coach",
                    json={"message": "yesterday I had pasta", "date": "2026-10-20"})
    assert r.json()["actions"] == ["Food · Pasta · 600 kcal · Mon 19 Oct"]


def test_coach_is_told_not_to_relog(client, user):
    fake = FakeClaude([_text("ok")])
    use(fake)
    client.post(f"/users/{user['id']}/coach", json={"message": "hi"})
    assert "NEVER log something a second time" in fake.calls[0]["system"]
