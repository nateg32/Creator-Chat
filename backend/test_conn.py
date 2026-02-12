import requests
try:
    print("Testing connection to http://127.0.0.1:8000/health...")
    r = requests.get("http://127.0.0.1:8000/health", timeout=2)
    print(f"Status: {r.status_code}")
    print(f"Content: {r.text}")
except Exception as e:
    print(f"Error: {e}")
