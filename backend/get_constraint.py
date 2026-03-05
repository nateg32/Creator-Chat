from backend.db import db
res = db.execute_query("SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'scrape_items_transcript_status_check'")
print(res[0]['def'])
