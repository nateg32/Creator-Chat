from pathlib import Path
from dotenv import load_dotenv
import os
from typing import Optional

# Load env from backend/.env
BASE_DIR = Path(__file__).resolve().parent
# Production/runtime environment variables should win over repo-local .env defaults.
load_dotenv(BASE_DIR / ".env", override=False)


def _gemini_model_env(name: str, default: str) -> str:
    value = (os.getenv(name, "") or "").strip()
    return value if value.lower().startswith("gemini-") else default


class Settings:
    BASE_DIR: Path = BASE_DIR
    # Database
    DB_HOST: str = os.getenv("DB_HOST", os.getenv("PGHOST", "localhost"))
    DB_PORT: int = int(os.getenv("DB_PORT", os.getenv("PGPORT", "5432")))
    DB_NAME: str = os.getenv("DB_NAME", os.getenv("PGDATABASE", "creator_chat"))
    DB_USER: str = os.getenv("DB_USER", os.getenv("PGUSER", "postgres"))
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", os.getenv("PGPASSWORD", ""))
    DB_POOL_MIN_SIZE: int = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
    DB_POOL_MAX_SIZE: int = int(os.getenv("DB_POOL_MAX_SIZE", "10"))
    
    # OpenAI-compatible credentials are kept for embeddings/Whisper transcription.
    # Creator chat and reply synthesis are Gemini-only.
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
    TRANSCRIPTION_MODEL: str = os.getenv("TRANSCRIPTION_MODEL", "whisper-1")
    ASSEMBLYAI_API_KEY: str = os.getenv("ASSEMBLYAI_API_KEY", "")
    ASSEMBLYAI_TRANSCRIPT_TIMEOUT_SECONDS: float = float(os.getenv("ASSEMBLYAI_TRANSCRIPT_TIMEOUT_SECONDS", "180"))
    ASSEMBLYAI_ENRICHMENT_ENABLED: bool = os.getenv("ASSEMBLYAI_ENRICHMENT_ENABLED", "true").lower() == "true"
    ASSEMBLYAI_CAPTIONS_ENABLED: bool = os.getenv("ASSEMBLYAI_CAPTIONS_ENABLED", "true").lower() == "true"
    ASSEMBLYAI_CHARS_PER_CAPTION: int = int(os.getenv("ASSEMBLYAI_CHARS_PER_CAPTION", "42"))

    # Google / Gemini
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", GEMINI_API_KEY)
    GEMINI_ANALYSIS_MODEL: str = os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-3-pro-preview")
    GEMINI_CHAT_MODEL: str = os.getenv("GEMINI_CHAT_MODEL", "gemini-3-flash-preview")
    GEMINI_GROUNDING_MODEL: str = os.getenv("GEMINI_GROUNDING_MODEL", "gemini-2.5-flash")
    GEMINI_VISION_MODEL: str = os.getenv("GEMINI_VISION_MODEL", "gemini-3-flash-preview")
    GEMINI_VISION_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_VISION_TIMEOUT_SECONDS", "12.0"))
    GEMINI_VISION_MAX_INLINE_BYTES: int = int(os.getenv("GEMINI_VISION_MAX_INLINE_BYTES", "8000000"))
    GEMINI_VISION_MEDIA_RESOLUTION: str = os.getenv("GEMINI_VISION_MEDIA_RESOLUTION", "MEDIA_RESOLUTION_HIGH")
    GEMINI_FACT_SYNTHESIS_MODEL: str = os.getenv("GEMINI_FACT_SYNTHESIS_MODEL", GEMINI_ANALYSIS_MODEL)
    GEMINI_CACHE_LOOKUP_MODEL: str = os.getenv("GEMINI_CACHE_LOOKUP_MODEL", "gemini-3-pro-preview")
    GEMINI_CACHE_ROUTER_MODEL: str = os.getenv("GEMINI_CACHE_ROUTER_MODEL", "gemini-3-flash-preview")
    GEMINI_REST_TIMEOUT_SECONDS: float = float(os.getenv("GEMINI_REST_TIMEOUT_SECONDS", "4.0"))
    GEMINI_SAFETY_THRESHOLD: str = os.getenv("GEMINI_SAFETY_THRESHOLD", "BLOCK_ONLY_HIGH")
    GEMINI_CONTEXT_CACHE_ENABLED: bool = os.getenv("GEMINI_CONTEXT_CACHE_ENABLED", "true").lower() == "true"
    GEMINI_CONTEXT_CACHE_TTL_SECONDS: int = int(os.getenv("GEMINI_CONTEXT_CACHE_TTL_SECONDS", "86400"))
    GEMINI_CONTEXT_CACHE_MAX_CHARS: int = int(os.getenv("GEMINI_CONTEXT_CACHE_MAX_CHARS", "800000"))
    THREAD_CONTEXT_CACHE_ENABLED: bool = os.getenv("THREAD_CONTEXT_CACHE_ENABLED", "true").lower() == "true"
    THREAD_CONTEXT_CACHE_TTL_SECONDS: int = int(os.getenv("THREAD_CONTEXT_CACHE_TTL_SECONDS", "900"))
    THREAD_CONTEXT_CACHE_MAX_ENTRIES: int = int(os.getenv("THREAD_CONTEXT_CACHE_MAX_ENTRIES", "4"))
    THREAD_CONTEXT_CACHE_MAX_CHUNKS: int = int(os.getenv("THREAD_CONTEXT_CACHE_MAX_CHUNKS", "6"))
    THREAD_CONTEXT_CACHE_MAX_BYTES: int = int(os.getenv("THREAD_CONTEXT_CACHE_MAX_BYTES", "120000"))
    THREAD_CONTEXT_CACHE_REDIS_URL: str = os.getenv(
        "THREAD_CONTEXT_CACHE_REDIS_URL",
        os.getenv("REDIS_URL", os.getenv("KEY_VALUE_URL", os.getenv("RENDER_REDIS_URL", ""))),
    )
    THREAD_MEMORY_LLM_UPDATES_ENABLED: bool = os.getenv("THREAD_MEMORY_LLM_UPDATES_ENABLED", "true").lower() == "true"
    THREAD_MEMORY_LLM_MIN_SIGNAL_WORDS: int = int(os.getenv("THREAD_MEMORY_LLM_MIN_SIGNAL_WORDS", "18"))
    GEMINI_DYNAMIC_RAG_ENABLED: bool = os.getenv("GEMINI_DYNAMIC_RAG_ENABLED", "true").lower() == "true"
    GEMINI_FACT_SYNTHESIS_ENABLED: bool = os.getenv("GEMINI_FACT_SYNTHESIS_ENABLED", "true").lower() == "true"
    AGENTIC_PARALLEL_RETRIEVAL_ENABLED: bool = os.getenv("AGENTIC_PARALLEL_RETRIEVAL_ENABLED", "true").lower() == "true"
    AGENTIC_SEARCH_TIMEOUT_SECONDS: float = float(os.getenv("AGENTIC_SEARCH_TIMEOUT_SECONDS", "1.2"))
    CHAT_PROVIDER: str = "gemini"
    SMART_INTENT_ROUTER_TIMEOUT_SECONDS: float = float(os.getenv("SMART_INTENT_ROUTER_TIMEOUT_SECONDS", "3.5"))
    SMART_INTENT_ROUTER_JOIN_TIMEOUT_SECONDS: float = float(os.getenv("SMART_INTENT_ROUTER_JOIN_TIMEOUT_SECONDS", "1.2"))
    SMART_INTENT_ROUTER_MODEL: str = _gemini_model_env(
        "SMART_INTENT_ROUTER_MODEL",
        _gemini_model_env("MODEL_CLASSIFICATION", GEMINI_CHAT_MODEL),
    )
    
    # Apify
    APIFY_TOKEN: Optional[str] = os.getenv("APIFY_TOKEN", None)
    
    # Search API (e.g. Brave Search, Exa, Google, Serper)
    SEARCH_API_KEY: Optional[str] = os.getenv("SEARCH_API_KEY")
    BRAVE_SEARCH_API_KEY: Optional[str] = os.getenv("BRAVE_SEARCH_API_KEY")
    EXA_API_KEY: Optional[str] = os.getenv("EXA_API_KEY")
    LIVE_SEARCH_PROVIDER: str = os.getenv("LIVE_SEARCH_PROVIDER", "gemini").lower()

    # Auth / JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-me-before-prod")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "true").lower() == "true"
    # Cross-site cookies for split frontend/backend deployments require SameSite=None + Secure=True.
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "none").lower()

    # Open-source scrape limits
    SCRAPE_MAX_PLATFORMS_PER_SEARCH: int = int(os.getenv("SCRAPE_MAX_PLATFORMS_PER_SEARCH", "8"))
    SCRAPE_MAX_ITEMS_PER_PLATFORM: int = int(os.getenv("SCRAPE_MAX_ITEMS_PER_PLATFORM", "25000"))
    SCRAPE_MAX_ITEMS_PER_SEARCH: int = int(os.getenv("SCRAPE_MAX_ITEMS_PER_SEARCH", "25000"))
    SCRAPE_MAX_CREATORS: int = int(os.getenv("SCRAPE_MAX_CREATORS", "250"))
    SCRAPE_MONTHLY_ITEM_ALLOWANCE: int = int(os.getenv("SCRAPE_MONTHLY_ITEM_ALLOWANCE", "25000"))
    
    # Transcription
    TRANSCRIBE_ON_INGEST: bool = os.getenv("TRANSCRIBE_ON_INGEST", "true").lower() == "true"
    DOCUMENT_CONTENT_PREVIEW_CHARS: int = int(os.getenv("DOCUMENT_CONTENT_PREVIEW_CHARS", "1200"))
    INGEST_CHUNK_SIZE: int = int(os.getenv("INGEST_CHUNK_SIZE", "1000"))
    INGEST_CHUNK_OVERLAP: int = int(os.getenv("INGEST_CHUNK_OVERLAP", "80"))

    # Worker queue
    SYSTEM_JOB_STALE_AFTER_MINUTES: int = int(os.getenv("SYSTEM_JOB_STALE_AFTER_MINUTES", "45"))
    SYSTEM_JOB_MAX_RETRIES: int = int(os.getenv("SYSTEM_JOB_MAX_RETRIES", "2"))
    
    # Embedding model
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    
    # --- Creator Chat Gemini model stack ---
    # Env overrides are accepted only when they are Gemini model IDs, so stale
    # GPT/OpenAI values in production cannot silently take over chat replies.
    # Routing / guardrails: cheap, fast classification and safety helpers.
    MODEL_CLASSIFICATION: str = _gemini_model_env("MODEL_CLASSIFICATION", GEMINI_CHAT_MODEL)
    MODEL_MEMORY: str = _gemini_model_env("MODEL_MEMORY", GEMINI_CHAT_MODEL)
    MODEL_VERIFY: str = _gemini_model_env("MODEL_VERIFY", GEMINI_ANALYSIS_MODEL)

    # Live persona chat: default production model for grounded creator replies.
    MODEL_SYNTHESIS: str = _gemini_model_env("MODEL_SYNTHESIS", GEMINI_CHAT_MODEL)
    MODEL_MAIN_REPLY: str = _gemini_model_env("MODEL_MAIN_REPLY", GEMINI_CHAT_MODEL)
    # Faster small model used on light routes (greeting / small talk) where
    # quality requirements are lower and TTFB matters most. Defaults to the
    # synthesis model so behavior is unchanged unless the operator opts in.
    MODEL_FAST_REPLY: str = _gemini_model_env("MODEL_FAST_REPLY", MODEL_SYNTHESIS)

    # Persona / style fingerprint creation: deeper, less frequent analysis pass.
    MODEL_PERSONA_ANALYSIS: str = _gemini_model_env("MODEL_PERSONA_ANALYSIS", GEMINI_ANALYSIS_MODEL)
    MODEL_PERSONA_ANALYSIS_ADVANCED: str = _gemini_model_env("MODEL_PERSONA_ANALYSIS_ADVANCED", GEMINI_ANALYSIS_MODEL)
    FINGERPRINT_PUBLIC_RESEARCH: str = os.getenv("FINGERPRINT_PUBLIC_RESEARCH", "off").lower()
    
    # Gemini-only retry models for malformed JSON or transient synthesis failures.
    MODEL_FALLBACK_FAST: str = _gemini_model_env("MODEL_FALLBACK_FAST", GEMINI_CHAT_MODEL)
    MODEL_FALLBACK_SMART: str = _gemini_model_env("MODEL_FALLBACK_SMART", GEMINI_ANALYSIS_MODEL)

    # Legacy settings (mapped for backward compatibility)
    CHAT_MODEL: str = MODEL_MAIN_REPLY
    VISION_MODEL: str = _gemini_model_env("VISION_MODEL", GEMINI_VISION_MODEL)
    ROUTER_MODEL: str = MODEL_CLASSIFICATION
    REWRITE_MODEL: str = MODEL_MEMORY
    RERANK_MODEL: str = MODEL_CLASSIFICATION
    FINAL_RESPONSE_MODEL: str = MODEL_MAIN_REPLY

settings = Settings()
