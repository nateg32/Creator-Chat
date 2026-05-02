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
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

    # xAI / Grok
    XAI_API_KEY: str = os.getenv("XAI_API_KEY", "")
    XAI_BASE_URL: str = os.getenv("XAI_BASE_URL", "https://api.x.ai/v1")

    # Provider-specific fallbacks for features xAI does not fully replace here
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", OPENAI_API_KEY)
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", OPENAI_BASE_URL)
    TRANSCRIPTION_API_KEY: str = os.getenv("TRANSCRIPTION_API_KEY", OPENAI_API_KEY)
    TRANSCRIPTION_BASE_URL: str = os.getenv("TRANSCRIPTION_BASE_URL", OPENAI_BASE_URL)

    # Google / Gemini
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    GEMINI_GROUNDING_MODEL: str = os.getenv("GEMINI_GROUNDING_MODEL", "gemini-2.5-flash")
    
    # Apify
    APIFY_TOKEN: Optional[str] = os.getenv("APIFY_TOKEN", None)
    
    # Search API (e.g. Brave Search, Google, Serper)
    SEARCH_API_KEY: Optional[str] = os.getenv("SEARCH_API_KEY", os.getenv("SERPAPI_API_KEY"))
    LIVE_SEARCH_PROVIDER: str = os.getenv("LIVE_SEARCH_PROVIDER", "gemini").lower()

    # Auth / JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-before-prod")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    # Cross-site cookies (e.g., Vercel frontend → Render backend) require SameSite=None + Secure=True.
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "none").lower()
    
    # Transcription
    TRANSCRIBE_ON_INGEST: bool = os.getenv("TRANSCRIBE_ON_INGEST", "false").lower() == "true"
    
    # Embedding model
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    
    # --- Creator Bot model stack ---
    # NOTE: defaults must be REAL OpenAI model IDs. Override per-tier via env vars
    # if you want to swap in newer models without touching code.
    # Routing / guardrails: cheap, fast classification and safety helpers.
    MODEL_CLASSIFICATION: str = os.getenv("MODEL_CLASSIFICATION", "gpt-5-nano")
    MODEL_MEMORY: str = os.getenv("MODEL_MEMORY", "gpt-5-nano")
    MODEL_VERIFY: str = os.getenv("MODEL_VERIFY", "gpt-5-nano")

    # Live persona chat: default production model for grounded creator replies.
    MODEL_SYNTHESIS: str = os.getenv("MODEL_SYNTHESIS", "gpt-5-mini")
    MODEL_MAIN_REPLY: str = os.getenv("MODEL_MAIN_REPLY", "gpt-5-mini")
    # Faster small model used on light routes (greeting / small talk) where
    # quality requirements are lower and TTFB matters most. Defaults to the
    # synthesis model so behavior is unchanged unless the operator opts in.
    MODEL_FAST_REPLY: str = os.getenv("MODEL_FAST_REPLY", os.getenv("MODEL_SYNTHESIS", "gpt-5-mini"))

    # Persona / style fingerprint creation: deeper, less frequent analysis pass.
    MODEL_PERSONA_ANALYSIS: str = os.getenv("MODEL_PERSONA_ANALYSIS", "gpt-5")
    MODEL_PERSONA_ANALYSIS_ADVANCED: str = os.getenv("MODEL_PERSONA_ANALYSIS_ADVANCED", "gpt-5")
    
    # Fallback models if above fail
    MODEL_FALLBACK_FAST: str = os.getenv("MODEL_FALLBACK_FAST", "gpt-5-nano")
    MODEL_FALLBACK_SMART: str = os.getenv("MODEL_FALLBACK_SMART", "gpt-5-mini")

    # Legacy settings (mapped for backward compatibility)
    CHAT_MODEL: str = MODEL_MAIN_REPLY
    VISION_MODEL: str = os.getenv("VISION_MODEL", "gpt-4o")
    ROUTER_MODEL: str = MODEL_CLASSIFICATION
    REWRITE_MODEL: str = MODEL_MEMORY
    RERANK_MODEL: str = MODEL_CLASSIFICATION
    FINAL_RESPONSE_MODEL: str = MODEL_MAIN_REPLY

settings = Settings()
