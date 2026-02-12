# Creator Bot - Backend Technical Reference

This directory contains the FastAPI-based backend and the custom Grounded-RAG infrastructure.

## 🧠 Core Intelligence Logic

### 1. Grounded-RAG Loop (`grounded_rag.py`)
Our retrieval-augmented generation pipeline uses a specialized multi-step process:
- **Intent Classification**: Detects if the user wants general information vs. a specific resource (Video, Article, Course).
- **Semantic Search**: Uses `pgvector` for vector similarity on approved knowledge.
- **Reranking**: Scores candidates based on relevance and presence of creator-specific phrasing.
- **Persona-Enforced Generation**: GPT-4o generates responses strictly following the "Human Voice" guidelines.

### 2. Creator Ownership Gate (COG) (`services/content_finder.py`)
This is a custom security layer that ensures brand safety:
- **Identity Matching**: Uses `youtube_channel_id` and `youtube_handle` to verify external content.
- **Strict Threholding**: Resource cards require a **Confidence Score >= 0.82**.
- **Relation Mapping**: Candidates are tagged as `SELF`, `AFFILIATED` (interviews/collabs), `OTHER` (competitor), or `UNKNOWN`.
- **Fallback Strategy**: If no specific video is found, the system returns a **Channel Search Card** allowing the user to search the creator's official channel directly.

### 3. Scraper Router (`scraper_router.py`)
- Distributes scraping tasks to Apify actors.
- Normalizes social media handles into canonical URLs.
- Handles asynchronous queueing of content for the Approval Gate.

## 🗄️ Database Schema highlights

- **`creators`**: Contains identity metadata and configuration (Style Fingerprints, Canonical Links).
- **`documents`**: Parent records for approved content items.
- **`chunks`**: Text segments (~800 chars) with parent document metadata.
- **`embeddings`**: High-dimensional vectors for semantic search via `pgvector`.

## 🛠️ Developer Commands

### Database Migrations
Custom migrations are often run via Python scripts in the `migrations/` folder or at the root (e.g., `migrate_cog.py`).
To check schema:
```powershell
python -c "from db import db; print(db.execute_query('SELECT * FROM information_schema.columns WHERE table_name = \'creators\''))"
```

### Local Development
```powershell
pip install -r requirements.txt
python -m uvicorn app:app --reload
```
