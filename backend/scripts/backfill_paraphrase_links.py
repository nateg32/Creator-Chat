"""Backfill cross-platform paraphrase links for already-ingested documents.

Walks every document grouped by creator and runs link_cross_platform_paraphrases
so previously ingested cross-posts get linked too. Safe to re-run; the merge
inside paraphrase_link is idempotent.

Usage:
    python -m backend.scripts.backfill_paraphrase_links
    python -m backend.scripts.backfill_paraphrase_links --creator-id 42
    python -m backend.scripts.backfill_paraphrase_links --threshold 0.20 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from backend.db import db
from backend.services.paraphrase_link import (
    DEFAULT_DISTANCE_THRESHOLD,
    link_cross_platform_paraphrases,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("backfill_paraphrase_links")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creator-id", type=int, default=None,
                        help="Limit backfill to a single creator. Default: all.")
    parser.add_argument("--threshold", type=float, default=DEFAULT_DISTANCE_THRESHOLD,
                        help=f"Cosine distance threshold (default {DEFAULT_DISTANCE_THRESHOLD}).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute matches but skip metadata writes (paraphrase_link still writes; this just stops after planning).")
    args = parser.parse_args()

    if args.dry_run:
        log.warning("--dry-run is informational only; per-doc linker still writes. Aborting before iteration.")
        return 0

    where = ""
    params: tuple = ()
    if args.creator_id is not None:
        where = "WHERE d.creator_id = %s"
        params = (args.creator_id,)

    docs = db.execute_query(
        f"""
        SELECT d.id AS document_id, d.creator_id
        FROM documents d
        WHERE EXISTS (
            SELECT 1 FROM chunks c
            JOIN embeddings e ON e.chunk_id = c.id
            WHERE c.document_id = d.id
              AND c.chunk_index = 0
              AND COALESCE((c.metadata->>'is_title_chunk')::boolean, false) = true
        )
        {("AND " + where[6:]) if where else ""}
        ORDER BY d.creator_id, d.id
        """,
        params,
    ) or []

    total = len(docs)
    if not total:
        log.info("No eligible documents found (need a title chunk with embedding).")
        return 0

    log.info("Backfilling paraphrase links for %d document(s)…", total)
    linked_docs = 0
    total_links = 0
    started = time.time()

    for i, row in enumerate(docs, start=1):
        doc_id = int(row["document_id"])
        creator_id = int(row["creator_id"])
        try:
            links = link_cross_platform_paraphrases(
                doc_id, creator_id, distance_threshold=args.threshold
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("doc %s failed: %s", doc_id, exc)
            continue
        if links:
            linked_docs += 1
            total_links += len(links)
            log.info("[%d/%d] doc %s → %d link(s): %s",
                     i, total, doc_id, len(links),
                     ", ".join(f"{l['platform'] or '?'}#{l['document_id']}({l['distance']:.3f})" for l in links))
        elif i % 25 == 0:
            log.info("[%d/%d] no links so far…", i, total)

    elapsed = time.time() - started
    log.info("Done. %d/%d docs got at least one link (%d total edges) in %.1fs.",
             linked_docs, total, total_links, elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
