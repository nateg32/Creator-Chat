from backend.db import db

q = """
SELECT title, url 
FROM content_metadata 
WHERE creator_id = 29 AND title ILIKE '%winning%'
"""

try:
    docs = db.execute_query(q)
    for d in docs:
        print(d)
except Exception as e:
    print(e)
