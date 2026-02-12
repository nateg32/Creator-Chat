import hashlib
import json
from typing import Dict, Any, Optional
from db import db

class ContentManager:
    """
    Handles deduplication, normalization, and ingest queuing.
    Strictly: Never allows duplicate (platform, source_id).
    Optionally: Detects duplicate content via hash.
    """

    @staticmethod
    def normalize_text(raw_json: Dict[str, Any], platform_key: str, content_type: str) -> str:
        """
        Extract searchable text from raw JSON based on platform.
        """
        # Generic fallback extraction
        parts = []
        
        # Title/Caption
        if "title" in raw_json: parts.append(raw_json["title"])
        if "caption" in raw_json: parts.append(raw_json["caption"])
        if "text" in raw_json: parts.append(raw_json["text"])
        if "description" in raw_json: parts.append(raw_json["description"])
        
        # Transcript (highest value)
        if "transcript" in raw_json: parts.append(raw_json["transcript"])
        
        # Combine
        text = "\n".join([str(p).strip() for p in parts if p])
        return text

    @staticmethod
    def compute_content_hash(text: str) -> str:
        """SHA256 hash of normalized text for exact content dedupe."""
        if not text:
            return hashlib.sha256(b"").hexdigest()
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def should_store_item(creator_id: int, platform_key: str, source_id: str, content_hash: str) -> bool:
        """
        Check if item already exists.
        Returns False if:
        - Exact duplicate (creator, platform, source_id) exists.
        - Content hash matches another item for this creator (optional strictly, but good for anti-spam).
        """
        # Check ID uniqueness
        # We can trust the unique constraint, but checking ahead saves a failed insert log.
        exists = db.execute_one(
            "SELECT 1 FROM source_items WHERE creator_id = %s AND platform_key = %s AND source_id = %s",
            (creator_id, platform_key, source_id)
        )
        if exists:
            return False
            
        # Check content hash (optional strictness)
        # For now, let's allow same content if it's a different source_id (e.g. cross-posting),
        # but you could return False here if you want strict cross-platform dedupe.
        return True

    @staticmethod
    def quality_score(text: str, raw_json: Dict[str, Any]) -> int:
        """
        Heuristic 0-100 score.
        """
        score = 50
        length = len(text)
        
        if length < 50: score -= 30
        if length > 200: score += 20
        if length > 1000: score += 10
        
        # Boost for transcript
        if raw_json.get("transcript"): score += 20
        
        return max(0, min(100, score))

    @staticmethod
    def save_item(creator_id: int, platform_key: str, item: Dict[str, Any]) -> str:
        """
        Normalize, hash, and save. Enqueues ingest job if visible.
        Returns 'NEW', 'DUPLICATE', or 'FILTERED'.
        """
        source_id = str(item.get("id") or item.get("source_id") or item.get("url"))
        if not source_id:
            return "SKIPPED_NO_ID"

        norm_text = ContentManager.normalize_text(item, platform_key, "post")
        content_hash = ContentManager.compute_content_hash(norm_text)
        
        if not ContentManager.should_store_item(creator_id, platform_key, source_id, content_hash):
            return "DUPLICATE"

        quality = ContentManager.quality_score(norm_text, item)
        status = "NEW"
        
        # Filter Logic
        if quality < 20: 
            status = "FILTERED_OUT"
        
        # Insert
        try:
            row_id = db.execute_insert(
                """
                INSERT INTO source_items (
                    creator_id, platform_key, source_id, source_url, 
                    published_at, content_type, raw_json, normalized_text,
                    content_hash, quality_score, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    creator_id, platform_key, source_id, item.get("url"),
                    item.get("published_at"), "post", json.dumps(item, default=str),
                    norm_text, content_hash, quality, status
                )
            )
            
            # Enqueue Ingest if good
            if status == "NEW":
                db.execute_insert(
                    """
                    INSERT INTO ingest_jobs (creator_id, platform_key, source_item_id, job_type, priority)
                    VALUES (%s, %s, %s, 'EMBED', 1)
                    RETURNING id
                    """,
                    (creator_id, platform_key, row_id)
                )
                return "NEW"
            else:
                return "FILTERED"
                
        except Exception as e:
            # Race condition on uniqueness or other DB error
            print(f"[ContentManager] Error saving item {source_id}: {e}")
            return "ERROR"
