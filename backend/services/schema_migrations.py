"""Small idempotent SQL migration runner for additive app-owned migrations."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from backend.db import db
from backend.settings import settings

logger = logging.getLogger(__name__)


def apply_sql_migration(filename: str) -> bool:
    """Apply one SQL migration from backend/migrations once.

    The repository already has several hand-run migration styles. This runner is
    intentionally tiny so startup can apply the new v2 foundation without
    attempting to replay old files with conflicting assumptions.
    """

    migration_path = settings.BASE_DIR / "migrations" / filename
    sql = migration_path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()

    db.execute_update(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    row = db.execute_one(
        "SELECT checksum FROM schema_migrations WHERE filename = %s",
        (filename,),
    )
    if row:
        if row.get("checksum") != checksum:
            logger.warning(
                "[MIGRATION] %s checksum changed after it was applied; leaving DB unchanged.",
                filename,
            )
        return False

    with db.pool.connection() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO schema_migrations (filename, checksum)
                    VALUES (%s, %s)
                    """,
                    (filename, checksum),
                )
                conn.commit()
                logger.info("[MIGRATION] Applied %s", filename)
                return True
            except Exception:
                conn.rollback()
                logger.exception("[MIGRATION] Failed to apply %s", filename)
                raise
