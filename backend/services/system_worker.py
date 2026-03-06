"""
Durable worker queue daemon.
Runs as a separate process, claims one job at a time via FOR UPDATE SKIP LOCKED,
and dispatches to the appropriate handler.

IMPORTANT: Do NOT import backend.app here — that boots all of FastAPI.
Import only lightweight backend modules directly.
"""
import time
import uuid
import os
import sys
import json
import logging
import traceback
import asyncio

# Ensure the repo root ("Creator Bot") is in sys.path so `backend.*` resolves.
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from backend.db import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("system_worker")

WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"


def _ensure_search_progress_table():
    db.execute_update(
        """
        CREATE TABLE IF NOT EXISTS search_progress (
            search_id UUID PRIMARY KEY,
            progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _set_search_progress(search_id: str, data: dict):
    _ensure_search_progress_table()
    db.execute_update(
        """
        INSERT INTO search_progress (search_id, progress_data, updated_at)
        VALUES (%s::uuid, %s::jsonb, NOW())
        ON CONFLICT (search_id) DO UPDATE SET
            progress_data = EXCLUDED.progress_data,
            updated_at = NOW()
        """,
        (search_id, json.dumps(data, default=str)),
    )


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

def update_job_progress(job_id: str, percent: int, status: str = "processing", message: str = ""):
    db.execute_update(
        "UPDATE system_jobs SET progress_percent = %s, status = %s, message = %s, updated_at = NOW() WHERE id = %s",
        (percent, status, message, job_id)
    )

def mark_job_completed(job_id: str, message: str = "Done"):
    db.execute_update(
        "UPDATE system_jobs SET status = 'completed', progress_percent = 100, message = %s, updated_at = NOW() WHERE id = %s",
        (message, job_id)
    )

def mark_job_failed(job_id: str, error: str):
    db.execute_update(
        "UPDATE system_jobs SET status = 'failed', error_log = %s, updated_at = NOW() WHERE id = %s",
        (error, job_id)
    )

# ---------------------------------------------------------------------------
# Job handlers
# ---------------------------------------------------------------------------

def _ensure_scrape_run(search_run_id: str, source_url: str, platform_tag: str, creator_handle: str):
    """Ensure scrape_runs row exists for worker-mode searches (scrape_items FK depends on it)."""
    if not search_run_id:
        return
    db.execute_update(
        """
        INSERT INTO scrape_runs (id, source_url, platform, mode, creator_handle, items_found)
        VALUES (%s::uuid, %s, %s, %s, %s, 0)
        ON CONFLICT (id) DO NOTHING
        """,
        (search_run_id, source_url or "", platform_tag or "multi", "profile", creator_handle or ""),
    )


def handle_scrape(job_id: str, payload: dict):
    """
    Run scrapers for a creator, then persist items to scrape_items table.
    Mirrors _run_search_background from app.py without importing FastAPI.
    """
    from backend.scraper_router import run_search_router

    creator_id = payload.get("creator_id")
    creator_handle = payload.get("creator_handle", "")
    platform_configs = payload.get("platform_configs", {})
    search_run_id = payload.get("search_run_id") or payload.get("search_id")
    source_url = payload.get("source_url", f"creator:{creator_id}")
    platform_tag = payload.get("platform_tag", "profile")

    update_job_progress(job_id, 5, "processing", "Initializing search...")
    if search_run_id:
        enabled_count = sum(1 for cfg in (platform_configs or {}).values() if isinstance(cfg, dict) and cfg.get("enabled"))
        _set_search_progress(search_run_id, {
            "status": "running",
            "stage": "search",
            "phase": "search",
            "percent": 2,
            "completed": 0,
            "total": enabled_count,
            "current_platform": None,
            "current_platform_label": None,
            "platform_statuses": {},
            "items_found": 0,
            "error": None,
            "message": "Preparing search...",
        })
        _ensure_scrape_run(search_run_id, source_url, platform_tag, creator_handle)

    def progress_callback(platform_key: str, status: str, current: int, total: int):
        if total > 0:
            pct = 5 + int((current / total) * 70)
        else:
            pct = 5
        update_job_progress(job_id, min(80, pct), "processing", f"Searching {platform_key}...")
        if search_run_id:
            percent = min(80.0, float(pct))
            _set_search_progress(search_run_id, {
                "status": "running",
                "stage": "search",
                "phase": "search",
                "percent": round(percent, 1),
                "completed": current,
                "total": total,
                "current_platform": platform_key,
                "current_platform_label": platform_key,
                "message": f"Searching {platform_key}...",
            })

    # Run scraper
    normalized_items, platform_statuses = run_search_router(
        creator_id, creator_handle, platform_configs,
        progress_callback=progress_callback
    )

    update_job_progress(job_id, 82, "processing", "Saving results...")
    if search_run_id:
        _set_search_progress(search_run_id, {
            "status": "running",
            "stage": "finalizing",
            "phase": "search",
            "percent": 82.0,
            "message": "Saving results...",
        })

    # Persist items to DB
    saved = 0
    failed = 0
    for item in normalized_items:
        try:
            _save_scrape_item(item, creator_id, search_run_id)
            saved += 1
        except Exception as e:
            item_url = item.get("source_url") or item.get("url")
            logger.error(f"Failed to save item ({item_url}): {e}")
            failed += 1

    # Update scrape_runs table
    if search_run_id:
        try:
            db.execute_update(
                "UPDATE scrape_runs SET status = 'completed', items_found = %s, updated_at = NOW() WHERE id = %s",
                (saved, search_run_id)
            )
        except Exception:
            pass

    update_job_progress(job_id, 92, "processing", "Updating platform config...")

    # Persist updated platform statuses back onto creator
    pc_updated = {}
    for k, cfg in (platform_configs or {}).items():
        c = dict(cfg) if isinstance(cfg, dict) else {}
        st = platform_statuses.get(k)
        if st:
            c["last_search_status"] = st.get("last_scrape_status")
            c["last_search_at"] = st.get("last_search_at")
            c["last_error"] = st.get("last_error")
        pc_updated[k] = c
    try:
        db.execute_update(
            "UPDATE creators SET platform_configs = %s WHERE id = %s",
            (json.dumps(pc_updated), creator_id),
        )
    except Exception as e:
        logger.warning(f"Could not update platform_configs: {e}")

    # Run transcript enrichment as part of search completion.
    if search_run_id:
        try:
            update_job_progress(job_id, 94, "processing", "Processing transcripts...")
            _set_search_progress(search_run_id, {
                "status": "running",
                "stage": "transcripts",
                "phase": "transcripts",
                "percent": 94.0,
                "items_found": saved,
                "failed_count": failed,
                "platform_statuses": platform_statuses,
                "message": "Processing transcripts...",
            })
            from backend.services.transcript_worker import run_transcripts_for_search
            run_transcripts_for_search(search_run_id)
        except Exception as e:
            logger.warning(f"Transcript step failed for search {search_run_id}: {e}")

        _set_search_progress(search_run_id, {
            "status": "completed",
            "stage": "done",
            "phase": "done",
            "percent": 100.0,
            "items_found": saved,
            "failed_count": failed,
            "platform_statuses": platform_statuses,
            "message": "Search complete",
        })

    mark_job_completed(job_id, f"Searched {saved} items ({failed} failed)")


def _save_scrape_item(item: dict, creator_id: int, search_run_id: str):
    """
    Persist a single scraped item into scrape_items table.
    Uses schema-compatible columns and source_url upsert semantics.
    """
    source_url = item.get("source_url") or item.get("url") or f"generated:{uuid.uuid4()}"
    caption = item.get("caption") or item.get("text") or ""
    transcript = item.get("transcript") or ""
    platform = item.get("platform") or item.get("source") or "unknown"
    creator_handle = item.get("creator_handle") or ""
    content_id = item.get("content_id") or str(uuid.uuid4())
    metadata = item.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = {
        **metadata,
        "platform": metadata.get("platform") or platform,
        "content_id": metadata.get("content_id") or content_id,
    }
    published_at = item.get("published_at") or item.get("timestamp") or None
    content_type = item.get("content_type") or "post"

    transcript_status = "present" if transcript else "missing"

    insert_sql = """
        INSERT INTO scrape_items (
            id, scrape_run_id, creator_handle, content_type, source_url,
            caption, transcript, transcript_status, published_at, metadata, review_status
        )
        VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'pending_review')
        ON CONFLICT (source_url) DO UPDATE SET
            scrape_run_id = EXCLUDED.scrape_run_id,
            creator_handle = EXCLUDED.creator_handle,
            content_type = EXCLUDED.content_type,
            caption = EXCLUDED.caption,
            transcript = EXCLUDED.transcript,
            transcript_status = EXCLUDED.transcript_status,
            published_at = EXCLUDED.published_at,
            metadata = EXCLUDED.metadata,
            review_status = 'pending_review'
    """
    db.execute_update(insert_sql, (
        str(uuid.uuid4()), search_run_id, creator_handle, content_type, source_url,
        caption, transcript, transcript_status, published_at, json.dumps(metadata)
    ))


def handle_transcript(job_id: str, payload: dict):
    from backend.services.transcript_worker import run_transcripts_for_search
    update_job_progress(job_id, 10, "processing", "Processing transcripts...")
    run_transcripts_for_search(payload["search_id"])
    mark_job_completed(job_id, "Transcripts finished")


def handle_ingest(job_id: str, payload: dict):
    from backend.ingest import chunk_text_structured, embed_chunks

    update_job_progress(job_id, 10, "processing", "Starting ingestion...")
    decisions = payload.get("decisions", [])
    creator_id = payload.get("creator_id")
    search_id = payload.get("search_id")

    # Backward/forward compatibility: newer queue payloads send approved_item_ids directly.
    approved_item_ids = [str(x) for x in (payload.get("approved_item_ids") or []) if x]
    if not approved_item_ids:
        approved_item_ids = [str(d["item_id"]) for d in decisions if d.get("decision") == "approve"]

    if not approved_item_ids:
        mark_job_completed(job_id, "No approved items to ingest.")
        return

    fetch_query = """
        SELECT id, creator_handle, source_url, caption, transcript,
               transcript_status, published_at, metadata, content_type
        FROM scrape_items
        WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
    """
    items = db.execute_query(fetch_query, (approved_item_ids, search_id))

    total_items = len(items)
    ingested_ok = 0
    failed_count = 0
    for idx, item in enumerate(items):
        item_id = item["id"]
        current_percent = 10 + int((idx / total_items) * 80)
        update_job_progress(job_id, current_percent, "processing", f"Ingesting item {idx+1}/{total_items}...")

        try:
            text_content = item.get("transcript") or item.get("caption") or ""
            if not text_content:
                failed_count += 1
                continue

            source_url = item["source_url"]
            meta = item.get("metadata") or {}
            platform = meta.get("platform", "unknown")
            content_id = meta.get("content_id", f"item_{item_id}")
            title = meta.get("title", source_url)

            doc_metadata = {"type": "content", "platform": platform, "source_url": source_url, "content_id": content_id}

            doc_query = """
                INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (source, source_id) DO UPDATE SET
                    creator_id = EXCLUDED.creator_id, title = EXCLUDED.title, content = EXCLUDED.content
                RETURNING id
            """
            document_id = db.execute_insert(
                doc_query,
                (creator_id, title, text_content, platform, str(content_id), json.dumps(doc_metadata))
            )

            db.execute_update(
                "INSERT INTO creator_documents (creator_id, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (creator_id, document_id)
            )

            chunks = chunk_text_structured(text=text_content, creator_id=creator_id, document_id=document_id, chunk_size=800, overlap=120)
            chunk_ids = []
            for chunk in chunks:
                c_id = db.execute_insert(
                    "INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text) VALUES (%s, %s, %s, %s) ON CONFLICT (document_id, chunk_index) DO NOTHING RETURNING id",
                    (creator_id, document_id, chunk["index"], chunk["text"])
                )
                if c_id:
                    chunk_ids.append(c_id)

            embed_chunks(chunk_ids)
            db.execute_update("UPDATE scrape_items SET review_status = 'approved', status = 'completed' WHERE id = %s", (str(item_id),))
            ingested_ok += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Error ingesting item {item_id}: {e}")

    if total_items > 0 and ingested_ok == 0:
        mark_job_failed(job_id, f"No items ingested successfully. failed={failed_count}/{total_items}")
        return

    update_job_progress(job_id, 95, "processing", "Updating creator state...")
    db.execute_update("UPDATE creators SET last_approved_version = config_version WHERE id = %s", (creator_id,))

    # Queue fingerprint rebuild so Persona step has analyzed content after ingest.
    try:
        db.execute_insert(
            """
            INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
            VALUES (%s, 'FINGERPRINT', %s::jsonb, 'queued', 0, 'Fingerprint job enqueued after ingest')
            RETURNING id
            """,
            (creator_id, json.dumps({"creator_id": creator_id}))
        )
    except Exception as e:
        logger.warning(f"Could not enqueue fingerprint job after ingest: {e}")

    mark_job_completed(job_id, f"Ingested {ingested_ok} items ({failed_count} failed)")


def handle_fingerprint(job_id: str, payload: dict):
    from backend.services.fingerprint_service import fingerprint_service
    update_job_progress(job_id, 10, "processing", "Analyzing personality traits...")
    creator_id = payload["creator_id"]
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(fingerprint_service.generate_fingerprint_async(creator_id))
        status_row = db.execute_one(
            "SELECT fingerprint_status FROM creators WHERE id = %s",
            (creator_id,),
        )
        status = (status_row or {}).get("fingerprint_status")
        if status == "error":
            mark_job_failed(job_id, "Fingerprint generation ended in error state")
            return
        mark_job_completed(job_id, "Fingerprint built.")
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Main claim-and-execute loop
# ---------------------------------------------------------------------------

def claim_and_execute_jobs():
    logger.info(f"[{WORKER_ID}] System Worker Queue started.")

    # Validate DB connection
    try:
        db.execute_query("SELECT 1")
        logger.info(f"[{WORKER_ID}] DB connection OK.")
    except Exception as e:
        logger.error(f"[{WORKER_ID}] DB connection failed: {e}")
        sys.exit(1)

    while True:
        try:
            claim_query = """
                UPDATE system_jobs
                SET status = 'processing', locked_at = NOW(), locked_by = %s
                WHERE id = (
                    SELECT id FROM system_jobs
                    WHERE status = 'queued'
                    ORDER BY created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING *;
            """
            job = db.execute_one(claim_query, (WORKER_ID,))

            if not job:
                time.sleep(2)
                continue

            job_id = str(job['id'])
            jtype = job['job_type']
            payload = job.get('payload') or {}
            if isinstance(payload, str):
                payload = json.loads(payload)

            logger.info(f"[{WORKER_ID}] Claimed job {job_id} type={jtype}")

            try:
                if jtype == 'SCRAPE':
                    handle_scrape(job_id, payload)
                elif jtype == 'TRANSCRIPT':
                    handle_transcript(job_id, payload)
                elif jtype == 'INGEST':
                    handle_ingest(job_id, payload)
                elif jtype == 'FINGERPRINT':
                    handle_fingerprint(job_id, payload)
                else:
                    raise ValueError(f"Unknown job type: {jtype}")
            except Exception as e:
                err_log = str(e) + "\n" + traceback.format_exc()
                logger.error(f"Job {job_id} failed: {e}")
                mark_job_failed(job_id, err_log)

        except Exception as queue_err:
            logger.error(f"System queue error: {queue_err}")
            time.sleep(5)


if __name__ == "__main__":
    claim_and_execute_jobs()
