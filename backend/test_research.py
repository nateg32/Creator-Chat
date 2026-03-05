from backend.db import db
from backend.services.research_provider import GeminiResearchProvider

creator_id = 29
profile = db.execute_one('SELECT * FROM creators WHERE id = %s', (creator_id,))
profile['platform_configs'] = db.execute_one('SELECT platform_configs FROM creators WHERE id = %s', (creator_id,))['platform_configs']

rp = GeminiResearchProvider()
results = rp.search("what video would you suggest to start dropshipping", profile)

print("Research Results:")
for r in results:
    print(r)
