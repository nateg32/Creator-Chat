from backend.db import db
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_migration():
    print("Migrating creators table for Config Versioning...")
    try:
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS config_version INT NOT NULL DEFAULT 1
        """)
        
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS last_approved_version INT NOT NULL DEFAULT 0
        """)
        
        print("Successfully added config_version and last_approved_version columns.")
    except Exception as e:
        print(f"Migration error: {e}")

if __name__ == "__main__":
    run_migration()
