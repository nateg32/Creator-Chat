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
    "contents": [
        {
            "parts": [
                {"text": "Who won the Super Bowl in 2025?"}
            ]
        }
    ],
    "tools": [
        {
            "google_search": {}
        }
    ]
}

print("Testing Gemini 2.0 Flash Grounding via REST API...")
response = requests.post(url, json=payload)
print(f"Status: {response.status_code}")
if response.status_code == 200:
    print("SUCCESS!")
    print(json.dumps(response.json(), indent=2))
else:
    print(f"FAIL: {response.text}")
