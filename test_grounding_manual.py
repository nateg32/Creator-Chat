import google.generativeai as genai
from google.generativeai import protos
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

print("Testing with manually initialized GoogleSearch field...")
try:
    tool = protos.Tool()
    # Access the field to initialize it if it's a message
    _ = tool.google_search
    
    model = genai.GenerativeModel('gemini-2.0-flash', tools=tool)
    response = model.generate_content("Who won the Super Bowl in 2025?")
    print("SUCCESS!")
    print(response.text)
except Exception as e:
    print(f"FAIL: {e}")
