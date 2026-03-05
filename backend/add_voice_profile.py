from backend.db import db
import sys
import os

# Add current directory to path so we can import db
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def add_voice_profile_column():
    print("Adding voice_profile column to creators table...")
    try:
        db.execute_update("""
            ALTER TABLE creators 
            ADD COLUMN IF NOT EXISTS voice_profile JSONB DEFAULT '{}'::jsonb
        """)
        print("Successfully added voice_profile column.")
    except Exception as e:
        print(f"Error adding column: {e}")

if __name__ == "__main__":
    add_voice_profile_column()
