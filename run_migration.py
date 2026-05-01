import sys
import os
import psycopg

# Add backend directory to path so imports work
current_dir = os.path.dirname(os.path.abspath(__file__))
# Note: if running from root, 'backend' is subdir. 
# If running relative to file, we might need adjustment.
sys.path.append(os.path.join(current_dir, 'backend'))

from settings import settings


def _resolve_migration_path(raw_name: str) -> str:
    candidate = raw_name.strip()
    if not candidate:
        raise ValueError("Migration name is required")

    if os.path.isabs(candidate):
        return candidate

    if not candidate.endswith(".sql"):
        candidate = f"{candidate}.sql"

    if os.path.dirname(candidate):
        return os.path.join(current_dir, candidate)

    return os.path.join(current_dir, "backend", "migrations", candidate)

def run_migration(migration_name: str):
    migration_path = _resolve_migration_path(migration_name)
    print(f"Connecting to DB: {settings.DB_HOST}")
    print(f"Using migration: {migration_path}")

    with open(migration_path, 'r', encoding='utf-8') as f:
        sql = f.read()

    conn_str = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or f"postgresql://{settings.DB_USER}:{settings.DB_PASSWORD}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
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
    migration_name = sys.argv[1] if len(sys.argv) > 1 else "006_chat_threads.sql"
    run_migration(migration_name)
