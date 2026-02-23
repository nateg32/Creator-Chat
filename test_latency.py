import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_ask():
    print("Testing /ask (Non-streaming)...")
    payload = {
        "creator_id": 1,
        "question": "What is the best way to scale a business?",
        "top_k": 3
    }
    start = time.time()
    response = requests.post(f"{BASE_URL}/ask", json=payload)
    end = time.time()
    print(f"Total Time: {end - start:.2f}s")
    # print(f"Answer: {response.json().get('answer', '')[:100]}...")

def test_ask_stream():
    print("\n[STREAMING] Testing /ask-stream...")
    payload = {
        "creator_id": 1,
        "question": "What is the best way to scale a business?",
        "top_k": 3
    }
    start = time.time()
    response = requests.post(f"{BASE_URL}/ask-stream", json=payload, stream=True)
    
    first_token_time = None
    full_text = ""
    
    for line in response.iter_content(chunk_size=1):
        if line:
            if first_token_time is None:
                first_token_time = time.time()
                print(f"Time to First Byte: {first_token_time - start:.2f}s")
            
            # Note: iter_content(1) is slow for parsing SSE, but good for TTFB.
            # We'll switch back to something more efficient after TTFB check if needed.
            pass

    # Re-run with proper SSE parsing for total time
    response = requests.post(f"{BASE_URL}/ask-stream", json=payload, stream=True)
    start = time.time()
    first_data_time = None
    for line in response.iter_lines():
        if line:
            if first_data_time is None:
                first_data_time = time.time()
                print(f"Time to First Data Chunk: {first_data_time - start:.2f}s")
            # Parse data...
    print(f"Streaming Total: {time.time() - start:.2f}s")

if __name__ == "__main__":
    try:
        test_ask()
        test_ask_stream()
    except Exception as e:
        print(f"Test failed: {e}. Make sure the backend is running.")
