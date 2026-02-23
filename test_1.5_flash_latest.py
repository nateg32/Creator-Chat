import google.generativeai as genai
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

model = genai.GenerativeModel('gemini-1.5-flash-latest')
print("Testing gemini-1.5-flash-latest with google_search_retrieval...")
try:
    response = model.generate_content("Who won the Super Bowl in 2025?", tools=[{"google_search_retrieval": {}}])
    print("SUCCESS!")
    print(response.text)
except Exception as e:
    print(f"FAIL: {e}")
