import os
import json
import psycopg
from psycopg.rows import dict_row

# Mock settings/db setup
DB_NAME = "creator_bot"
DB_USER = "postgres"
DB_PASS = "Kipkogey2019!"
DB_HOST = "localhost"

def check_db():
    try:
        conn = psycopg.connect(f"dbname={DB_NAME} user={DB_USER} password={DB_PASS} host={DB_HOST}")
        cur = conn.cursor(row_factory=dict_row)
        
        print("Checking creators...")
        cur.execute("SELECT id, name, handle FROM creators")
        creators = cur.fetchall()
        for r in creators:
            print(f"Creator: {r['id']} - {r['name']} (@{r['handle']})")
            
            print(f"  Checking 'market structure' documents for creator {r['id']}...")
            cur.execute("SELECT title, url FROM documents WHERE creator_id = %s AND title ILIKE '%%market structure%%' LIMIT 5", (r['id'],))
            docs = cur.fetchall()
            for d in docs:
                print(f"    - {d['title']} ({d['url']})")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_db()
