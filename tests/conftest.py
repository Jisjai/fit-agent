"""Test fixtures: every test gets a brand-new, empty in-memory database."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def db_session():
    # StaticPool keeps ONE connection alive, so the in-memory DB isn't lost
    # between the moment we create tables and the moment a request runs.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    # Swap the real get_db for one that hands out our test session.
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def user(client):
    r = client.post("/users", json={
        "name": "Test User", "height_cm": 180, "birth_year": 1998,
        "sex": "male", "goal": "maintain", "activity_level": "moderate",
    })
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def exercises(client):
    ids = {}
    for name, group in [("Bench Press", "chest"), ("Back Squat", "legs")]:
        r = client.post("/exercises", json={"name": name, "muscle_group": group})
        ids[name] = r.json()["id"]
    return ids
