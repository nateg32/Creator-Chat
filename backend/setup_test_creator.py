from db import db
import json

creator_id = 26
stronghold = json.dumps({
    "allowed_domains": ["trading", "psychology", "risk management", "markets"],
    "forbidden_topics": ["politics", "medical advice", "cooking"]
})
rhythm = json.dumps({
    "dm_chunk_style": "two_block",
    "avg_sentence_words": 12,
    "connector_avoidance": ["therefore", "moreover"]
})
curiosity = json.dumps({
    "early_stage_questions": [
        "What are you selling — product, service, or content?",
        "Do you have a skill, an audience, or capital?",
        "Are you trying to make money fast, or build something long-term?"
    ]
})

db.execute_update("UPDATE creators SET name='Expert Trader', display_name='Expert Trader', stronghold_json=%s, rhythm_profile_json=%s, curiosity_profile_json=%s WHERE id=%s", (stronghold, rhythm, curiosity, creator_id))
db.execute_update("DELETE FROM documents WHERE creator_id=%s AND source='persona'", (creator_id,))
db.execute_update("INSERT INTO documents (creator_id, content, source, title) VALUES (%s, %s, 'persona', 'Expert Trader Persona')", (creator_id, 'Professional trader persona.'))
db.execute_update("INSERT INTO documents (creator_id, content, source, title) VALUES (%s, %s, 'video', 'Risk Management')", (creator_id, 'Risk management is key.'))

print(f"Done for {creator_id}")
