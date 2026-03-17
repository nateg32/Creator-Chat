import argparse
import json

from backend.db import db


def _creator_row(creator_id: int = None, handle: str = ""):
    if creator_id is not None:
        return db.execute_one(
            """
            SELECT id, name, handle, fingerprint_status, fingerprint_updated_at,
                   style_fingerprint IS NOT NULL AND style_fingerprint::text <> '{}' AS has_style_fingerprint,
                   identity_fingerprint IS NOT NULL AND identity_fingerprint::text <> '{}' AS has_identity_fingerprint,
                   soul_md IS NOT NULL AND soul_md <> '' AS has_soul_md
            FROM creators
            WHERE id = %s
            """,
            (creator_id,),
        )
    if handle:
        return db.execute_one(
            """
            SELECT id, name, handle, fingerprint_status, fingerprint_updated_at,
                   style_fingerprint IS NOT NULL AND style_fingerprint::text <> '{}' AS has_style_fingerprint,
                   identity_fingerprint IS NOT NULL AND identity_fingerprint::text <> '{}' AS has_identity_fingerprint,
                   soul_md IS NOT NULL AND soul_md <> '' AS has_soul_md
            FROM creators
            WHERE lower(handle) = lower(%s) OR lower(name) = lower(%s)
            """,
            (handle, handle),
        )
    return None


def main():
    parser = argparse.ArgumentParser(description="Diagnose creator pipeline state.")
    parser.add_argument("--creator-id", type=int)
    parser.add_argument("--handle", type=str, default="")
    args = parser.parse_args()

    creator = _creator_row(args.creator_id, args.handle)
    if creator:
        creator_id = creator["id"]
        handle = creator.get("handle") or ""
    else:
        creator_id = args.creator_id
        handle = args.handle

    scrape_stats = db.execute_one(
        """
        SELECT
            count(*) AS total_items,
            count(*) FILTER (WHERE coalesce(transcript, '') <> '') AS transcript_text_items,
            count(*) FILTER (WHERE transcript_status = 'present') AS transcript_present_items,
            count(*) FILTER (WHERE transcript_status = 'error') AS transcript_error_items
        FROM scrape_items
        WHERE (%s IS NOT NULL AND creator_handle = %s) OR (%s IS NOT NULL AND creator_handle = %s)
        """,
        (handle if handle else None, handle, handle if handle else None, handle),
    ) if handle else {}

    doc_stats = db.execute_one(
        """
        SELECT
            count(*) AS documents,
            count(*) FILTER (WHERE source = 'persona') AS persona_docs
        FROM documents
        WHERE creator_id = %s
        """,
        (creator_id,),
    ) if creator_id is not None else {}

    chunk_stats = db.execute_one(
        "SELECT count(*) AS chunks FROM chunks WHERE creator_id = %s",
        (creator_id,),
    ) if creator_id is not None else {}

    job_rows = db.execute_query(
        """
        SELECT id, job_type, status, progress_percent, message, created_at
        FROM system_jobs
        WHERE creator_id = %s
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (creator_id,),
    ) if creator_id is not None else []

    payload = {
        "lookup": {"creator_id": args.creator_id, "handle": args.handle},
        "creator": creator,
        "scrape_items": scrape_stats,
        "documents": doc_stats,
        "chunks": chunk_stats,
        "recent_jobs": job_rows,
    }
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
