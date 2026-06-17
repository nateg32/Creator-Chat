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
import signal
from datetime import datetime, timezone
from urllib.parse import urlparse

# Ensure the repo root ("Creator Chat") is in sys.path so `backend.*` resolves.
root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from backend.db import db
from backend.settings import settings
from backend.services.corpus_state import (
    apply_chunked_storage_metadata,
    compact_document_content_for_storage,
    compute_item_ingest_checksum,
    delete_document_chunks_and_embeddings,
    find_existing_document,
    get_document_ingest_checksum,
    prune_scrape_item_transcripts_after_review,
    refresh_creator_corpus_state,
)
from backend.services.search_persistence import (
    persist_search_items,
    merge_platform_statuses_with_checkpoints,
    resolve_transcript_status,
)
from backend.services.transcript_quality import transcript_needs_recovery
from backend.ingest import clean_transcript_for_ingestion
from backend.services.system_jobs import creator_job_lock, enqueue_system_job, requeue_job_later
from backend.services.schema_migrations import apply_sql_migration

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("system_worker")

WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"
_LAST_STALE_RECOVERY_AT = 0.0
_SHUTDOWN_REQUESTED = False


def _request_shutdown(signum=None, frame=None):
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True
    logger.info(f"[{WORKER_ID}] Shutdown requested; finishing current job before exit.")


def _install_signal_handlers():
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _request_shutdown)
        except (ValueError, OSError):
            logger.warning(f"[{WORKER_ID}] Could not install handler for {sig_name}")


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
    percent = max(0, min(100, int(percent or 0)))
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
        "UPDATE system_jobs SET status = 'failed', error_log = %s, message = 'Knowledge update failed', updated_at = NOW() WHERE id = %s",
        (error, job_id)
    )


