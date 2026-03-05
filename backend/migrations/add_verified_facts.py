from backend.db import db

def migrate():
    print("Running migration: Creating verified_facts table...")
    try:
        # Create verified_facts table
        db.execute_update("""
            CREATE TABLE IF NOT EXISTS verified_facts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                creator_id INT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                fact_key TEXT NOT NULL,
                value TEXT NOT NULL,
                confidence TEXT NOT NULL CHECK (confidence IN ('HIGH', 'MEDIUM', 'LOW')),
                source_hashes JSONB DEFAULT '[]'::jsonb,
                last_verified_at TIMESTAMPTZ DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(creator_id, fact_key)
            )
        """)
        print("Successfully created verified_facts table.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
