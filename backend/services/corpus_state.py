import hashlib
import json
from typing import Any, Dict, List, Optional

from backend.db import db
from backend.settings import settings


DOCUMENT_STORAGE_POLICY = "chunked_preview_v1"


def load_jsonish(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def compute_item_ingest_checksum(
    *,
    platform: str,
    source_url: str,
    source_id: str,
    title: str,
    text_content: str,
    transcript_status: str,
    published_at: Any,
) -> str:
    payload = {
        "platform": str(platform or ""),
        "source_url": str(source_url or ""),
        "source_id": str(source_id or ""),
        "title": str(title or ""),
        "text_content": str(text_content or ""),
        "transcript_status": str(transcript_status or ""),
        "published_at": str(published_at or ""),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get_document_ingest_checksum(metadata: Any) -> str:
    meta = load_jsonish(metadata)
    checksum = meta.get("ingest_checksum")
    return str(checksum or "").strip()


def compact_document_content_for_storage(
    *,
    title: str,
    platform: str,
    source_url: str,
    text_content: str,
    limit: Optional[int] = None,
) -> str:
    """Return a small document preview while chunks keep the retrieval text.

    The full transcript/post body is already stored in `chunks.chunk_text` for
    retrieval and embeddings. Keeping the same long body in `documents.content`
    is unnecessary duplication, but a compact preview remains useful for admin
    screens, title resolution, and legacy fallbacks.
    """

    max_chars = int(limit or settings.DOCUMENT_CONTENT_PREVIEW_CHARS or 1200)
    max_chars = max(300, min(max_chars, 4000))
    title_clean = " ".join(str(title or "").split())
    platform_clean = " ".join(str(platform or "").split())
    url_clean = " ".join(str(source_url or "").split())
    body = str(text_content or "").strip()
    if len(body) > max_chars:
        body = body[:max_chars].rstrip() + "..."

    header_bits = []
    if title_clean:
        header_bits.append(title_clean)
    if platform_clean:
        header_bits.append(platform_clean)
    if url_clean:
        header_bits.append(url_clean)
    header = " | ".join(header_bits)
    return f"{header}\n\n{body}".strip() if header else body


def apply_chunked_storage_metadata(
    metadata: Dict[str, Any],
    *,
    text_content: str,
    document_content: str,
    chunk_size: int,
    chunk_overlap: int,
) -> Dict[str, Any]:
    out = dict(metadata or {})
    out.update(
        {
            "storage_policy": DOCUMENT_STORAGE_POLICY,
            "full_text_available_in": "chunks",
            "source_text_char_count": len(str(text_content or "")),
            "document_preview_char_count": len(str(document_content or "")),
            "chunk_size": int(chunk_size),
            "chunk_overlap": int(chunk_overlap),
        }
    )
    return out


def find_existing_document(
    creator_id: int,
    *,
    source: str,
    source_id: str,
    source_url: str,
) -> Optional[Dict[str, Any]]:
    if source and source_id:
        row = db.execute_one(
            """
            SELECT d.id, d.source, d.source_id, d.metadata,
                   (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
            FROM documents d
            WHERE d.creator_id = %s
              AND d.source = %s
              AND d.source_id = %s
            ORDER BY d.id DESC
            LIMIT 1
            """,
            (creator_id, source, source_id),
        )
        if row:
            return row

    if source_url:
        return db.execute_one(
            """
            SELECT d.id, d.source, d.source_id, d.metadata,
                   (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
            FROM documents d
            WHERE d.creator_id = %s
              AND (
                  d.metadata->>'source_url' = %s
                  OR d.metadata->>'canonical_url' = %s
                  OR COALESCE(d.url, '') = %s
              )
            ORDER BY d.id DESC
            LIMIT 1
            """,
            (creator_id, source_url, source_url, source_url),
        )

    return None


def _scrape_item_source_id_candidates(creator_id: int, item: Dict[str, Any]) -> List[str]:
    metadata = load_jsonish(item.get("metadata"))
    item_id = str(item.get("id") or "").strip()
    raw_ids: List[str] = []
    for value in (
        metadata.get("content_id"),
        metadata.get("id"),
        item.get("content_id"),
        item.get("source_id"),
    ):
        value_text = str(value or "").strip()
        if value_text and value_text not in raw_ids:
            raw_ids.append(value_text)
    if item_id:
        for value in (f"item_{item_id}", f"search_item_{item_id}", item_id):
            if value not in raw_ids:
                raw_ids.append(value)

    candidates: List[str] = []
    for raw_id in raw_ids:
        for value in (raw_id, f"{creator_id}:{raw_id}"):
            if value and value not in candidates:
                candidates.append(value)
    return candidates


def find_existing_document_for_scrape_item(
    creator_id: int,
    item: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Find the searchable document that should correspond to a staged scrape item.

    Historical ingestion used raw content ids as ``documents.source_id`` while
    the worker path now prefixes them with the creator id.  Source URL metadata
    is the stable bridge between both versions, and source id candidates cover
    rows that were ingested before source URL metadata was consistently stored.
    """

    source_url = str(item.get("source_url") or item.get("url") or "").strip()
    source_ids = _scrape_item_source_id_candidates(creator_id, item)
    clauses: List[str] = []
    params: List[Any] = [creator_id]

    if source_url:
        clauses.append(
            "(d.metadata->>'source_url' = %s OR d.metadata->>'canonical_url' = %s OR COALESCE(d.url, '') = %s)"
        )
        params.extend([source_url, source_url, source_url])
    if source_ids:
        clauses.append("d.source_id = ANY(%s)")
        params.append(source_ids)

    if not clauses:
        return None

    return db.execute_one(
        f"""
        SELECT d.id, d.source, d.source_id, d.metadata,
               (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id) AS chunk_count
        FROM documents d
        WHERE d.creator_id = %s
          AND d.source != 'persona'
          AND ({' OR '.join(clauses)})
        ORDER BY chunk_count DESC, d.id DESC
        LIMIT 1
        """,
        tuple(params),
    )


def scrape_item_has_searchable_document(creator_id: int, item: Dict[str, Any]) -> bool:
    document = find_existing_document_for_scrape_item(creator_id, item)
    if not document:
        return False
    try:
        return int(document.get("chunk_count") or 0) > 0
    except Exception:
        return False


def compute_creator_corpus_checksum(creator_id: int) -> str:
    rows = db.execute_query(
        """
        SELECT source, source_id, metadata, content
        FROM documents
        WHERE creator_id = %s
          AND source != 'persona'
        ORDER BY source ASC, source_id ASC, id ASC
        """,
        (creator_id,),
    )

    digest = hashlib.sha256()
    for row in rows:
        metadata = load_jsonish(row.get("metadata"))
        ingest_checksum = get_document_ingest_checksum(metadata)
        if not ingest_checksum:
            ingest_checksum = hashlib.sha256(str(row.get("content") or "").encode("utf-8")).hexdigest()
        key = {
            "source": row.get("source") or "",
            "source_id": row.get("source_id") or "",
            "ingest_checksum": ingest_checksum,
        }
        digest.update(json.dumps(key, sort_keys=True, ensure_ascii=True).encode("utf-8"))

    return digest.hexdigest()


def refresh_creator_corpus_state(creator_id: int, *, sync_fingerprint: bool = False) -> str:
    checksum = compute_creator_corpus_checksum(creator_id)
    if sync_fingerprint:
        creator = db.execute_one(
            """
            SELECT content_corpus_checksum, fingerprint_corpus_checksum, style_fingerprint, soul_md
            FROM creators
            WHERE id = %s
            """,
            (creator_id,),
        ) or {}
        prior_content_checksum = str(creator.get("content_corpus_checksum") or "").strip()
        prior_fingerprint_checksum = str(creator.get("fingerprint_corpus_checksum") or "").strip()
        has_fingerprint_snapshot = bool(creator.get("style_fingerprint") or creator.get("soul_md"))
        if has_fingerprint_snapshot and prior_content_checksum and prior_content_checksum == prior_fingerprint_checksum:
            db.execute_update(
                """
                UPDATE creators
                SET content_corpus_checksum = %s,
                    fingerprint_corpus_checksum = %s
                WHERE id = %s
                """,
                (checksum, checksum, creator_id),
            )
        else:
            db.execute_update(
                "UPDATE creators SET content_corpus_checksum = %s WHERE id = %s",
                (checksum, creator_id),
            )
    else:
        db.execute_update(
            "UPDATE creators SET content_corpus_checksum = %s WHERE id = %s",
            (checksum, creator_id),
        )
    return checksum


def delete_document_chunks_and_embeddings(document_ids: list[int]) -> None:
    if not document_ids:
        return

    db.execute_update(
        "DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id = ANY(%s))",
        (document_ids,),
    )
    db.execute_update("DELETE FROM chunks WHERE document_id = ANY(%s)", (document_ids,))


def delete_document_corpus(document_ids: list[int]) -> None:
    if not document_ids:
        return

    delete_document_chunks_and_embeddings(document_ids)
    db.execute_update("DELETE FROM documents WHERE id = ANY(%s)", (document_ids,))


def prune_scrape_item_transcripts_after_review(search_id: Optional[str]) -> int:
    """Drop raw transcript text for denied/duplicate staging rows after review.

    Approved primary rows keep their raw transcript as the canonical recovery
    copy. Denied rows and duplicate staging rows do not need to carry large text
    forever because they are not the retrieval source of truth.
    """

    if not search_id:
        return 0

    marker = json.dumps({"transcript_storage": "pruned_after_review"})
    query_with_duplicate_flag = """
        UPDATE scrape_items
        SET transcript = NULL,
            metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
        WHERE scrape_run_id = %s
          AND COALESCE(transcript, '') <> ''
          AND (
              review_status = 'denied'
              OR COALESCE(is_primary, true) = false
          )
    """
    try:
        return db.execute_update(query_with_duplicate_flag, (marker, search_id))
    except Exception:
        return db.execute_update(
            """
            UPDATE scrape_items
            SET transcript = NULL,
                metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
            WHERE scrape_run_id = %s
              AND COALESCE(transcript, '') <> ''
              AND review_status = 'denied'
            """,
            (marker, search_id),
        )
