# Creator Chat

Creator Chat is a self-hosted app for turning public creator content into personalized, source-aware AI chat experiences.

It gives you a full workflow: add a creator, search public content, review and approve the useful signal, build a creator profile, personalize the user experience, and chat with context grounded in the approved corpus.

Creator Chat is not affiliated with, endorsed by, or officially connected to any creator whose public content you ingest. Use it only with content you are allowed to process, and review each source before it becomes memory.

## Features

- Multi-creator workspace with separate chat threads.
- Public content collection through Apify-backed platform scrapers.
- Review gate so discovered content is approved before ingestion.
- Source-backed answers with links to the material used.
- Creator persona generation from approved content.
- Creator customization for profile, retrieval mode, colors, and identity.
- User personalization for name, photo, response style, and background context.
- Strict RAG or content-plus-web retrieval modes.
- PostgreSQL and pgvector storage for searchable knowledge.
- Optional background workers for scraping, transcripts, ingestion, and profile jobs.
- Open-source deployment helpers for Render and Vercel.

## Stack

- Backend: Python, FastAPI, Pydantic, psycopg 3.
- Frontend: React, Vite, plain CSS.
- Database: PostgreSQL with `pgvector`.
- AI/providers: Gemini for chat and analysis; OpenAI-compatible APIs can be used for embeddings or transcription paths; Apify is used for social scraping.
- Optional services: Redis-compatible cache, AssemblyAI, Brave Search, Exa.

## Prerequisites

- Python 3.11+
- Node.js 20.19+ or 22.12+
- PostgreSQL 12+ with `pgvector`
- API keys for the providers you enable:
  - `GEMINI_API_KEY` for creator chat and analysis.
  - `APIFY_TOKEN` for scraping.
  - `OPENAI_API_KEY` or `EMBEDDING_API_KEY` for embeddings.
  - Optional: `ASSEMBLYAI_API_KEY`, `BRAVE_SEARCH_API_KEY`, `EXA_API_KEY`.

## Backend Setup

Create and activate a virtual environment, then install dependencies:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `backend/.env` from `backend/env.example` and fill in the required values:

```powershell
Copy-Item env.example .env
```

At minimum, configure database access, `JWT_SECRET_KEY`, `GEMINI_API_KEY`, `APIFY_TOKEN`, and an embedding key.

For local cookies, `backend/env.example` uses `COOKIE_SECURE=false` and `COOKIE_SAMESITE=lax`. Use secure cookie settings for HTTPS deployments.

Create the database and enable pgvector:

```sql
CREATE DATABASE creator_chat;
\c creator_chat
CREATE EXTENSION IF NOT EXISTS vector;
```

Apply SQL migrations from the repository root:

```powershell
Get-ChildItem backend/migrations/*.sql | Sort-Object Name | ForEach-Object { python run_migration.py $_.Name }
```

Fresh installs do not create a default account during migration. Create the first user through the app or `POST /auth/register` after the backend is running.

Run the backend:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

Optional worker process:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m backend.services.system_worker
```

## Frontend Setup

```powershell
cd frontend/creator-chat
npm install
Copy-Item .env.example .env
npm run dev
```

The frontend defaults to `http://127.0.0.1:8000`. Set `VITE_API_BASE_URL` in `frontend/creator-chat/.env` when the backend runs elsewhere.

## Key Environment Variables

- `DATABASE_URL` or `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- `JWT_SECRET_KEY`
- `GEMINI_API_KEY`
- `APIFY_TOKEN`
- `OPENAI_API_KEY` or `EMBEDDING_API_KEY`
- `TRANSCRIPTION_API_KEY` or `OPENAI_API_KEY`
- `ASSEMBLYAI_API_KEY`
- `SEARCH_API_KEY`, `BRAVE_SEARCH_API_KEY`, or `EXA_API_KEY`
- `THREAD_CONTEXT_CACHE_REDIS_URL`
- `CORS_ORIGINS`, `FRONTEND_URL`, or `FRONTEND_URLS`
- `SCRAPE_MAX_PLATFORMS_PER_SEARCH`
- `SCRAPE_MAX_ITEMS_PER_PLATFORM`
- `SCRAPE_MAX_ITEMS_PER_SEARCH`
- `SCRAPE_MAX_CREATORS`
- `SCRAPE_MONTHLY_ITEM_ALLOWANCE`

See `backend/env.example` and `frontend/creator-chat/.env.example` for the full list.

## Deployment

`render.yaml` is a starter Render Blueprint with secrets marked `sync: false`.

If you use `scripts/redeploy-live.ps1`, provide Render service IDs through environment variables or flags instead of committing them:

```powershell
$env:RENDER_BACKEND_SERVICE_ID = "srv-your-backend"
$env:RENDER_WORKER_SERVICE_ID = "srv-your-worker"
.\scripts\redeploy-live.ps1 -Backend -IncludeWorker
```

The frontend lives in `frontend/creator-chat`.

The `frontend/anti-gravity` folder is a small compatibility wrapper for an existing Vercel project whose root directory was already set to that path. New deployments can point directly at `frontend/creator-chat` and ignore the compatibility wrapper.

## Checks

Backend tests:

```powershell
python -m pytest backend/tests
```

Frontend lint and build:

```powershell
cd frontend/creator-chat
npm run lint
npm run build
```

Open-source readiness checks:

```powershell
python -m pytest backend/tests/test_open_source_readiness.py
```

## Safety And Responsible Use

- Do not commit `.env`, API keys, database URLs, local caches, or generated logs.
- Rotate any secrets that were ever committed before publishing a repository.
- Set a strong `JWT_SECRET_KEY` for every deployed environment.
- Keep `COOKIE_SECURE=true` when serving over HTTPS.
- Review provider logs and scraped data before publishing demo databases or fixtures.
- Respect platform terms and creator rights. Creator Chat is designed for public content workflows, but you are responsible for how you collect and use content.
- Generated replies are AI outputs based on approved public sources and configuration. They should not be presented as official creator statements.

## Contributing

Contributions are welcome. Read `CONTRIBUTING.md` before opening a pull request.

For vulnerabilities or sensitive issues, read `SECURITY.md` and avoid posting exploit details publicly.

## License

Creator Chat is licensed under the MIT License. See `LICENSE`.
