"""Database setup: engine, session factory, Base class, and the get_db dependency.

Locally the app uses a SQLite file. Online (Vercel) it uses Postgres: set the
DATABASE_URL environment variable and nothing else in the code changes.
That's the payoff of going through SQLAlchemy instead of writing SQL by hand.
"""
import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def _database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "sqlite:///./fit_agent.db"
    # Hosts hand out "postgres://..." URLs; SQLAlchemy needs to be told which driver
    # to use, so we rewrite them to the psycopg (v3) driver.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

if IS_SQLITE:
    # check_same_thread=False: FastAPI may handle a request on a different thread
    # than the one that opened the connection. SQLite refuses that by default.
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _record):
        # SQLite ignores foreign keys unless you switch them on per connection.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # pool_pre_ping: check a pooled connection is still alive before using it.
    # Serverless databases close idle connections, so this avoids random errors.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=3, max_overflow=2)


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """All ORM models inherit from this, so Base.metadata knows every table."""


def get_db():
    """FastAPI dependency: one session per request, always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
