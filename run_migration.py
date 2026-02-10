import sys
import os
import psycopg
from urllib.parse import urlparse

# Add backend directory to path so imports work
current_dir = os.path.dirname(os.path.abspath(__file__))
# Note: if running from root, 'backend' is subdir. 
# If running relative to file, we might need adjustment.
sys.path.append(os.path.join(current_dir, 'backend'))

from settings import settings

def run_migration():
    print(f"Connecting to DB: {settings.DB_HOST}")
    
    # Read migration file
    with open(os.path.join(current_dir, 'backend/migrations/006_chat_threads.sql'), 'r') as f:
        sql = f.read()

    conn_str = f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
    try:
        conn = psycopg.connect(conn_str)
        cur = conn.cursor()
        print("Executing migration...")
        cur.execute(sql)
        conn.commit()
        print("Migration successful!")
        cur.close()
    except Exception as e:
        print(f"Error: {e}")
        # psycopg 3 rolls back automatically on exception context usually, but explicit is fine
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    run_migration()
