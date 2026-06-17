"""Helpers for durable background jobs.

Centralizing queue writes keeps duplicate work, retry delays, and per-creator
isolation consistent across API routes and workers.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import logging
from typing import Any, Dict, Iterator, Optional, Tuple

from backend.db import db

logger = logging.getLogger(__name__)

_ADVISORY_NAMESPACE = 482017


def _payload_json(payload: Optional[Dict[str, Any]]) -> str:
    return json.dumps(payload or {}, default=str)


def make_job_dedupe_key(
    *,
    creator_id: int,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    payload = payload or {}
    normalized_type = str(job_type or "").upper()
    if normalized_type == "FINGERPRINT":
        mode = str(payload.get("mode") or "full").lower()
        refresh = "refresh" if payload.get("refresh") else "current"
        return f"creator:{creator_id}:fingerprint:{mode}:{refresh}"
    if normalized_type in {"INGEST", "TRANSCRIPT", "SCRAPE"}:
        scope = payload.get("search_id") or payload.get("search_run_id") or payload.get("scrape_id")
        if scope:
            return f"creator:{creator_id}:{normalized_type.lower()}:{scope}"
    return f"creator:{creator_id}:{normalized_type.lower()}"


def enqueue_system_job(
    *,
    creator_id: int,
    job_type: str,
    payload: Optional[Dict[str, Any]] = None,
    message: str = "",
    dedupe_key: Optional[str] = None,
) -> Any:
    """Insert a queued job, coalescing equivalent queued/processing jobs.

    Existing deployments without the new columns fall back to a plain insert so
    rollout stays safe while migrations apply on startup.
    """

    payload = payload or {}
    job_type = str(job_type or "").upper()
    dedupe_key = dedupe_key or make_job_dedupe_key(
        creator_id=creator_id,
        job_type=job_type,
        payload=payload,
    )
    try:
        return db.execute_insert(
            """
            INSERT INTO system_jobs (
                creator_id, job_type, payload, status, progress_percent,
                message, dedupe_key, available_at
            )
            VALUES (%s, %s, %s::jsonb, 'queued', 0, %s, %s, NOW())
            ON CONFLICT (dedupe_key)
            WHERE dedupe_key IS NOT NULL AND status IN ('queued', 'processing')
            DO UPDATE SET
                payload = EXCLUDED.payload,
                message = EXCLUDED.message,
                updated_at = NOW()
            RETURNING id
            """,
            (creator_id, job_type, _payload_json(payload), message, dedupe_key),
        )
    except Exception as exc:
        text = str(exc).lower()
        if "dedupe_key" not in text and "available_at" not in text:
            raise
        logger.warning("system_jobs migration not available yet; using plain insert: %s", exc)
        return db.execute_insert(
            """
            INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
            VALUES (%s, %s, %s::jsonb, 'queued', 0, %s)
            RETURNING id
            """,
            (creator_id, job_type, _payload_json(payload), message),
        )


def requeue_job_later(job_id: str, *, seconds: int = 10, message: str = "Waiting for related creator work") -> None:
    delay = max(1, min(int(seconds or 10), 300))
    try:
        db.execute_update(
            """
            UPDATE system_jobs
            SET status = 'queued',
                locked_at = NULL,
                locked_by = NULL,
                message = %s,
                available_at = NOW() + (%s * INTERVAL '1 second'),
                updated_at = NOW()
            WHERE id = %s
            """,
            (message, delay, job_id),
        )
    except Exception as exc:
        if "available_at" not in str(exc).lower():
            raise
        db.execute_update(
            """
            UPDATE system_jobs
            SET status = 'queued',
                locked_at = NULL,
                locked_by = NULL,
                message = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (message, job_id),
        )


@contextmanager
def creator_job_lock(creator_id: int, lock_name: str = "creator-write") -> Iterator[Tuple[bool, Optional[str]]]:
    """Hold a DB advisory lock for creator-scoped write-heavy work."""

    if creator_id is None:
        yield True, None
        return

    key = int(creator_id)
    with db.pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, %s)", (_ADVISORY_NAMESPACE, key))
            acquired = bool(cur.fetchone()[0])
            if not acquired:
                yield False, f"{lock_name} for creator {creator_id} is already running"
                return
            try:
                yield True, None
            finally:
                try:
                    cur.execute("SELECT pg_advisory_unlock(%s, %s)", (_ADVISORY_NAMESPACE, key))
                    conn.commit()
                except Exception:
                    conn.rollback()
                    logger.exception("Failed to release advisory lock for creator %s", creator_id)
