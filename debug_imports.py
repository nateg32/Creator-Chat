import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

print(f"Python: {sys.version}")

try:
    print("Importing numpy...")
    import numpy
    print(f"Numpy imported: {numpy.__version__}")
except ImportError as e:
    print(f"Numpy fail: {e}")
except Exception as e:
    print(f"Numpy crash: {e}")

try:
    print("Importing openai...")
    import openai
    print("OpenAI imported.")
except ImportError as e:
    print(f"OpenAI fail: {e}")
except Exception as e:
    print(f"OpenAI crash: {e}")

try:
    print("Importing simple_vector_store...")
    import backend.core.simple_vector_store as svs
    print("Vector store imported.")
except ImportError as e:
    # Try alternate path
    try:
        sys.path.append(os.path.join(os.getcwd(), "backend"))
        from core.simple_vector_store import SimpleJSONVectorStore
        print("Vector store imported (alt path).")
    except ImportError as e2:
        print(f"Vector store fail: {e2}")
    except Exception as e2:
        print(f"Vector store crash: {e2}")

try:
    print("Importing memory_integration...")
    from core.memory_integration import MemoryIntegration
    print("Memory integration imported.")
except ImportError as e:
    print(f"Memory integration fail: {e}")
except Exception as e:
    print(f"Memory integration crash: {e}")
