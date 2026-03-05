import sys, os, json
sys.path.append(os.path.join(os.getcwd(), 'backend'))
from db import db

def main():
    creator = db.execute_one("SELECT id FROM creators ORDER BY id DESC LIMIT 1")
    rows = db.execute_query("SELECT id, title, source_id, source, metadata FROM documents WHERE creator_id = %s LIMIT 5", (creator['id'],))
    output = []
    for r in rows:
        meta = r.get('metadata')
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except: pass
        output.append({
            "title": r.get('title'),
            "source_id": r.get('source_id'),
            "source": r.get('source'),
            "metadata": meta
        })
    with open('db_url_out.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)

if __name__ == "__main__":
    main()
