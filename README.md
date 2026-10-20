# Fit Agent

Tell the coach what you ate, weighed or trained, and it logs it for you and coaches you
on your own data. Python backend (FastAPI + SQLAlchemy) with a single web page as the
front end. The coach runs on Claude (Anthropic API). Users sign in with Google, and
each account only ever sees its own data.

## Run it on your laptop

You need Python 3.11 or newer. In a terminal, inside this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file with your keys:

```bash
echo 'ANTHROPIC_API_KEY=paste-your-key-here' > .env
echo 'GOOGLE_CLIENT_ID=paste-your-client-id.apps.googleusercontent.com' >> .env
echo "SESSION_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> .env
```

- Anthropic key: https://console.anthropic.com → API Keys (Default Workspace).
- Google client ID: Google Cloud Console → APIs & Services → Credentials →
  Create credentials → OAuth client ID → Web application. Under **Authorized JavaScript
  origins** add `http://localhost:8000` and your Vercel URL(s). It's public, not a secret.
- `SESSION_SECRET` signs the login cookie. Changing it signs everyone out.

Start the app:

```bash
uvicorn app.main:app --reload --env-file .env
```

Open **http://localhost:8000** (Google sign-in needs `localhost`, not `127.0.0.1`).
**http://localhost:8000/docs** lists every API endpoint.
Locally your data is stored in `fit_agent.db`; delete that file to start over.

## Settings (environment variables)

| Variable | What it does |
|---|---|
| `ANTHROPIC_API_KEY` | Needed for the coach. Everything else works without it. |
| `DATABASE_URL` | Postgres connection URL. If not set, a local SQLite file is used. |
| `GOOGLE_CLIENT_ID` | Needed for sign-in. The OAuth client ID from Google Cloud. |
| `SESSION_SECRET` | Needed for sign-in. A long random string that signs the login cookie. |
| `COACH_DAILY_LIMIT` | Optional. Coach messages per user per day (default 20). |
| `COACH_MODEL` | Optional. Claude model to use (default `claude-sonnet-5-5`). |
| `COACH_FALLBACK_MODEL` | Optional. Used when the main model is overloaded (default `claude-haiku-5-5`). |

## Online (Vercel)

Vercel runs `app/main.py` automatically. The project needs a Postgres database
(Neon, from Vercel's Storage tab, which sets `DATABASE_URL`) plus `ANTHROPIC_API_KEY`,
`GOOGLE_CLIENT_ID` and `SESSION_SECRET` under Project Settings → Environment Variables.
Google doesn't accept wildcard origins, so each Vercel URL you sign in on (production,
and any preview you want to test) must be listed in the OAuth client's JavaScript origins.

## Tests

```bash
pytest -q
```

They use a temporary in-memory database and a fake Claude client, so they're free and fast.

## How the code is organized

| File | Job |
|---|---|
| `app/main.py` | Creates the app, plugs in the routers, serves the web page |
| `app/database.py` | Database connection (SQLite locally, Postgres online) and `get_db` |
| `app/auth.py` | Sign in with Google, and the signed session cookie checked before every API request |
| `app/models.py` | Database tables |
| `app/schemas.py` | What the API accepts and returns, with validation |
| `app/deps.py` | `get_user`: shared "is this profile yours?" check |
| `app/services.py` | Shared logic: logging, summaries, weight trend, calorie targets with safety floors |
| `app/coach.py` | The AI agent: its instructions, the tools it can call, and the agent loop |
| `app/routers/` | The API endpoints, one file per area |
| `app/static/index.html` | The web page |
| `app/seed_data.py` | Starter list of exercises, added on startup |
| `tests/` | Automated tests |
