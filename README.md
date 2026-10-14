# Fit Agent

Tell the coach what you ate, weighed or trained, and it logs it for you and coaches you
on your own data. Python backend (FastAPI + SQLAlchemy) with a single web page as the
front end. The coach runs on Google Gemini.

## Run it on your laptop

You need Python 3.11 or newer. In a terminal, inside this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Put your Gemini API key (from https://aistudio.google.com/apikey) in a `.env` file:

```bash
echo 'GEMINI_API_KEY=paste-your-key-here' > .env
```

Start the app:

```bash
uvicorn app.main:app --reload --env-file .env
```

Open **http://127.0.0.1:8000**. **http://127.0.0.1:8000/docs** lists every API endpoint.
Locally your data is stored in `fit_agent.db`; delete that file to start over.

## Settings (environment variables)

| Variable | What it does |
|---|---|
| `GEMINI_API_KEY` | Needed for the coach. Everything else works without it. |
| `DATABASE_URL` | Postgres connection URL. If not set, a local SQLite file is used. |
| `APP_PASSWORD` | If set, the app asks for this password before showing anything. |
| `COACH_MODEL` | Optional. Gemini model to use (default `gemini-flash-latest`). |

## Online (Vercel)

Vercel runs `app/main.py` automatically. The project needs a Postgres database
(Neon, from Vercel's Storage tab, which sets `DATABASE_URL`) plus `GEMINI_API_KEY`
and `APP_PASSWORD` under Project Settings → Environment Variables.

## Tests

```bash
pytest -q
```

They use a temporary in-memory database and a fake Gemini, so they're free and fast.

## How the code is organized

| File | Job |
|---|---|
| `app/main.py` | Creates the app, plugs in the routers, serves the web page |
| `app/database.py` | Database connection (SQLite locally, Postgres online) and `get_db` |
| `app/auth.py` | Password gate: login cookie checked before every API request |
| `app/models.py` | Database tables |
| `app/schemas.py` | What the API accepts and returns, with validation |
| `app/deps.py` | `get_user`: shared "does this user exist?" check |
| `app/services.py` | Shared logic: logging, summaries, weight trend, calorie targets with safety floors |
| `app/coach.py` | The AI agent: its instructions, the tools it can call, and the agent loop |
| `app/routers/` | The API endpoints, one file per area |
| `app/static/index.html` | The web page |
| `app/seed_data.py` | Starter list of exercises, added on startup |
| `tests/` | Automated tests |
