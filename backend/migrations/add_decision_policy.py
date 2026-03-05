
from backend.db import db

def migrate():
    print("Running migration: Adding decision_policy to creators table...")
    try:
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS decision_policy JSONB NOT NULL DEFAULT '{}'::jsonb
        """)
        print("Successfully added decision_policy column.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
