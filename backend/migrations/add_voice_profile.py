import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import db


def run_migration():
    print("Migrating creators table: adding voice_profile column...")
    try:
        db.execute_update("""
            ALTER TABLE creators
            ADD COLUMN IF NOT EXISTS voice_profile JSONB DEFAULT '{}'::jsonb
        """)
        print("Successfully added voice_profile column.")
    except Exception as e:
        print(f"Migration error: {e}")


if __name__ == "__main__":
    run_migration()
