"""
Create system_jobs and creator_documents tables for the durable worker queue.
Also adds UNIQUE constraint on documents(source, source_id) if missing.
"""
import sys
import os

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from backend.db import db

MIGRATION_SQL = [
    # 1. system_jobs — the durable job queue table
    """
    CREATE TABLE IF NOT EXISTS system_jobs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        job_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        creator_id INTEGER,
        payload JSONB DEFAULT '{}',
        progress_percent INTEGER DEFAULT 0,
        message TEXT DEFAULT '',
        error_log TEXT,
        retry_count INTEGER DEFAULT 0,
        locked_at TIMESTAMPTZ,
        locked_by TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    # 2. Index for fast claim query
    """
    CREATE INDEX IF NOT EXISTS idx_system_jobs_status_created
    ON system_jobs (status, created_at ASC);
    """,
    # 3. creator_documents — join table for global doc caching
    """
    CREATE TABLE IF NOT EXISTS creator_documents (
        creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
        document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        PRIMARY KEY (creator_id, document_id)
    );
    """,
    # 4. Enforce UNIQUE(source, source_id) on documents
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'documents_source_source_id_unique'
        ) THEN
            ALTER TABLE documents ADD CONSTRAINT documents_source_source_id_unique UNIQUE (source, source_id);
        END IF;
    END $$;
    """,
]

def migrate():
    print("Running worker queue schema migration...")
    for sql in MIGRATION_SQL:
        try:
            db.execute_update(sql)
            print(f"  OK: {sql.strip().splitlines()[0][:60]}")
        except Exception as e:
            print(f"  WARN: {e}")
    print("Migration complete.")

if __name__ == "__main__":
    migrate()
