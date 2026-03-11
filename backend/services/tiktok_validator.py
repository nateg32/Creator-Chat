from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


def _canonical_handle(value: Optional[str]) -> str:
    return str(value or "").strip().lower().lstrip("@")


def _url_matches_handle(url: str, expected_handle: str) -> bool:
    expected = _canonical_handle(expected_handle)
    if not url or not expected:
        return False
    try:
        parsed = urlparse(url)
        path = (parsed.path or "").strip("/")
        if not path:
            return False
        first = path.split("/", 1)[0].lower()
        return first == f"@{expected}"
    except Exception:
        return False


def verify_tiktok_profile_with_actor(
    url: str,
    handle: Optional[str],
    fetch_posts_fn=None,
) -> Dict[str, Any]:
    """Use the TikTok actor as a secondary verifier for profile URLs.

    Returns a small result payload instead of raising so callers can fall back
    to the softer HTML-based validation path when TikTok is flaky.
    """
    expected_handle = _canonical_handle(handle)
    fetch_posts = fetch_posts_fn
    if fetch_posts is None:
        from backend.apify_service import search_tiktok_posts
        fetch_posts = search_tiktok_posts

    try:
        items: List[Dict[str, Any]] = fetch_posts(url, expected_handle, limit=3, skip_transcripts=True) or []
    except Exception as exc:
        return {
            "confirmed": False,
            "checked_via": "tiktok_actor_soft",
            "reason": "actor_error",
            "error": str(exc),
        }

    if not items:
        return {
            "confirmed": False,
            "checked_via": "tiktok_actor_soft",
            "reason": "no_items",
        }

    for item in items:
        candidate_urls = [
            item.get("source_url") or "",
            ((item.get("metadata") or {}).get("canonical_url") or ""),
        ]
        if any(_url_matches_handle(candidate, expected_handle) for candidate in candidate_urls):
            matched_url = next((candidate for candidate in candidate_urls if _url_matches_handle(candidate, expected_handle)), url)
            return {
                "confirmed": True,
                "checked_via": "tiktok_actor",
                "matched_url": matched_url or url,
                "item_count": len(items),
            }

    return {
        "confirmed": False,
        "checked_via": "tiktok_actor_soft",
        "reason": "handle_mismatch",
        "item_count": len(items),
    }
