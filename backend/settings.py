from pathlib import Path
from dotenv import load_dotenv
import os
from typing import Optional

# Load env from backend/.env
BASE_DIR = Path(__file__).resolve().parent
# override=True ensures values in backend/.env win over any existing environment values
load_dotenv(BASE_DIR / ".env", override=True)

class Settings:
    # Database
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5433"))
    DB_NAME: str = os.getenv("DB_NAME", "rag_db")
    DB_USER: str = os.getenv("DB_USER", "postgres")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    
    # Apify
    APIFY_TOKEN: Optional[str] = os.getenv("APIFY_TOKEN", None)
    
    # Transcription
    TRANSCRIBE_ON_INGEST: bool = os.getenv("TRANSCRIBE_ON_INGEST", "false").lower() == "true"
    
    # Embedding model
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    
    # Chat model
    CHAT_MODEL: str = "gpt-4o-mini"

settings = Settings()
