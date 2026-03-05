import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.db import db

def run_migration():
    print("Migrating scrape_items table for Duplicates...")
    try:
        db.execute_update("""
            ALTER TABLE scrape_items 
            ADD COLUMN IF NOT EXISTS canonical_key TEXT,
            ADD COLUMN IF NOT EXISTS duplicate_of_item_id UUID,
            ADD COLUMN IF NOT EXISTS duplicate_method VARCHAR(50),
            ADD COLUMN IF NOT EXISTS duplicate_confidence FLOAT,
            ADD COLUMN IF NOT EXISTS is_primary BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS content_fingerprint BIGINT
        """)
        
        db.execute_update("""
            CREATE INDEX IF NOT EXISTS idx_scrape_items_canonical_key ON scrape_items(canonical_key);
        """)
        
        db.execute_update("""
            CREATE INDEX IF NOT EXISTS idx_scrape_items_content_fingerprint ON scrape_items(content_fingerprint);
        """)
        
        print("Successfully migrated database for Duplicates.")
    except Exception as e:
        print(f"Migration error: {e}")

if __name__ == "__main__":
    run_migration()
