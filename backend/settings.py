from pathlib import Path
from dotenv import load_dotenv
import os
from typing import Optional

# Load env from backend/.env
BASE_DIR = Path(__file__).resolve().parent
# Production/runtime environment variables should win over repo-local .env defaults.
load_dotenv(BASE_DIR / ".env", override=False)

class Settings:
    BASE_DIR: Path = BASE_DIR
    # Database
    DB_HOST: str = os.getenv("DB_HOST", os.getenv("PGHOST", "localhost"))
    DB_PORT: int = int(os.getenv("DB_PORT", os.getenv("PGPORT", "5433")))
    DB_NAME: str = os.getenv("DB_NAME", os.getenv("PGDATABASE", "rag_db"))
    DB_USER: str = os.getenv("DB_USER", os.getenv("PGUSER", "postgres"))
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", os.getenv("PGPASSWORD", ""))
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Google / Gemini
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    GEMINI_GROUNDING_MODEL: str = os.getenv("GEMINI_GROUNDING_MODEL", "gemini-2.5-flash")
    
    # Apify
    APIFY_TOKEN: Optional[str] = os.getenv("APIFY_TOKEN", None)
    
    # Search API (e.g. Brave Search, Google, Serper)
    SEARCH_API_KEY: Optional[str] = os.getenv("SEARCH_API_KEY", os.getenv("SERPAPI_API_KEY"))
    LIVE_SEARCH_PROVIDER: str = os.getenv("LIVE_SEARCH_PROVIDER", "auto").lower()

    # Auth / JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-before-prod")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "43200"))
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    
    # Transcription
    TRANSCRIBE_ON_INGEST: bool = os.getenv("TRANSCRIBE_ON_INGEST", "false").lower() == "true"
    
    # Embedding model
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    
    # --- Production-Grade Model Router Tiering ---
    MODEL_CLASSIFICATION: str = "gpt-4o-mini"       # Intent, Emotion, Domain, User State (FAST)
    MODEL_MEMORY: str = "gpt-4o-mini"               # Memory update & Extraction (FAST)
    MODEL_SYNTHESIS: str = "gpt-5.1"            # RAG Synthesis
    MODEL_VERIFY: str = "gpt-4o-mini"               # Web Search & Fact Verify (FAST)
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
