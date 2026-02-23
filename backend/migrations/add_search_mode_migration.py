import sys
import os

# Add parent directory (backend root) to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import db

def run_migration():
    print("Migrating creators table for search_mode...")
    try:
        # Add search_mode column, defaulting to 'hybrid' (Ingested + Web Search)
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS search_mode VARCHAR(50) DEFAULT 'hybrid'
        """)
        
        print("Successfully migrated database for search_mode.")
    except Exception as e:
        print(f"Migration error: {e}")

if __name__ == "__main__":
    run_migration()
