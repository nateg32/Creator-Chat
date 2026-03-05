from backend.db import db
import sys
import os

# Add current directory to path so we can import db
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def create_conversation_state_table():
    print("Creating conversation_state table...")
    try:
        db.execute_update("""
            CREATE TABLE IF NOT EXISTS conversation_state (
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
                known_slots JSONB DEFAULT '{}'::jsonb,
                last_question JSONB DEFAULT '{}'::jsonb,
                last_intent TEXT,
                verbosity_pref TEXT DEFAULT 'short',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (user_id, creator_id)
            )
        """)
        print("Successfully created conversation_state table.")
    except Exception as e:
        print(f"Error creating table: {e}")

if __name__ == "__main__":
    create_conversation_state_table()
