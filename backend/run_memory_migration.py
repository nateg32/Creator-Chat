
from db import db
import os

def migrate():
    print("Running migration: Creating conversation_memories table...")
    sql_path = os.path.join(os.path.dirname(__file__), "migrations", "008_conversation_memory.sql")
    try:
        with open(sql_path, "r") as f:
            sql = f.read()
        
        # Split by command if necessary, but execute_update likely handles multiple statements if supported by driver
        # but let's be safe and split by ; if psylcopg2 doesn't handle multiple
        # Actually simplest is to execute full block
        db.execute_update(sql)
        print("Successfully created conversation_memories table.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
