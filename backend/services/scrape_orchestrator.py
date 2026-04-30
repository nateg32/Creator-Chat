from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from backend.db import db
from backend.services.content_manager import ContentManager
from backend.services.cursor_manager import CursorManager
from backend.apify_service import (
    scrape_custom_urls,
    scrape_tiktok_posts,
    search_facebook_posts,
    search_instagram_reels,
    search_linkedin_posts,
    search_reddit_user,
    search_twitter_profile,
    search_youtube_channel,
)

class ScrapeOrchestrator:
    """
    Manages the entire scraping workflow:
    1. Check last cursor
    2. Fetch new items from platform (via adapter)
    3. Filter/Normalize/Dedupe
    4. Save to source_items + Queue ingest jobs
    5. Update cursor
    """

    def __init__(self, creator_id: int):
        self.creator_id = creator_id

    def run(self, platform_configs: List[Dict[str, Any]]):
        """Execute scrape for provided platforms."""
        results = []
        for config in platform_configs:
            key = config.get("platform_key")
            if not key: continue

            # Create run record
            run_id = db.execute_insert(
                """
                INSERT INTO scrape_runs (creator_id, platform_key, status)
                VALUES (%s, %s, 'RUNNING') RETURNING id
                """,
                (self.creator_id, key)
            )

            try:
                # Get Cursor
                cursor = CursorManager.get_cursor(self.creator_id, key)
                last_id = cursor.get("last_item_id")
                last_time = cursor.get("last_fetched_mp")

                new_items = self._fetch_platform_items(key, config, last_id, last_time)
                
                stats = {
                    "fetched": len(new_items),
                    "new": 0,
                    "deduped": 0,
                    "filtered": 0,
                    "queued": 0
                }

                # Process
                items_processed = []
                for item in new_items:
                    res = ContentManager.save_item(self.creator_id, key, item)
                    if res == "NEW":
                        stats["new"] += 1
                        stats["queued"] += 1
                        items_processed.append(item)
                    elif res == "DUPLICATE":
                        stats["deduped"] += 1
                    elif res == "FILTERED":
                        stats["filtered"] += 1
                
                # Update Cursor (if new items found)
                if items_processed:
                    # Sort by id/time to get latest cursor
                    # Assuming items are chronological or ID-ordered
                    latest = items_processed[-1]
                    new_cursor = {
                        "last_item_id": latest.get("id") or latest.get("source_id") or latest.get("source_url"),
                        "last_fetched_mp": datetime.now(timezone.utc).isoformat()
                    }
                    CursorManager.update_cursor(self.creator_id, key, new_cursor)

                # Finalize Run Record
                db.execute_update(
                    """
                    UPDATE scrape_runs 
                    SET 
                        status = 'COMPLETED', 
                        finished_at = NOW(),
                        items_fetched = %s,
                        items_new = %s,
                        items_deduped = %s,
                        items_filtered_out = %s,
                        jobs_enqueued = %s
                    WHERE id = %s
                    """,
                    (stats["fetched"], stats["new"], stats["deduped"], stats["filtered"], stats["queued"], run_id)
                )
                results.append({"platform": key, "status": "COMPLETED", "stats": stats})

            except Exception as e:
                # Mark failed run
                db.execute_update(
                    "UPDATE scrape_runs SET status = 'FAILED', error_message = %s, finished_at = NOW() WHERE id = %s",
                    (str(e), run_id)
                )
                results.append({"platform": key, "status": "FAILED", "error": str(e)})

        # Final trigger: Update Fingerprint if any new content was found
        if any(r.get("stats", {}).get("new", 0) > 0 for r in results):
             import asyncio
             from backend.services.fingerprint_service import fingerprint_service
             # Fire and forget if running in an async context, 
             # but ScrapeOrchestrator.run might be synchronous.
             # We assume orchestrator is called in background_tasks or threaded.
             loop = asyncio.new_event_loop()
             asyncio.set_event_loop(loop)
             loop.run_until_complete(fingerprint_service.generate_fingerprint_async(self.creator_id))

        return results

    def _fetch_platform_items(
        self,
        platform: str,
        config: Dict[str, Any],
        since_id: Optional[str] = None,
        since_time: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dispatch to the real platform fetcher for the requested config."""
        limit = max(1, int(config.get("maxItems") or 10))
        url = str(config.get("url") or "").strip()
        handle = str(config.get("handle") or config.get("creator_handle") or "").strip()
        time_filter = config.get("timeFilter") if isinstance(config.get("timeFilter"), dict) else None

        if platform == "youtube":
            return search_youtube_channel(url, handle or None, limit=limit, time_filter=time_filter)
        if platform == "youtube_shorts":
            return search_youtube_channel(
                url,
                handle or None,
                limit=limit,
                time_filter=time_filter,
                youtube_shorts_only=True,
            )
        if platform == "instagram":
            instagram_handle = handle.lstrip("@") or self._extract_handle_from_url(url)
            return search_instagram_reels(instagram_handle, limit=limit)
        if platform == "twitter":
            twitter_handle = handle.lstrip("@") or self._extract_handle_from_url(url)
            return search_twitter_profile(twitter_handle, url=url, limit=limit, time_filter=time_filter)
        if platform == "linkedin":
            return search_linkedin_posts(url, limit=limit)
        if platform == "facebook":
            return search_facebook_posts(url, handle or None, limit=limit, time_filter=time_filter)
        if platform == "tiktok":
            return scrape_tiktok_posts(url, handle or None, limit=limit)
        if platform == "reddit":
            return search_reddit_user(url, handle or None, limit=limit, time_filter=time_filter)
        if platform == "custom":
            urls = [line.strip() for line in url.splitlines() if line.strip()]
            return scrape_custom_urls(urls[:limit], creator_handle=handle or "custom", limit=limit)

        raise ValueError(f"Unsupported platform for scraping: {platform}")

    @staticmethod
    def _extract_handle_from_url(url: str) -> str:
        cleaned = str(url or "").strip().rstrip("/")
        if not cleaned:
            return ""
        return cleaned.split("/")[-1].lstrip("@")
