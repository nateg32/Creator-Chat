# Creator Bot Builder

A full-stack application for building AI-powered creator bots with RAG (Retrieval-Augmented Generation). Users can scrape content from multiple sources, approve items for ingestion, and chat with creator bots that maintain consistent personas.

## Architecture

- **Backend**: FastAPI (Python) with Postgres + pgvector for vector search
- **Frontend**: React + Vite with clean Apple-style UI
- **AI**: OpenAI (text-embedding-3-small for embeddings, gpt-4o-mini for chat)
- **Scraping**: Apify integration (with mock fallback for development)

## Prerequisites

- Python 3.9+
- Node.js 18+
- PostgreSQL 12+ with pgvector extension
- OpenAI API key
- (Optional) Apify token for real scraping

## Database Setup

1. Ensure PostgreSQL is running with pgvector installed:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

2. Your existing schema should have these tables:
   - `documents` (id, creator_id, title, content, source, source_id, metadata JSONB, created_at)
   - `chunks` (id, document_id, chunk_index, content)
   - `embeddings` (chunk_id UNIQUE, model, embedding vector, created_at)

3. Ensure the database is accessible on port 5433 (or update `DB_PORT` in backend/settings.py)

## Backend Setup

1. Navigate to the project root:
```bash
cd "C:\Users\Nathan\Downloads\anti-gravity"
```

2. Create a virtual environment (recommended):
```bash
python -m venv venv
# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate
```

3. Install Python dependencies:
```bash
cd backend
pip install -r requirements.txt
```

4. Set environment variables:
```bash
# Windows PowerShell
$env:OPENAI_API_KEY="your-openai-api-key"
$env:APIFY_TOKEN="your-apify-token"  # Optional
$env:DB_HOST="localhost"
$env:DB_PORT="5433"
$env:DB_NAME="creator_bot"
$env:DB_USER="postgres"
$env:DB_PASSWORD="your-password"

# Windows CMD
set OPENAI_API_KEY=your-openai-api-key
set APIFY_TOKEN=your-apify-token
set DB_HOST=localhost
set DB_PORT=5433
set DB_NAME=creator_bot
set DB_USER=postgres
set DB_PASSWORD=your-password

# Mac/Linux
export OPENAI_API_KEY="your-openai-api-key"
export APIFY_TOKEN="your-apify-token"
export DB_HOST="localhost"
export DB_PORT="5433"
export DB_NAME="creator_bot"
export DB_USER="postgres"
export DB_PASSWORD="your-password"
```

Or create a `.env` file in the backend folder (requires python-dotenv):
```
OPENAI_API_KEY=your-openai-api-key
APIFY_TOKEN=your-apify-token
DB_HOST=localhost
DB_PORT=5433
DB_NAME=creator_bot
DB_USER=postgres
DB_PASSWORD=your-password
```

5. Run the backend server:
```bash
# From the backend directory
uvicorn app:app --reload --port 8000

# Or from project root
uvicorn backend.app:app --reload --port 8000
```

The API will be available at `http://127.0.0.1:8000`

## Frontend Setup

1. Navigate to the project root:
```bash
cd "C:\Users\Nathan\Downloads\anti-gravity"
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

The frontend will be available at `http://localhost:5173` (or the port shown in terminal)

## API Endpoints

### GET /health
Health check endpoint.
```json
{"ok": true}
```

### POST /ask
Ask a question to a creator bot.
```json
{
  "creator_id": 1,
  "question": "What are your favorite projects?",
  "top_k": 5,
  "max_distance": 1.15
}
```

### POST /scrape
Scrape content from multiple sources.
```json
{
  "creator_handle": "mrbeast",
  "sources": ["youtube", "twitter", "reddit"],
  "limit": 10
}
```

### POST /approve_ingest
Ingest multiple approved items.
```json
{
  "creator_id": 1,
  "approvals": [
    {
      "source": "youtube",
      "source_id": "yt_001",
      "title": "Video Title",
      "content": "Full content...",
      "url": "https://..."
    }
  ]
}
```

### POST /ingest
Ingest a single document (for manual ingestion or persona).
```json
{
  "creator_id": 1,
  "title": "Persona Document",
  "content": "You are a helpful creator...",
  "source": "manual",
  "source_id": "persona_001",
  "doc_type": "persona"
}
```

### GET /creator/{creator_id}/persona
Get persona document for a creator.

## Usage Flow

1. **Setup Creator**: Enter a creator handle (e.g., "mrbeast") and select sources (YouTube, Twitter, Reddit)
2. **Scrape**: Click "Scrape" to fetch content from selected sources
3. **Approve**: Review scraped items, select which ones to include, then click "Ingest Selected"
4. **Chat**: Once content is ingested, chat with the creator bot using the chat interface
5. **Debug**: Enable "Debug Sources" to see retrieved chunk IDs and distances

## Features

- **Multi-source scraping**: YouTube, Twitter/X, Reddit (with mock fallback)
- **Approval workflow**: Review and approve content before ingestion
- **RAG-powered chat**: Answers based on ingested content with persona support
- **Vector search**: pgvector for semantic similarity search
- **Persona support**: Separate persona documents that guide bot behavior
- **Multi-creator**: Each creator has their own persona and knowledge base

## Development Notes

- Mock scraping works without Apify token for end-to-end testing
- Backend uses OpenAI SDK v1.x (client-based)
- Frontend uses fetch API with proper error handling
- All API calls handle network errors gracefully
- UI is responsive and stacks on small screens

## Troubleshooting

**Backend won't start:**
- Check PostgreSQL is running and accessible
- Verify environment variables are set correctly
- Ensure pgvector extension is installed: `CREATE EXTENSION vector;`

**Frontend can't connect to backend:**
- Verify backend is running on port 8000
- Check CORS settings in `backend/app.py`
- Ensure API_BASE_URL in `src/config.js` matches backend URL

**Scraping returns empty:**
- Without Apify token, mock data is returned (this is expected)
- With Apify token, check token is valid and actors are available

**Chat not working:**
- Ensure content has been ingested (check database)
- Verify OpenAI API key is set correctly
- Check browser console for errors

## Quick Test Flow

1. Start backend: `uvicorn backend.app:app --reload --port 8000`
2. Start frontend: `npm run dev`
3. Enter creator handle: "testcreator"
4. Select sources and click "Scrape" (will use mock data)
5. Select items and click "Ingest Selected"
6. Chat: "What projects are you working on?"
7. Enable "Debug Sources" to see retrieval details

## File Structure

```
anti-gravity/
├── backend/
│   ├── __init__.py
│   ├── app.py              # FastAPI app and endpoints
│   ├── db.py               # Database connection
│   ├── settings.py         # Environment config
│   ├── models.py           # Pydantic models
│   ├── rag.py              # RAG logic (persona, retrieval, generation)
│   ├── ingest.py           # Chunking, embedding, insertion
│   ├── apify_client.py     # Scraping functions
│   └── requirements.txt
├── src/
│   ├── components/
│   │   ├── TopBar.jsx
│   │   ├── SettingsBar.jsx
│   │   ├── CreatorSetup.jsx
│   │   ├── ApprovalList.jsx
│   │   ├── Chat.jsx
│   │   └── SourcesPanel.jsx
│   ├── api/
│   │   └── client.js       # API client functions
│   ├── App.jsx
│   ├── main.jsx
│   ├── config.js
│   └── index.css
├── package.json
└── README.md
```

## Next Steps

- Implement actual Apify actors for YouTube, Twitter, Reddit
- Add LangChain agent graph (see `backend/agents.py` - optional)
- Add authentication if needed
- Add creator management UI
- Add persona editor UI
