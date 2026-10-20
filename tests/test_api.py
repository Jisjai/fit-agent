from conftest import sign_in
from sqlalchemy import func, select

from app.models import Workout, WorkoutSet

DAY = "2026-10-14"


def food(**overrides):
    entry = {"date": DAY, "meal": "lunch", "description": "chicken and rice",
             "calories": 600, "protein_g": 45, "carbs_g": 70, "fat_g": 12}
    entry.update(overrides)
    return entry


# 1. Summary totals add up
def test_summary_totals(client, user):
    uid = user["id"]
    client.post(f"/users/{uid}/food", json=food())
    client.post(f"/users/{uid}/food", json=food(meal="snack", calories=200,
                                                protein_g=20, carbs_g=10, fat_g=8))
    client.post(f"/users/{uid}/food", json=food(date="2026-10-13", calories=9999))  # other day

    r = client.get(f"/users/{uid}/summary", params={"date": DAY})
    assert r.status_code == 200
    s = r.json()
    assert s["calories"] == 800
    assert s["protein_g"] == 65
    assert s["food_entries"] == 2


# 2. Same-day weigh-in replaces the first one
def test_same_day_weigh_in_replaces(client, user):
    uid = user["id"]
    first = client.post(f"/users/{uid}/weigh-ins", json={"date": DAY, "weight_kg": 80.0})
    second = client.post(f"/users/{uid}/weigh-ins", json={"date": DAY, "weight_kg": 79.4})
    assert first.status_code == 201
    assert second.status_code == 200

    all_ = client.get(f"/users/{uid}/weigh-ins").json()
    assert len(all_) == 1
    assert all_[0]["weight_kg"] == 79.4


# 3. Workout comes back with nested sets and exercise names
def test_workout_nested_sets(client, user, exercises):
    uid = user["id"]
    r = client.post(f"/users/{uid}/workouts", json={
        "date": DAY,
        "sets": [
            {"exercise_id": exercises["Bench Press"], "reps": 8, "weight_kg": 60},
            {"exercise_id": exercises["Bench Press"], "reps": 6, "weight_kg": 65},
            {"exercise_id": exercises["Back Squat"], "reps": 5, "weight_kg": 100},
        ],
    })
    assert r.status_code == 201
    w = r.json()
    assert [s["set_number"] for s in w["sets"]] == [1, 2, 3]
    assert w["sets"][2]["exercise_name"] == "Back Squat"
    assert w["total_volume_kg"] == 8 * 60 + 6 * 65 + 5 * 100

    fetched = client.get(f"/users/{uid}/workouts/{w['id']}").json()
    assert fetched == w


# 4. Bad exercise id saves nothing at all
def test_bad_exercise_saves_nothing(client, db_session, user, exercises):
    uid = user["id"]
    r = client.post(f"/users/{uid}/workouts", json={
        "date": DAY,
        "sets": [
            {"exercise_id": exercises["Bench Press"], "reps": 8, "weight_kg": 60},
            {"exercise_id": 9999, "reps": 8, "weight_kg": 60},
        ],
    })
    assert r.status_code == 400
    assert db_session.scalar(select(func.count(Workout.id))) == 0
    assert db_session.scalar(select(func.count(WorkoutSet.id))) == 0


# 5. Invalid values are rejected by validation (422)
def test_validation_errors(client, user, exercises):
    uid = user["id"]
    assert client.post(f"/users/{uid}/food", json=food(calories=-100)).status_code == 422
    assert client.post(f"/users/{uid}/weigh-ins",
                       json={"date": DAY, "weight_kg": 900}).status_code == 422
    assert client.post(f"/users/{uid}/workouts", json={
        "date": DAY, "sets": [{"exercise_id": exercises["Bench Press"], "reps": 0, "weight_kg": 60}],
    }).status_code == 422
    assert client.post(f"/users/{uid}/workouts",
                       json={"date": DAY, "sets": []}).status_code == 422


# 6. Users can't see or touch each other's data
def test_user_isolation(client, user):
    a = user["id"]
    entry_id = client.post(f"/users/{a}/food", json=food()).json()["id"]

    # Someone else signs in on the same server and makes their own profile.
    sign_in(client, sub="google-user-2", email="two@example.com")
    b = client.post("/users", json={
        "name": "Other", "height_cm": 165, "birth_year": 2000,
        "sex": "female", "goal": "lose", "activity_level": "light",
    }).json()["id"]

    # They only see their own profile...
    assert [u["id"] for u in client.get("/users").json()] == [b]
    # ...and A's data is invisible to them, exactly like a profile that doesn't exist.
    assert client.get(f"/users/{a}").status_code == 404
    assert client.get(f"/users/{a}/food").status_code == 404
    assert client.post(f"/users/{a}/food", json=food()).status_code == 404
    assert client.delete(f"/users/{a}/food/{entry_id}").status_code == 404
    # Deleting A's entry through B's own profile doesn't work either.
    assert client.delete(f"/users/{b}/food/{entry_id}").status_code == 404

    sign_in(client)  # back to A: the entry is untouched
    assert len(client.get(f"/users/{a}/food").json()) == 1


def test_one_profile_per_account(client, user):
    r = client.post("/users", json={
        "name": "Second", "height_cm": 170, "birth_year": 1990,
        "sex": "male", "goal": "gain", "activity_level": "active",
    })
    assert r.status_code == 409


# Extras
def test_missing_user_404(client):
    assert client.get("/users/123/food").status_code == 404


def test_duplicate_exercise_409(client, exercises):
    r = client.post("/exercises", json={"name": "Bench Press", "muscle_group": "chest"})
    assert r.status_code == 409


def test_summary_uses_latest_earlier_weight(client, user):
    uid = user["id"]
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-10", "weight_kg": 81})
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-12", "weight_kg": 80.5})
    client.post(f"/users/{uid}/weigh-ins", json={"date": "2026-10-20", "weight_kg": 79})  # future
    s = client.get(f"/users/{uid}/summary", params={"date": DAY}).json()
    assert s["latest_weight_kg"] == 80.5
    assert s["calories"] == 0  # no food logged, coalesce gives 0 not null


def test_delete_workout(client, user, exercises):
    uid = user["id"]
    wid = client.post(f"/users/{uid}/workouts", json={
        "date": DAY, "sets": [{"exercise_id": exercises["Bench Press"], "reps": 8, "weight_kg": 60}],
    }).json()["id"]

    assert client.delete(f"/users/{uid}/workouts/{wid}").status_code == 204
    assert client.get(f"/users/{uid}/workouts", params={"start": DAY, "end": DAY}).json() == []
    assert client.delete(f"/users/{uid}/workouts/{wid}").status_code == 404   # already gone


def test_cannot_delete_someone_elses_workout(client, user, exercises):
    wid = client.post(f"/users/{user['id']}/workouts", json={
        "date": DAY, "sets": [{"exercise_id": exercises["Bench Press"], "reps": 8, "weight_kg": 60}],
    }).json()["id"]

    sign_in(client, sub="google-user-2", email="two@example.com")
    b = client.post("/users", json={
        "name": "Other", "height_cm": 165, "birth_year": 2000,
        "sex": "female", "goal": "lose", "activity_level": "light",
    }).json()["id"]
    assert client.delete(f"/users/{b}/workouts/{wid}").status_code == 404

    sign_in(client)
    assert len(client.get(f"/users/{user['id']}/workouts",
                          params={"start": DAY, "end": DAY}).json()) == 1
