from datetime import datetime, timezone
import random
import time
from typing import List, Dict, Any, Optional
from db import db
from services.content_manager import ContentManager
from services.cursor_manager import CursorManager
from config.platforms import PLATFORMS, get_platform

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

                # Mock Scrape (Replace with actual API call)
                # In real code, call: adapter.fetch(config, since_id=last_id)
                new_items = self._mock_fetch(key, config, last_id)
                
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
                        "last_item_id": latest.get("id"),
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

        return results

    def _mock_fetch(self, platform: str, config: Dict, since_id=None) -> List[Dict]:
        """Placeholder for actual platform API calls."""
        # This would import correct adapter based on platform key
        # Return dummy list for now to demonstrate pipeline flow
        time.sleep(1) # Simulate network
        return [
            {
                "id": f"{platform}_post_{random.randint(1000,9999)}",
                "text": f"New content from {platform} about AI agents. #tech",
                "url": f"https://{platform}.com/post/123",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "author_id": config.get("handle")
            },
            {
                 "id": f"{platform}_post_{9999}", # Simulate duplicate if needed
                 "text": "Old content",
                 "url": f"https://{platform}.com/post/old",
                 "published_at": "2023-01-01T00:00:00Z"
            }
        ]
