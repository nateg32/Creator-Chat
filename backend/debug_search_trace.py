"""
Run a focused search trace through the public-fact pipeline.

Usage:
    python backend/debug_search_trace.py --creator-id 1 --query "when did u write buy back your time"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from backend.db import db
from backend.services.personal_bio_service import personal_bio_service


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _load_creator(creator_id: int):
    row = db.execute_one(
        """
        SELECT id, name, handle, voice_profile, decision_policy, search_mode,
               soul_md, identity_fingerprint, research_summary, official_domains,
               platform_configs
        FROM creators
        WHERE id = %s
        """,
        (creator_id,),
    )
    if not row:
        raise RuntimeError(f"Creator {creator_id} not found")
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--creator-id", type=int, required=True)
    parser.add_argument("--query", type=str, default="when did u write buy back your time")
    parser.add_argument("--user-id", type=int, default=1)
    args = parser.parse_args()

    _configure_logging()
    creator = _load_creator(args.creator_id)
    creator_name = creator.get("name") or creator.get("handle") or "the creator"

    started = time.monotonic()
    result = personal_bio_service.handle_personal_question(
        user_id=args.user_id,
        creator_id=args.creator_id,
        question=args.query,
        voice_profile=creator.get("voice_profile") or {},
        creator_name=creator_name,
        decision_policy=creator.get("decision_policy") or {},
        creator_profile=dict(creator),
        conversation_history=[],
        allow_web=(creator.get("search_mode") or "hybrid") == "hybrid",
    )
    elapsed = time.monotonic() - started

    print("\n=== FINAL RESULT ===")
    print(json.dumps(
        {
            "answer": result.get("answer"),
            "move": result.get("move"),
            "confidence": result.get("confidence"),
            "fact_cache_hit": result.get("fact_cache_hit"),
            "evidence_plan": result.get("evidence_plan"),
            "sources": result.get("sources"),
            "elapsed_seconds": round(elapsed, 2),
        },
        indent=2,
        default=str,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
