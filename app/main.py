from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from . import auth
from .database import Base, SessionLocal, engine
from .routers import coach, food, summary, users, weight, workouts
from .seed_data import seed_exercises

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On startup: create tables if missing and make sure exercises exist.
    # (Later we'd replace create_all with proper migrations via Alembic.)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_exercises(db)
    yield


app = FastAPI(title="Fit Agent", version="0.3.0", lifespan=lifespan)

# Runs before every request: blocks the API unless logged in (when APP_PASSWORD is set).
app.middleware("http")(auth.require_login)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(weight.router)
app.include_router(food.router)
app.include_router(workouts.exercises_router)
app.include_router(workouts.router)
app.include_router(summary.router)
app.include_router(coach.router)


@app.get("/", include_in_schema=False)
def home():
    """Serve the web page. Everything else on this server is the JSON API it talks to."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
