from backend.db import db

def migrate():
    print("Running migration: Adding style_fingerprint to creators table...")
    try:
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS style_fingerprint JSONB NOT NULL DEFAULT '{}'::jsonb
        """)
        print("Successfully added style_fingerprint column.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
