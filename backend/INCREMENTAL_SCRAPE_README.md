# Increment Scrape Implementation Notes

## Architecture
This feature implements a robust, stateful scraping pipeline designed for scale.

- **Incremental Scraping**: Uses `scrape_cursors` table to track the last seen `item_id` or `timestamp` per platform/creator. Future fetches only request new data.
- **Strict Deduplication**: Uses `source_items` unique constraint `(creator_id, platform_key, source_id)` to prevent re-saving the same item.
- **Content Hashing**: Calculates SHA256 of normalized text to detect identical content across different logical IDs (optional strictness).
- **Asynchronous Ingestion**: Items are saved as "NEW", then an `ingest_jobs` row is created. A separate worker (`services.ingest_worker`) picks these up for embedding/transcription.

## How to Test

### 1. Database Migration
Ensure the migration ran:
```bash
# Check tables
psql -d creator_bot -c "\dt"
# Should see scrape_cursors, source_items, ingest_jobs, scrape_runs
```

### 2. Start Worker (Dev Only)
For local testing, you can start the ingestion worker process via API:
```bash
curl -X POST http://localhost:8000/ingest/worker/start
```
*In production, run `python services/ingest_worker.py` as a separate service.*

### 3. Run a Scrape
Trigger a scrape for a specific creator:
```bash
curl -X POST http://localhost:8000/scrape/run \
  -H "Content-Type: application/json" \
  -d '{"creator_id": 1, "force_full": false}'
```

### 4. Check Status
Monitor the run status:
```bash
curl "http://localhost:8000/scrape/runs?creator_id=1&limit=5"
```
Monitor the ingestion queue:
```bash
curl "http://localhost:8000/ingest/jobs?creator_id=1&status=PENDING"
```

## Troubleshooting
- **No items found**: Check `scrape_cursors`. If `last_item_id` is set, only newer items are fetched. Pass `force_full: true` (not yet fully implemented in mock) to reset.
- **Worker not processing**: Check `ingest_jobs.next_run_at`. If it's in the future (backoff), it won't run yet.
- **Duplicate handling**: If `ScrapeOrchestrator` sees duplicates, it logs `items_deduped` count in `scrape_runs`.

## Data Flow
Platform API -> `ScraperOrchestrator` -> `ContentManager` (Norm/Dedupe) -> `source_items` (INSERT) -> `ingest_jobs` (INSERT) -> `IngestWorker` (SELECT FOR UPDATE) -> Embeddings/VectorDB.
