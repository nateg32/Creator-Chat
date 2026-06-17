import hashlib
import json
from typing import Any, Dict, Optional

from backend.db import db
from backend.services.duplicate_detection import generate_canonical_key
from backend.services.transcript_quality import assess_transcript_quality


def _metadata_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value else {}
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def transcript_asset_key(source_url: str, platform: str = "") -> str:
    key = generate_canonical_key(source_url or "", platform or "")
    return key or hashlib.sha256(str(source_url or "").strip().encode("utf-8")).hexdigest()


def get_transcript_asset(source_url: str, platform: str = "") -> Optional[Dict[str, Any]]:
    """Return a reusable public transcript asset for a source URL, when present."""

    if not source_url:
        return None
    key = transcript_asset_key(source_url, platform)
    try:
        row = db.execute_one(
            """
            SELECT id, canonical_key, source_url, platform, title, transcript,
                   transcript_status, transcript_checksum, metadata
            FROM transcript_assets
            WHERE canonical_key = %s
               OR source_url = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (key, source_url),
        )
    except Exception:
        return None
    if not row:
        return None
    row["metadata"] = _metadata_dict(row.get("metadata"))
    return row


def get_usable_transcript_asset(
    source_url: str,
    platform: str = "",
    *,
    caption: str = "",
    title: str = "",
) -> Optional[Dict[str, Any]]:
    asset = get_transcript_asset(source_url, platform)
    if not asset:
        return None
    transcript = str(asset.get("transcript") or "").strip()
    if not transcript:
        return None
    diagnostics = assess_transcript_quality(transcript, caption=caption, title=title or asset.get("title") or "")
    if not diagnostics.get("usable"):
        return None
    asset["quality"] = diagnostics
    return asset


def upsert_transcript_asset(
    *,
    source_url: str,
    platform: str = "",
    title: str = "",
    transcript: str,
    transcript_status: str = "present",
    metadata: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Store one canonical transcript copy for a public source URL."""

    transcript_text = str(transcript or "").strip()
    if not source_url or not transcript_text:
        return None
    key = transcript_asset_key(source_url, platform)
    checksum = hashlib.sha256(transcript_text.encode("utf-8")).hexdigest()
    asset_metadata = _metadata_dict(metadata)
    return db.execute_insert(
        """
        INSERT INTO transcript_assets (
            canonical_key, source_url, platform, title, transcript,
            transcript_status, transcript_checksum, metadata, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ON CONFLICT (canonical_key) DO UPDATE SET
            source_url = COALESCE(EXCLUDED.source_url, transcript_assets.source_url),
            platform = COALESCE(NULLIF(EXCLUDED.platform, ''), transcript_assets.platform),
            title = COALESCE(NULLIF(EXCLUDED.title, ''), transcript_assets.title),
            transcript = EXCLUDED.transcript,
            transcript_status = EXCLUDED.transcript_status,
            transcript_checksum = EXCLUDED.transcript_checksum,
            metadata = COALESCE(transcript_assets.metadata, '{}'::jsonb) || EXCLUDED.metadata,
            updated_at = NOW()
        RETURNING id
        """,
        (
            key,
            source_url,
            platform,
            title,
            transcript_text,
            transcript_status or "present",
            checksum,
            json.dumps(asset_metadata, default=str),
        ),
    )


def apply_transcript_asset_to_metadata(
    metadata: Optional[Dict[str, Any]],
    asset: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge reusable asset metadata without hiding the source of the transcript."""

    merged = _metadata_dict(metadata)
    asset_meta = _metadata_dict(asset.get("metadata"))
    for key, value in asset_meta.items():
        if key not in merged and value not in (None, ""):
            merged[key] = value
    merged["transcript_asset_id"] = str(asset.get("id") or "")
    merged["transcript_asset_key"] = str(asset.get("canonical_key") or "")
    merged["transcript_reused_from_asset"] = True
    if asset.get("transcript_checksum"):
        merged["transcript_checksum"] = asset.get("transcript_checksum")
    return merged
