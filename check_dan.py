from backend.db import db
res = db.execute_query("SELECT id, name, handle FROM creators WHERE handle ILIKE '%danmartell%' OR name ILIKE '%danmartell%'")
for r in res:
    print(r)
