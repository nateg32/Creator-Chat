from datetime import datetime, timezone
from typing import Dict, Any, Optional
from db import db
import json

class CursorManager:
    """
    Manages incremental scrape cursors (pagination tokens, last seeen IDs, timestamps).
    """

    @staticmethod
    def get_cursor(creator_id: int, platform_key: str) -> Dict[str, Any]:
        """Get the current cursor for a creator + platform."""
        row = db.execute_one(
            "SELECT cursor_data FROM scrape_cursors WHERE creator_id = %s AND platform_key = %s",
            (creator_id, platform_key)
        )
        return row['cursor_data'] if row and row.get('cursor_data') else {}

    @staticmethod
    def update_cursor(creator_id: int, platform_key: str, cursor_data: Dict[str, Any]):
        """Update the cursor state."""
        # Merge with existing cursor data to preserve other fields if needed
        current = CursorManager.get_cursor(creator_id, platform_key)
        updated = {**current, **cursor_data}
        
        db.execute_update(
            """
            INSERT INTO scrape_cursors (creator_id, platform_key, cursor_data, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (creator_id, platform_key) 
            DO UPDATE SET cursor_data = EXCLUDED.cursor_data, updated_at = NOW()
            """,
            (creator_id, platform_key, json.dumps(updated))
        )
