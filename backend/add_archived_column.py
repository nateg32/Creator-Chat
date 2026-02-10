from db import Database

try:
    db = Database()
    print("Adding is_archived column...")
    db.execute_update("ALTER TABLE chat_threads ADD COLUMN IF NOT EXISTS is_archived BOOLEAN DEFAULT FALSE")
    print("Done")
except Exception as e:
    print(f"Error: {e}")
