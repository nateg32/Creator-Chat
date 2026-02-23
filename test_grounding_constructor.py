import google.generativeai as genai
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

print("Testing with tools in constructor...")
try:
    # Use the string directly if possible, or the list of dicts
    # Try the list of dicts approach which failed in generate_content
    model = genai.GenerativeModel('gemini-2.0-flash', tools=[{"google_search": {}}])
    response = model.generate_content("Who won the Super Bowl in 2025?")
    print("SUCCESS!")
    print(response.text)
except Exception as e:
    print(f"FAIL: {e}")
