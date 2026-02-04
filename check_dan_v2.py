from backend.db import db
res = db.execute_query("SELECT id, handle, display_name FROM creators WHERE handle ILIKE '%danmartell%' OR display_name ILIKE '%danmartell%'")
for r in res:
    print(r)
