# Scrape Pipeline Implementation Summary

## Files Changed

### Backend
1. **backend/migrations/001_scrape_queue.sql** (NEW)
   - Creates `scrape_queue` table with status tracking
   - Indexes for efficient queries

2. **backend/models.py**
   - Updated `ScrapeRequest` model: `creator_id`, `handle`, `source`, `limit`
   - Added `ApproveIngestRequestNew`, `ApproveIngestResponseNew`, `ApproveIngestItem`
   - Added `PersonaResponse` model
   - Updated `ScrapedItem` to use `queue_id` instead of `source_id`

3. **backend/app.py**
   - Added helper functions: `mock_scrape()`, `try_apify_scrape()`, `insert_scrape_queue_items()`, `fetch_queue_items()`, `mark_queue_ingested()`
   - Updated `/scrape` endpoint: stores items in queue, returns queue_ids
   - Updated `/approve_ingest` endpoint: uses queue_ids, reuses existing `ingest_document()` logic
   - Updated `/creator/{creator_id}/persona` endpoint: returns `PersonaResponse` format
   - **Preserved existing endpoints**: `/health`, `/ask`, `/ingest` work exactly as before

### Frontend
1. **src/api/client.js**
   - Updated `scrape()`: uses `creator_id`, `handle`, `source`, `limit`
   - Updated `approveIngest()`: uses `creator_id`, `queue_ids`, `doc_type`, `title_prefix`

2. **src/components/CreatorSetup.jsx**
   - Updated to use new API format (single source dropdown instead of checkboxes)
   - Added persona preview functionality
   - Passes `creatorId` prop

3. **src/components/ApprovalList.jsx**
   - Updated to use `queue_id` instead of array indices
   - Uses new `approveIngest()` API format
   - Displays results with document_id and chunks_inserted

4. **src/App.jsx**
   - Passes `creatorId` to `CreatorSetup` component

5. **src/index.css**
   - Added styles for select dropdown, button-group, persona-preview

## Database Migration

Run the migration file to create the `scrape_queue` table:

```bash
# Using psql
psql -U postgres -d creator_bot -f backend/migrations/001_scrape_queue.sql

# Or using pgAdmin
# Open pgAdmin → Connect to database → Right-click → Query Tool → Paste SQL → Execute
```

## Testing Instructions

1. **Run Migration:**
   ```bash
   psql -U postgres -d creator_bot -f backend/migrations/001_scrape_queue.sql
   ```

2. **Start Backend:**
   ```bash
   cd backend
   python -m uvicorn app:app --reload --port 8000
   ```

3. **Test in Swagger UI** (http://127.0.0.1:8000/docs):
   - POST `/scrape` with:
     ```json
     {
       "creator_id": 1,
       "handle": "test",
       "source": "tiktok",
       "limit": 8
     }
     ```
   - Copy `queue_ids` from response
   - POST `/approve_ingest` with:
     ```json
     {
       "creator_id": 1,
       "queue_ids": [1, 2, 3],
       "doc_type": "knowledge",
       "title_prefix": "Approved"
     }
     ```
   - POST `/ask` to verify ingested content:
     ```json
     {
       "creator_id": 1,
       "question": "What projects are you working on?",
       "top_k": 5,
       "max_distance": 1.15
     }
     ```

4. **Test Frontend:**
   - Start frontend: `npm run dev`
   - Enter creator handle → Select source → Click "Scrape"
   - Select items → Click "Ingest Selected"
   - Chat with the bot

## Key Features

- **Queue-based workflow**: Items stored in `scrape_queue` before approval
- **Mock scraping**: Works without Apify token (returns realistic mock content)
- **Partial success handling**: Skips missing/invalid queue_ids, continues with valid ones
- **Reuses existing logic**: `/approve_ingest` uses same `ingest_document()` function as `/ingest`
- **Backward compatible**: Existing `/ask` and `/ingest` endpoints unchanged

## API Changes

### POST /scrape (Updated)
**Request:**
```json
{
  "creator_id": 1,
  "handle": "mrbeast",
  "source": "tiktok",
  "limit": 10
}
```

**Response:**
```json
{
  "items": [
    {
      "queue_id": 1,
      "title": "mrbeast - Content 1",
      "url": "https://tiktok.com/mrbeast/post_1",
      "preview": "Hey everyone! mrbeast here..."
    }
  ]
}
```

### POST /approve_ingest (Updated)
**Request:**
```json
{
  "creator_id": 1,
  "queue_ids": [1, 2, 3],
  "doc_type": "knowledge",
  "title_prefix": "Approved"
}
```

**Response:**
```json
{
  "approved": 3,
  "ingested": [
    {
      "queue_id": 1,
      "document_id": 123,
      "chunks_inserted": 4
    }
  ]
}
```

### GET /creator/{creator_id}/persona (Updated)
**Response:**
```json
{
  "creator_id": 1,
  "persona": "You are a helpful creator...",
  "found": true
}
```

## Notes

- Mock scraping generates 6-10 realistic content snippets based on limit
- Queue items are marked as 'ingested' after successful processing
- Missing queue_ids are silently skipped (partial success)
- All database operations use existing `db` helper methods with auto-commit
