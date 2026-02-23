from pathlib import Path
from dotenv import load_dotenv
import os
from typing import Optional

# Load env from backend/.env
BASE_DIR = Path(__file__).resolve().parent
# override=True ensures values in backend/.env win over any existing environment values
load_dotenv(BASE_DIR / ".env", override=True)

class Settings:
    BASE_DIR: Path = BASE_DIR
    # Database
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5433"))
    DB_NAME: str = os.getenv("DB_NAME", "rag_db")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Google / Gemini
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    
    # Apify
    APIFY_TOKEN: Optional[str] = os.getenv("APIFY_TOKEN", None)
    
    # Search API (e.g. Brave Search, Google, Serper)
    SEARCH_API_KEY: Optional[str] = os.getenv("SEARCH_API_KEY", os.getenv("SERPAPI_API_KEY"))
    
    # Transcription
    TRANSCRIBE_ON_INGEST: bool = os.getenv("TRANSCRIBE_ON_INGEST", "false").lower() == "true"
    
    # Embedding model
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    
    # --- Production-Grade Model Router Tiering ---
    MODEL_CLASSIFICATION: str = "gpt-4.1"       # Intent, Emotion, Domain, User State
    MODEL_MEMORY: str = "gpt-4.1"               # Memory update & Extraction
    MODEL_SYNTHESIS: str = "gpt-5.1"            # RAG Synthesis
    MODEL_VERIFY: str = "gpt-5.2"               # Web Search & Fact Verify
    MODEL_MAIN_REPLY: str = "gpt-5.2"           # Final Creator Persona Output
    
    # Fallback models if above fail
    MODEL_FALLBACK_FAST: str = "gpt-4o-mini"
    MODEL_FALLBACK_SMART: str = "gpt-4o"

    # Legacy settings (mapped for backward compatibility)
    CHAT_MODEL: str = MODEL_MAIN_REPLY
    VISION_MODEL: str = "gpt-4o"
    ROUTER_MODEL: str = MODEL_CLASSIFICATION
    REWRITE_MODEL: str = MODEL_MEMORY
    RERANK_MODEL: str = MODEL_CLASSIFICATION
    FINAL_RESPONSE_MODEL: str = MODEL_MAIN_REPLY

settings = Settings()
