"""
Search router: platform → mapper mapping. No if/elif chains.
One platform failing does not fail the entire search run.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Callable

from .config.platforms import get_platform, validate_url, normalize_url, extract_handle
from .lib.instagram_parser import parse_instagram_url
from .apify_client import (
    search_instagram_reels,
    search_youtube_channel,
    search_twitter_profile,
    search_facebook_posts,
    search_reddit_user,
    search_linkedin_posts,
    search_tiktok_posts,
)


def _apply_time_filter(
    items: List[Dict[str, Any]],
    time_filter: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Post-filter items by time_filter. Sets matched_time_filter on each kept item."""
    if not time_filter or (time_filter.get("mode") or "all") == "all":
        for it in items:
            it["matched_time_filter"] = True
        return items
    mode = time_filter.get("mode")
    kept = []
    for it in items:
        pub = it.get("published_at")
        if not pub:
            it["matched_time_filter"] = False
            continue
        try:
            if isinstance(pub, str):
                ts = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            else:
                ts = pub
            if not ts.tzinfo:
                ts = ts.replace(tzinfo=timezone.utc)
        except Exception:
            it["matched_time_filter"] = False
            kept.append(it)
            continue
        now = datetime.now(timezone.utc)
        include = False
        if mode == "since":
            since_s = time_filter.get("since") or ""
            if since_s:
                try:
                    since = datetime.fromisoformat(since_s.replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                    include = ts >= since
                except Exception:
                    include = True
            else:
                include = True
        elif mode == "last_days":
            d = time_filter.get("days") or 30
            cutoff = now - timedelta(days=int(d))
            include = ts >= cutoff
        else:
            include = True
        it["matched_time_filter"] = include
        if include:
            kept.append(it)
    return kept


def _map_instagram(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map Instagram context to Apify input, run actor, return normalized items."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url and not handle:
        return []
    parsed = parse_instagram_url(url) if url else None
    if not handle and parsed:
        handle = parsed.get("handle")
    if not handle:
        return []
    reel_id = parsed.get("reel_id") if parsed else None
    max_items = min(int(ctx.get("max_items") or 10), 10)
    items = search_instagram_reels(handle, reel_id, max_items)
    creator_handle = ctx.get("creator_handle") or handle
    platform = "instagram"
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = platform
    tf = ctx.get("time_filter") or {}
    return _apply_time_filter(items, tf)


def _map_youtube(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search YouTube channel using apidojo/youtube-scraper."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or handle or "youtube"
    max_items = min(int(ctx.get("max_items") or 10), 50)
    tf = ctx.get("time_filter") or {}
    items = search_youtube_channel(url, handle, limit=max_items, time_filter=tf)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "youtube"
    return _apply_time_filter(items, tf)


def _map_youtube_shorts(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search YouTube Shorts using apidojo/youtube-scraper with shorts_only=True."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or handle or "youtube"
    max_items = min(int(ctx.get("max_items") or 10), 50)
    tf = ctx.get("time_filter") or {}
    items = search_youtube_channel(url, handle, limit=max_items, time_filter=tf, youtube_shorts_only=True)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "youtube_shorts"
    return _apply_time_filter(items, tf)


def _map_twitter(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search Twitter/X profile using apidojo/twitter-scraper-lite."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not handle and not url:
        return []
    h = (handle or "").strip().lstrip("@")
    if not h and url:
        from urllib.parse import urlparse
        path = urlparse(url).path.strip("/").split("/")
        if path and path[0] not in ("status", "i", "search"):
            h = path[0]
    if not h:
        return []
    creator_handle = ctx.get("creator_handle") or h
    max_items = min(int(ctx.get("max_items") or 20), 100)
    tf = ctx.get("time_filter") or {}
    items = search_twitter_profile(h, url=url or None, limit=max_items, time_filter=tf)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "twitter"
    return _apply_time_filter(items, tf)


def _map_linkedin(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search LinkedIn posts using supreme_coder/linkedin-post."""
    url = (ctx.get("url") or "").strip()
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or "linkedin"
    max_items = min(int(ctx.get("max_items") or 20), 100)
    items = search_linkedin_posts(url, limit=max_items, deep_search=True)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "linkedin"
    # LinkedIn actor does not support our time filters (we'll just mark as matched)
    return _apply_time_filter(items, {"mode": "all"})


def _map_reddit(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search Reddit user using harshmaur/reddit-scraper."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or handle or "reddit"
    max_items = min(int(ctx.get("max_items") or 20), 100)
    tf = ctx.get("time_filter") or {}
    items = search_reddit_user(url, handle, limit=max_items, time_filter=tf)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "reddit"
    return _apply_time_filter(items, tf)


def _map_facebook(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search Facebook page using apify/facebook-posts-scraper."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or handle or "facebook"
    max_items = min(int(ctx.get("max_items") or 20), 100)
    tf = ctx.get("time_filter") or {}
    items = search_facebook_posts(url, handle, limit=max_items, time_filter=tf)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "facebook"
    return _apply_time_filter(items, tf)


def _map_tiktok(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scrape TikTok posts using thenetaji/tiktok-post-scraper."""
    url = (ctx.get("url") or "").strip()
    handle = ctx.get("handle")
    if not url:
        return []
    creator_handle = ctx.get("creator_handle") or handle or "tiktok"
    max_items = min(int(ctx.get("max_items") or 20), 100)
    items = search_tiktok_posts(url, handle, limit=max_items)
    for it in items:
        it["creator_handle"] = creator_handle
        it["platform"] = "tiktok"
    # Actor doesn't support our time filter directly; just mark as matched.
    return _apply_time_filter(items, {"mode": "all"})


PLATFORM_MAPPERS: Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]] = {
    "instagram": _map_instagram,
    "youtube": _map_youtube,
    "youtube_shorts": _map_youtube_shorts,
    "twitter": _map_twitter,
    "linkedin": _map_linkedin,
    "reddit": _map_reddit,
    "facebook": _map_facebook,
    "tiktok": _map_tiktok,
}


def run_search_router(
    creator_id: int,
    creator_handle: str,
    platform_configs: Dict[str, Any],
    progress_callback: Optional[Callable[[str, str, int, int], None]] = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """
    For each enabled platform, run the mapped search. One platform failing
    does not fail the run. Returns (all_items, platform_statuses).
    platform_statuses[key] = { last_search_status, last_search_at, last_error }.
    
    Args:
        progress_callback: Optional callback(platform_key, status, current, total) for progress updates.
    """
    all_items: List[Dict[str, Any]] = []
    platform_statuses: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now(timezone.utc).isoformat()
    
    # Count enabled platforms
    enabled_platforms = [
        k for k, cfg in (platform_configs or {}).items()
        if isinstance(cfg, dict) and cfg.get("enabled")
    ]
    total_platforms = len(enabled_platforms)
    current_platform = 0

    for key, cfg in (platform_configs or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        url = (cfg.get("url") or "").strip()
        if not url:
            current_platform += 1
            platform_statuses[key] = {
                "last_scrape_status": "skipped",
                "last_search_at": now_iso,
                "last_error": "No URL",
                "items_found": 0,
            }
            if progress_callback:
                progress_callback(key, "skipped", current_platform, total_platforms)
            continue
        plat = get_platform(key)
        if not plat:
            current_platform += 1
            platform_statuses[key] = {
                "last_scrape_status": "skipped",
                "last_search_at": now_iso,
                "last_error": "Unknown platform",
                "items_found": 0,
            }
            if progress_callback:
                progress_callback(key, "skipped", current_platform, total_platforms)
            continue
        ok, err = validate_url(url, key)
        if not ok:
            current_platform += 1
            platform_statuses[key] = {
                "last_scrape_status": "error",
                "last_search_at": now_iso,
                "last_error": err or "Invalid URL",
                "items_found": 0,
            }
            if progress_callback:
                progress_callback(key, "error", current_platform, total_platforms)
            continue
        norm_url = normalize_url(url, key)
        handle = cfg.get("handle") or extract_handle(norm_url, key)
        mapper = PLATFORM_MAPPERS.get(key)
        if not mapper:
            current_platform += 1
            platform_statuses[key] = {
                "last_scrape_status": "skipped",
                "last_search_at": now_iso,
                "last_error": "No search implemented",
                "items_found": 0,
            }
            if progress_callback:
                progress_callback(key, "skipped", current_platform, total_platforms)
            continue
        ctx = {
            "url": norm_url,
            "handle": handle,
            "time_filter": cfg.get("timeFilter") or {"mode": "all"},
            "max_items": min(int(cfg.get("maxItems") or 10), 50),
            "creator_handle": creator_handle,
        }
        current_platform += 1
        if progress_callback:
            progress_callback(key, "searching", current_platform, total_platforms)
        try:
            print(f"[SCRAPE] {key} url={norm_url} handle={handle}", flush=True)
            items = mapper(ctx)
            all_items.extend(items)
            print(f"[SCRAPE] {key} items_found={len(items)}", flush=True)
            platform_statuses[key] = {
                "last_scrape_status": "success",
                "last_search_at": now_iso,
                "last_error": None,
                "items_found": len(items),
            }
            if progress_callback:
                progress_callback(key, "completed", current_platform, total_platforms)
        except Exception as e:
            print(f"[SCRAPE] {key} ERROR: {e}", flush=True)
            platform_statuses[key] = {
                "last_scrape_status": "error",
                "last_search_at": now_iso,
                "last_error": str(e),
                "items_found": 0,
            }
            if progress_callback:
                progress_callback(key, "error", current_platform, total_platforms)

    # Log summary
    successful = sum(1 for s in platform_statuses.values() if s.get("last_scrape_status") == "success")
    failed = sum(1 for s in platform_statuses.values() if s.get("last_scrape_status") == "error")
    skipped = sum(1 for s in platform_statuses.values() if s.get("last_scrape_status") == "skipped")
    total_items = sum(s.get("items_found", 0) for s in platform_statuses.values())
    
    print(f"[SEARCH] Summary: {successful} succeeded, {failed} failed, {skipped} skipped")
    print(f"[SEARCH] Total items found: {len(all_items)} (sum: {total_items})")
    for key, status in platform_statuses.items():
        items_count = status.get("items_found", 0)
        status_val = status.get("last_scrape_status", "unknown")
        error = status.get("last_error")
        print(f"[SEARCH]   {key}: {status_val} ({items_count} items)" + (f" - {error}" if error else ""))
    
    return all_items, platform_statuses
