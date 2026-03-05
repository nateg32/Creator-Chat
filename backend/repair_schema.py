import sys
import os

# Add the current directory to sys.path to ensure db can be imported
sys.path.append(os.getcwd())

try:
    from backend.db import db
    print("DB imported successfully.")
    
    sql = """
    ALTER TABLE creators 
    ADD COLUMN IF NOT EXISTS identity_fingerprint JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS fingerprint_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS fingerprint_status VARCHAR(50) DEFAULT 'idle';
    """
    
    db.execute_update(sql)
    print("Migration: Successfully ensured fingerprint columns exist.")
    
except Exception as e:
    print(f"Error during migration: {e}")
    sys.exit(1)
