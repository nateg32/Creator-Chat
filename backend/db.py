import os
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from typing import Optional, List, Dict, Any
from backend.settings import settings

class Database:
    def __init__(self):
        self._pool = None
    
    @property
    def pool(self) -> ConnectionPool:
        """Lazy initialization of connection pool"""
        if self._pool is None:
            # Prefer managed connection strings in cloud environments (e.g. Render).
            conninfo = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
            if not conninfo:
                conninfo = (
                    f"host={settings.DB_HOST} port={settings.DB_PORT} "
                    f"dbname={settings.DB_NAME} user={settings.DB_USER} password={settings.DB_PASSWORD}"
                )
            # min_size 1, max_size 10 provides enough leeway for async workers without draining PG bounds
            self._pool = ConnectionPool(conninfo=conninfo, min_size=1, max_size=10)
        return self._pool
    
    def get_cursor(self):
        """Not safely supported with pool pattern context-managers. Use execute_ APIs instead."""
        raise NotImplementedError("Use execute_query, execute_update, or execute_insert instead of raw cursors with ConnectionPool.")
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Execute SELECT query and return results as list of dicts"""
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                try:
                    cur.execute(query, params)
                    return cur.fetchall()
                except Exception as e:
                    print(f"[DB] execute_query error: {e}")
                    print(f"[DB] Query: {query}")
                    conn.rollback()
                    raise
    
    def execute_one(self, query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
        """Execute SELECT query and return single result"""
        results = self.execute_query(query, params)
        return results[0] if results else None
    
    def execute_update(self, query: str, params: tuple = None) -> int:
        """Execute INSERT/UPDATE/DELETE and return rowcount"""
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                try:
                    cur.execute(query, params)
                    conn.commit()
                    return cur.rowcount
                except Exception as e:
                    print(f"[DB] execute_update error: {e}")
                    print(f"[DB] Query: {query}")
                    conn.rollback()
                    raise
    
    def execute_insert(self, query: str, params: tuple = None) -> Any:
        """Execute INSERT and return inserted ID (assuming RETURNING clause)"""
        with self.pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                try:
                    cur.execute(query, params)
                    conn.commit()
                    result = cur.fetchone()
                    return result[list(result.keys())[0]] if result else None
                except Exception as e:
                    print(f"[DB] execute_insert error: {e}")
                    print(f"[DB] Query: {query}")
                    conn.rollback()
                    raise
    
    def close(self):
        """Close connection pool"""
        if self._pool:
            self._pool.close()

# Global database instance
db = Database()

def get_pool():
    return db.pool
