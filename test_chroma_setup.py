import sys
try:
    import chromadb
    print(f"ChromaDB imported successfully. Version: {chromadb.__version__}")
    
    # Try creating a client
    client = chromadb.PersistentClient(path="./test_chroma_db")
    print("PersistentClient created successfully.")
    
    collection = client.get_or_create_collection("test_collection")
    collection.add(documents=["hello world"], ids=["id1"])
    results = collection.query(query_texts=["hello"], n_results=1)
    print(f"Query result: {results}")
    
except ImportError as e:
    print(f"Import Error: {e}")
except Exception as e:
    print(f"Runtime Error: {e}")
