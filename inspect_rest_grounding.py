import requests
import os
import json
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"

payload = {
    "contents": [{"parts": [{"text": "NVDA stock price"}]}],
    "tools": [{"google_search": {}}]
}

response = requests.post(url, json=payload)
data = response.json()

# Look for grounding metadata
if "candidates" in data:
    cand = data["candidates"][0]
    if "groundingMetadata" in cand:
        print("--- GROUNDING METADATA ---")
        print(json.dumps(cand["groundingMetadata"], indent=2))
    
    print("--- RESPONSE TEXT ---")
    print(cand["content"]["parts"][0]["text"])
else:
    print(f"Error: {data}")
