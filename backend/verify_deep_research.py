
import asyncio
import json
import sys
import os

# Add current dir to path for imports
sys.path.append(os.getcwd())

from db import db
from services.fingerprint_service import fingerprint_service

async def verify():
    print("--- DEEP RESEARCH 2.0 VERIFICATION ---")
    
    # 1. Find Jordan Welch
    row = db.execute_one("SELECT id, display_name FROM creators WHERE display_name LIKE '%%Jordan%%' OR handle LIKE '%%Jordan%%' LIMIT 1")
    if not row:
        print("Error: Jordan Welch not found in database.")
        return
    
    creator_id = row['id']
    name = row['display_name']
    print(f"Found Creator: {name} (ID: {creator_id})")
    
    # 2. Trigger Fresh Research
    print("\nTriggering Fresh Research (Phase 1-5 + 12-Layer Blueprint)...")
    await fingerprint_service.generate_fingerprint_async(creator_id, refresh=True)
    
    # 3. Verify Results
    print("\nResearch Complete. Verifying Artifacts...")
    
    final_row = db.execute_one("SELECT soul_md, research_summary, identity_fingerprint, style_fingerprint FROM creators WHERE id = %s", (creator_id,))
    
    soul_md = final_row.get("soul_md")
    summary = final_row.get("research_summary")
    
    if not soul_md or not summary:
        print("Error: Research failed to populate soul_md or research_summary.")
        return

    # Check for 12-layer blueprint
    layers = [
        "CORE IDENTITY", "BEHAVIORAL PATTERNS", "LINGUISTIC DNA", 
        "STRUCTURAL RESPONSE", "COGNITIVE STYLE", "HUMOR DETECTION",
        "CONFLICT & BOUNDARY", "PUBLIC IDENTITY GUARDRAILS", "PERSONA INTEGRITY",
        "EMOTIONAL SIGNATURE", "AUDIENCE PERCEPTION", "POWER DYNAMICS"
    ]
    
    found_layers = [l for l in layers if l in soul_md.upper()]
    print(f"Soul.md Layers Found: {len(found_layers)}/12")
    
    # Check for Investigative Dossier
    if isinstance(summary, str):
        summary = json.loads(summary)
    
    dossier = summary.get("investigative_dossier", {})
    if dossier:
        print("Investigative Dossier: FOUND")
        print(f"  - Biography: {dossier.get('biography', {}).get('age', 'Unknown')}")
        print(f"  - Specific Wins: {len(dossier.get('specific_wins', []))} products found")
    else:
        print("Investigative Dossier: MISSING")

    print("\n--- VERIFICATION FINISHED ---")

if __name__ == "__main__":
    db.connect()
    asyncio.run(verify())
