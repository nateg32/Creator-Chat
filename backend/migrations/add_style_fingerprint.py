from db import db
import sys
import os

# Add parent directory (backend root) to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_migration():
    print("Migrating creators table for Style Fingerprint...")
    try:
        # 1. Add identity_fingerprint for biographical layer
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS identity_fingerprint JSONB DEFAULT '{}'::jsonb
        """)
        
        # 2. Add tracking for update timestamps
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS fingerprint_updated_at TIMESTAMPTZ
        """)
        
        # 3. Add status column for generation tracking
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS fingerprint_status VARCHAR(50) DEFAULT 'idle'
        """)
        
        print("Successfully migrated database for Style Fingerprint.")
    except Exception as e:
        print(f"Migration error: {e}")

if __name__ == "__main__":
    run_migration()
