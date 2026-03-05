from backend.db import db
import backend.rag as rag
from backend.core.interaction_engine import InteractionEngine
import json
import os
from openai import OpenAI
from backend.settings import settings

client = OpenAI(api_key=settings.OPENAI_API_KEY)

creator_id = 29
query = "yo what video would u suggest to start dropshipping"

emb = client.embeddings.create(model=settings.EMBEDDING_MODEL, input=query)
q_emb = emb.data[0].embedding
chunks = rag.retrieve_chunks(creator_id, q_emb, top_k=5)

out = f"Testing retrieval for query: {query}\n"

for i, c in enumerate(chunks):
    out += f"--- Chunk {i+1} ---\n"
    out += f"URL: {c.get('url')}\n"
    out += f"Snippet: {c.get('content')[:100]}...\n"

# Get creator profile
profile = db.execute_one('SELECT * FROM creators WHERE id = %s', (creator_id,))
profile['platform_configs'] = db.execute_one('SELECT platform_configs FROM creators WHERE id = %s', (creator_id,))['platform_configs']

ie = InteractionEngine()
plan = ie.build_interaction_plan(user_msg=query, history=[], creator_profile=profile, rag_chunks=chunks)
out += "\nInteraction Plan:\n"
out += json.dumps(plan.dict(), indent=2) + "\n"

out += "\nCombined System Prompt:\n"
prompt = ie._build_combined_system_prompt(
    creator_profile=profile,
    rag_chunks=chunks,
    creator_id=creator_id,
    user_id=1,
    thread_id="test",
    user_name="User",
    persona=None,
    history=[],
    user_preferences=None
)
out += prompt

with open("debug_out.txt", "w", encoding="utf-8") as f:
    f.write(out)

print("Done. Saved to debug_out.txt")
