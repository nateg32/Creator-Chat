# Instagram Reels Scraping - Complete Implementation

## ✅ What's Been Implemented

### Backend

1. **Database Migration** (`003_instagram_reels_staging.sql`)
   - ✅ `scrape_runs` table - tracks each scrape request
   - ✅ `scrape_items` table - staging approval gate with transcript tracking
   - ✅ Constraints and indexes for efficient queries

2. **New/Updated Files**
   - ✅ `backend/lib/instagram_parser.py` - URL parsing for Instagram
   - ✅ `backend/lib/transcription.py` - OpenAI Whisper transcription fallback
   - ✅ `backend/apify_client.py` - Updated with `scrape_instagram_reels()` using `apify/instagram-reel-scraper`
   - ✅ `backend/models.py` - New request/response models
   - ✅ `backend/settings.py` - Added `TRANSCRIBE_ON_INGEST` env var
   - ✅ `backend/app.py` - **RESTORED** with all endpoints

3. **Endpoints**
   - ✅ `POST /scrape` - Accepts URL, parses Instagram, scrapes via Apify, stores in `scrape_items`
   - ✅ `POST /approve_ingest_v2` - Approves/denies items, handles transcript fallback, ingests to KB
   - ✅ `GET /scrape/{scrape_id}/items` - Fetch items for a scrape run
   - ✅ All existing endpoints preserved (`/health`, `/ask`, `/ingest`, `/approve_ingest`, `/creator/{id}/persona`)

### Frontend

1. **API Client** (`src/api/client.js`)
   - ✅ `scrape({ url, limit })` - New format
   - ✅ `approveIngestV2({ scrape_id, decisions })` - New endpoint
   - ✅ `getScrapeItems(scrape_id)` - Fetch items

2. **Components**
   - ✅ `ApprovalGate` - Updated to work with `item_id` and show transcript status
   - ✅ `ScrapePreview` - Shows transcript availability
   - ✅ `App.jsx` - Updated to use new API format and store `scrape_id`

## 🔧 What You Need To Do

### 1. Environment Variables

Set these in PowerShell before starting the server:

```powershell
$env:APIFY_TOKEN="apify_api_KT1BxcfCBwoTxkPcbFog0KwQc2BNHK4nJDUg"
$env:OPENAI_API_KEY="your-openai-key"
$env:DB_PASSWORD="Kipkogey2019!"
$env:TRANSCRIBE_ON_INGEST="false"  # Set to "true" to enable transcription fallback
```

### 2. Run Backend

```powershell
cd "C:\Users\Nathan\Documents\Creator Bot"
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

### 3. Run Frontend

```bash
cd frontend/anti-gravity
npm run dev
```

## 📋 API Endpoints

### POST /scrape
**Request:**
```json
{
  "url": "https://instagram.com/username",
  "limit": 10
}
```

**Response:**
```json
{
  "scrape_id": "uuid",
  "items": [
    {
      "item_id": "uuid",
      "source_url": "https://instagram.com/reel/...",
      "caption": "...",
      "transcript_status": "present" | "missing" | "error",
      "published_at": "2024-01-01T00:00:00",
      "metadata": {...},
      "preview": "..."
    }
  ]
}
```

### POST /approve_ingest_v2
**Request:**
```json
{
  "scrape_id": "uuid",
  "decisions": [
    {"item_id": "uuid1", "decision": "approve"},
    {"item_id": "uuid2", "decision": "deny"}
  ]
}
```

**Response:**
```json
{
  "approved": 1,
  "ingested": [
    {
      "queue_id": "uuid1",
      "document_id": 123,
      "chunks_inserted": 4
    }
  ]
}
```

### GET /scrape/{scrape_id}/items
Returns all items for a scrape run with their review status.

## 🔄 Flow

1. **User enters Instagram URL** → Frontend calls `POST /scrape` with URL
2. **Backend**:
   - Parses URL to extract handle/reel_id
   - Calls Apify `instagram-reel-scraper` actor
   - Stores items in `scrape_items` with `review_status='pending_review'`
   - Returns `scrape_id` and items preview
3. **User reviews items** → Frontend shows items with transcript status
4. **User approves/denies** → Frontend calls `POST /approve_ingest_v2` with decisions
5. **Backend**:
   - Updates denied items to `review_status='denied'`
   - For approved items:
     - If transcript missing and `TRANSCRIBE_ON_INGEST=true`, transcribes video
     - Creates documents
     - Chunks with metadata (platform, type, creator_handle, source_url, transcript_status)
     - Generates embeddings
     - Updates `review_status='approved'`
6. **User chats** → RAG retrieves chunks and generates answers

## 🎯 Key Features

- ✅ **Strict 10-reel limit** enforced in backend
- ✅ **Staging approval gate** - nothing goes to KB until approved
- ✅ **Transcript tracking** - status: present/missing/error
- ✅ **Transcription fallback** - optional OpenAI Whisper (feature-flagged)
- ✅ **Rich metadata** - platform, type, creator_handle, source_url, published_at in chunks
- ✅ **Clean chunking** - 800 chars, 120 overlap, never mixes reels
- ✅ **Error handling** - Clear messages for missing tokens, DB errors, etc.

## 🧪 Testing

1. **Test Scraping:**
   - Enter: `https://instagram.com/username` or `@username`
   - Should see items with transcript status

2. **Test Approval:**
   - Approve some items, deny others
   - Click "Save to knowledge base"
   - Should see success message with chunks inserted

3. **Test Chat:**
   - Ask questions about the scraped content
   - Should get answers based on ingested reels

## 📝 Notes

- Default `creator_id=1` is used (can be mapped from handle later)
- Transcription is disabled by default (`TRANSCRIBE_ON_INGEST=false`)
- All secrets are read from environment variables (never hardcoded)
- Backend enforces 10-reel limit regardless of frontend input
- Items are deduplicated by `source_url` (ON CONFLICT DO UPDATE)
