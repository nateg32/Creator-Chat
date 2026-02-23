
import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db
rows = db.execute_query("SELECT column_name FROM information_schema.columns WHERE table_name = 'chunks'")
for r in rows:
    print(r['column_name'])
