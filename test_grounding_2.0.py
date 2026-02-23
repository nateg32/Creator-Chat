import google.generativeai as genai
from google.generativeai import protos
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

model = genai.GenerativeModel('gemini-2.0-flash')

def test_grounding():
    print("Testing gemini-2.0-flash with google_search...")
    try:
        # Construct the tool object exactly as the SDK expects
        tool = protos.Tool(google_search=protos.Tool.GoogleSearch())
        response = model.generate_content("Who won the Super Bowl in 2025?", tools=[tool])
        print("SUCCESS!")
        print(response.text)
    except Exception as e:
        print(f"FAIL: {e}")

test_grounding()
