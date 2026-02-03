import psycopg
from psycopg.rows import dict_row
from typing import Optional, List, Dict, Any
from .settings import settings

class Database:
    def __init__(self):
        self.conn = None
    
    def connect(self):
        """Create database connection"""
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                dbname=settings.DB_NAME,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD
            )
        return self.conn
    
    def get_cursor(self):
        """Get a cursor with dict_row for dict-like results"""
        return self.connect().cursor(row_factory=dict_row)
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        """Execute SELECT query and return results as list of dicts"""
        with self.connect().cursor(row_factory=dict_row) as cur:
            try:
                cur.execute(query, params)
                return cur.fetchall()
            except Exception:
                self.conn.rollback()
                raise
    
    def execute_one(self, query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
        """Execute SELECT query and return single result"""
        results = self.execute_query(query, params)
        return results[0] if results else None
    
    def execute_update(self, query: str, params: tuple = None) -> int:
        """Execute INSERT/UPDATE/DELETE and return rowcount"""
        with self.connect().cursor(row_factory=dict_row) as cur:
            try:
                cur.execute(query, params)
                self.conn.commit()
                return cur.rowcount
            except Exception:
                self.conn.rollback()
                raise
    
    def execute_insert(self, query: str, params: tuple = None) -> Any:
        """Execute INSERT and return inserted ID (assuming RETURNING clause)"""
        with self.connect().cursor(row_factory=dict_row) as cur:
            try:
                cur.execute(query, params)
                self.conn.commit()
                result = cur.fetchone()
                return result[list(result.keys())[0]] if result else None
            except Exception:
                self.conn.rollback()
                raise
    
    def close(self):
        """Close connection"""
        if self.conn and not self.conn.closed:
            self.conn.close()

# Global database instance
db = Database()
