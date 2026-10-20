from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import auth
from .database import Base, SessionLocal, add_missing_columns, engine
from .routers import coach, food, push, summary, users, weight, workouts
from .seed_data import seed_exercises

STATIC_DIR = Path(__file__).parent / "static"
ICON_DIR = STATIC_DIR / "icons"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On startup: create tables if missing and make sure exercises exist.
    # (Later we'd replace create_all with proper migrations via Alembic.)
    Base.metadata.create_all(bind=engine)
    add_missing_columns()
    with SessionLocal() as db:
        seed_exercises(db)
    yield


app = FastAPI(title="Fit Agent", version="0.5.0", lifespan=lifespan)

# Runs before every request: blocks the API unless signed in with Google.
app.middleware("http")(auth.require_login)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(weight.router)
app.include_router(food.router)
app.include_router(workouts.exercises_router)
app.include_router(workouts.router)
app.include_router(summary.router)
app.include_router(coach.router)
app.include_router(push.router)


@app.get("/", include_in_schema=False)
def home():
    """Serve the web page. Everything else on this server is the JSON API it talks to."""
    return FileResponse(STATIC_DIR / "index.html")


# --- Files that make it installable as a phone app ----------------------------

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest",
                        media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # Must be served from the root path so it controls the whole app,
    # and never cached, so phones pick up new versions right away.
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/icons/{name}", include_in_schema=False)
def icons(name: str):
    path = (ICON_DIR / name).resolve()
    # Only serve real files from the icons folder: blocks tricks like "../secrets".
    if path.parent != ICON_DIR.resolve() or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=604800"})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(ICON_DIR / "favicon-32.png")


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
