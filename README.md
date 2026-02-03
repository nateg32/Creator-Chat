# Creator Bot Project Documentation

## 1. Project Overview
Project Name: Creator Bot
Objective: A system designed to build AI-driven chat-bots that mimic the persona and knowledge of specific content creators. The bot achieves this by scraping creator content across multiple social media platforms, indexing it via a RAG (Retrieval-Augmented Generation) pipeline, and generating a persona based on the ingested material.

High-Level Architecture:
- Frontend: React-based wizard for creator setup, content discovery, and chat.
- Backend: FastAPI service orchestrating scraping, database operations, and LLM interactions.
- Scraping: Integration with Apify actors for multi-platform data extraction.
- RAG: Vector-based search (pgvector) for retrieving relevant creator content for grounding LLM responses.

## 2. Tech Stack
- Frontend: React (Vite), Vanilla CSS, React-Stepper.
- Backend: Python 3, FastAPI, Uvicorn as the ASGI server.
- Database: PostgreSQL with pgvector extension.
- Scraping Engine: Apify SDK/API utilizing specialized actors:
  - Instagram: `apify/instagram-reel-scraper`
  - YouTube: `apidojo/youtube-scraper` + `tictechid/anoxvanzi-Transcriber`
  - LinkedIn: `apimaestro/linkedin-profile-posts`
  - TikTok: `clockworks/tiktok-scraper`
  - Twitter/X: `kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest`
  - Facebook: `apify/facebook-posts-scraper`
- Env Management: `python-dotenv` for backend, standard Vite env for frontend.
- AI/LLM: OpenAI API (or compatible) for embeddings and text generation.

## 3. Folder Structure
- `backend/`: Core service logic.
  - `app.py`: Main FastAPI entry point, endpoint definitions, and background task orchestration.
  - `apify_client.py`: Low-level Apify actor wrappers and response normalization.
  - `scraper_router.py`: Logic for selecting and routing scraping tasks based on creator configuration.
  - `models.py`: Pydantic schemas for request/response validation.
  - `db.py`: Database connection pool and execution helpers.
  - `ingest.py`: Document chunking and embedding logic.
  - `lib/`: Platform-specific parsers and transcription utilities.
- `frontend/anti-gravity/`: React application.
  - `src/api/client.js`: Centralized API communication layer.
  - `src/components/`: Modular UI components for search progress, previews, and chat.
  - `src/App.jsx`: State management for the multi-step wizard.
- `.env`: Universal configuration file (Root).
- `backend/error_log.txt` / `panic_log.txt`: Persistent backend error logs.

## 4. Backend Architecture
The backend is built with FastAPI and utilizes `BackgroundTasks` for non-blocking scraping operations. 

Request Flow:
1. Frontend initiates a search via `POST /search`.
2. Backend validates creator configuration, generates a `search_id` (UUID), and persists an initial "running" status in the `search_progress` table.
3. A background task (`_run_search_background`) is spawned to invoke `scraper_router.py`.
4. `scraper_router.py` calls platform-specific functions in `apify_client.py`.
5. Scraped items are normalized and saved to `scrape_items` (staging table) and indexed by `scrape_run_id`.
6. Frontend polls `GET /search/{search_id}/progress` to receive real-time status updates per platform.
7. Once complete, items are retrieved via `GET /search/{search_id}/items`.

Logging:
Detailed exception tracking is implemented in the background task runner, logging full tracebacks to `error_log.txt` and `panic_log.txt` to troubleshoot silent failures in asynchronous threads.

## 5. Frontend Architecture
The frontend is a single-page application (SPA) structured as a 5-step wizard:
1. Setup: Define creator handles and platform URLs.
2. Search: Initiate and monitor scraping progress.
3. Approve: Review staged items and select content for ingestion.
4. Persona: Edit and refine the generated creator persona.
5. Chat: Interact with the creator bot.

Communication:
- Managed via `src/api/client.js`.
- Uses `AbortController` for 30-second request timeouts.
- Implements cache-busting on polling requests using timestamp parameters (`_t=...`).

