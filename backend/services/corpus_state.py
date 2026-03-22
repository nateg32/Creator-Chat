import hashlib
import json
from typing import Any, Dict, Optional

from backend.db import db


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
            SELECT id, source, source_id, metadata
            FROM documents
            WHERE creator_id = %s
              AND source = %s
              AND source_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (creator_id, source, source_id),
        )
        if row:
            return row

    if source_url:
        return db.execute_one(
            """
            SELECT id, source, source_id, metadata
            FROM documents
            WHERE creator_id = %s
              AND metadata->>'source_url' = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (creator_id, source_url),
        )

    return None


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
