from db import db
creators = db.execute_query("SELECT id, name FROM creators")
for c in creators:
    print(c)