## 6. Environment Variables
Stored in `.env` (Root) and `backend/.env`:
- `APIFY_TOKEN`: Required for all scraping operations. **Must be consistent in both .env files.**
- `OPENAI_API_KEY`: Required for generating embeddings and chat responses.
- `DB_PASSWORD`: Password for the PostgreSQL instance.
- `DATABASE_URL`: Full connection string if not using discrete components.
- `TRANSCRIBE_ON_INGEST`: (Boolean) enables expensive video-to-text conversion during the ingestion phase.

## 7. Scraping Logic
URL Validation:
Platform handlers in `backend/config/platforms.py` validate and normalize input URLs (e.g., ensuring LinkedIn profiles are full URLs).

Router Decision:
The `run_search_router` checks the `enabled` flag and presence of a valid URL/handle for each platform before invoking the corresponding Apify actor.

Platform Specifics:
- Instagram: Uses a reels-only scraper. Normal posts are currently ignored.
- LinkedIn: Uses the `apimaestro/linkedin-profile-posts` actor. It requires the input to be passed via specific keys (`profileUrl`, `urls`, and `profileUrls`) as lists/strings depending on version. The system automatically normalizes handles like `dmartell` into full `linkedin.com/in/...` URLs to ensure accuracy.
- TikTok: Uses `clockworks` scraper; input is passed via a `profiles` list.

Failure Modes:
- "No items found": Typically caused by stale Apify actors, restricted profile privacy settings, or incorrect handle formatting.
- "Searching failed": Often indicates a backend exception (e.g., database schema mismatch) or an Apify budget/concurrency limit.

## 8. Debugging & Known Issues
- Port Conflicts: Defaults to port 8001 for the backend. If port 8000 is occupied by a "zombie" uvicorn process, verify and kill it via Task Manager or `netstat`.
- Windows `--reload`: The uvicorn `--reload` flag can occasionally leave ghost processes active on Windows. Manual restarts are recommended when editing core async handlers.
- Schema Mismatches: The database uses `scrape_run_id` as the foreign key in `scrape_items`. Historical code incorrectly used `search_run_id`, which was fixed to maintain referential integrity.
- 404 on Root: `GET /` on the backend is not a defined endpoint and returns 404 by design. Use `/health` or `/creators` to verify backend status.
- Pydantic 422 Errors: Occur if the frontend `scrape_id` does not match the backend `search_id` field in ingestion models. Backend `models.py` now supports both as aliases.
- Dual .env Confusion: If API tokens work in one place but fail in another, verify that BOTH the root `.env` and `backend/.env` have the correct keys. The backend service prioritized `backend/.env`.
- Satya Nadella Default: In the `apimaestro/linkedin-profile-posts` scraper, if the input key is unrecognized, it defaults to Satya Nadella. The correct JSON field name is `username`. The system now uses this key specifically to ensure the correct creator is fetched.

## 9. How to Run the Project
Backend:
1. Ensure PostgreSQL is running.
2. Navigate to project root.
3. Execute: `python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001`

Frontend:
1. Navigate to `frontend/anti-gravity/`.
2. Execute: `npm run dev`
3. Open `http://localhost:5173` in a browser.

Verification:
- Backend Health: `http://127.0.0.1:8001/health`
- Frontend Config: Verify `API_BASE_URL` in `src/config.js` is set to `http://127.0.0.1:8001`.

## 10. Development Notes & Best Practices
- Backend Port: 8001 is the standard for this project to avoid conflicts with services often pre-bound to 8000 (like AirPlay or other dev servers).
- Scraper Cost: Avoid high `maxItems` values during testing to preserve Apify usage credits.
- Database Schema: Always check `models.py` against the PostgreSQL DDL if adding new metadata fields to `scrape_items`.
- Asynchronous Consistency: `_set_search_progress` updates both the in-memory cache and the database `search_progress` table to ensure polling reliability.
