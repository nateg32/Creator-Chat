import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db import db

def migrate():
    print("Starting production pipeline schema migration v2...")
    
    queries = [
        # 1. Creators table enhancements
        """
        ALTER TABLE creators 
        ADD COLUMN IF NOT EXISTS stronghold_json JSONB DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS curiosity_profile_json JSONB DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS rhythm_profile_json JSONB DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS forbidden_phrases_json JSONB DEFAULT '[]';
        """,
        
        # 2. Conversation State enhancements
        """
        ALTER TABLE conversation_state
        ADD COLUMN IF NOT EXISTS user_state_json JSONB DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS last_router_meta_json JSONB DEFAULT '{}',
        ADD COLUMN IF NOT EXISTS memory_json JSONB DEFAULT '{}';
        """,
        
        # 3. Chat Messages table enhancements
        """
        ALTER TABLE chat_messages
        ADD COLUMN IF NOT EXISTS meta_json JSONB DEFAULT '{}';
        """
    ]
    
    for query in queries:
        try:
            db.execute_update(query)
            print(f"Executed: {query.strip().splitlines()[0]}...")
        except Exception as e:
            print(f"Error: {e}")
            
    print("Migration v2 complete.")

if __name__ == "__main__":
    migrate()
