import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_query(question):
    print(f"\n[TEST] Query: {question}")
    payload = {
        "creator_id": 1,
        "question": question,
        "top_k": 3
    }
    start = time.time()
    response = requests.post(f"{BASE_URL}/ask-stream", json=payload, stream=True)
    
    first_token_time = None
    for line in response.iter_content(chunk_size=1):
        if line:
            if first_token_time is None:
                first_token_time = time.time()
                print(f"Time to First Byte: {first_token_time - start:.2f}s")
            break
    
    # Read rest
    for line in response.iter_lines():
        pass
    print(f"Total Time: {time.time() - start:.2f}s")

if __name__ == "__main__":
    test_query("yo i wanna start trading")
    test_query("hello")
    test_query("what is the best way to scale a business?")
