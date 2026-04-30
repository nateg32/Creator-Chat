import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.db import db
from backend.services.duplicate_detection import compute_normalized_text, generate_canonical_key, hamming_distance, simhash64

_ALLOWED_TRANSCRIPT_STATUSES = None


def normalize_timestamp(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        try:
            if ts > 4102444800:
                ts = ts / 1000.0
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (ValueError, OSError):
            return None
    if isinstance(ts, str):
        try:
            if ts.endswith('Z'):
                ts = ts.replace('Z', '+00:00')
            dt = datetime.fromisoformat(ts)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                ts_float = float(ts)
                if ts_float > 4102444800:
                    ts_float = ts_float / 1000.0
                return datetime.fromtimestamp(ts_float, tz=timezone.utc)
            except (ValueError, OSError):
                return None
    return None


def normalize_transcript_status(input_status: str) -> str:
    global _ALLOWED_TRANSCRIPT_STATUSES
    if _ALLOWED_TRANSCRIPT_STATUSES is None:
        try:
            res = db.execute_query(
                "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'scrape_items_transcript_status_check'"
            )
            if res and res[0].get('def'):
                matches = re.findall(r"'([^']+)'::text", res[0]['def'])
                if matches:
                    _ALLOWED_TRANSCRIPT_STATUSES = set(matches)
        except Exception:
            _ALLOWED_TRANSCRIPT_STATUSES = None

    if not _ALLOWED_TRANSCRIPT_STATUSES:
        _ALLOWED_TRANSCRIPT_STATUSES = {'present', 'missing', 'error', 'not_started', 'queued', 'processing', 'pending'}

    status = str(input_status or 'missing').lower()
    if status in _ALLOWED_TRANSCRIPT_STATUSES:
        return status

    for fallback in ('not_started', 'queued', 'missing', 'pending'):
        if fallback in _ALLOWED_TRANSCRIPT_STATUSES:
            return fallback
    return sorted(_ALLOWED_TRANSCRIPT_STATUSES)[0]


def resolve_transcript_status(transcript_text: Any, input_status: str) -> str:
    text = str(transcript_text or "").strip()
    if text:
        return normalize_transcript_status("present")
    return normalize_transcript_status(input_status or "missing")


def _infer_platform(item: Dict[str, Any], default_platform: str) -> str:
    base_meta = item.get('metadata') or {}
    if not isinstance(base_meta, dict):
        base_meta = {}
    item_platform = item.get('platform') or base_meta.get('platform')
    if item_platform and item_platform not in ('multi', 'unknown'):
        return item_platform

    surl = (item.get('source_url') or '').lower()
    if 'youtube.com' in surl or 'youtu.be' in surl:
        return 'youtube'
    if 'instagram.com' in surl:
        return 'instagram'
    if 'twitter.com' in surl or 'x.com' in surl:
        return 'twitter'
    if 'tiktok.com' in surl:
        return 'tiktok'
    if 'facebook.com' in surl or 'fb.com' in surl:
        return 'facebook'
    if 'linkedin.com' in surl:
        return 'linkedin'
    if 'reddit.com' in surl:
        return 'reddit'
    return default_platform or 'unknown'


def _load_duplicate_context(creator_handle: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not creator_handle:
        return {}, []
    rows = db.execute_query(
        """
        SELECT id, canonical_key, content_fingerprint
        FROM scrape_items
        WHERE creator_handle = %s
        ORDER BY created_at DESC
        LIMIT 500
        """,
        (creator_handle,),
    )
    canonical_map: Dict[str, Any] = {}
    fingerprints: List[Dict[str, Any]] = []
    for row in rows:
        canonical_key = row.get('canonical_key')
        if canonical_key and canonical_key not in canonical_map:
            canonical_map[canonical_key] = row.get('id')
        fp = row.get('content_fingerprint')
        if fp is not None:
            fingerprints.append({'id': row.get('id'), 'content_fingerprint': fp})
    return canonical_map, fingerprints


def _find_duplicate_preloaded(
    canonical_key: str,
    content_fingerprint: int,
    canonical_map: Dict[str, Any],
    fingerprints: List[Dict[str, Any]],
) -> Tuple[bool, Optional[str], Optional[str], float]:
    if canonical_key and canonical_key in canonical_map:
        return False, canonical_map[canonical_key], 'canonical', 1.0

    if content_fingerprint and content_fingerprint != 0:
        best_match_id = None
        best_distance = 64
        for item in fingerprints[:200]:
            dist = hamming_distance(content_fingerprint, item.get('content_fingerprint'))
            if dist <= 3 and dist < best_distance:
                best_distance = dist
                best_match_id = item.get('id')
        if best_match_id:
            confidence = 1.0 - (best_distance / 64.0)
            return False, best_match_id, 'fingerprint', confidence

    return True, None, None, 0.0


def _find_near_duplicate_preloaded(
    content_fingerprint: int,
    fingerprints: List[Dict[str, Any]],
    exclude_id: Optional[Any] = None,
    distance_threshold: int = 10,
) -> Tuple[Optional[Any], float]:
    """Detect cross-platform paraphrases (3 < distance <= threshold)."""
    if not content_fingerprint or content_fingerprint == 0:
        return None, 0.0
    best_id = None
    best_distance = 64
    for item in fingerprints[:200]:
        if exclude_id and item.get('id') == exclude_id:
            continue
        dist = hamming_distance(content_fingerprint, item.get('content_fingerprint'))
        if 3 < dist <= distance_threshold and dist < best_distance:
            best_distance = dist
            best_id = item.get('id')
    if best_id is None:
        return None, 0.0
    return best_id, 1.0 - (best_distance / 64.0)


def _build_response_item(db_item_id: Any, item: Dict[str, Any], creator_handle: str, item_platform: str, published_at: Optional[datetime], meta: Dict[str, Any], norm_status: str, is_primary: bool, dup_item_id: Optional[str]) -> Dict[str, Any]:
    preview_text = item.get('transcript') or item.get('caption', '') or ''
    preview = preview_text[:200] + '...' if len(preview_text) > 200 else preview_text
    published_at_str = published_at.isoformat() if isinstance(published_at, datetime) else (str(published_at) if published_at else None)
    return {
        'item_id': str(db_item_id),
        'source_url': item['source_url'],
        'title': meta.get('title') or item.get('caption') or '',
        'caption': item.get('caption'),
        'content': preview_text,
        'creator_handle': creator_handle,
        'transcript_status': norm_status,
        'published_at': published_at_str,
        'platform': item_platform,
        'metadata': meta,
        'preview': preview,
        'is_primary': is_primary,
        'duplicate_of_item_id': dup_item_id,
    }


def persist_search_items(
    creator_id: int,
    creator_handle: str,
    normalized_items: List[Dict[str, Any]],
    source_url: str,
    platform: str,
    mode: str,
    search_run_id: Optional[str] = None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    if not search_run_id:
        search_run_id = str(uuid.uuid4())

    db.execute_update(
        """
        INSERT INTO scrape_runs (id, source_url, platform, mode, creator_handle, items_found)
        VALUES (%s::uuid, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            platform = EXCLUDED.platform,
            mode = EXCLUDED.mode,
            creator_handle = EXCLUDED.creator_handle,
            items_found = EXCLUDED.items_found
        """,
        (search_run_id, source_url, platform, mode, creator_handle, len(normalized_items)),
    )

    response_items: List[Dict[str, Any]] = []
    failed_items: List[Dict[str, Any]] = []
    checkpoints: Dict[str, Dict[str, Any]] = {}
    canonical_map, fingerprints = _load_duplicate_context(creator_handle)

    insert_query = """
        INSERT INTO scrape_items (
            id, scrape_run_id, creator_handle, content_type, source_url,
            caption, transcript, transcript_status, published_at, metadata, review_status,
            canonical_key, content_fingerprint, is_primary, duplicate_of_item_id, duplicate_method, duplicate_confidence
        )
        VALUES (%s, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_url) DO UPDATE SET
            scrape_run_id = EXCLUDED.scrape_run_id,
            creator_handle = EXCLUDED.creator_handle,
            content_type = EXCLUDED.content_type,
            caption = EXCLUDED.caption,
            transcript = EXCLUDED.transcript,
            transcript_status = EXCLUDED.transcript_status,
            published_at = EXCLUDED.published_at,
            metadata = EXCLUDED.metadata,
            review_status = 'pending_review',
            canonical_key = EXCLUDED.canonical_key,
            content_fingerprint = EXCLUDED.content_fingerprint,
            is_primary = EXCLUDED.is_primary,
            duplicate_of_item_id = EXCLUDED.duplicate_of_item_id,
            duplicate_method = EXCLUDED.duplicate_method,
            duplicate_confidence = EXCLUDED.duplicate_confidence
        RETURNING id
    """

    for item in normalized_items:
        try:
            base_meta = item.get('metadata') or {}
            if not isinstance(base_meta, dict):
                base_meta = {}
            item_platform = _infer_platform(item, platform)
            item_creator_handle = item.get('creator_handle') or base_meta.get('creator_handle') or creator_handle or 'unknown'
            if str(item_creator_handle).lower() == 'unknown' and creator_handle:
                item_creator_handle = creator_handle

            meta = {
                **base_meta,
                'platform': item_platform,
                'matched_time_filter': item.get('matched_time_filter', True),
            }
            metadata_json = json.dumps(meta, default=str)
            published_at = normalize_timestamp(item.get('published_at'))
            canon_key = generate_canonical_key(item['source_url'], item_platform)
            norm_text = compute_normalized_text(item.get('title', ''), item.get('description', ''), item.get('caption', ''))
            fingerprint = simhash64(norm_text)
            is_primary, dup_item_id, dup_method, dup_confidence = _find_duplicate_preloaded(
                canon_key, fingerprint, canonical_map, fingerprints
            )
            # Detect cross-platform near-duplicates (paraphrased content). These
            # are NOT marked as duplicates (still primary) but linked via
            # metadata so retrieval can show all platforms covering the same
            # topic in one answer.
            related_id, related_conf = _find_near_duplicate_preloaded(
                fingerprint, fingerprints, exclude_id=dup_item_id
            )
            if related_id and is_primary:
                meta['related_item_id'] = str(related_id)
                meta['related_confidence'] = round(related_conf, 3)
                meta['related_method'] = 'fingerprint_near'
                metadata_json = json.dumps(meta, default=str)
            norm_status = resolve_transcript_status(item.get('transcript'), item.get('transcript_status', 'missing'))
            db_item_id = db.execute_insert(
                insert_query,
                (
                    str(uuid.uuid4()), search_run_id, item_creator_handle, item['content_type'],
                    item['source_url'], item.get('caption'), item.get('transcript'),
                    norm_status, published_at, metadata_json, 'pending_review',
                    canon_key, fingerprint, is_primary, dup_item_id, dup_method, dup_confidence,
                ),
            )
            if canon_key and is_primary and canon_key not in canonical_map:
                canonical_map[canon_key] = db_item_id
            if fingerprint:
                fingerprints.insert(0, {'id': db_item_id, 'content_fingerprint': fingerprint})
                if len(fingerprints) > 500:
                    fingerprints.pop()

            response_items.append(_build_response_item(db_item_id, item, item_creator_handle, item_platform, published_at, meta, norm_status, is_primary, dup_item_id))

            cp = checkpoints.setdefault(item_platform, {'latest_published_at': None, 'latest_content_ids': []})
            if published_at and (cp['latest_published_at'] is None or published_at > cp['latest_published_at']):
                cp['latest_published_at'] = published_at
            content_id = (meta.get('content_id') or '').strip() if isinstance(meta.get('content_id'), str) else meta.get('content_id')
            if content_id and content_id not in cp['latest_content_ids']:
                cp['latest_content_ids'].append(content_id)
                cp['latest_content_ids'] = cp['latest_content_ids'][-20:]
        except Exception:
            failed_items.append({'url': item.get('source_url'), 'reason_sanitized': 'Database insertion failed for this item.'})

    return search_run_id, response_items, failed_items, checkpoints


def merge_platform_statuses_with_checkpoints(platform_configs: Dict[str, Any], platform_statuses: Dict[str, Dict[str, Any]], checkpoints: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    updated = {}
    for key, cfg in (platform_configs or {}).items():
        current = dict(cfg) if isinstance(cfg, dict) else {}
        status = platform_statuses.get(key) or {}
        checkpoint = checkpoints.get(key) or {}
        if status:
            current["last_search_status"] = status.get("last_scrape_status") or status.get("last_search_status")
            current["last_search_at"] = status.get("last_search_at")
            current["last_error"] = status.get("last_error")
        latest_published_at = checkpoint.get("latest_published_at")
        if latest_published_at:
            current["last_checkpoint_published_at"] = latest_published_at.isoformat() if isinstance(latest_published_at, datetime) else str(latest_published_at)
        latest_content_ids = checkpoint.get("latest_content_ids") or []
        if latest_content_ids:
            current["last_checkpoint_content_ids"] = latest_content_ids
        updated[key] = current
    return updated
