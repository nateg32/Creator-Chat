"""Cross-platform paraphrase linking via embedding similarity.

After a document is ingested and embedded, we look for *other* documents owned
by the same creator whose **title chunk** embedding is very close to this
document's title chunk. That catches paraphrased reposts that the simhash
fingerprint tier (token-based) misses — e.g. a YouTube video titled
"Spending 1 Million in Vegas" and a LinkedIn post titled
"Spent $1M in Vegas - here's why".

We don't merge the documents. We just stamp each document's metadata with
``related_document_ids`` so the UI can show "also posted on linkedin" and
retrieval can de-duplicate citations.
"""
from __future__ import annotations

import json
import logging
from typing import Iterable

from backend.db import db
from backend.settings import settings

log = logging.getLogger(__name__)

# Cosine distance threshold for "same idea, different wording".
# OpenAI text-embedding-3-* title-chunk vectors typically land:
#   exact match           -> 0.00
#   paraphrase / repost   -> 0.05 - 0.20
#   same topic, different angle -> 0.20 - 0.35
#   unrelated             -> 0.40+
# 0.18 is conservative enough to avoid false positives.
DEFAULT_DISTANCE_THRESHOLD = 0.18
MAX_LINKS_PER_DOC = 5


def link_cross_platform_paraphrases(
    document_id: int,
    creator_id: int,
    distance_threshold: float = DEFAULT_DISTANCE_THRESHOLD,
) -> list[dict]:
    """Find documents whose title chunk paraphrases this document's title chunk.

    Updates ``documents.metadata.related_document_ids`` on both sides
    (bidirectional link) and returns the list of matches:
        [{"document_id": int, "title": str, "platform": str, "distance": float}, ...]
    """
    try:
        # 1. Get the title-chunk embedding for the just-ingested document.
        row = db.execute_one(
            """
            SELECT e.embedding::text AS emb_text
            FROM chunks c
            JOIN embeddings e ON e.chunk_id = c.id
            WHERE c.document_id = %s
              AND c.chunk_index = 0
              AND e.model = %s
              AND COALESCE((c.metadata->>'is_title_chunk')::boolean, false) = true
            LIMIT 1
            """,
            (document_id, settings.EMBEDDING_MODEL),
        )
        if not row or not row.get("emb_text"):
            return []
        embedding_str = row["emb_text"]

        # 2. Search the same creator's other documents' title chunks for close matches.
        matches = db.execute_query(
            """
            SELECT
                d.id AS document_id,
                d.title AS title,
                COALESCE(d.metadata->>'platform', d.source) AS platform,
                d.metadata AS doc_metadata,
                (e.embedding <=> %s::vector) AS distance
            FROM chunks c
            JOIN embeddings e ON e.chunk_id = c.id
            JOIN documents d ON d.id = c.document_id
            WHERE d.creator_id = %s
              AND d.id <> %s
              AND c.chunk_index = 0
              AND e.model = %s
              AND COALESCE((c.metadata->>'is_title_chunk')::boolean, false) = true
              AND (e.embedding <=> %s::vector) <= %s
            ORDER BY (e.embedding <=> %s::vector) ASC
            LIMIT %s
            """,
            (
                embedding_str,
                creator_id,
                document_id,
                settings.EMBEDDING_MODEL,
                embedding_str,
                distance_threshold,
                embedding_str,
                MAX_LINKS_PER_DOC,
            ),
        ) or []

        if not matches:
            return []

        # 3. Stamp this document's metadata with the related ids (bidirectional).
        related = [
            {
                "document_id": int(m["document_id"]),
                "title": m.get("title") or "",
                "platform": (m.get("platform") or "").lower(),
                "distance": float(m["distance"]),
            }
            for m in matches
        ]
        _merge_related(document_id, related)
        for m in matches:
            _merge_related(
                int(m["document_id"]),
                [
                    {
                        "document_id": document_id,
                        "title": "",  # backfilled lazily; the other side will overwrite on its own pass
                        "platform": "",
                        "distance": float(m["distance"]),
                    }
                ],
            )
        return related
    except Exception as exc:  # noqa: BLE001
        log.warning("paraphrase link failed for doc %s: %s", document_id, exc)
        return []


def _merge_related(document_id: int, new_links: Iterable[dict]) -> None:
    """Idempotently merge ``new_links`` into documents.metadata.related_document_ids."""
    row = db.execute_one(
        "SELECT metadata FROM documents WHERE id = %s",
        (document_id,),
    )
    if not row:
        return
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}

    existing = meta.get("related_document_ids") or []
    by_id: dict[int, dict] = {}
    for entry in existing:
        if isinstance(entry, dict) and entry.get("document_id"):
            by_id[int(entry["document_id"])] = entry
    for entry in new_links:
        did = int(entry["document_id"])
        prev = by_id.get(did) or {}
        # Keep the smaller (closer) distance and any non-empty title/platform.
        merged = {
            "document_id": did,
            "title": entry.get("title") or prev.get("title", ""),
            "platform": entry.get("platform") or prev.get("platform", ""),
            "distance": min(
                float(entry.get("distance", 1.0)),
                float(prev.get("distance", 1.0)),
            ),
        }
        by_id[did] = merged

    meta["related_document_ids"] = sorted(
        by_id.values(), key=lambda e: e.get("distance", 1.0)
    )[:MAX_LINKS_PER_DOC]

    db.execute_update(
        "UPDATE documents SET metadata = %s::jsonb WHERE id = %s",
        (json.dumps(meta, default=str), document_id),
    )
