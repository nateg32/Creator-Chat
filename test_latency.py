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
    
    for line in response.iter_lines():
        if line:
            if first_token_time is None:
                first_token_time = time.time()
                print(f"Time to First Byte: {first_token_time - start:.2f}s")
            
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                data_str = line_str[6:]
                if data_str != '[DONE]':
                    try:
                        data = json.loads(data_str)
                        if 'content' in data:
                            full_text += data['content']
                            print(data['content'], end='', flush=True)
                        elif 'error' in data:
                            print(f"\n[ERROR] {data['error']}", flush=True)
                            if 'traceback' in data:
                                print(f"\n[TRACE] {data['traceback']}", flush=True)
                    except json.JSONDecodeError:
                        pass
    
    print(f"\nStreaming Total: {time.time() - start:.2f}s")

if __name__ == "__main__":
    try:
        test_ask()
        test_ask_stream()
    except Exception as e:
        print(f"Test failed: {e}. Make sure the backend is running.")