def recover_stale_jobs():
    """
    Requeue jobs abandoned by a crashed worker.
    Active jobs update `updated_at` throughout processing, so this only touches
    work that has been silent longer than the configured stale window.
    """
    stale_minutes = max(10, int(getattr(settings, "SYSTEM_JOB_STALE_AFTER_MINUTES", 45) or 45))
    max_retries = max(0, int(getattr(settings, "SYSTEM_JOB_MAX_RETRIES", 2) or 2))
    db.execute_update(
        """
        UPDATE system_jobs
        SET status = 'queued',
            locked_at = NULL,
            locked_by = NULL,
            retry_count = COALESCE(retry_count, 0) + 1,
            message = 'Requeued after worker interruption',
            updated_at = NOW()
        WHERE status = 'processing'
          AND updated_at < NOW() - (%s * INTERVAL '1 minute')
          AND COALESCE(retry_count, 0) < %s
        """,
        (stale_minutes, max_retries),
    )
    db.execute_update(
        """
        UPDATE system_jobs
        SET status = 'failed',
            error_log = 'Job stopped making progress and exceeded retry limit.',
            message = 'Knowledge update failed',
            updated_at = NOW()
        WHERE status = 'processing'
          AND updated_at < NOW() - (%s * INTERVAL '1 minute')
          AND COALESCE(retry_count, 0) >= %s
        """,
        (stale_minutes, max_retries),
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
        progress_callback=progress_callback,
        enrich_transcripts=False,
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

    _, response_items, failed_items, checkpoints = persist_search_items(
        creator_id=creator_id,
        creator_handle=creator_handle,
        normalized_items=normalized_items,
        source_url=source_url or f"creator:{creator_id}",
        platform=platform_tag,
        mode="profile",
        search_run_id=search_run_id,
    )
    saved = len(response_items)
    failed = len(failed_items)

    update_job_progress(job_id, 92, "processing", "Updating platform config...")

    pc_updated = merge_platform_statuses_with_checkpoints(platform_configs, platform_statuses, checkpoints)
    try:
        db.execute_update(
            "UPDATE creators SET platform_configs = %s WHERE id = %s",
            (json.dumps(pc_updated), creator_id),
        )
    except Exception as e:
        logger.warning(f"Could not update platform_configs: {e}")

    if search_run_id:
        _set_search_progress(search_run_id, {
            "status": "completed",
            "stage": "done",
            "phase": "done",
            "percent": 100.0,
            "items_found": saved,
            "failed_count": failed,
            "platform_statuses": platform_statuses,
            "transcript_job_status": "queued",
            "message": "Search complete. Transcript enrichment continues in background." if saved else "Search complete. No public content found for the selected sources.",
        })
        try:
            enqueue_system_job(
                creator_id=creator_id,
                job_type="TRANSCRIPT",
                payload={"search_id": search_run_id},
                message="Transcript job enqueued after search",
            )
        except Exception as e:
            logger.warning(f"Could not enqueue transcript job for search {search_run_id}: {e}")

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

    transcript_status = resolve_transcript_status(transcript, item.get("transcript_status"))

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
            review_status = CASE
                WHEN scrape_items.review_status IN ('approved', 'denied')
                 AND COALESCE(scrape_items.caption, '') = COALESCE(EXCLUDED.caption, '')
                 AND COALESCE(scrape_items.transcript, '') = COALESCE(EXCLUDED.transcript, '')
                THEN scrape_items.review_status
                ELSE 'pending_review'
            END
    """
    db.execute_update(insert_sql, (
        str(uuid.uuid4()), search_run_id, creator_handle, content_type, source_url,
        caption, transcript, transcript_status, published_at, json.dumps(metadata)
    ))


def _compose_ingest_text(caption: str, transcript: str) -> str:
    caption_text = str(caption or "").strip()
    transcript_text = clean_transcript_for_ingestion(transcript)

    if not caption_text and not transcript_text:
        return ""
    if not caption_text:
        return transcript_text
    if not transcript_text:
        return caption_text

    cap_norm = " ".join(caption_text.split()).casefold()
    transcript_norm = " ".join(transcript_text.split()).casefold()
    if cap_norm == transcript_norm:
        return transcript_text if len(transcript_text) >= len(caption_text) else caption_text
    if cap_norm in transcript_norm:
        return transcript_text
    if transcript_norm in cap_norm:
        return caption_text

    return f"{caption_text}\n\n---\n\n{transcript_text}"


def _is_probable_direct_media_url(url: str) -> bool:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False

    path = parsed.path.lower()
    if path.endswith((".mp3", ".mp4", ".m4a", ".mpeg", ".mpga", ".wav", ".webm", ".mov")):
        return True

    host = parsed.netloc.lower()
    page_hosts = (
        "youtube.com", "youtu.be", "instagram.com", "tiktok.com",
        "twitter.com", "x.com", "linkedin.com", "facebook.com",
    )
    return not any(page_host in host for page_host in page_hosts)


def handle_transcript(job_id: str, payload: dict):
    from backend.services.transcript_worker import run_transcripts_for_search
    update_job_progress(job_id, 10, "processing", "Processing transcripts...")
    run_transcripts_for_search(payload["search_id"])
    mark_job_completed(job_id, "Transcripts finished")


def handle_ingest(job_id: str, payload: dict):
    from backend.ingest import chunk_text_structured, embed_chunks
    from backend.apify_service import extract_content_id, extract_title_from_metadata
    from backend.services.transcript_worker import process_transcript_job

    update_job_progress(job_id, 8, "processing", "Preparing approved content...")
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
    update_job_progress(job_id, 12, "processing", "Loading approved sources...")
    items = db.execute_query(fetch_query, (approved_item_ids, search_id))

    total_items = len(items)
    if total_items == 0:
        db.execute_update("UPDATE creators SET last_approved_version = config_version WHERE id = %s", (creator_id,))
        mark_job_completed(job_id, "No approved items found to ingest.")
        return

    ingested_ok = 0
    failed_count = 0
    changed_item_count = 0
    skipped_item_count = 0

    for idx, item in enumerate(items):
        item_id = item["id"]
        current_percent = 14 + int((idx / total_items) * 74)
        update_job_progress(job_id, current_percent, "processing", f"Preparing source {idx+1}/{total_items}...")

        try:
            source_url = item["source_url"]
            meta = item.get("metadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            platform = meta.get("platform", "unknown")
            if not platform or platform == "unknown":
                if "instagram.com" in source_url:
                    platform = "instagram"
                elif "youtube.com" in source_url or "youtu.be" in source_url:
                    platform = "youtube"
                elif "twitter.com" in source_url or "x.com" in source_url:
                    platform = "twitter"
                elif "tiktok.com" in source_url:
                    platform = "tiktok"
                elif "linkedin.com" in source_url:
                    platform = "linkedin"
                elif "facebook.com" in source_url:
                    platform = "facebook"
                elif "reddit.com" in source_url:
                    platform = "reddit"
                else:
                    platform = "unknown"

            content_id = meta.get("content_id") or extract_content_id(source_url, platform) or f"item_{item_id}"
            title = meta.get("title") or extract_title_from_metadata(meta, platform, source_url) or source_url or "Untitled"
            # The historical documents uniqueness constraint is global on
            # (source, source_id). Prefix the stored source_id with creator_id
            # so two creators can approve the same public URL without one
            # tenant overwriting the other's document/chunks.
            source_id = f"{creator_id}:{content_id}"
            transcript = item.get("transcript") or ""
            transcript_status = item.get("transcript_status") or "missing"
            text_content = _compose_ingest_text(item.get("caption"), transcript)

            existing_doc = find_existing_document(
                creator_id,
                source=str(platform),
                source_id=source_id,
                source_url=source_url,
            )
            existing_doc_has_chunks = bool(existing_doc and int(existing_doc.get("chunk_count") or 0) > 0)

            current_checksum = ""
            if text_content:
                current_checksum = compute_item_ingest_checksum(
                    platform=str(platform),
                    source_url=source_url,
                    source_id=source_id,
                    title=title,
                    text_content=text_content,
                    transcript_status=transcript_status,
                    published_at=item.get("published_at"),
                )

            if existing_doc_has_chunks and current_checksum and get_document_ingest_checksum(existing_doc.get("metadata")) == current_checksum:
                db.execute_update(
                    "UPDATE scrape_items SET review_status = 'approved', status = 'completed' WHERE id = %s::uuid",
                    (str(item_id),),
                )
                skipped_item_count += 1
                ingested_ok += 1
                continue

            if settings.TRANSCRIBE_ON_INGEST and transcript_needs_recovery(
                transcript,
                caption=item.get("caption") or "",
                title=title,
            ):
                try:
                    update_job_progress(
                        job_id,
                        min(88, current_percent + 2),
                        "processing",
                        f"Recovering transcript {idx+1}/{total_items}...",
                    )
                    process_transcript_job(
                        str(item_id),
                        source_url,
                        str(platform),
                        item.get("caption") or "",
                        meta,
                        transcript,
                    )
                    refreshed_item = db.execute_one(
                        "SELECT transcript, transcript_status, metadata FROM scrape_items WHERE id = %s::uuid",
                        (str(item_id),),
                    ) or {}
                    transcript = str(refreshed_item.get("transcript") or "")
                    transcript_status = refreshed_item.get("transcript_status") or transcript_status
                    refreshed_meta = refreshed_item.get("metadata") or {}
                    if isinstance(refreshed_meta, str):
                        try:
                            refreshed_meta = json.loads(refreshed_meta)
                        except Exception:
                            refreshed_meta = {}
                    if isinstance(refreshed_meta, dict):
                        meta.update(refreshed_meta)
                except Exception as e:
                    logger.warning(f"Transcription failed for item {item_id}: {e}")
                    transcript_status = "error"

            text_content = _compose_ingest_text(item.get("caption"), transcript)
            if not text_content:
                failed_count += 1
                continue

            ingest_checksum = compute_item_ingest_checksum(
                platform=str(platform),
                source_url=source_url,
                source_id=source_id,
                title=title,
                text_content=text_content,
                transcript_status=transcript_status,
                published_at=item.get("published_at"),
            )

            if existing_doc_has_chunks and get_document_ingest_checksum(existing_doc.get("metadata")) == ingest_checksum:
                db.execute_update(
                    "UPDATE scrape_items SET review_status = 'approved', status = 'completed' WHERE id = %s::uuid",
                    (str(item_id),),
                )
                skipped_item_count += 1
                ingested_ok += 1
                continue

            doc_metadata = {
                "type": "content",
                "platform": platform,
                "content_type": item.get("content_type", "unknown"),
                "creator_handle": item.get("creator_handle"),
                "source_url": source_url,
                "content_id": content_id,
                "canonical_url": source_url,
                "search_run_id": search_id,
                "transcript_status": transcript_status,
                "published_at": item.get("published_at"),
                "ingest_checksum": ingest_checksum,
            }
            for key, value in meta.items():
                if key not in ("platform", "content_id", "canonical_url", "title"):
                    doc_metadata[key] = value
            chunk_size = max(400, int(settings.INGEST_CHUNK_SIZE or 1000))
            chunk_overlap = max(0, min(int(settings.INGEST_CHUNK_OVERLAP or 80), chunk_size // 3))
            document_content = compact_document_content_for_storage(
                title=title,
                platform=str(platform),
                source_url=source_url,
                text_content=text_content,
            )
            doc_metadata = apply_chunked_storage_metadata(
                doc_metadata,
                text_content=text_content,
                document_content=document_content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            if existing_doc:
                delete_document_chunks_and_embeddings([int(existing_doc["id"])])
                doc_query = """
                    UPDATE documents
                    SET creator_id = %s,
                        title = %s,
                        content = %s,
                        source = %s,
                        source_id = %s,
                        metadata = %s::jsonb
                    WHERE id = %s
                    RETURNING id
                """
                document_id = db.execute_insert(
                    doc_query,
                    (
                        creator_id,
                        title,
                        document_content,
                        str(platform),
                        source_id,
                        json.dumps(doc_metadata, default=str),
                        int(existing_doc["id"]),
                    ),
                )
            else:
                doc_query = """
                    INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (source, source_id) DO UPDATE SET
                        creator_id = EXCLUDED.creator_id,
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata
                    RETURNING id
                """

                document_id = db.execute_insert(
                    doc_query,
                    (creator_id, title, document_content, str(platform), source_id, json.dumps(doc_metadata, default=str))
                )

            db.execute_update(
                "INSERT INTO creator_documents (creator_id, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (creator_id, document_id)
            )

            # ── Retrieve timing map from scrape_items metadata (if YouTube) ──
            item_meta = item.get("metadata") or {}
            if isinstance(item_meta, str):
                try:
                    item_meta = json.loads(item_meta)
                except Exception:
                    item_meta = {}
            timing_map = item_meta.get("transcript_timing_map") if isinstance(item_meta, dict) else None

            chunks = chunk_text_structured(
                text=text_content,
                creator_id=creator_id,
                document_id=document_id,
                chunk_size=chunk_size,
                overlap=chunk_overlap,
                timing_map=timing_map,
            )
            title_chunk_text = f"{title}\n\n{platform} - {item.get('creator_handle') or ''}\n\n{text_content[:240]}".strip()
            if title_chunk_text:
                for chunk in chunks:
                    chunk["index"] = chunk["index"] + 1
                chunks.insert(0, {
                    "index": 0,
                    "text": title_chunk_text,
                    "creator_id": creator_id,
                    "document_id": document_id,
                })
            chunk_ids = []
            for chunk in chunks:
                chunk_meta = {
                    "platform": platform,
                    "type": item.get("content_type", "unknown"),
                    "creator_handle": item.get("creator_handle"),
                    "source_url": source_url,
                    "content_id": content_id,
                    "canonical_url": source_url,
                    "title": title,
                    "search_run_id": search_id,
                    "transcript_status": transcript_status,
                    "published_at": item.get("published_at"),
                    "source_ref": {
                        "platform": platform,
                        "content_id": content_id,
                        "canonical_url": source_url,
                        "title": title,
                        "published_at": item.get("published_at"),
                        "content_type": item.get("content_type", "unknown"),
                    },
                    "is_title_chunk": chunk["index"] == 0,
                }
                # ── Inject chunk-level video timestamps ──
                if chunk.get("start_time_sec") is not None:
                    chunk_meta["start_time_sec"] = chunk["start_time_sec"]
                    chunk_meta["source_ref"]["start_time_sec"] = chunk["start_time_sec"]
                if chunk.get("end_time_sec") is not None:
                    chunk_meta["end_time_sec"] = chunk["end_time_sec"]
                    chunk_meta["source_ref"]["end_time_sec"] = chunk["end_time_sec"]

                c_id = db.execute_insert(
                    """
                    INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text, metadata)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        metadata = EXCLUDED.metadata,
                        creator_id = EXCLUDED.creator_id
                    RETURNING id
                    """,
                    (
                        creator_id,
                        document_id,
                        chunk["index"],
                        chunk["text"],
                        json.dumps(chunk_meta, default=str),
                    ),
                )
                if c_id:
                    chunk_ids.append(c_id)

            if chunk_ids:
                batch_size = max(1, int(os.getenv("EMBED_BATCH_SIZE", "128")))
                total_batches = max(1, (len(chunk_ids) + batch_size - 1) // batch_size)
                for batch_index, start in enumerate(range(0, len(chunk_ids), batch_size), start=1):
                    update_job_progress(
                        job_id,
                        min(92, current_percent + int((batch_index / total_batches) * 8)),
                        "processing",
                        f"Embedding source {idx+1}/{total_items}...",
                    )
                    embed_chunks(chunk_ids[start:start + batch_size])

            db.execute_update(
                "UPDATE scrape_items SET review_status = 'approved', status = 'completed' WHERE id = %s::uuid",
                (str(item_id),),
            )

            ingested_ok += 1
            changed_item_count += 1
        except Exception as e:
            failed_count += 1
            logger.error(f"Error ingesting item {item_id}: {e}")

    if total_items > 0 and ingested_ok == 0:
        mark_job_failed(job_id, f"No items ingested successfully. failed={failed_count}/{total_items}")
        return

    try:
        prune_scrape_item_transcripts_after_review(search_id)
    except Exception as cleanup_exc:
        logger.warning("Transcript staging cleanup skipped for search %s: %s", search_id, cleanup_exc)

    update_job_progress(job_id, 94, "processing", "Refreshing creator memory...")
    db.execute_update("UPDATE creators SET last_approved_version = config_version WHERE id = %s", (creator_id,))
    refresh_creator_corpus_state(creator_id, sync_fingerprint=(changed_item_count == 0))

    fingerprint_row = db.execute_one(
        "SELECT style_fingerprint, soul_md, fingerprint_status FROM creators WHERE id = %s",
        (creator_id,),
    ) or {}
    has_fingerprint = bool(fingerprint_row.get("style_fingerprint") or fingerprint_row.get("soul_md"))
    needs_fingerprint_refresh = changed_item_count > 0 or not has_fingerprint

    if needs_fingerprint_refresh:
        try:
            update_job_progress(job_id, 97, "processing", "Scheduling persona refresh...")
            db.execute_update(
                """
                UPDATE creators
                SET fingerprint_status = 'processing',
                    fingerprint_progress = %s::jsonb
                WHERE id = %s
                """,
                (
                    json.dumps({
                        "status": "processing",
                        "percent": 2,
                        "stage": "queued",
                        "message": "Persona analysis queued from approved content.",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    creator_id,
                ),
            )
            enqueue_system_job(
                creator_id=creator_id,
                job_type="FINGERPRINT",
                payload={
                    "creator_id": creator_id,
                    "refresh": False,
                    "mode": "incremental" if has_fingerprint else "full",
                },
                message="Creator profile generation queued after ingest",
            )
        except Exception as e:
            logger.warning(f"Could not enqueue fingerprint job after ingest: {e}")

    mark_job_completed(
        job_id,
        f"Ingested {ingested_ok} items ({changed_item_count} changed, {skipped_item_count} unchanged, {failed_count} failed)",
    )


def handle_fingerprint(job_id: str, payload: dict):
    from backend.services.fingerprint_service import fingerprint_service
    update_job_progress(job_id, 10, "processing", "Analyzing creator voice and values...")
    creator_id = payload["creator_id"]
    refresh = bool(payload.get("refresh"))
    mode = payload.get("mode") or "full"
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(fingerprint_service.generate_fingerprint_async(creator_id, refresh=refresh, mode=mode))
        status_row = db.execute_one(
            "SELECT fingerprint_status FROM creators WHERE id = %s",
            (creator_id,),
        )
        status = (status_row or {}).get("fingerprint_status")
        if status == "error":
            mark_job_failed(job_id, "Creator profile generation ended in error state")
            return
        mark_job_completed(job_id, "Creator profile built.")
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Main claim-and-execute loop
# ---------------------------------------------------------------------------

def claim_and_execute_jobs():
    logger.info(f"[{WORKER_ID}] System Worker Queue started.")
    global _LAST_STALE_RECOVERY_AT
    _install_signal_handlers()

    # Validate DB connection
    try:
        db.execute_query("SELECT 1")
        logger.info(f"[{WORKER_ID}] DB connection OK.")
    except Exception as e:
        logger.error(f"[{WORKER_ID}] DB connection failed: {e}")
        sys.exit(1)

    for migration in (
        "014_system_jobs_progress.sql",
        "015_system_jobs_scale_safety.sql",
        "017_transcript_status_pipeline.sql",
        "018_transcript_storage_efficiency.sql",
    ):
        try:
            apply_sql_migration(migration)
        except Exception as e:
            logger.warning(f"[{WORKER_ID}] Could not apply {migration}: {e}")

    while not _SHUTDOWN_REQUESTED:
        try:
            now = time.time()
            if now - _LAST_STALE_RECOVERY_AT > 60:
                recover_stale_jobs()
                _LAST_STALE_RECOVERY_AT = now

            claim_query = """
                UPDATE system_jobs
                SET status = 'processing', locked_at = NOW(), locked_by = %s
                WHERE id = (
                    SELECT id FROM system_jobs
                    WHERE status = 'queued'
                      AND COALESCE(available_at, created_at) <= NOW()
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
                    with creator_job_lock(payload.get("creator_id"), "ingest/persona") as (acquired, reason):
                        if not acquired:
                            requeue_job_later(job_id, seconds=15, message=reason or "Waiting for creator lock")
                            continue
                        handle_ingest(job_id, payload)
                elif jtype == 'FINGERPRINT':
                    with creator_job_lock(payload.get("creator_id"), "ingest/persona") as (acquired, reason):
                        if not acquired:
                            requeue_job_later(job_id, seconds=15, message=reason or "Waiting for creator lock")
                            continue
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

    logger.info(f"[{WORKER_ID}] Worker stopped gracefully.")
    db.close()


if __name__ == "__main__":
    claim_and_execute_jobs()
