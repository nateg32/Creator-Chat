import google.generativeai as genai
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent / "backend"
load_dotenv(BASE_DIR / ".env", override=True)

api_key = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=api_key)

def test_tool(model_name, tool_name):
    print(f"Testing {model_name} with {tool_name}...")
    model = genai.GenerativeModel(model_name)
    try:
        # Create tool object explicitly
        from google.generativeai import protos
        if tool_name == "google_search":
            tool = protos.Tool(google_search=protos.GoogleSearch())
        else:
            tool = protos.Tool(google_search_retrieval=protos.GoogleSearchRetrieval())
        
        response = model.generate_content("Current stock price of NVDA", tools=[tool])
        print(f"SUCCESS: {model_name} + {tool_name}")
        return True
    except Exception as e:
        print(f"FAIL: {model_name} + {tool_name} | Error: {str(e)[:100]}")
        return False

models_to_test = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-flash-latest", "gemini-2.5-flash"]
tools_to_test = ["google_search", "google_search_retrieval"]

for m in models_to_test:
    for t in tools_to_test:
        test_tool(m, t)
        print("-" * 20)
