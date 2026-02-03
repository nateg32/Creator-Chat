# Creator Bot - Wizard UI

A polished, modern wizard interface for building AI bots that sound like your favorite creators.

## Features

- **4-Step Wizard Flow:**
  1. **Setup** - Paste a creator URL (Instagram, YouTube, TikTok, Twitter, or website)
  2. **Scrape** - Preview scraped content from Apify
  3. **Approve** - Approve/deny items before adding to knowledge base
  4. **Persona** - Configure tone, style, and behavior
  5. **Chat** - Interact with the creator bot

## Running the Application

### Backend

1. **Set environment variables:**
   ```powershell
   $env:DB_PASSWORD="Kipkogey2019!"
   $env:APIFY_TOKEN="apify_api_KT1BxcfCBwoTxkPcbFog0KwQc2BNHK4nJDUg"
   $env:OPENAI_API_KEY="your-openai-key"
   ```

2. **Start the server:**
   ```powershell
   cd "C:\Users\Nathan\Documents\Creator Bot"
   .\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
   ```

### Frontend

1. **Install dependencies (if needed):**
   ```bash
   cd frontend/anti-gravity
   npm install
   ```

2. **Start the dev server:**
   ```bash
   npm run dev
   ```

3. **Open in browser:**
   - Frontend: http://localhost:5173
   - Backend: http://127.0.0.1:8000

## Environment Variables

### Backend (.env or PowerShell)

- `DB_HOST` - PostgreSQL host (default: localhost)
- `DB_PORT` - PostgreSQL port (default: 5433)
- `DB_NAME` - Database name (default: rag_db)
- `DB_USER` - Database user (default: postgres)
- `DB_PASSWORD` - Database password (required)
- `APIFY_TOKEN` - Apify API token (required for scraping)
- `OPENAI_API_KEY` - OpenAI API key (required for embeddings and chat)

## API Endpoints

### Scraping
- `POST /scrape` - Scrape content from a creator URL
  - Body: `{ creator_id, handle, source, limit }`
  - Returns: `{ items: [{ queue_id, title, url, preview }] }`

### Approval
- `POST /approve_ingest` - Approve items and add to knowledge base
  - Body: `{ creator_id, queue_ids: [1, 2, 3] }`
  - Returns: `{ approved: N, ingested: [{ queue_id, document_id, chunks_inserted }] }`

### Persona
- `GET /creator/{id}/persona` - Get persona settings
- `POST /creator/{id}/persona` - Save persona settings
  - Body: `{ persona: "text content" }`

### Chat
- `POST /ask` - Ask a question to the creator bot
  - Body: `{ creator_id, question, top_k, max_distance }`
  - Returns: `{ answer: "...", retrieved: [...] }`

## Approval Flow

1. User scrapes content → items stored in `scrape_queue` table with status="pending"
2. User reviews items in Approval Gate → selects items to approve/deny
3. User clicks "Save to knowledge base" → calls `POST /approve_ingest` with `queue_ids` array
4. Backend:
   - Fetches items from `scrape_queue` by `queue_ids`
   - Creates documents in `documents` table
   - Chunks documents using `chunk_text_structured`
   - Generates embeddings using OpenAI
   - Stores embeddings in `embeddings` table
   - Updates `scrape_queue` status to "ingested"

## URL Parsing

The frontend automatically detects platform and extracts handle from URLs:
- Instagram: `instagram.com/username` or `@username`
- YouTube: `youtube.com/@handle` or `youtube.com/channel/...`
- TikTok: `tiktok.com/@username`
- Twitter/X: `twitter.com/username` or `x.com/username`
- Website: Any other URL (uses domain as handle)

## Persona Format

Persona is stored as plain text with the following structure:
```
Tone: Friendly
Style: Concise, Playful, Direct

Always do:
- Use emojis
- Be enthusiastic

Never do:
- Use formal language
- Make political statements

Example response:
Hey! That's awesome! 🎉
```

## Notes

- Default creator_id is `1` (MrBeast template)
- All scraping goes through backend (frontend never talks to Apify directly)
- Approval decisions are stored in frontend state until "Save to knowledge base" is clicked
- Persona is saved as a document with `metadata->>'type' = 'persona'`
- Chat uses RAG (Retrieval-Augmented Generation) with vector similarity search
