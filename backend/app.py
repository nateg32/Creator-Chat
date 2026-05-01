import logging
import base64
import hmac
import hashlib
from fastapi import FastAPI, HTTPException, Cookie, Depends, Response, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import os
import json
import re
import bcrypt
import uuid
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import asyncio
from fastapi import BackgroundTasks
from urllib.parse import urlparse
from backend.models import (
    AskRequest, AskResponse,
    IngestRequest, IngestResponse,
    SearchRequest, SearchResponse,
    ApproveIngestRequest, ApproveIngestResponse,
    ApproveIngestRequestNew, ApproveIngestResponseNew, ApproveIngestItem,
    PersonaRequest, PersonaResponse,
    HealthResponse,
    LoginRequest, LoginResponse, SessionResponse,
    Creator, CreateCreatorRequest, CreatorStats, CreatorsListResponse,
    CreateCreatorWithConfigRequest, UpdateCreatorRequest, CreatorWithConfigResponse,
    ApproveIngestRequestV2,
    UserSettings, UpdateUserSettingsRequest,
    CreateThreadRequest, ThreadResponse, MessageResponse, UpdateThreadRequest,
    RecommendationFeedbackRequest,
)
from backend.rag import get_persona
import backend.rag as rag
from backend.creator_engine import ask as creator_ask
from backend.grounded_rag import grounded_rag_ask, grounded_rag_stream
from backend.ingest import clean_transcript_for_ingestion, ingest_document
from backend.services.identity_manager import autofill_creator_identity
from backend.apify_service import search_all, search_instagram_reels
from backend.lib.instagram_parser import parse_instagram_url
from backend.config.platforms import (
    PLATFORMS,
    choose_valid_normalized_url,
    get_platform,
    validate_url,
    normalize_url,
    extract_handle,
    validate_time_filter,
    _path_matches_platform,
)
from backend.scraper_router import run_search_router, PLATFORM_MAPPERS
from backend.db import db
from backend.settings import settings
from backend.personality_analyzer import PersonalityAnalyzer
from backend.core.interaction_engine import interaction_engine
from backend.utils.name_formatter import normalize_creator_name
from backend.services.formatting import clean_response, clean_for_stream_chunk, prepare_chat_response, should_strip_hyphens
from backend.services.rhythm_shaper import rhythm_shaper
from backend.services.preview_cards import extract_preview_cards, merge_preview_cards
from backend.services.tiktok_validator import verify_tiktok_profile, verify_tiktok_profile_with_actor
from backend.services.prompt_injection_guard import normalize_user_preferences
from backend.services.regurgitation_guard import score_response_quality
from backend.services.transcript_quality import transcript_needs_recovery
from backend.services.creator_entity_service import creator_entity_service
from backend.services.creator_fact_policy import classify_creator_fact_query, extract_timeline_focus
from backend.services.evidence_router import recent_evidence_activity
from backend.services.fact_registry import fact_registry
from backend.services.stream_fact_recovery import recover_streamed_creator_fact_answer
from backend.services.recommendation_feedback_service import recommendation_feedback_service
from backend.services.corpus_state import (
    compute_item_ingest_checksum,
    delete_document_corpus,
    delete_document_chunks_and_embeddings,
    find_existing_document,
    get_document_ingest_checksum,
    refresh_creator_corpus_state,
)
from backend.core.interaction_engine import RESPONSE_PRESETS
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_IS_PRODUCTION = os.getenv("RENDER", "") != "" or os.getenv("ENV", "").lower() == "production"
app = FastAPI(
    title="Creator Bot API",
    docs_url=None if _IS_PRODUCTION else "/docs",
    redoc_url=None if _IS_PRODUCTION else "/redoc",
    openapi_url=None if _IS_PRODUCTION else "/openapi.json",
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


app.add_middleware(SecurityHeadersMiddleware)


_CREATOR_COLUMN_CACHE: Dict[str, bool] = {}


def _creator_column_exists(column_name: str) -> bool:
    cached = _CREATOR_COLUMN_CACHE.get(column_name)
    if cached is not None:
        return cached
    row = db.execute_one(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
        ("creators", column_name),
    )
    exists = bool(row)
    _CREATOR_COLUMN_CACHE[column_name] = exists
    return exists


def _creator_select_expr(column_name: str) -> str:
    return column_name if _creator_column_exists(column_name) else f"NULL AS {column_name}"


def _get_creator_cleaning_profile(creator_id: int, user_id: Optional[int] = None) -> Dict[str, Any]:
    query = f"SELECT {_creator_select_expr('voice_patterns')} FROM creators WHERE id = %s"
    params: List[Any] = [creator_id]
    if user_id is not None:
        query += " AND user_id = %s"
        params.append(user_id)
    return db.execute_one(query, tuple(params)) or {}


def _find_stream_emit_boundary(text: str, tail_size: int = 24) -> int:
    if not text:
        return 0

    last_sentence_break = None
    for match in re.finditer(r"(?<=[.!?])\s+|\n", text):
        last_sentence_break = match
    if last_sentence_break:
        return last_sentence_break.end()

    if len(text) <= tail_size:
        return 0

    limit = len(text) - tail_size
    for index in range(limit, 0, -1):
        if text[index - 1].isspace():
            return index
    return 0


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Log all unhandled exceptions so they appear in the terminal."""
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        raise exc  # Let FastAPI handle HTTPException normally
    import traceback
    print(f"[ERROR] Unhandled exception: {exc}", flush=True)
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": _safe_error_detail(exc)})


@app.on_event("startup")
def startup_event():
    """Minimal startup - DB table created on first use to avoid blocking app start."""
    print("[STARTUP] Backend ready", flush=True)


# In-memory progress tracking for search (key: search_id, value: progress dict)
# Also persisted to DB so progress survives backend restarts (e.g. uvicorn --reload)
_search_progress: Dict[str, Dict[str, Any]] = {}

_FINGERPRINT_STAGE_FLOW = [
    {
        "key": "preparing",
        "label": "Preparing Workspace",
        "description": "Checking creator config, approved content, and any reusable research.",
        "range": [0, 12],
    },
    {
        "key": "research_cache",
        "label": "Loading Cached Research",
        "description": "Reusing dossier material that is still valid so we do not waste time.",
        "range": [12, 30],
    },
    {
        "key": "link_scan",
        "label": "Scanning Public Signals",
        "description": "Walking official links, channels, and domains for identity clues.",
        "range": [18, 40],
    },
    {
        "key": "voice_analysis",
        "label": "Distilling Voice",
        "description": "Mining approved content for recurring values, cadence, arguments, and tells.",
        "range": [40, 62],
    },
    {
        "key": "dossier",
        "label": "Expanding Public Profile",
        "description": "Filling missing identity gaps with targeted public research.",
        "range": [62, 80],
    },
    {
        "key": "synthesis",
        "label": "Building The Model",
        "description": "Combining research, worldview, and voice into the working fingerprint.",
        "range": [80, 92],
    },
    {
        "key": "finalizing",
        "label": "Writing The Soul",
        "description": "Finalizing soul.md, runtime instructions, and last pass validation.",
        "range": [92, 99],
    },
    {
        "key": "complete",
        "label": "Ready",
        "description": "Fingerprint is built and ready to steer replies.",
        "range": [100, 100],
    },
]


def _fingerprint_stage_meta(stage: str) -> Dict[str, Any]:
    stage_key = str(stage or "").lower().strip()
    for item in _FINGERPRINT_STAGE_FLOW:
        if item["key"] == stage_key:
            return item
    if stage_key == "error":
        return {
            "key": "error",
            "label": "Error",
            "description": "The build hit an error before the fingerprint finished.",
            "range": [0, 0],
        }
    return {
        "key": stage_key or "processing",
        "label": "Processing",
        "description": "Fingerprint generation is in progress.",
        "range": [0, 100],
    }


def _fingerprint_fun_line(stage: str) -> str:
    lines = {
        "preparing": "Opening the case file and checking what we already know.",
        "research_cache": "Dusting off cached receipts instead of reinventing the wheel.",
        "link_scan": "Walking the public trail for bios, channels, and identity clues.",
        "voice_analysis": "Listening for signature phrases, recurring values, and favorite arguments.",
        "dossier": "Cross-examining the public record to fill in the missing pieces.",
        "synthesis": "Turning scattered evidence into one coherent operating system.",
        "finalizing": "Polishing the brain, the voice, and the soul.md handoff.",
        "complete": "The profile is locked in and ready to talk.",
        "error": "The fingerprint machine dropped a bolt. It needs another pass.",
    }
    return lines.get(str(stage or "").lower().strip(), "Fingerprint generation is moving through the pipeline.")


def _build_fingerprint_stage_list(current_stage: str, percent: int, status: str) -> List[Dict[str, Any]]:
    current_key = str(current_stage or "").lower().strip()
    complete = str(status or "").lower().strip() != "processing"
    stage_cards = []
    for index, item in enumerate(_FINGERPRINT_STAGE_FLOW, start=1):
        state = "upcoming"
        if complete:
            state = "complete"
        elif item["key"] == current_key:
            state = "current"
        elif percent >= item["range"][1]:
            state = "complete"
        stage_cards.append({
            "key": item["key"],
            "label": item["label"],
            "description": item["description"],
            "state": state,
            "index": index,
        })
    return stage_cards


def _ensure_search_progress_table():
    """Create search_progress table if it doesn't exist."""
    try:
        db.execute_update("""
            CREATE TABLE IF NOT EXISTS search_progress (
                search_id UUID PRIMARY KEY,
                progress_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    except Exception as e:
        print(f"[SEARCH] Could not create search_progress table: {e}")


def _get_search_progress(search_id: str) -> Optional[Dict[str, Any]]:
    """Get progress from DB first, then local memory fallback."""
    try:
        row = db.execute_one(
            "SELECT progress_data FROM search_progress WHERE search_id = %s",
            (search_id,),
        )
        if row and row.get("progress_data"):
            data = row["progress_data"]
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                _search_progress[search_id] = dict(data)
                return _search_progress[search_id]
            return None
    except Exception:
        pass
    if search_id in _search_progress:
        return _search_progress[search_id]
    return None


def _set_search_progress(search_id: str, data: Dict[str, Any]):
    """Write progress to memory and DB."""
    _search_progress[search_id] = data
    try:
        _ensure_search_progress_table()
        db.execute_update(
            """
            INSERT INTO search_progress (search_id, progress_data, updated_at)
            VALUES (%s::uuid, %s::jsonb, NOW())
            ON CONFLICT (search_id) DO UPDATE SET
                progress_data = EXCLUDED.progress_data,
                updated_at = NOW()
            """,
            (search_id, json.dumps(data, default=str)),
        )
    except Exception as e:
        print(f"[SEARCH] Could not persist progress to DB: {e}")

def _safe_error_detail(exc: Exception, fallback: str = "Unexpected server error") -> str:
    if isinstance(exc, HTTPException):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, (dict, list)):
            try:
                payload = json.dumps(detail)
                if payload and payload.strip():
                    return payload
            except Exception:
                pass
    message = str(exc).strip() if exc is not None else ""
    if message:
        return message
    exc_name = exc.__class__.__name__ if exc is not None else ""
    if exc_name and exc_name != "Exception":
        return exc_name
    return fallback


def _internal_server_error(exc: Exception, fallback: str = "Unexpected server error") -> HTTPException:
    logger.exception(fallback, exc_info=exc)
    detail = fallback if _IS_PRODUCTION else _safe_error_detail(exc, fallback)
    return HTTPException(status_code=500, detail=detail)


def _get_cors_origins() -> List[str]:
    """Allow localhost in dev plus deployed frontend URLs via env vars."""
    default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ]

    configured_origins: List[str] = []
    for env_name in ("CORS_ORIGINS", "FRONTEND_URL", "FRONTEND_URLS"):
        configured_origins.extend(
            origin.strip().rstrip("/")
            for origin in os.getenv(env_name, "").split(",")
            if origin.strip()
        )

    return list(dict.fromkeys(default_origins + configured_origins))


# CORS middleware - allow common development ports + configured deployed frontend URLs
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Id", "Accept"],
)

# Auth helpers (kept for backward compatibility)
def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a hash"""
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def create_session(user_id: int) -> str:
    """Create a new session and return session ID"""
    session_id = str(uuid.uuid4())
    expires_at = datetime.utcnow() + timedelta(days=30)
    
    query = """
        INSERT INTO sessions (id, user_id, expires_at)
        VALUES (%s, %s, %s)
    """
    db.execute_update(query, (session_id, user_id, expires_at))
    return session_id

def _jwt_b64encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")

def _jwt_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))

def create_access_token(user_id: int, email: str) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)).timestamp()),
    }
    header = {"alg": settings.JWT_ALGORITHM, "typ": "JWT"}
    signing_input = f"{_jwt_b64encode(json.dumps(header, separators=(',', ':')).encode())}.{_jwt_b64encode(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.new(
        settings.JWT_SECRET_KEY.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_jwt_b64encode(signature)}"


def normalize_user_email(email: Optional[str]) -> str:
    return str(email or "").strip().lower()


def normalize_creator_handle(handle: Optional[str]) -> Optional[str]:
    value = str(handle or "").strip().lower().lstrip("@")
    return value or None

def get_user_from_bearer(authorization: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(
            settings.JWT_SECRET_KEY.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected_sig, _jwt_b64decode(signature_b64)):
            return None
        payload = json.loads(_jwt_b64decode(payload_b64).decode("utf-8"))
        if int(payload.get("exp", 0)) <= int(datetime.utcnow().timestamp()):
            return None
        user_id = int(payload.get("sub"))
    except Exception:
        return None

    return db.execute_one("SELECT id, email FROM users WHERE id = %s", (user_id,))

def get_user_from_session(session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Get user from session ID"""
    if not session_id:
        return None
    
    query = """
        SELECT u.id, u.email
        FROM users u
        JOIN sessions s ON s.user_id = u.id
        WHERE s.id = %s AND s.expires_at > NOW()
    """
    result = db.execute_one(query, (session_id,))
    return result

def require_auth(
    session_id: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
) -> Dict[str, Any]:
    """Dependency to require authentication"""
    user = (
        get_user_from_bearer(authorization)
        or get_user_from_session(session_id)
        or get_user_from_session(x_session_id)
    )
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

def ensure_creator_access(creator_id: int, user_id: int) -> None:
    row = db.execute_one("SELECT id FROM creators WHERE id = %s AND user_id = %s", (creator_id, user_id))
    if not row:
        raise HTTPException(status_code=404, detail="Creator not found")

def question_refers_to_recent_image(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    image_tokens = ["image", "photo", "picture", "pic", "screenshot", "this", "that", "she", "he", "her", "him", "girl", "guy", "chick", "woman", "man", "person"]
    return any(token in text for token in image_tokens)

def get_latest_thread_images(thread_id: Optional[str], user_id: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    if not thread_id:
        return None
    if user_id is not None:
        thread = db.execute_one(
            "SELECT id FROM chat_threads WHERE id = %s AND user_id = %s",
            (thread_id, user_id),
        )
        if not thread:
            return None
    row = db.execute_one(
        """
        SELECT metadata
        FROM chat_messages
        WHERE thread_id = %s
          AND role = 'user'
          AND metadata IS NOT NULL
          AND metadata ? 'images'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (thread_id,),
    )
    if not row:
        return None
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    images = metadata.get("images")
    return images if isinstance(images, list) and images else None


def _parse_message_metadata(raw_metadata: Any) -> Dict[str, Any]:
    if isinstance(raw_metadata, dict):
        return raw_metadata
    if isinstance(raw_metadata, str):
        try:
            parsed = json.loads(raw_metadata)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _history_message_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    message = {
        "role": row.get("role"),
        "content": row.get("content") or "",
    }
    metadata = _parse_message_metadata(row.get("metadata"))
    cards = metadata.get("cards")
    if isinstance(cards, list) and cards:
        message["cards"] = cards
    citations = metadata.get("citations")
    if isinstance(citations, list) and citations:
        message["citations"] = citations
    return message

@app.on_event("startup")
async def startup():
    """Initialize database connection"""
    try:
        if settings.JWT_SECRET_KEY == "change-me-before-prod":
            raise RuntimeError(
                "JWT_SECRET_KEY is set to the default insecure value. "
                "Set the JWT_SECRET_KEY environment variable before running in production."
            )
        db.execute_query("SELECT 1")
        print("[STARTUP] DB connection OK")
    except Exception as e:
        print(f"[STARTUP] DB connection warning: {e}")
    # Migration: Add soul and fingerprint columns if missing
    try:
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS profile_picture_url TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS creator_category TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS identity_fingerprint JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS style_fingerprint JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS soul_md TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS research_summary JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_status TEXT DEFAULT 'idle'")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_progress JSONB DEFAULT '{}'::jsonb")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_updated_at TIMESTAMPTZ")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS content_corpus_checksum TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_corpus_checksum TEXT")
        # Content/creator archetype detection (drives fingerprint policy)
        db.execute_update("ALTER TABLE scrape_items ADD COLUMN IF NOT EXISTS item_archetype TEXT")
        db.execute_update("ALTER TABLE scrape_items ADD COLUMN IF NOT EXISTS archetype_confidence FLOAT")
        db.execute_update("ALTER TABLE scrape_items ADD COLUMN IF NOT EXISTS archetype_signals JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS creator_archetype TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS archetype_distribution JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS archetype_updated_at TIMESTAMPTZ")
    except Exception as e:
        print(f"[STARTUP] Migration warning: {e}")

@app.on_event("shutdown")
async def shutdown():
    """Close database connection"""
    db.close()

# ============================================================================
# Helper Functions
# ============================================================================

def mock_search(handle: str, source: str, limit: int) -> List[Dict[str, Any]]:
    """Generate mock searched content"""
    items = []
    base_content = [
        f"Hey everyone! {handle} here. Just wanted to share some thoughts on my latest project. It's been an incredible journey and I'm excited to see where it goes.",
        f"Quick update from {handle}: Working on something big behind the scenes. Can't wait to share it with you all soon. Stay tuned!",
        f"Reflecting on the past few months, {handle} here. The support from this community has been amazing. Thank you all for being part of this journey.",
        f"New video dropping soon! {handle} here with a behind-the-scenes look at what we've been building. This one's going to be special.",
        f"Just finished an amazing collaboration. {handle} here to tell you all about it. The energy was incredible and I think you're going to love what we created together.",
    ]
    
    for i in range(min(limit, len(base_content))):
        items.append({
            "source": source,
            "source_id": f"{source}_{handle}_{i:03d}",
            "title": f"{handle} - Content {i+1}",
            "url": f"https://{source}.com/{handle}/post_{i+1}",
            "raw_text": base_content[i],
            "metadata": {"mock": True, "index": i}
        })
    
    return items

def try_apify_search(handle: str, source: str, limit: int) -> List[Dict[str, Any]]:
    """Attempt Apify search, raises exception if fails"""
    if not settings.APIFY_TOKEN:
        raise ValueError("APIFY_TOKEN is not set")
    
    # Use the search_all function from apify_client
    items = search_all(handle, [source], limit)
    if not items:
        raise ValueError(f"No items found for @{handle} on {source}")
    return items


_CREATORS_HAS_PLATFORMS_CACHE: Optional[bool] = None
_CREATOR_DISPLAY_COL_CACHE: Optional[str] = None


def _creator_display_column() -> str:
    """Return 'display_name' or 'name' depending on creators table schema."""
    global _CREATOR_DISPLAY_COL_CACHE
    if _CREATOR_DISPLAY_COL_CACHE is not None:
        return _CREATOR_DISPLAY_COL_CACHE
    for col in ("display_name", "name"):
        try:
            r = db.execute_one(
                "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                ("creators", col),
            )
            if r:
                _CREATOR_DISPLAY_COL_CACHE = col
                return col
        except Exception:
            pass
    _CREATOR_DISPLAY_COL_CACHE = "display_name"
    return "display_name"


def _jsonish_to_plain(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and stripped[:1] in "{[":
            try:
                return json.loads(stripped)
            except Exception:
                return value
        return stripped
    if isinstance(value, dict):
        return {k: _jsonish_to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish_to_plain(v) for v in value]
    if hasattr(value, "items"):
        return {k: _jsonish_to_plain(v) for k, v in value.items()}
    return value


def _normalize_optional_string(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return value.strip()


def _values_differ(current: Any, incoming: Any) -> bool:
    return _jsonish_to_plain(current) != _jsonish_to_plain(incoming)


_VALIDATION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CreatorBotValidator/1.0; +https://creator-bot.local)",
    "Accept-Language": "en-US,en;q=0.9",
}


def _host_matches(host: str, candidates: List[str]) -> bool:
    host = (host or "").lower()
    return any(host == candidate or host.endswith(f".{candidate}") for candidate in candidates)


def _extract_html_title(body: str) -> str:
    if not body:
        return ""
    try:
        import re
        match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return " ".join(match.group(1).split()).strip().lower()
    except Exception:
        return ""


def _handle_from_profile_url(url: str, platform_key: str) -> str:
    try:
        handle = extract_handle(url, platform_key) or ""
        return str(handle).strip().lower().lstrip("@")
    except Exception:
        return ""


def _page_has_positive_profile_signal(platform_key: str, requested_url: str, resolved_url: str, body: str) -> bool:
    body_lower = (body or "").lower()
    title = _extract_html_title(body or "")
    handle = _handle_from_profile_url(resolved_url or requested_url, platform_key)
    slug = handle.replace("-", " ").replace("_", " ").strip()

    if platform_key in {"youtube", "youtube_shorts"}:
        return True

    if platform_key == "instagram":
        if title in {"instagram", "login ? instagram", "login ? instagram photos and videos"}:
            return False
        positives = [
            f'"username":"{handle}"' if handle else "",
            f'/{handle}/' if handle else "",
            f'@{handle}' if handle else "",
            "instagram photos and videos",
        ]
        return any(p and p in body_lower for p in positives) or (handle and handle in title)

    if platform_key == "tiktok":
        if title in {"tiktok", "tiktok - make your day", "make your day", "log in | tiktok"}:
            return False
        positives = [f'@{handle}' if handle else "", f'/@{handle}' if handle else "", f'"uniqueid":"{handle}"' if handle else ""]
        return any(p and p in body_lower for p in positives) or (handle and f'@{handle}' in title)

    if platform_key == "twitter":
        if title in {"x", "twitter", "x / ?search", "log in to x / x"}:
            return False
        positives = [f'@{handle}' if handle else "", f'/@{handle}' if handle else "", f'"uniqueid":"{handle}"' if handle else ""]
        return any(p and p in body_lower for p in positives) or (handle and handle in title)

    if platform_key == "linkedin":
        if title in {"linkedin", "linkedin: log in or sign up", "signin | linkedin"}:
            return False
        positives = [f'/in/{handle}' if handle else "", f'/company/{handle}' if handle else "", slug]
        return any(p and p in body_lower for p in positives if p)

    if platform_key == "reddit":
        if title in {"reddit", "reddit - dive into anything", "sign up or log in"}:
            return False
        positives = [f'/user/{handle}' if handle else "", f'/u/{handle}' if handle else "", handle]
        return any(p and p in body_lower for p in positives if p) or (handle and handle in title)

    if platform_key == "facebook":
        if title in {"facebook", "log into facebook", "facebook - log in or sign up"}:
            return False
        parsed = urlparse(resolved_url or requested_url or "")
        path = (parsed.path or "").strip("/").lower()
        positives = [path, slug, handle]
        return any(p and p in body_lower for p in positives if p) or bool(path and path in title)

    return True


def _is_platform_auth_redirect(platform_key: str, resolved_url: str) -> bool:
    parsed = urlparse(resolved_url or "")
    path = (parsed.path or "").strip("/").lower()

    auth_prefixes = {
        "linkedin": {"authwall", "checkpoint", "login", "signup"},
        "tiktok": {"login", "signup", "foryou", "explore"},
        "instagram": {"accounts", "login", "challenge"},
        "twitter": {"i", "login", "signup"},
        "facebook": {"login", "checkpoint"},
    }
    prefixes = auth_prefixes.get(platform_key, set())
    if not path or not prefixes:
        return False
    first = path.split("/", 1)[0]
    return first in prefixes


def _resolved_path_matches_platform(platform_key: str, resolved_url: str) -> bool:
    parsed = urlparse(resolved_url or "")
    return _path_matches_platform(parsed, platform_key)


def _validate_platform_availability(platform_key: str, url: str) -> Dict[str, Any]:
    if platform_key == "custom":
        return {"exists": True, "checked_via": "format_only"}

    tiktok_handle = _handle_from_profile_url(url, "tiktok") if platform_key == "tiktok" else ""

    timeout = 3 if platform_key == "tiktok" else 12
    try:
        response = requests.get(url, headers=_VALIDATION_HEADERS, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        if platform_key == "tiktok":
            tiktok_result = verify_tiktok_profile(url, tiktok_handle, fetch_posts_fn=None)
            if tiktok_result.get("confirmed"):
                return {
                    "exists": True,
                    "checked_via": tiktok_result.get("checked_via") or "tiktok_actor",
                    "resolved_url": tiktok_result.get("matched_url") or url,
                }
            return {
                "exists": False,
                "error": tiktok_result.get("error") or "TikTok could not verify that this account exists publicly.",
                "checked_via": tiktok_result.get("checked_via") or "tiktok_strict",
                "resolved_url": url,
            }
        return {
            "exists": True,
            "checked_via": "format_only_fallback",
            "resolved_url": url,
            "warning": "Valid format. Live verification is unavailable right now.",
        }

    final_url = response.url or url
    final_host = (urlparse(final_url).netloc or "").lower()
    body = (response.text or "").lower()

    invalid_markers = {
        "youtube": ["this page isn't available", "video unavailable", "channel not found", "this channel does not exist"],
        "youtube_shorts": ["this page isn't available", "video unavailable", "channel not found", "this channel does not exist"],
        "instagram": ["sorry, this page isn't available", "page not found", "link you followed may be broken"],
        "tiktok": ["couldn't find this account", "couldn't find this video", "page not available"],
        "facebook": ["this content isn't available right now", "may have been removed", "page isn't available"],
        "twitter": ["this page doesn?t exist", "this page doesn't exist", "something went wrong. try reloading"],
        "linkedin": ["page not found", "profile unavailable"],
        "reddit": ["sorry, nobody on reddit goes by that name", "page not found"],
    }
    valid_hosts = {
        "youtube": ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"],
        "youtube_shorts": ["youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"],
        "instagram": ["instagram.com", "www.instagram.com"],
        "tiktok": ["tiktok.com", "www.tiktok.com", "m.tiktok.com"],
        "facebook": ["facebook.com", "www.facebook.com", "m.facebook.com", "fb.com"],
        "twitter": ["twitter.com", "www.twitter.com", "x.com", "www.x.com"],
        "linkedin": ["linkedin.com", "www.linkedin.com"],
        "reddit": ["reddit.com", "www.reddit.com"],
    }

    if response.status_code >= 400:
        if response.status_code in {401, 403, 429, 500, 502, 503, 504, 999}:
            return {
                "exists": True,
                "checked_via": "http_fetch_soft",
                "resolved_url": final_url,
                "warning": "Valid format. The platform blocked live verification right now.",
            }
        return {
            "exists": False,
            "error": "Link invalid",
            "checked_via": "http_fetch",
            "resolved_url": final_url,
        }

    if platform_key in valid_hosts and not _host_matches(final_host, valid_hosts[platform_key]):
        return {
            "exists": False,
            "error": "Link invalid",
            "checked_via": "redirect_check",
            "resolved_url": final_url,
        }

    if _is_platform_auth_redirect(platform_key, final_url):
        if platform_key == "tiktok":
            tiktok_result = verify_tiktok_profile(
                url,
                tiktok_handle,
                resolved_url=final_url,
                page_title=_extract_html_title(response.text or ""),
                page_body=response.text or "",
            )
            if tiktok_result.get("confirmed"):
                return {
                    "exists": True,
                    "checked_via": tiktok_result.get("checked_via") or "tiktok_actor",
                    "resolved_url": tiktok_result.get("matched_url") or final_url,
                }
            return {
                "exists": False,
                "error": tiktok_result.get("error") or "TikTok could not verify that this account exists publicly.",
                "checked_via": tiktok_result.get("checked_via") or "tiktok_strict",
                "resolved_url": final_url,
            }
        return {
            "exists": True,
            "checked_via": "profile_signal_soft",
            "resolved_url": final_url,
            "warning": "Valid platform match. Live profile verification was inconclusive.",
        }

    if not _resolved_path_matches_platform(platform_key, final_url):
        return {
            "exists": False,
            "error": "Link invalid",
            "checked_via": "path_check",
            "resolved_url": final_url,
        }

    title = _extract_html_title(response.text or "")

    # YouTube channel pages often contain "video unavailable" for individual
    # video thumbnails even when the channel itself is perfectly valid.
    # Check positive profile signals BEFORE invalid markers for @handle URLs.
    if platform_key in ("youtube", "youtube_shorts"):
        path = (urlparse(final_url).path or "").strip("/")
        has_positive = any(s in body for s in ("channel", "videos", "subscribers"))
        if path.startswith("@"):
            if has_positive:
                return {
                    "exists": True,
                    "checked_via": "page_content",
                    "resolved_url": final_url,
                }
            # No positive signals – soft pass (could be bot-blocked page)
            return {
                "exists": True,
                "checked_via": "page_content_soft",
                "resolved_url": final_url,
                "warning": "Valid channel path. Live content signals were inconclusive.",
            }
        # Non-@handle YouTube URL (e.g. /c/ or /channel/) – still skip
        # "video unavailable" marker when the page has positive signals.
        if has_positive:
            safe_markers = [m for m in invalid_markers.get(platform_key, [])
                            if m != "video unavailable"]
            for marker in safe_markers:
                if marker in body:
                    return {
                        "exists": False,
                        "error": "Link invalid",
                        "checked_via": "page_content",
                        "resolved_url": final_url,
                    }
            return {
                "exists": True,
                "checked_via": "page_content",
                "resolved_url": final_url,
            }

    # TikTok pages often contain generic error strings ("couldn't find this
    # account", "page not available") even for valid profiles.  Skip the
    # generic marker check and fall through to verify_tiktok_profile instead.
    if platform_key == "tiktok":
        pass   # handled below
    else:
        for marker in invalid_markers.get(platform_key, []):
            if marker in body:
                return {
                    "exists": False,
                    "error": "Link invalid",
                    "checked_via": "page_content",
                    "resolved_url": final_url,
                }

    if platform_key == "tiktok":
        tiktok_result = verify_tiktok_profile(
            url,
            tiktok_handle,
            resolved_url=final_url,
            page_title=title,
            page_body=response.text or "",
        )
        if tiktok_result.get("confirmed"):
            return {
                "exists": True,
                "checked_via": tiktok_result.get("checked_via") or "tiktok_page",
                "resolved_url": tiktok_result.get("matched_url") or final_url,
            }
        return {
            "exists": False,
            "error": tiktok_result.get("error") or "TikTok could not verify that this account exists publicly.",
            "checked_via": tiktok_result.get("checked_via") or "tiktok_strict",
            "resolved_url": final_url,
        }

    if not _page_has_positive_profile_signal(platform_key, url, final_url, response.text or ""):
        return {
            "exists": True,
            "checked_via": "profile_signal_soft",
            "resolved_url": final_url,
            "warning": "Valid platform match. Live profile verification was inconclusive.",
        }

    return {
        "exists": True,
        "checked_via": "http_fetch",
        "resolved_url": final_url,
    }


def _creator_has_column(column_name: str) -> bool:
    try:
        row = db.execute_one(
            "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
            ("creators", column_name),
        )
        return bool(row)
    except Exception:
        return False


def _creators_has_platforms_column() -> bool:
    """
    Some installs have an older `creators` table without the `platforms` column.
    Detect the schema at runtime and cache the result.
    """
    global _CREATORS_HAS_PLATFORMS_CACHE
    if _CREATORS_HAS_PLATFORMS_CACHE is not None:
        return _CREATORS_HAS_PLATFORMS_CACHE

    try:
        row = db.execute_one(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'creators' AND column_name = 'platforms'
            LIMIT 1
            """,
            (),
        )
        _CREATORS_HAS_PLATFORMS_CACHE = bool(row)
    except Exception:
        _CREATORS_HAS_PLATFORMS_CACHE = False

    return _CREATORS_HAS_PLATFORMS_CACHE


def get_or_create_creator_for_handle(handle: str, user_id: int, platform: str = "instagram") -> int:
    """
    Find or create a creator row for the given handle.
    This lets us have separate personas and stats per creator instead of hardcoding id=1.
    """
    handle = normalize_creator_handle(handle)
    if not handle:
        raise ValueError("Creator handle is required")

    has_platforms = _creators_has_platforms_column()

    # Try to find existing creator by handle for this user only.
    existing = None
    try:
        if has_platforms:
            existing = db.execute_one(
                "SELECT id, platforms FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
                (user_id, handle),
            )
        else:
            existing = db.execute_one(
                "SELECT id, platform_configs FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
                (user_id, handle),
            )
    except Exception:
        # If schema differs unexpectedly, fall back to minimal select
        existing = db.execute_one(
            "SELECT id, platform_configs FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
            (user_id, handle),
        )
    if existing:
        # Ensure platform is recorded (only if column exists)
        if has_platforms:
            platforms = existing.get("platforms") or []
            if isinstance(platforms, str):
                platforms = json.loads(platforms) if platforms else []
            if platform not in platforms:
                platforms.append(platform)
                db.execute_update(
                    "UPDATE creators SET platforms = %s WHERE id = %s",
                    (json.dumps(platforms), existing["id"]),
                )
        return existing["id"]

    has_name_col = _creator_has_column("name")
    has_display_name_col = _creator_has_column("display_name")

    insert_cols = ["user_id", "handle"]
    insert_vals: List[Any] = [user_id, handle]

    if has_name_col:
        insert_cols.append("name")
        insert_vals.append(handle)
    if has_display_name_col:
        insert_cols.append("display_name")
        insert_vals.append(handle)

    if has_platforms:
        insert_cols.append("platforms")
        insert_vals.append(json.dumps([platform]))

    placeholders = ", ".join(["%s"] * len(insert_cols))
    creator_id = db.execute_insert(
        f"INSERT INTO creators ({', '.join(insert_cols)}) VALUES ({placeholders}) RETURNING id",
        tuple(insert_vals),
    )
    return creator_id

def insert_scrape_queue_items(conn, creator_id: int, source: str, items: List[Dict[str, Any]]) -> List[int]:
    """Insert items into scrape_queue and return inserted IDs"""
    queue_ids = []
    query = """
        INSERT INTO scrape_queue (creator_id, source, source_id, url, title, raw_text, metadata, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        RETURNING id
    """
    
    for item in items:
        metadata_json = json.dumps(item.get("metadata", {}))
        queue_id = db.execute_insert(
            query,
            (creator_id, source, item.get("source_id"), item.get("url"), item.get("title"), item["raw_text"], metadata_json)
        )
        if queue_id:
            queue_ids.append(queue_id)
    
    return queue_ids

def fetch_queue_items(conn, creator_id: int, queue_ids: List[int]) -> List[tuple]:
    """Fetch queue items for given IDs belonging to creator"""
    if not queue_ids:
        return []
    
    query = """
        SELECT id, raw_text, title, url
        FROM scrape_queue
        WHERE creator_id = %s AND id = ANY(%s)
    """
    results = db.execute_query(query, (creator_id, queue_ids))
    return results

def mark_queue_ingested(conn, creator_id: int, queue_ids: List[int]):
    """Mark queue items as ingested"""
    query = """
        UPDATE scrape_queue
        SET status = 'ingested'
        WHERE creator_id = %s AND id = ANY(%s)
    """
    db.execute_update(query, (creator_id, queue_ids))

# ============================================================================
# Auth Endpoints (kept for backward compatibility)
# ============================================================================

@app.post("/auth/login", response_model=LoginResponse)
@limiter.limit("10/minute")
async def login(request: Request, payload: LoginRequest, response: Response):
    """Login and create session"""
    try:
        email = normalize_user_email(payload.email)
        query = "SELECT id, password_hash FROM users WHERE email = %s"
        user = db.execute_one(query, (email,))
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        session_id = create_session(user["id"])
        
        response.set_cookie(
            key="session_id",
            value=session_id,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite=settings.COOKIE_SAMESITE,
            secure=settings.COOKIE_SECURE,
        )
        
        return LoginResponse(
            session_id=session_id,
            user_id=user["id"],
            access_token=create_access_token(user["id"], email),
            token_type="bearer",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Login failed")

@app.post("/auth/register")
@limiter.limit("5/minute")
async def register(request: Request, payload: LoginRequest, response: Response):
    """Register a new user"""
    try:
        email = normalize_user_email(payload.email)
        if not email:
            raise HTTPException(status_code=400, detail="Email is required")
        query = "SELECT id FROM users WHERE email = %s"
        existing = db.execute_one(query, (email,))
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        
        password_hash = hash_password(payload.password)
        query = "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id"
        user_id = db.execute_insert(query, (email, password_hash))
        
        session_id = create_session(user_id)
        
        response.set_cookie(
            key="session_id",
            value=session_id,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite=settings.COOKIE_SAMESITE,
            secure=settings.COOKIE_SECURE,
        )
        
        return LoginResponse(
            session_id=session_id,
            user_id=user_id,
            access_token=create_access_token(user_id, email),
            token_type="bearer",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Registration failed")

@app.get("/auth/session", response_model=SessionResponse)
async def get_session(
    session_id: Optional[str] = Cookie(None),
    authorization: Optional[str] = Header(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    """Get current session info"""
    user = (
        get_user_from_bearer(authorization)
        or get_user_from_session(session_id)
        or get_user_from_session(x_session_id)
    )
    if not user:
        return SessionResponse(user_id=0, email="", valid=False)
    return SessionResponse(user_id=user["id"], email=user["email"], valid=True)

@app.post("/auth/logout")
async def logout(
    response: Response,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-Id"),
):
    """Logout and delete session"""
    sid = session_id or x_session_id
    if sid:
        query = "DELETE FROM sessions WHERE id = %s"
        db.execute_update(query, (sid,))
    response.delete_cookie(
        key="session_id",
        samesite=settings.COOKIE_SAMESITE,
        secure=settings.COOKIE_SECURE,
    )
    return {"ok": True}

# ============================================================================
# Platforms config (for UI)
# ============================================================================

@app.get("/platforms")
def list_platforms():
    """Returns platform config for UI: key, label, icon, placeholder, time_modes, default_max_items, supports_since_date."""
    print("[SEARCH] GET /platforms", flush=True)
    from backend.config.platforms import TIME_MODES, LAST_DAYS_OPTIONS
    try:
        result = [
            {
                "key": p["key"],
                "label": p["label"],
                "icon": p["icon"],
                "placeholder": p["placeholder"],
                "time_modes": [
                    {"value": "all", "label": "All available"},
                    {"value": "last_days", "label": "Last X days"},
                    {"value": "since", "label": "Since date"},
                ],
                "last_days_options": list(LAST_DAYS_OPTIONS),
                "default_max_items": p.get("default_max_items", 10),
                "supports_since_date": bool(p.get("supports_since_date")),
                "implemented": p["key"] in PLATFORM_MAPPERS,
            }
            for p in PLATFORMS
        ]
        return result
    except Exception as e:
        import traceback
        print(f"Error in /platforms: {e}")
        traceback.print_exc()
        raise _internal_server_error(e, "Failed to load platforms")


@app.get("/platforms/{key}/validate")
def validate_platform_url(key: str, url: str = ""):
    """Validate URL for a platform. Returns { valid, error?, normalized?, handle? }."""
    # Normalize first so query params like ?lang=en don't fail validation
    raw_url = (url or "").strip()
    norm = normalize_url(url, key)
    ok, err = validate_url(norm, key)
    if not ok:
        return {"valid": False, "error": err or "Invalid"}
    availability = _validate_platform_availability(key, norm)
    if not availability.get("exists"):
        return {
            "valid": False,
            "normalized": norm,
            "error": availability.get("error") or "Could not verify this link",
            "checked_via": availability.get("checked_via"),
            "resolved_url": availability.get("resolved_url"),
        }
    h = extract_handle(norm, key)
    soft_checks = {"format_only_fallback", "http_fetch_soft", "page_content_soft", "profile_signal_soft"}
    checked_via = availability.get("checked_via")
    scrape_ready = checked_via not in soft_checks

    if key == "tiktok" and checked_via == "profile_signal_soft":
        scrape_ready = False
        if availability.get("warning"):
            availability["warning"] = "Valid format, but TikTok could not verify that this account exists publicly."
    out = {
        "valid": True,
        "scrape_ready": scrape_ready,
        "normalized": choose_valid_normalized_url(key, norm, availability.get("resolved_url") or ""),
        "checked_via": checked_via,
    }
    if availability.get("warning"):
        out["message"] = availability.get("warning")
    elif key == "tiktok" and raw_url and out["normalized"] != raw_url:
        out["message"] = "Valid public link. Converted to creator profile URL."
    if h:
        out["handle"] = h
    return out


# ============================================================================
# Creator Management Endpoints
# ============================================================================

@app.get("/creators", response_model=CreatorsListResponse)
async def list_creators(current_user: Dict[str, Any] = Depends(require_auth)):
    """List all creators"""
    try:
        dcol = _creator_display_column()
        query = f"""
            SELECT c.id, c.{dcol} as name, c.handle, c.created_at, c.profile_picture_url, c.visual_config, c.style_fingerprint,
                   c.search_mode,
                   (SELECT COUNT(*) FROM scrape_queue q WHERE q.creator_id = c.id AND q.status = 'ingested') as item_count
            FROM creators c
            WHERE c.user_id = %s
            ORDER BY c.created_at DESC
        """
        results = db.execute_query(query, (current_user["id"],))
        
        creators = []
        for row in results:
            creators.append(Creator(
                id=row["id"],
                name=row["name"] or row.get("handle") or "Unknown",
                handle=row.get("handle"),
                profile_picture_url=row.get("profile_picture_url"),
                platforms=[], # Platforms column might be missing, omit for list
                item_count=row.get("item_count", 0),
                created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                visual_config=row.get("visual_config") if isinstance(row.get("visual_config"), dict) else (json.loads(row.get("visual_config")) if isinstance(row.get("visual_config"), str) else {}),
                style_fingerprint=row.get("style_fingerprint") if isinstance(row.get("style_fingerprint"), dict) else (json.loads(row.get("style_fingerprint")) if isinstance(row.get("style_fingerprint"), str) else {}),
                search_mode=row.get("search_mode") or "hybrid"
            ))
        
        return CreatorsListResponse(creators=creators)
    except Exception as e:
        print(f"[RECOVERABLE] list_creators error: {e}")
        # Return empty list rather than 500 to keep UI alive
        return CreatorsListResponse(creators=[])

@app.post("/creators", response_model=Creator)
async def create_creator(request: CreateCreatorRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    """Create a new creator (not used in simplified UI)"""
    try:
        # Name validation and normalization
        name_raw = request.name
        norm_res = normalize_creator_name(name_raw)
        if not norm_res.is_valid:
            raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
        name = norm_res.normalized
        
        handle = normalize_creator_handle(request.handle)
        platforms_json = json.dumps(request.platforms or [])

        if handle:
            existing = db.execute_one(
                "SELECT id FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
                (current_user["id"], handle),
            )
            if existing:
                raise HTTPException(status_code=409, detail="You already have a creator with that handle.")

        query = """
            INSERT INTO creators (user_id, name, handle, platforms)
            VALUES (%s, %s, %s, %s)
            RETURNING id, name, handle, platforms, created_at
        """
        result = db.execute_query(query, (current_user["id"], name, handle, platforms_json))
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create creator")
        
        row = result[0]
        platforms = row.get("platforms") or []
        if isinstance(platforms, str):
            platforms = json.loads(platforms) if platforms else []
        
        return Creator(
            id=row["id"],
            name=row["name"],
            handle=row.get("handle"),
            platforms=platforms if isinstance(platforms, list) else [],
            item_count=0,
            created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"])
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to create creator")

def get_creator_status(creator_id: int) -> dict:
    row = db.execute_one(
        "SELECT config_version, last_approved_version, fingerprint_status, fingerprint_updated_at FROM creators WHERE id = %s",
        (creator_id,)
    )
    if not row:
        return {"ready_to_chat": False, "block_reason": "Creator not found."}
    
    config_version = row.get("config_version", 1)
    last_approved = row.get("last_approved_version", 0)
    fingerprint_status = row.get("fingerprint_status", "empty")
    fingerprint_updated_at = row.get("fingerprint_updated_at")
    
    # Needs reapproval if config incremented past last approved
    needs_reapproval = last_approved < config_version
    
    # Get approved item count (join via scrape_runs so it works even if scrape_items.creator_handle differs).
    approved_count = db.execute_one(
        """
        SELECT COUNT(*) AS count
        FROM scrape_items si
        JOIN scrape_runs sr ON si.scrape_run_id = sr.id
        WHERE si.review_status = 'approved'
          AND sr.creator_handle = (SELECT handle FROM creators WHERE id = %s)
        """,
        (creator_id,)
    )
    approved_item_count = approved_count["count"] if approved_count else 0
    
    # Get ingested doc count
    doc_count = db.execute_one(
        "SELECT COUNT(*) as count FROM documents WHERE creator_id = %s",
        (creator_id,)
    )
    ingested_doc_count = doc_count["count"] if doc_count else 0
    
    fingerprint_built = fingerprint_status == "ready" or (
        fingerprint_status == "idle" and fingerprint_updated_at is not None
    )
    ready_to_chat = (
        not needs_reapproval
        and ingested_doc_count >= 1
        and fingerprint_built
        and fingerprint_status != "processing"
        and fingerprint_status != "error"
    )
    
    block_reason = ""
    if needs_reapproval:
        block_reason = "Changes detected. Approve content to continue."
    elif ingested_doc_count == 0:
        block_reason = "Waiting for content to be ingested."
    elif fingerprint_status == "processing":
        block_reason = "Fingerprint analysis is still running."
    elif fingerprint_status == "error":
        block_reason = "Fingerprint failed to build. Try approving again."
    elif not fingerprint_built:
        block_reason = "Approve content to build the fingerprint."
        
    return {
        "fingerprint_status": fingerprint_status,
        "approved_item_count": approved_item_count,
        "ingested_doc_count": ingested_doc_count,
        "needs_reapproval": needs_reapproval,
        "ready_to_chat": ready_to_chat,
        "block_reason": block_reason
    }

def _validate_and_normalize_platform_configs(configs: Dict[str, Any]) -> Dict[str, Any]:
    """Validate URLs and time filters (exactly one mode per platform). Store handle per platform."""
    out = {}
    for key, cfg in (configs or {}).items():
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        url = (cfg.get("url") or "").strip()
        if not url:
            continue
        ok, err = validate_url(url, key)
        if not ok:
            raise HTTPException(status_code=400, detail=f"{key}: {err or 'invalid URL'}")
        norm = normalize_url(url, key)
        h = extract_handle(norm, key)
        tf = cfg.get("timeFilter") or {}
        mode = (tf.get("mode") or "all").strip().lower()
        since = tf.get("since")
        days = tf.get("days")
        if mode == "all":
            time_filter = {"mode": "all"}
        elif mode == "since":
            since_s = (since or "").strip() if since else ""
            if not since_s:
                raise HTTPException(status_code=400, detail=f"{key}: timeFilter mode 'since' requires a date (YYYY-MM-DD)")
            time_filter = {"mode": "since", "since": since_s, "days": None}
        elif mode == "last_days":
            d = days if isinstance(days, int) else (int(days) if days is not None else None)
            if d is None:
                raise HTTPException(status_code=400, detail=f"{key}: timeFilter mode 'last_days' requires days (7, 30, 90)")
            if d not in (7, 30, 90):
                raise HTTPException(status_code=400, detail=f"{key}: timeFilter days must be 7, 30, or 90")
            time_filter = {"mode": "last_days", "since": None, "days": d}
        else:
            raise HTTPException(status_code=400, detail=f"{key}: timeFilter mode must be since, last_days, or all")
        ok_t, err_t = validate_time_filter(time_filter, key)
        if not ok_t:
            raise HTTPException(status_code=400, detail=f"{key}: {err_t or 'invalid time filter'}")
        max_items = cfg.get("maxItems")
        plat = get_platform(key)
        default_max = plat.get("default_max_items", 10) if plat else 10
        if max_items is None:
            max_items = default_max
        entry = {
            "enabled": True,
            "url": norm,
            "timeFilter": time_filter,
            "maxItems": min(int(max_items), 50),
        }
        if h:
            entry["handle"] = h
        out[key] = entry
    return out


def _derive_handle_from_configs(configs: Dict[str, Any]) -> Optional[str]:
    for key, cfg in (configs or {}).items():
        if not cfg.get("enabled") or not cfg.get("url"):
            continue
        h = cfg.get("handle") or extract_handle(cfg["url"], key)
        if h:
            return h
    return None


def _slugify_creator_name(name: str) -> Optional[str]:
    value = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return value or None


@app.post("/creators/config", response_model=CreatorWithConfigResponse)
async def create_creator_with_config(request: CreateCreatorWithConfigRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    """Create creator with platform_configs. Validate & normalize URLs, then save."""
    try:
        configs = _validate_and_normalize_platform_configs(request.platform_configs)
        if not configs:
            raise HTTPException(status_code=400, detail="At least one enabled platform with URL is required.")
        
        # Identity Auto-Fill Hook at Creation
        dummy_profile = {"platform_configs": configs}
        # Note: We don't have a creator_id yet, but autofill works purely on the dict currently.
        # Pass 0 or None as the ID.
        updated_profile = autofill_creator_identity(0, dummy_profile)
        configs = updated_profile.get("platform_configs", configs)

        name_raw = request.name
        if not name_raw:
            raise HTTPException(status_code=400, detail={"field": "name", "message": "Creator name is required."})
        norm_res = normalize_creator_name(name_raw)
        if not norm_res.is_valid:
            raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
        name = norm_res.normalized
        handle = normalize_creator_handle(request.handle or _derive_handle_from_configs(configs) or _slugify_creator_name(name))
        if not handle:
            raise HTTPException(status_code=400, detail="Could not derive a stable creator id from the selected URLs or name.")

        user_id = current_user["id"]

        has_pc = _creator_has_column("platform_configs")
        has_name_col = _creator_has_column("name")
        has_display_name_col = _creator_has_column("display_name")

        dcol = _creator_display_column()

        def _creator_name_updates() -> List[str]:
            updates = []
            if has_name_col:
                updates.append("name = %s")
            if has_display_name_col:
                updates.append("display_name = %s")
            if not updates:
                updates.append(f"{dcol} = %s")
            return updates

        def _creator_name_params() -> List[Any]:
            params = []
            if has_name_col:
                params.append(name)
            if has_display_name_col:
                params.append(name)
            if not params:
                params.append(name)
            return params

        # If creator already exists for this user + handle, update config instead of failing on unique constraint.
        existing = db.execute_one(
            "SELECT id, platform_configs FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
            (user_id, handle),
        )
        if existing and existing.get("id"):
            creator_id = existing["id"]
            updates = _creator_name_updates()
            params = _creator_name_params()
            if has_pc:
                existing_configs = _jsonish_to_plain(existing.get("platform_configs") or {})
                if _values_differ(existing_configs, configs):
                    updates.append("platform_configs = %s")
                    params.append(json.dumps(configs))
                    content_affecting_change = True
                    content_affecting_change = True
            params.append(creator_id)
            db.execute_update(f"UPDATE creators SET {', '.join(updates)} WHERE id = %s", tuple(params))
        else:
            try:
                insert_cols = ["user_id", "handle"]
                insert_vals: List[Any] = [user_id, handle]

                if has_name_col:
                    insert_cols.append("name")
                    insert_vals.append(name)
                if has_display_name_col:
                    insert_cols.append("display_name")
                    insert_vals.append(name)
                if not has_name_col and not has_display_name_col:
                    insert_cols.append(dcol)
                    insert_vals.append(name)

                insert_cols.extend([
                    "profile_picture_url",
                    "youtube_channel_id",
                    "youtube_handle",
                    "official_domains",
                    "course_domains",
                    "course_base_urls",
                ])
                insert_vals.extend([
                    request.profile_picture_url,
                    request.youtube_channel_id,
                    request.youtube_handle,
                    request.official_domains,
                    request.course_domains,
                    request.course_base_urls,
                ])

                if has_pc:
                    insert_cols.append("platform_configs")
                    insert_vals.append(json.dumps(configs))

                placeholders = ", ".join(["%s"] * len(insert_cols))
                creator_id = db.execute_insert(
                    f"INSERT INTO creators ({', '.join(insert_cols)}) VALUES ({placeholders}) RETURNING id",
                    tuple(insert_vals),
                )
            except Exception as e:
                # Handle races / uniqueness: if this user already has the handle, update it instead.
                msg = str(e)
                if "duplicate key value" in msg and "handle" in msg:
                    existing = db.execute_one(
                        "SELECT id, platform_configs FROM creators WHERE user_id = %s AND handle = %s LIMIT 1",
                        (user_id, handle),
                    )
                    if existing and existing.get("id"):
                        creator_id = existing["id"]
                        updates = _creator_name_updates()
                        params = _creator_name_params()
                        if has_pc:
                            updates.append("platform_configs = %s")
                            params.append(json.dumps(configs))
                        params.append(creator_id)
                        db.execute_update(f"UPDATE creators SET {', '.join(updates)} WHERE id = %s", tuple(params))
                    else:
                        raise
                else:
                    raise
        
        if not creator_id:
            raise HTTPException(status_code=500, detail="Failed to create creator.")

        creator = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, style_fingerprint, created_at, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls FROM creators WHERE id = %s", (creator_id,))
        if has_pc:
            pc = db.execute_one("SELECT platform_configs FROM creators WHERE id = %s", (creator_id,))
            configs_out = pc.get("platform_configs") if pc else configs
            if hasattr(configs_out, "copy"):
                configs_out = dict(configs_out) if configs_out else {}
            else:
                configs_out = json.loads(configs_out) if isinstance(configs_out, str) else (configs_out or {})
        else:
            configs_out = configs

        vc = creator.get("visual_config") or request.visual_config
        if isinstance(vc, str):
            vc = json.loads(vc)
        elif not isinstance(vc, dict):
            vc = {}

        sf = creator.get("style_fingerprint") or {}
        if isinstance(sf, str):
            sf = json.loads(sf)
        elif not isinstance(sf, dict):
            sf = {}

        return CreatorWithConfigResponse(
            id=creator_id,
            name=creator.get("display_name") or creator.get("handle") or name,
            handle=creator.get("handle"),
            platform_configs=configs_out,
            visual_config=vc,
            style_fingerprint=sf,
            youtube_channel_id=creator.get("youtube_channel_id"),
            youtube_handle=creator.get("youtube_handle"),
            official_domains=creator.get("official_domains") or [],
            course_domains=creator.get("course_domains") or [],
            course_base_urls=creator.get("course_base_urls") or [],
            created_at=creator["created_at"].isoformat() if creator.get("created_at") and hasattr(creator["created_at"], "isoformat") else None,
            name_raw=name_raw,
            name_suggested=norm_res.suggested if norm_res else None,
            name_flags=norm_res.flags if norm_res else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to create creator with config")


@app.put("/creators/{creator_id}", response_model=CreatorWithConfigResponse)
async def update_creator(creator_id: int, request: UpdateCreatorRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    """Update creator name, handle, and/or platform_configs."""
    try:
        dcol = _creator_display_column()
        existing = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, style_fingerprint, visual_config, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, search_mode FROM creators WHERE id = %s AND user_id = %s", (creator_id, current_user['id']))
        if not existing:
            raise HTTPException(status_code=404, detail="Creator not found.")

        print(f"[DEBUG] update_creator id={creator_id} request={request.dict(exclude={'profile_picture_url'})} has_pic={bool(request.profile_picture_url)}", flush=True)

        updates = []
        params = []
        content_affecting_change = False
        name_raw = None
        norm_res = None
        if request.name is not None:
            name_raw = request.name
            norm_res = normalize_creator_name(name_raw)
            if not norm_res.is_valid:
                raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
            if _values_differ(existing.get("display_name"), norm_res.normalized):
                updates.append(f"{dcol} = %s")
                params.append(norm_res.normalized)
        if request.handle is not None:
            normalized_handle = normalize_creator_handle(request.handle)
            if _values_differ(existing.get("handle"), normalized_handle):
                updates.append("handle = %s")
                params.append(normalized_handle)
                content_affecting_change = True
        if request.profile_picture_url is not None:
            normalized_profile_picture_url = _normalize_optional_string(request.profile_picture_url)
            if _values_differ(existing.get("profile_picture_url"), normalized_profile_picture_url):
                updates.append("profile_picture_url = %s")
                params.append(normalized_profile_picture_url)
        if request.platform_configs is not None:
            configs = _validate_and_normalize_platform_configs(request.platform_configs)
            # Identity Auto-Fill Hook
            dummy_profile = {"platform_configs": configs}
            updated_profile = autofill_creator_identity(creator_id, dummy_profile)
            configs = updated_profile.get("platform_configs", configs)

            try:
                r = db.execute_one(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                    ("creators", "platform_configs"),
                )
            except Exception:
                r = None
            if r:
                existing_configs = _jsonish_to_plain(existing.get("platform_configs") or {})
                if _values_differ(existing_configs, configs):
                    updates.append("platform_configs = %s")
                    params.append(json.dumps(configs))
        if request.visual_config is not None:
            existing_visual_config = _jsonish_to_plain(existing.get("visual_config") or {})
            if _values_differ(existing_visual_config, request.visual_config or {}):
                updates.append("visual_config = %s")
                params.append(json.dumps(request.visual_config))

        if request.youtube_channel_id is not None:
            normalized_youtube_channel_id = _normalize_optional_string(request.youtube_channel_id)
            if _values_differ(existing.get("youtube_channel_id"), normalized_youtube_channel_id):
                updates.append("youtube_channel_id = %s")
                params.append(normalized_youtube_channel_id)
                content_affecting_change = True
        if request.youtube_handle is not None:
            normalized_youtube_handle = _normalize_optional_string(request.youtube_handle)
            if _values_differ(existing.get("youtube_handle"), normalized_youtube_handle):
                updates.append("youtube_handle = %s")
                params.append(normalized_youtube_handle)
                content_affecting_change = True
        if request.official_domains is not None:
            if _values_differ(existing.get("official_domains") or [], request.official_domains or []):
                updates.append("official_domains = %s")
                params.append(request.official_domains)
                content_affecting_change = True
        if request.course_domains is not None:
            if _values_differ(existing.get("course_domains") or [], request.course_domains or []):
                updates.append("course_domains = %s")
                params.append(request.course_domains)
                content_affecting_change = True
        if request.course_base_urls is not None:
            if _values_differ(existing.get("course_base_urls") or [], request.course_base_urls or []):
                updates.append("course_base_urls = %s")
                params.append(request.course_base_urls)
                content_affecting_change = True
        if request.search_mode is not None:
            normalized_search_mode = _normalize_optional_string(request.search_mode)
            if _values_differ(existing.get("search_mode") or "hybrid", normalized_search_mode or "hybrid"):
                updates.append("search_mode = %s")
                params.append(normalized_search_mode)

        if not updates:
            configs_out = existing.get("platform_configs") or {}
            if hasattr(configs_out, "copy"):
                configs_out = dict(configs_out) if configs_out else {}
            else:
                configs_out = json.loads(configs_out) if isinstance(configs_out, str) else {}
            visual_config = existing.get("visual_config") or {}
            if hasattr(visual_config, "copy"):
                visual_config = dict(visual_config) if visual_config else {}
            else:
                visual_config = json.loads(visual_config) if isinstance(visual_config, str) else {}
            style_fingerprint = existing.get("style_fingerprint") or {}
            if hasattr(style_fingerprint, "copy"):
                style_fingerprint = dict(style_fingerprint) if style_fingerprint else {}
            else:
                style_fingerprint = json.loads(style_fingerprint) if isinstance(style_fingerprint, str) else {}
            status_obj = get_creator_status(creator_id)
            return CreatorWithConfigResponse(
                id=existing["id"],
                name=existing.get("display_name") or existing.get("handle") or "",
                handle=existing.get("handle"),
                profile_picture_url=existing.get("profile_picture_url"),
                platform_configs=configs_out,
                visual_config=visual_config,
                style_fingerprint=style_fingerprint,
                youtube_channel_id=existing.get("youtube_channel_id"),
                youtube_handle=existing.get("youtube_handle"),
                official_domains=existing.get("official_domains") or [],
                course_domains=existing.get("course_domains") or [],
                course_base_urls=existing.get("course_base_urls") or [],
                search_mode=existing.get("search_mode") or "hybrid",
                status=status_obj,
                created_at=None,
            )
        if content_affecting_change:
            updates.append("config_version = config_version + 1")
        
        params.append(creator_id)
        params.append(current_user["id"])
        db.execute_update(
            f"UPDATE creators SET {', '.join(updates)} WHERE id = %s AND user_id = %s",
            tuple(params),
        )
        row = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, visual_config, style_fingerprint, created_at, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, search_mode FROM creators WHERE id = %s AND user_id = %s", (creator_id, current_user['id']))
        pc = row.get("platform_configs") or {}
        if hasattr(pc, "copy"):
            pc = dict(pc) if pc else {}
        else:
            pc = json.loads(pc) if isinstance(pc, str) else {}
        
        vc = row.get("visual_config") or {}
        if hasattr(vc, "copy"):
            vc = dict(vc) if vc else {}
        else:
            vc = json.loads(vc) if isinstance(vc, str) else {}
        
        sf = row.get("style_fingerprint") or {}
        if hasattr(sf, "copy"):
            sf = dict(sf) if sf else {}
        else:
            sf = json.loads(sf) if isinstance(sf, str) else {}

        
        status_obj = get_creator_status(creator_id)
        
        return CreatorWithConfigResponse(
            id=row["id"],
            name=row.get("display_name") or row.get("handle") or "",
            handle=row.get("handle"),
            profile_picture_url=row.get("profile_picture_url"),
            platform_configs=pc,
            visual_config=vc,
            style_fingerprint=sf,
            youtube_channel_id=row.get("youtube_channel_id"),
            youtube_handle=row.get("youtube_handle"),
            official_domains=row.get("official_domains") or [],
            course_domains=row.get("course_domains") or [],
            course_base_urls=row.get("course_base_urls") or [],
            created_at=row["created_at"].isoformat() if row.get("created_at") and hasattr(row["created_at"], "isoformat") else None,
            name_raw=row.get("name_raw"),
            search_mode=row.get("search_mode") or "hybrid",
            status=status_obj
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to update creator")


@app.delete("/creators/{creator_id}")
async def delete_creator(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Delete a creator and all associated data."""
    try:
        # Check if creator exists (simple check)
        existing = db.execute_one("SELECT id FROM creators WHERE id = %s AND user_id = %s", (creator_id, current_user["id"]))
        if not existing:
            # If not found, create a dummy response or error
            # But maybe the user clicked delete twice. Let's return 404.
            raise HTTPException(status_code=404, detail="Creator not found")

        # Delete associated data in strict foreign-key dependency order
        
        def safe_delete(query, params):
            try:
                db.execute_update(query, params)
            except Exception as e:
                # Ignore if table doesn't exist, log otherwise
                if "does not exist" not in str(e):
                    print(f"Delete warning for {query}: {e}")

        # 1. User preferences & facts & turns
        safe_delete("DELETE FROM user_creator_preferences WHERE creator_id = %s", (creator_id,))
        safe_delete("DELETE FROM verified_facts WHERE creator_id = %s", (creator_id,))
        safe_delete("DELETE FROM conversation_turns WHERE creator_id = %s", (creator_id,))
        
        # 2. Chat Threads & Messages
        safe_delete("DELETE FROM chat_messages WHERE thread_id IN (SELECT id FROM chat_threads WHERE creator_id = %s)", (creator_id,))
        safe_delete("DELETE FROM chat_threads WHERE creator_id = %s", (creator_id,))
        
        # 3. Embeddings, Chunks & Documents & Queue
        safe_delete("DELETE FROM embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE creator_id = %s)", (creator_id,))
        safe_delete("DELETE FROM chunks WHERE creator_id = %s", (creator_id,))
        safe_delete("DELETE FROM documents WHERE creator_id = %s", (creator_id,))
        safe_delete("DELETE FROM scrape_queue WHERE creator_id = %s", (creator_id,))
        
        # 4. Finally, delete the creator
        count = db.execute_update("DELETE FROM creators WHERE id = %s AND user_id = %s", (creator_id, current_user["id"]))
        if count == 0:
             raise HTTPException(status_code=404, detail="Creator not found during delete")

        return {"ok": True, "message": f"Creator {creator_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to delete creator")


@app.get("/creators/{creator_id}/config", response_model=CreatorWithConfigResponse)
async def get_creator_config(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get creator with platform_configs."""
    dcol = _creator_display_column()
    row = db.execute_one(
        f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, visual_config, style_fingerprint, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, search_mode, created_at FROM creators WHERE id = %s AND user_id = %s",
        (creator_id, current_user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Creator not found.")
    pc = row.get("platform_configs") or {}
    if hasattr(pc, "copy"):
        pc = dict(pc) if pc else {}
    else:
        pc = json.loads(pc) if isinstance(pc, str) else {}
    
    vc = row.get("visual_config") or {}
    if hasattr(vc, "copy"):
        vc = dict(vc) if vc else {}
    else:
        vc = json.loads(vc) if isinstance(vc, str) else {}

    sf = row.get("style_fingerprint") or {}
    if hasattr(sf, "copy"):
        sf = dict(sf) if sf else {}
    else:
        sf = json.loads(sf) if isinstance(sf, str) else {}

    status_obj = get_creator_status(creator_id)

    return CreatorWithConfigResponse(
        id=row["id"],
        name=row.get("display_name") or row.get("handle") or "",
        handle=row.get("handle"),
        profile_picture_url=row.get("profile_picture_url"),
        platform_configs=pc,
        visual_config=vc,
        style_fingerprint=sf,
        youtube_channel_id=row.get("youtube_channel_id"),
        youtube_handle=row.get("youtube_handle"),
        official_domains=row.get("official_domains") or [],
        course_domains=row.get("course_domains") or [],
        course_base_urls=row.get("course_base_urls") or [],
        search_mode=row.get("search_mode") or "hybrid",
        status=status_obj,
        created_at=row["created_at"].isoformat() if row.get("created_at") and hasattr(row["created_at"], "isoformat") else None,
    )


@app.get("/creators/{creator_id}/workflow")
async def get_creator_workflow(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Single source of truth for the 5-step workflow FSM (Setup -> Search -> Approve -> Persona -> Chat).
    The frontend should derive ALL navigation/lock/badge state from this response.
    """
    ensure_creator_access(creator_id, current_user["id"])

    creator = db.execute_one(
        "SELECT id, handle, platform_configs, soul_md, config_version, last_approved_version, "
        "fingerprint_status, fingerprint_updated_at FROM creators WHERE id = %s",
        (creator_id,),
    )
    if not creator:
        raise HTTPException(status_code=404, detail="Creator not found")

    pc = creator.get("platform_configs") or {}
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except Exception:
            pc = {}
    source_count = sum(
        1 for v in pc.values()
        if isinstance(v, dict) and v.get("enabled") and (v.get("url") or "").strip()
    )

    handle = creator.get("handle")
    last_run = None
    if handle:
        last_run = db.execute_one(
            "SELECT id, status, created_at FROM scrape_runs WHERE creator_handle = %s ORDER BY created_at DESC LIMIT 1",
            (handle,),
        )

    counts_row = db.execute_one(
        """
        SELECT
          COUNT(*) FILTER (WHERE si.review_status = 'pending')  AS pending,
          COUNT(*) FILTER (WHERE si.review_status = 'approved') AS approved,
          COUNT(*) FILTER (WHERE si.review_status = 'denied')   AS denied,
          COUNT(*)                                              AS total
        FROM scrape_items si
        JOIN scrape_runs sr ON si.scrape_run_id = sr.id
        WHERE sr.creator_handle = %s
        """,
        (handle,),
    ) if handle else None
    pending = int((counts_row or {}).get("pending") or 0)
    approved = int((counts_row or {}).get("approved") or 0)
    denied = int((counts_row or {}).get("denied") or 0)
    total_items = int((counts_row or {}).get("total") or 0)

    doc_row = db.execute_one(
        "SELECT COUNT(*) AS count FROM documents WHERE creator_id = %s",
        (creator_id,),
    )
    ingested_docs = int((doc_row or {}).get("count") or 0)

    fingerprint_status = creator.get("fingerprint_status") or "empty"
    fingerprint_updated_at = creator.get("fingerprint_updated_at")
    fingerprint_built = fingerprint_status == "ready" or (
        fingerprint_status == "idle" and fingerprint_updated_at is not None
    )
    has_persona = bool((creator.get("soul_md") or "").strip())

    config_version = int(creator.get("config_version") or 1)
    last_approved_version = int(creator.get("last_approved_version") or 0)
    needs_reapproval = last_approved_version < config_version and approved > 0

    setup_complete = source_count > 0
    search_complete = bool(last_run) and total_items > 0
    search_running = bool(last_run) and (last_run.get("status") in ("running", "queued", "pending"))
    approve_complete = approved >= 1 and pending == 0
    persona_complete = (
        has_persona and fingerprint_built and fingerprint_status not in ("processing", "error")
    )

    approve_stale = needs_reapproval
    persona_stale = persona_complete and (needs_reapproval or pending > 0)

    def _step(key, label, *, status, ready, blocked_reason=None, stale=False, count=None, hidden=False):
        return {
            "key": key,
            "label": label,
            "status": status,
            "ready": ready,
            "stale": stale,
            "blocked_reason": blocked_reason,
            "count": count,
            "hidden": hidden,
        }

    steps = []

    steps.append(_step(
        "setup", "Setup",
        status="complete" if setup_complete else "active",
        ready=True,
        count={"sources": source_count} if source_count else None,
    ))

    # Search is a visible status step, but it is never directly navigable.
    steps.append(_step(
        "search", "Search",
        status="active" if search_running else ("complete" if search_complete else "locked"),
        ready=False,
        hidden=False,
        blocked_reason="Search runs automatically from Setup and cannot be opened directly.",
        count={"items": total_items} if total_items else None,
    ))

    if total_items == 0:
        steps.append(_step(
            "approve", "Approve",
            status="locked", ready=False,
            blocked_reason="Run Search to gather items to approve.",
        ))
    else:
        if approve_complete and not approve_stale:
            approve_status = "complete"
        elif pending > 0:
            approve_status = "active"
        else:
            approve_status = "available"
        steps.append(_step(
            "approve", "Approve",
            status=approve_status,
            ready=True,
            stale=approve_stale,
            blocked_reason="Re-approve items: sources changed." if approve_stale else None,
            count={"pending": pending, "approved": approved, "denied": denied},
        ))

    if approved == 0 or ingested_docs == 0:
        steps.append(_step(
            "persona", "Persona",
            status="locked", ready=False,
            blocked_reason="Approve at least one item to build the persona.",
        ))
    else:
        if fingerprint_status == "processing":
            persona_status, persona_blocked = "active", None
        elif fingerprint_status == "error":
            persona_status, persona_blocked = "available", "Persona build failed. Re-run from Approve."
        elif persona_complete:
            persona_status, persona_blocked = "complete", None
        else:
            persona_status, persona_blocked = "available", None
        steps.append(_step(
            "persona", "Persona",
            status=persona_status,
            ready=True,
            stale=persona_stale,
            blocked_reason=persona_blocked,
            count={"docs": ingested_docs} if ingested_docs else None,
        ))

    chat_status = get_creator_status(creator_id)
    ready_to_chat = bool(chat_status.get("ready_to_chat"))
    if not ready_to_chat:
        steps.append(_step(
            "chat", "Chat",
            status="locked", ready=False,
            blocked_reason=chat_status.get("block_reason") or "Finish the previous steps to start chatting.",
        ))
    else:
        steps.append(_step("chat", "Chat", status="active", ready=True))

    current_step = "search" if search_running else "chat"
    if not search_running:
        for s in steps:
            if s.get("hidden"):
                continue
            if s["status"] in ("active", "available") or s["stale"]:
                current_step = s["key"]
                break
            if s["status"] == "locked":
                current_step = s["key"]
                break

    return {
        "creator_id": creator_id,
        "current_step": current_step,
        "ready_to_chat": ready_to_chat,
        "steps": steps,
    }


@app.get("/creators/{creator_id}/stats", response_model=CreatorStats)
async def get_creator_stats(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get stats for a creator"""
    try:
        query = "SELECT id, name, handle, platforms FROM creators WHERE id = %s AND user_id = %s"
        creator = db.execute_one(query, (creator_id, current_user["id"]))
        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")
        
        scrape_query = """
            SELECT MAX(created_at) as last_scrape
            FROM scrape_queue
            WHERE creator_id = %s
        """
        scrape_result = db.execute_one(scrape_query, (creator_id,))
        last_scrape = scrape_result.get("last_scrape") if scrape_result else None
        
        ingested_query = """
            SELECT COUNT(*) as count
            FROM scrape_queue
            WHERE creator_id = %s AND status = 'ingested'
        """
        ingested_result = db.execute_one(ingested_query, (creator_id,))
        items_ingested = ingested_result.get("count", 0) if ingested_result else 0
        
        chunks_query = """
            SELECT COUNT(*) as count
            FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.creator_id = %s
        """
        chunks_result = db.execute_one(chunks_query, (creator_id,))
        total_chunks = chunks_result.get("count", 0) if chunks_result else 0
        
        platforms = creator.get("platforms") or []
        if isinstance(platforms, str):
            platforms = json.loads(platforms) if platforms else []
        
        return CreatorStats(
            creator_id=creator["id"],
            name=creator["name"],
            handle=creator.get("handle"),
            platforms=platforms if isinstance(platforms, list) else [],
            last_scrape_time=last_scrape.isoformat() if last_scrape and hasattr(last_scrape, "isoformat") else (str(last_scrape) if last_scrape else None),
            items_ingested=items_ingested,
            total_chunks=total_chunks
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to load creator stats")


@app.get("/creators/{creator_id}/evidence-dashboard")
async def get_creator_evidence_dashboard(
    creator_id: int,
    limit: int = 40,
    refresh_entities: bool = False,
    current_user: Dict[str, Any] = Depends(require_auth),
):
    try:
        ensure_creator_access(creator_id, current_user["id"])
        creator_row = db.execute_one(
            f"""
            SELECT
                id,
                name,
                handle,
                {_creator_select_expr('identity_fingerprint')},
                {_creator_select_expr('research_summary')},
                {_creator_select_expr('style_fingerprint')},
                {_creator_select_expr('soul_md')},
                {_creator_select_expr('platform_configs')}
            FROM creators
            WHERE id = %s AND user_id = %s
            """,
            (creator_id, current_user["id"]),
        )
        if not creator_row:
            raise HTTPException(status_code=404, detail="Creator not found")

        entity_graph = creator_entity_service.build_entity_graph(
            creator_id=creator_id,
            creator_profile=creator_row,
            refresh=refresh_entities,
        )
        return {
            "creator_id": creator_id,
            "entity_graph": entity_graph,
            "recent_evidence_plans": recent_evidence_activity(creator_id, limit=limit),
            "fact_registry": fact_registry.list_facts(creator_id, limit=limit),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to load creator evidence dashboard")

# ============================================================================
# Core Endpoints
# ============================================================================

@app.get("/user/settings", response_model=UserSettings)
async def get_user_settings(current_user: Dict[str, Any] = Depends(require_auth)):
    row = db.execute_one(
        "SELECT display_name, profile_picture_url, response_preferences FROM users WHERE id = %s",
        (current_user["id"],),
    )
    if not row:
        return UserSettings()
    
    prefs = row.get("response_preferences") or {}
    if hasattr(prefs, "copy"):
        prefs = dict(prefs) if prefs else {}
    else:
        prefs = json.loads(prefs) if isinstance(prefs, str) else {}
    prefs = normalize_user_preferences(prefs, RESPONSE_PRESETS.keys())

    return UserSettings(
        display_name=row.get("display_name"),
        profile_picture_url=row.get("profile_picture_url"),
        response_preferences=prefs
    )

@app.put("/user/settings", response_model=UserSettings)
async def update_user_settings(request: UpdateUserSettingsRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    updates = []
    params = []
    
    if request.display_name is not None:
        updates.append("display_name = %s")
        params.append(request.display_name)
    
    if request.profile_picture_url is not None:
        updates.append("profile_picture_url = %s")
        params.append(request.profile_picture_url)
        
    if request.response_preferences is not None:
        updates.append("response_preferences = %s")
        params.append(json.dumps(normalize_user_preferences(request.response_preferences, RESPONSE_PRESETS.keys())))
        
    if not updates:
        return await get_user_settings(current_user)
        
    params.append(current_user["id"])
    
    db.execute_update(
        f"UPDATE users SET {', '.join(updates)} WHERE id = %s",
        tuple(params)
    )
    return await get_user_settings(current_user)

@app.get("/health")
async def health():
    """Health check endpoint - minimal, no DB dependency."""
    try:
        print("[HEALTH] GET /health - request received", flush=True)
        return {"ok": True}
    except Exception as e:
        print(f"[HEALTH] ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        raise

@app.post("/ask-stream")
@limiter.limit("60/minute")
async def ask_stream_endpoint(request: Request, payload: AskRequest, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Streaming version of /ask. 
    Bypasses deep classification/planning for immediate time-to-first-token.
    """
    try:
        ensure_creator_access(payload.creator_id, current_user["id"])
        status_obj = get_creator_status(payload.creator_id)
        if not status_obj["ready_to_chat"]:
            raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})
            
        import asyncio
        
        # 1. Fetch creator soul metadata + Check fingerprint (Async)
        def _get_creator_meta():
            creator_row = db.execute_one("SELECT soul_md, fingerprint_status FROM creators WHERE id = %s", (payload.creator_id,))
            if creator_row and not creator_row.get("soul_md") and creator_row.get("fingerprint_status") != "processing":
                print(f"[CHAT] Missing soul for creator {payload.creator_id}, enqueueing FINGERPRINT job...")
                db.execute_insert(
                    "INSERT INTO system_jobs (creator_id, job_type, payload, message) VALUES (%s, 'FINGERPRINT', %s::jsonb, 'Auto-enqueued from chat')",
                    (payload.creator_id, json.dumps({"creator_id": payload.creator_id}))
                )
            return creator_row
            
        # 2. Fetch user prefs & history (Async)
        def _get_user_meta():
            user_row = db.execute_one("SELECT response_preferences, display_name FROM users WHERE id = %s", (current_user["id"],))
            user_prefs = None
            user_name = None
            if user_row:
                up = user_row.get("response_preferences")
                user_name = user_row.get("display_name")
                if isinstance(up, str):
                    try: user_prefs = json.loads(up)
                    except: pass
                elif isinstance(up, dict): user_prefs = up
            user_prefs = normalize_user_preferences(user_prefs, RESPONSE_PRESETS.keys())
            return user_prefs, user_name

        # 3. Thread Logic & History (Async)
        def _get_thread_history():
            conversation_history = []
            if payload.thread_id:
                try:
                    uuid.UUID(str(payload.thread_id))
                    
                    # Auto-initialize thread if missing
                    db.execute_update("""
                        INSERT INTO chat_threads (id, user_id, creator_id, title)
                        VALUES (%s, %s, %s, 'New conversation')
                        ON CONFLICT (id) DO NOTHING
                    """, (payload.thread_id, current_user["id"], payload.creator_id))

                    msgs_rows = db.execute_query("""
                        SELECT role, content, metadata FROM chat_messages 
                        WHERE thread_id = %s
                          AND EXISTS (
                              SELECT 1 FROM chat_threads t
                              WHERE t.id = %s AND t.user_id = %s
                          )
                        ORDER BY created_at DESC 
                        LIMIT 30
                    """, (payload.thread_id, payload.thread_id, current_user["id"]))
                    if msgs_rows:
                        msgs_rows.reverse()
                        conversation_history = [_history_message_from_row(m) for m in msgs_rows]
                except ValueError:
                    payload.thread_id = None
            return conversation_history

        # Execute DB calls sequentially to prevent psycopg connection threading issues
        # (psycopg single connections are not thread-safe for concurrent queries)
        _get_creator_meta()
        user_prefs, user_name = _get_user_meta()
        conversation_history = _get_thread_history()

        images_payload = None
        user_image_metadata = {}
        if payload.images and len(payload.images) > 0:
            images_payload = [{"data_url": img.data_url, "detail": img.detail} for img in payload.images[:4]]
            user_image_metadata["images"] = images_payload
        elif question_refers_to_recent_image(payload.question):
            recent_images = get_latest_thread_images(payload.thread_id, current_user["id"])
            if recent_images:
                images_payload = recent_images[:4]

        # 3. Generator Wrapper to capture full answer
        async def stream_wrapper():
            import copy
            explicit_cards = []
            explicit_citations = []
            explicit_support = []
            assembled = []
            pending_stream_text = ""
            creator_cleaning_profile = _get_creator_cleaning_profile(payload.creator_id, current_user["id"])
            strip_hyphens = should_strip_hyphens(creator_cleaning_profile)
            try:
                yield f"data: {json.dumps({'status': 'thinking'})}\n\n"
                if images_payload:
                    creator_name = "Creator"
                    try:
                        creator_row = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (payload.creator_id,))
                        if creator_row:
                            creator_name = (creator_row.get("name") or "").strip()
                            if not creator_name:
                                creator_name = (creator_row.get("handle") or "").strip().lstrip("@") or "Creator"
                    except Exception:
                        pass

                    image_question = payload.question
                    if not image_question or not image_question.strip():
                        image_question = "Describe this image and point out anything important."

                    result = grounded_rag_ask(
                        creator_id=payload.creator_id,
                        question=image_question,
                        thread_id=payload.thread_id,
                        conversation_history=copy.deepcopy(conversation_history) if conversation_history else [],
                        top_k=payload.top_k or 6,
                        max_distance=payload.max_distance or 1.15,
                        debug=payload.debug or False,
                        user_preferences=user_prefs,
                        user_name=user_name,
                        creator_name=creator_name,
                        images=images_payload,
                        user_id=current_user["id"],
                    )
                    cards = merge_preview_cards(result.get("cards") or [], enrich_titles=True)
                    citations = result.get("citations") or []
                    full_answer = prepare_chat_response(
                        result.get("answer") or "",
                        cards=cards,
                        strip_hyphens=strip_hyphens,
                    )
                    for token in re.findall(r".{1,120}(?:\s+|$)", full_answer):
                        if token:
                            yield f"data: {json.dumps({'content': token})}\n\n"

                    if payload.thread_id:
                        finalize_stream_interaction(
                            payload.thread_id,
                            image_question,
                            full_answer,
                            cards=cards,
                            citations=citations,
                            user_metadata=user_image_metadata,
                            user_id=current_user["id"],
                            creator_profile=creator_cleaning_profile,
                        )
                        thread = db.execute_one("SELECT title, title_locked FROM chat_threads WHERE id = %s", (payload.thread_id,))
                        if thread and thread['title'] == 'New conversation' and not thread['title_locked']:
                            background_tasks.add_task(_update_thread_title_background, payload.thread_id)

                    if cards:
                        yield f"data: {json.dumps({'cards': cards})}\n\n"
                    if citations:
                        yield f"data: {json.dumps({'citations': citations})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                # Explicitly deepcopy conversation history to prevent frozenset cache poisoning
                safe_history = copy.deepcopy(conversation_history) if conversation_history else []
                async for chunk in grounded_rag_stream(
                    creator_id=payload.creator_id,
                    question=payload.question,
                    thread_id=payload.thread_id,
                    conversation_history=safe_history,
                    user_preferences=user_prefs,
                    user_name=user_name,
                    user_id=current_user["id"]
                ):
                    if chunk == " ":
                        # Early TTFB heartbeat
                        yield f"data: {json.dumps({'content': ' '})}\n\n"
                        continue
                    if isinstance(chunk, str) and chunk.startswith("__STATUS__"):
                        status = chunk[len("__STATUS__"):].strip().lower()
                        if status:
                            yield f"data: {json.dumps({'status': status})}\n\n"
                        continue
                    if isinstance(chunk, str) and chunk.startswith("__CARDS__"):
                        try:
                            payload = json.loads(chunk[len("__CARDS__"):])
                            if isinstance(payload, list):
                                explicit_cards = merge_preview_cards(explicit_cards, payload, enrich_titles=True)
                        except Exception:
                            logger.warning("Failed to parse streamed cards payload.")
                        continue
                    if isinstance(chunk, str) and chunk.startswith("__CITATIONS__"):
                        try:
                            payload = json.loads(chunk[len("__CITATIONS__"):])
                            if isinstance(payload, list):
                                explicit_citations = payload
                        except Exception:
                            logger.warning("Failed to parse streamed citations payload.")
                        continue
                    if isinstance(chunk, str) and chunk.startswith("__SUPPORT__"):
                        try:
                            payload = json.loads(chunk[len("__SUPPORT__"):])
                            if isinstance(payload, list):
                                explicit_support = payload
                        except Exception:
                            logger.warning("Failed to parse streamed support payload.")
                        continue
                    if isinstance(chunk, str) and chunk.startswith("__FINAL_CONTENT__"):
                        # grounded_rag_stream detected placeholder artifacts and replaced
                        # the answer with a clean fallback — override assembled text
                        assembled = [chunk[len("__FINAL_CONTENT__"):]]
                        continue

                    cleaned_chunk = clean_for_stream_chunk(chunk)
                    if cleaned_chunk:
                        pending_stream_text += cleaned_chunk
                        emit_boundary = _find_stream_emit_boundary(pending_stream_text)
                        if emit_boundary > 0:
                            safe_chunk = pending_stream_text[:emit_boundary]
                            pending_stream_text = pending_stream_text[emit_boundary:]
                            assembled.append(safe_chunk)
                            yield f"data: {json.dumps({'content': safe_chunk})}\n\n"

                # 4. Finalize (Post-stream)
                # After the stream is exhausted, we do the background work
                if pending_stream_text:
                    assembled.append(pending_stream_text)
                    yield f"data: {json.dumps({'content': pending_stream_text})}\n\n"
                    pending_stream_text = ""
                raw_streamed_answer = clean_response("".join(assembled), strip_hyphens=strip_hyphens)
                streamed_answer = raw_streamed_answer
                # ── Post-stream biography guard: catch metadata-as-biography hallucinations ──
                streamed_answer, metadata_bio_fallback_applied = _repair_metadata_biography(streamed_answer, payload.question)
                recovered_citations = []
                if metadata_bio_fallback_applied:
                    recovery_profile = db.execute_one(
                        f"""
                        SELECT
                            name,
                            handle,
                            search_mode,
                            {_creator_select_expr('voice_profile')},
                            {_creator_select_expr('decision_policy')}
                        FROM creators
                        WHERE id = %s AND user_id = %s
                        """,
                        (payload.creator_id, current_user["id"]),
                    )
                    recovery_result = recover_streamed_creator_fact_answer(
                        user_id=current_user["id"],
                        creator_id=payload.creator_id,
                        question=payload.question,
                        creator_row=recovery_profile,
                        conversation_history=safe_history,
                    )
                    recovered_answer = str(recovery_result.get("answer") or "").strip()
                    if recovered_answer:
                        logger.info("Recovered creator fact answer after metadata-biography fallback for streamed response.")
                        streamed_answer = recovered_answer
                        metadata_bio_fallback_applied = False
                        recovered_citations = list(recovery_result.get("citations") or [])
                cards = (
                    merge_preview_cards(explicit_cards, enrich_titles=True)
                    if explicit_cards
                    else merge_preview_cards(_extract_stream_cards(streamed_answer), enrich_titles=True)
                )
                citations = recovered_citations if recovered_citations else (explicit_citations if explicit_citations else [])
                if metadata_bio_fallback_applied:
                    citations = []
                full_answer = prepare_chat_response(
                    streamed_answer,
                    cards=cards,
                    strip_hyphens=strip_hyphens,
                )
                
                # P2.3: Apply rhythm shaper before creator integrity 
                full_answer = rhythm_shaper.apply_rhythm(
                    full_answer,
                    profile=creator_cleaning_profile,
                )
                
                full_answer = _apply_stream_creator_integrity(
                    payload.creator_id,
                    current_user["id"],
                    payload.question,
                    full_answer,
                    cards=cards,
                    support_chunks=explicit_support,
                )
                quality_report = _score_saved_answer_quality(
                    payload.creator_id,
                    current_user["id"],
                    payload.question,
                    full_answer,
                    explicit_support or _card_chunks_for_integrity(cards),
                )
                if full_answer != raw_streamed_answer:
                    yield f"data: {json.dumps({'final_content': full_answer})}\n\n"
                if payload.thread_id:
                    finalize_stream_interaction(
                        payload.thread_id,
                        payload.question,
                        full_answer,
                        cards=cards,
                        citations=citations,
                        user_metadata=user_image_metadata,
                        user_id=current_user["id"],
                        quality_report=quality_report,
                        creator_profile=creator_cleaning_profile,
                    )
                    # Check for title update
                    thread = db.execute_one("SELECT title, title_locked FROM chat_threads WHERE id = %s", (payload.thread_id,))
                    if thread and thread['title'] == 'New conversation' and not thread['title_locked']:
                        background_tasks.add_task(_update_thread_title_background, payload.thread_id)
                
                if cards:
                    yield f"data: {json.dumps({'cards': cards})}\n\n"
                if citations:
                    yield f"data: {json.dumps({'citations': citations})}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                logger.error(f"Error mid-stream: {stream_err}", exc_info=True)
                yield f"data: {json.dumps({'error': str(stream_err)})}\n\n"

        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Streaming failed before started: {e}")
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=_safe_error_detail(e, "Chat stream failed before start"))

def _extract_stream_cards(answer: str):
    """Best-effort card extraction for streamed answers."""
    return extract_preview_cards(answer, enrich_titles=True)


def _card_chunks_for_integrity(cards):
    chunks = []
    for card in cards or []:
        title = (card or {}).get("title") or ""
        url = (card or {}).get("url") or ""
        if not title and not url:
            continue
        chunks.append(
            {
                "title": title,
                "url": url,
                "source_ref": {
                    "title": title,
                    "canonical_url": url,
                },
            }
        )
    return chunks


def _quality_markers_for_creator_row(creator_row: Dict[str, Any]) -> List[str]:
    style_fingerprint = creator_row.get("style_fingerprint") or {}
    voice_profile = creator_row.get("voice_profile") or {}
    if isinstance(style_fingerprint, str):
        try:
            style_fingerprint = json.loads(style_fingerprint)
        except Exception:
            style_fingerprint = {}
    if isinstance(voice_profile, str):
        try:
            voice_profile = json.loads(voice_profile)
        except Exception:
            voice_profile = {}

    lexical_rules = (style_fingerprint or {}).get("lexical_rules") or {}
    value_model = (style_fingerprint or {}).get("value_model") or {}
    candidates = (
        list((style_fingerprint or {}).get("evidence_snippets") or [])
        + list((style_fingerprint or {}).get("signature_moves") or [])
        + list((style_fingerprint or {}).get("signature_response_moves") or [])
        + list((value_model or {}).get("decision_heuristics") or [])
        + list((lexical_rules or {}).get("signature_phrases") or [])
        + list((voice_profile or {}).get("signature_phrases") or [])
    )
    markers: List[str] = []
    for value in candidates:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in markers:
            markers.append(cleaned)
        if len(markers) >= 12:
            break
    return markers


def _score_saved_answer_quality(
    creator_id: int,
    user_id: int,
    question: str,
    answer: str,
    support_chunks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    select_expr = ", ".join(
        [
            _creator_select_expr("style_fingerprint"),
            _creator_select_expr("voice_profile"),
        ]
    )
    creator_row = db.execute_one(
        f"""
        SELECT {select_expr}
        FROM creators
        WHERE id = %s AND user_id = %s
        """,
        (creator_id, user_id),
    )
    creator_markers = _quality_markers_for_creator_row(creator_row or {})
    return score_response_quality(
        question,
        answer,
        support_chunks or [],
        creator_markers=creator_markers,
    )


import re as _re_app

# ── Metadata-as-biography repair ──
_METADATA_BIO_PATTERNS = [
    # "I was published in YYYY" → remove the sentence
    _re_app.compile(r"\b[Ii]\s+was\s+published\s+in\s+\d{4}\b[^.]*\.?", _re_app.IGNORECASE),
    # "I was uploaded in YYYY"
    _re_app.compile(r"\b[Ii]\s+was\s+uploaded\s+in\s+\d{4}\b[^.]*\.?", _re_app.IGNORECASE),
    # "I was posted in YYYY"
    _re_app.compile(r"\b[Ii]\s+was\s+posted\s+in\s+\d{4}\b[^.]*\.?", _re_app.IGNORECASE),
    # "I was released in YYYY"
    _re_app.compile(r"\b[Ii]\s+was\s+released\s+in\s+\d{4}\b[^.]*\.?", _re_app.IGNORECASE),
]

def _metadata_biography_fallback(question: str) -> str:
    policy = classify_creator_fact_query(question or "")
    focus = extract_timeline_focus(question or "")
    if policy.kind == "creator_start_timeline":
        if focus:
            return f"I don't want to fake the year on that. I couldn't pin down an exact date for when I started {focus}, so I won't make one up."
        return "I don't want to fake the year on that. I couldn't pin down an exact date, so I won't make one up."
    if policy.kind == "creator_journey":
        if focus:
            return f"I've talked about why I got into {focus} in my content, but I don't want to make up the story from memory."
        return "I've talked about that in my content, but I don't want to make up the story from memory."
    return "I don't want to fake that detail. If I can't pin it down cleanly, I'd rather keep it straight."


def _repair_metadata_biography(text: str, question: str = "") -> tuple[str, bool]:
    """Catch and remove sentences where the LLM confused content metadata with personal biography."""
    if not text:
        return text, False
    repaired = text
    for pat in _METADATA_BIO_PATTERNS:
        repaired = pat.sub("", repaired).strip()
    # If the entire answer was just the bad sentence, return a graceful fallback
    if not repaired or len(repaired) < 10:
        return _metadata_biography_fallback(question), True
    return repaired, repaired != text


def _apply_stream_creator_integrity(creator_id: int, user_id: int, question: str, answer: str, cards=None, support_chunks=None) -> str:
    try:
        select_expr = ", ".join(
            [
                "name",
                    _creator_select_expr("creator_category"),
                _creator_select_expr("voice_profile"),
                _creator_select_expr("style_fingerprint"),
                _creator_select_expr("identity_fingerprint"),
                _creator_select_expr("soul_md"),
            ]
        )
        creator_row = db.execute_one(
            f"""
            SELECT {select_expr}
            FROM creators
            WHERE id = %s AND user_id = %s
            """,
            (creator_id, user_id),
        )
        if not creator_row:
            return answer
        return interaction_engine._apply_creator_integrity_guard(
            answer,
            creator_row,
            support_chunks or _card_chunks_for_integrity(cards),
            question,
            allow_links=False,
            persona=creator_row.get("soul_md"),
        )
    except Exception as exc:
        logger.error(f"Stream creator integrity pass failed: {exc}")
        return answer


def finalize_stream_interaction(
    thread_id: str,
    question: str,
    answer: str,
    cards=None,
    citations=None,
    user_metadata=None,
    user_id: int = 1,
    quality_report: Optional[Dict[str, Any]] = None,
    creator_profile: Optional[Dict[str, Any]] = None,
):
    """Save the final interaction to DB after stream completion."""
    try:
        answer = prepare_chat_response(
            answer,
            cards=cards,
            strip_hyphens=should_strip_hyphens(creator_profile or {}),
        )
        user_metadata = user_metadata or {}
        # Save User Message
        db.execute_update("""
            INSERT INTO chat_messages (thread_id, role, content, metadata)
            VALUES (%s, 'user', %s, %s::jsonb)
        """, (thread_id, question, json.dumps(user_metadata)))

        assistant_metadata = {}
        if cards:
            assistant_metadata["cards"] = cards
        if citations:
            assistant_metadata["citations"] = citations
        if quality_report:
            assistant_metadata["quality_grade"] = quality_report.get("grade")
            assistant_metadata["quality_score"] = quality_report.get("score")

        # Save Assistant Message
        db.execute_update("""
            INSERT INTO chat_messages (thread_id, role, content, metadata)
            VALUES (%s, 'assistant', %s, %s::jsonb)
        """, (thread_id, answer, json.dumps(assistant_metadata)))

        # Update thread preview
        preview = answer[:60] + "..." if len(answer) > 60 else answer
        db.execute_update("""
            UPDATE chat_threads 
            SET last_message_at = NOW(), last_preview = %s 
            WHERE id = %s
        """, (preview, thread_id))
        
        # Sync memory in background
        from db import interaction_engine
        interaction_engine.store_interaction(str(user_id), str(user_id), thread_id, question, answer)
    except Exception as e:
        import traceback
        import logging
        logger = logging.getLogger(__name__)
        err_msg = str(e).lower()
        if "foreign key constraint" in err_msg or "violates foreign key" in err_msg:
            logger.warning(f"Thread {thread_id} was likely deleted during streaming. Ignoring save.")
        else:
            logger.error(f"Failed to finalize stream: {e}")
            logger.debug(traceback.format_exc())

@app.post("/creators/{creator_id}/fingerprint/generate")
async def generate_fingerprint_endpoint(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Manually trigger or regenerate a creator fingerprint via background worker queue.
    """
    try:
        ensure_creator_access(creator_id, current_user["id"])
        creator_row = db.execute_one(
            "SELECT id FROM creators WHERE id = %s AND user_id = %s",
            (creator_id, current_user["id"]),
        )
        if not creator_row:
            raise HTTPException(status_code=404, detail="Creator not found")

        job_id = db.execute_insert(
            """
            INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
            VALUES (%s, 'FINGERPRINT', %s::jsonb, 'queued', 0, 'Fingerprint generation enqueued')
            RETURNING id
            """,
            (creator_id, json.dumps({"creator_id": creator_id}))
        )
        return {"job_id": job_id, "status": "queued"}
    except Exception as e:
        raise _internal_server_error(e, "Failed to enqueue fingerprint job")

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(payload: AskRequest, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    # Pre-chat check: Ensure soul assets exist
    ensure_creator_access(payload.creator_id, current_user["id"])
    creator_row = db.execute_one("SELECT soul_md, fingerprint_status FROM creators WHERE id = %s", (payload.creator_id,))
    if creator_row and not creator_row.get("soul_md") and creator_row.get("fingerprint_status") != "processing":
        print(f"[ASK] Missing soul for creator {payload.creator_id}, enqueueing FINGERPRINT job...")
        db.execute_insert(
            "INSERT INTO system_jobs (creator_id, job_type, payload, message) VALUES (%s, 'FINGERPRINT', %s::jsonb, 'Auto-enqueued from chat')",
            (payload.creator_id, json.dumps({"creator_id": payload.creator_id}))
        )

    """
    Ask a question using Grounded-RAG Loop algorithm.
    Uses broad retrieval + re-ranking + answer contract + grounding validation.
    Handles thread persistence if thread_id is provided.
    """
    try:
        status_obj = get_creator_status(payload.creator_id)
        if not status_obj["ready_to_chat"]:
            raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})
            
        # Get user preferences
        user_row = db.execute_one("SELECT response_preferences, display_name FROM users WHERE id = %s", (current_user["id"],))
        user_prefs = None
        user_name = None
        if user_row:
             up = user_row.get("response_preferences")
             user_name = user_row.get("display_name")
             if isinstance(up, str):
                 try:
                     user_prefs = json.loads(up)
                 except: pass
             elif isinstance(up, dict):
                 user_prefs = up
        user_prefs = normalize_user_preferences(user_prefs, RESPONSE_PRESETS.keys())
        
        # Thread Logic (Session Persistence)
        conversation_history = payload.messages
        thread = None
        
        if payload.thread_id:
             # Validate UUID format
             try:
                 uuid.UUID(str(payload.thread_id))
                 
                 # Auto-initialize thread if missing
                 db.execute_update("""
                     INSERT INTO chat_threads (id, user_id, creator_id, title)
                     VALUES (%s, %s, %s, 'New conversation')
                     ON CONFLICT (id) DO NOTHING
                 """, (payload.thread_id, current_user["id"], payload.creator_id))
                 
                 # Verify thread exists
                 thread = db.execute_one("SELECT id, user_id, title, title_locked FROM chat_threads WHERE id = %s AND user_id = %s", (payload.thread_id, current_user["id"]))
             except ValueError:
                 print(f"[WARN] Invalid UUID received for thread_id: {payload.thread_id}. Treating as new thread.")
                 payload.thread_id = None
                 thread = None

             if thread:
                 # Update last active thread preference
                 db.execute_update("""
                    INSERT INTO user_creator_preferences (user_id, creator_id, last_active_thread_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, creator_id) 
                    DO UPDATE SET last_active_thread_id = EXCLUDED.last_active_thread_id, updated_at = NOW()
                 """, (current_user["id"], payload.creator_id, payload.thread_id))
                 
                 # Save user message with images (persisted in metadata)
                 user_metadata = {}
                 if payload.images and len(payload.images) > 0:
                     # Store images in metadata JSON so they persist on refresh
                     # Note: Storing base64 strings in DB can be heavy, but required for persistence without S3.
                     user_metadata["images"] = [
                         {"data_url": img.data_url, "detail": img.detail} 
                         for img in payload.images
                     ]

                 db.execute_update("""
                    INSERT INTO chat_messages (thread_id, role, content, metadata)
                    VALUES (%s, 'user', %s, %s::jsonb)
                 """, (payload.thread_id, payload.question, json.dumps(user_metadata)))
                 
                 # Fetch history from DB for RAG context (last 20 messages)
                 # We want the messages BEFORE the one we just inserted.
                 # So we fetch limit 21 desc, and look at them.
                 msgs_rows = db.execute_query("""
                    SELECT role, content, metadata FROM chat_messages 
                    WHERE thread_id = %s 
                    ORDER BY created_at DESC 
                    LIMIT 21
                 """, (payload.thread_id,))
                 
                 if msgs_rows:
                     # Reverse to chronological order [oldest ... newest]
                     msgs_rows.reverse()
                     
                     # The last message in msgs_rows should be the one we just inserted (user question).
                     # We want history *excluding* the current question for the RAG 'conversation_history' param.
                     # (grounded_rag_ask treats 'question' as new, 'conversation_history' as past)
                     if msgs_rows[-1]['role'] == 'user' and msgs_rows[-1]['content'] == payload.question:
                          msgs_rows.pop() 
                     
                     conversation_history = [_history_message_from_row(m) for m in msgs_rows]
        
        # Get creator name
        creator_name = "Creator"
        try:
            cr = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (payload.creator_id,))
            if cr:
                creator_name = (cr.get("name") or "").strip()
                if not creator_name:
                    creator_name = (cr.get("handle") or "").strip()
                    if creator_name.startswith("@"):
                        creator_name = creator_name[1:]
                if not creator_name:
                    creator_name = "Creator"
        except: pass
        
        # Prepare images for vision model
        images_payload = None
        if payload.images and len(payload.images) > 0:
            images_payload = [{"data_url": img.data_url, "detail": img.detail} for img in payload.images[:4]]
            print(f"[ASK] {len(images_payload)} image(s) attached, using vision model")
        elif question_refers_to_recent_image(payload.question):
            recent_images = get_latest_thread_images(payload.thread_id, current_user["id"])
            if recent_images:
                images_payload = recent_images[:4]
        
        # Auto-inject default question for image-only messages
        question = payload.question
        if images_payload and (not question or not question.strip()):
            question = "Describe this image and point out anything important."
        
        # Use grounded RAG algorithm for better grounding
        result = grounded_rag_ask(
            creator_id=payload.creator_id,
            question=question,
            conversation_history=conversation_history,
            top_k=payload.top_k or 6,
            max_distance=payload.max_distance or 1.15,
            debug=payload.debug or False,
            user_preferences=user_prefs,
            user_name=user_name,
            creator_name=creator_name,
            images=images_payload,
            user_id=thread.get("user_id", current_user["id"]) if thread else current_user["id"],
            thread_id=payload.thread_id
        )
        
        creator_cleaning_profile = _get_creator_cleaning_profile(payload.creator_id, current_user["id"])
        strip_hyphens = should_strip_hyphens(creator_cleaning_profile)
        answer_text = clean_response(result["answer"] or "", strip_hyphens=strip_hyphens)
        explicit_cards = result.get("cards") or ([] if result.get("card") is None else [result.get("card")])
        cards = (
            merge_preview_cards(explicit_cards, enrich_titles=True)
            if explicit_cards
            else merge_preview_cards(extract_preview_cards(answer_text, enrich_titles=True), enrich_titles=True)
        )
        citations = result.get("citations") or []
        answer_text = prepare_chat_response(
            answer_text,
            cards=cards,
            strip_hyphens=strip_hyphens,
        )
        quality_report = ((result.get("meta") or {}).get("quality_report") or {})

        # Post-Processing: Save Assistant Message & Update Thread
        if payload.thread_id and thread:
             # Save assistant message with cards in metadata
             assistant_metadata = {}
             if cards:
                 assistant_metadata["cards"] = cards
             if citations:
                 assistant_metadata["citations"] = citations
             if quality_report:
                 assistant_metadata["quality_grade"] = quality_report.get("grade")
                 assistant_metadata["quality_score"] = quality_report.get("score")
             evidence_plan = ((result.get("meta") or {}).get("evidence_plan") or {})
             if evidence_plan:
                 assistant_metadata["evidence_plan"] = evidence_plan
             contradiction_report = ((result.get("meta") or {}).get("contradiction_report") or {})
             if contradiction_report:
                 assistant_metadata["contradiction_report"] = contradiction_report
             recommendation_feedback_event_id = ((result.get("meta") or {}).get("recommendation_feedback_event_id"))
             if recommendation_feedback_event_id:
                 assistant_metadata["recommendation_feedback_event_id"] = recommendation_feedback_event_id
             recommendation_query_variants = ((result.get("meta") or {}).get("recommendation_query_variants") or [])
             if recommendation_query_variants:
                 assistant_metadata["recommendation_query_variants"] = recommendation_query_variants

             db.execute_update("""
                INSERT INTO chat_messages (thread_id, role, content, metadata)
                VALUES (%s, 'assistant', %s, %s::jsonb)
             """, (payload.thread_id, answer_text, json.dumps(assistant_metadata)))
             
             # Update thread metadata
             preview = answer_text[:60] + "..." if len(answer_text) > 60 else answer_text
             db.execute_update("""
                UPDATE chat_threads 
                SET last_message_at = NOW(), last_preview = %s 
                WHERE id = %s
             """, (preview, payload.thread_id))
             
             # Trigger title update if needed (only if 'New conversation' and unlocked)
             if thread['title'] == 'New conversation' and not thread['title_locked']:
                  background_tasks.add_task(_update_thread_title_background, payload.thread_id)

        # Ensure response matches AskResponse format
        return {
            "answer": answer_text,
            "retrieved": result.get("retrieved", []),
            "sources": result.get("sources", []),
            "cards": cards,
            "citations": citations,
            "debug_info": result.get("debug") if payload.debug else None,
        }
    except Exception as e:
        raise _internal_server_error(e, "Failed to answer chat request")


@app.post("/recommendations/feedback")
async def recommendation_feedback_endpoint(
    payload: RecommendationFeedbackRequest,
    current_user: Dict[str, Any] = Depends(require_auth),
):
    ensure_creator_access(payload.creator_id, current_user["id"])
    event_id = recommendation_feedback_service.log_event(
        event_type=payload.event_type,
        user_id=current_user["id"],
        creator_id=payload.creator_id,
        thread_id=payload.thread_id,
        query="",
        candidate_title=payload.title or "",
        candidate_url=payload.url or "",
        metadata={
            "recommendation_event_id": payload.recommendation_event_id,
            **(payload.metadata or {}),
        },
    )
    return {"ok": True, "event_id": event_id}

@app.post("/ingest", response_model=IngestResponse)
async def ingest(payload: IngestRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    """Ingest a single document"""
    try:
        ensure_creator_access(payload.creator_id, current_user["id"])
        result = ingest_document(
            creator_id=payload.creator_id,
            title=payload.title,
            content=payload.content,
            source=payload.source,
            source_id=payload.source_id,
            doc_type=payload.doc_type
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to ingest document")

# ============================================================================
# Scraping Endpoints
# ============================================================================

def _execute_search_run(creator_id: int, creator_handle: str, normalized_items: List[Dict[str, Any]], source_url: str, platform: str, mode: str, search_run_id: Optional[str] = None):
    """Create scrape_run + scrape_items, return (search_run_id, response_items, failed_items)."""
    search_run_id, response_items, failed_items, _ = persist_search_items(
        creator_id=creator_id,
        creator_handle=creator_handle,
        normalized_items=normalized_items,
        source_url=source_url,
        platform=platform,
        mode=mode,
        search_run_id=search_run_id,
    )
    return search_run_id, response_items, failed_items


def _run_search_background(
    search_run_id: str,
    creator_id: int,
    creator_handle: str,
    pc: Dict[str, Any],
    source_url: str,
    platform_tag: str,
):
    """
    Background task to run scraping and update progress.
    Implements weighted stage progress:
    - Initializing: 0-5%
    - Scraping: 5-80% (split by platform)
    - Finalizing: 90-95% (100% on success)
    """
    try:
        # Ensure progress exists (may already be created by main handler)
        # 1. Initializing Stage (0-5%)
        enabled_count = sum(1 for cfg in pc.values() if isinstance(cfg, dict) and cfg.get("enabled"))
        current_data = _get_search_progress(search_run_id) or {}
        
        # Initialize
        _set_search_progress(search_run_id, {
            "status": "running",
            "percent": 2,
            "stage": "initializing",
            "current_platform": None,
            "current_platform_label": None,
            "completed": 0,
            "total": enabled_count,
            "platform_statuses": current_data.get("platform_statuses", {}),
            "items_found": 0,
            "error": None,
            "message": "Preparing search...",
            "phase": "search"
        })
        
        def progress_callback(platform_key: str, status: str, current: int, total: int):
            """Update progress for this search run."""
            prog = _get_search_progress(search_run_id)
            if prog is not None:
                plat = get_platform(platform_key)
                label = plat.get("label", platform_key) if plat else platform_key
                platform_statuses_progress = prog.get("platform_statuses", {})
                if platform_key not in platform_statuses_progress:
                    platform_statuses_progress[platform_key] = {}
                
                # Update specific platform status
                platform_statuses_progress[platform_key].update({
                    "status": status,
                    "label": label,
                })
                
                # Calculate weighted progress
                # Scraping stage: 5% to 80% (Range size: 75%)
                # Only increase progress on completion of a platform
                base_scraping = 5.0
                scrape_range = 75.0
                
                # If status is finished (completed/error/skipped), contribution = 1.0 * step
                # If status is searching, we don't advance percentage yet (or maybe just a tiny bit?)
                # Requirement: "Progress increases only when a platform finishes"
                
                completed_count = current if status in ("completed", "error", "skipped") else (current - 1)
                
                if total > 0:
                    percent = base_scraping + (completed_count / total) * scrape_range
                else:
                    percent = base_scraping
                
                # Ensure we don't exceed 80% during scraping
                percent = min(80.0, percent)
                
                msg = f"Collecting content from {label}..." if status == "searching" else "Collecting content..."

                prog.update({
                    "current_platform": platform_key,
                    "current_platform_label": label,
                    "completed": current,
                    "total": total,
                    "status": "running",
                    "stage": "search",
                    "phase": "search",
                    "percent": round(percent, 1),
                    "platform_statuses": platform_statuses_progress,
                    "message": msg
                })
                _set_search_progress(search_run_id, prog)
        
        # Run search router with progress callback
        normalized_items, platform_statuses = run_search_router(
            creator_id, creator_handle, pc, progress_callback=progress_callback, enrich_transcripts=False
        )

        _set_search_progress(search_run_id, {
            **(_get_search_progress(search_run_id) or {}),
            "stage": "finalizing",
            "phase": "search",
            "percent": 88.0,
            "message": "Saving results..."
        })

        _, response_items, failed_items, checkpoints = persist_search_items(
            creator_id=creator_id,
            creator_handle=creator_handle,
            normalized_items=normalized_items,
            source_url=source_url or f"creator:{creator_id}",
            platform=platform_tag,
            mode="profile",
            search_run_id=search_run_id,
        )

        pc_updated = merge_platform_statuses_with_checkpoints(pc, platform_statuses, checkpoints)
        try:
            r = db.execute_one(
                "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                ("creators", "platform_configs"),
            )
        except Exception:
            r = None
        if r:
            db.execute_update(
                "UPDATE creators SET platform_configs = %s WHERE id = %s",
                (json.dumps(pc_updated), creator_id),
            )

        platform_summary = {}
        for key, status in platform_statuses.items():
            plat = get_platform(key)
            label = plat.get("label", key) if plat else key
            platform_summary[key] = {
                "label": label,
                "status": status.get("last_scrape_status") or status.get("last_search_status", "unknown"),
                "items_found": status.get("items_found", 0),
                "error": status.get("last_error"),
            }

        _set_search_progress(search_run_id, {
            **(_get_search_progress(search_run_id) or {}),
            "status": "completed",
            "stage": "done",
            "phase": "done",
            "percent": 100.0,
            "items_found": len(response_items),
            "failed_count": len(failed_items),
            "platform_statuses": platform_statuses,
            "platform_summary": platform_summary,
            "completed": enabled_count,
            "transcript_job_status": "queued",
            "message": "Search complete. Transcript enrichment continues in background.",
        })

        try:
            db.execute_insert(
                """
                INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
                VALUES (%s, 'TRANSCRIPT', %s::jsonb, 'queued', 0, 'Transcript job enqueued after search')
                RETURNING id
                """,
                (creator_id, json.dumps({"search_id": search_run_id}))
            )
        except Exception as transcript_job_err:
            print(f"[SEARCH] Could not enqueue transcript job: {transcript_job_err}", flush=True)
    except BaseException as e:
        msg = str(e) or repr(e) or "Critical unknown error"
        print(f"[SEARCH] Background task CRASH: {msg}", flush=True)
        import traceback
        traceback.print_exc()
        try:
            with open("panic_log.txt", "a") as f:
                f.write(f"CRASH: {msg}\n")
                traceback.print_exc(file=f)
        except:
            pass
            
        prog = _get_search_progress(search_run_id)
        if prog is not None:
            prog.update({"status": "error", "percent": prog.get("percent", 0), "error": msg, "message": "Search failed"})
            _set_search_progress(search_run_id, prog)



@app.post("/search", response_model=SearchResponse)
@limiter.limit("30/minute")
async def search_endpoint(request: Request, payload: SearchRequest, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Search via Apify. Two modes:
    - Legacy: provide `url` (Instagram) + optional `limit`. Creates creator by handle.
    - Config: provide `creator_id`. Loads platform_configs from DB (or override via `platform_configs`), runs router.
    
    Returns immediately with search_id. Use /search/{search_id}/progress to track progress.
    """
    try:
        log_payload = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    except Exception:
        log_payload = {"creator_id": getattr(payload, "creator_id", None)}
    print("[SEARCH] request payload:", log_payload, flush=True)
    print("[APIFY] token present:", bool(settings.APIFY_TOKEN), flush=True)
    search_run_id = None  # Initialize to avoid UnboundLocalError
    try:
        if payload.creator_id is not None:
            # Config-based flow: load creator + platform_configs, run search router async
            if not settings.APIFY_TOKEN:
                raise HTTPException(status_code=500, detail="APIFY_TOKEN is not set.")
            dcol = _creator_display_column()
            row = db.execute_one(
                f"SELECT id, handle, {dcol} AS display_name, platform_configs FROM creators WHERE id = %s",
                (payload.creator_id,),
            )
            if not row:
                raise HTTPException(status_code=404, detail="Creator not found.")
            creator_handle = row.get("handle") or row.get("display_name") or "creator"
            pc = row.get("platform_configs") or {}
            if payload.platform_configs is not None:
                pc = _validate_and_normalize_platform_configs(payload.platform_configs)
            else:
                if hasattr(pc, "copy"):
                    pc = dict(pc) if pc else {}
                else:
                    pc = json.loads(pc) if isinstance(pc, str) else (pc or {})
            print("[SEARCH] platform_configs from DB:", json.dumps(pc, default=str), flush=True)
            
            # Generate search_id early
            search_run_id = str(uuid.uuid4())
            
            # Create progress entry immediately (persisted to DB so it survives restarts)
            enabled_count = sum(1 for cfg in pc.values() if isinstance(cfg, dict) and cfg.get("enabled"))
            _set_search_progress(search_run_id, {
                "status": "running",
                "current_platform": None,
                "current_platform_label": None,
                "completed": 0,
                "total": enabled_count,
                "platform_statuses": {},
                "items_found": 0,
                "error": None,
            })
            
            # Determine source URL and platform tag
            source_url = ""
            for _, cfg in pc.items():
                if cfg.get("url"):
                    source_url = cfg["url"]
                    break
            platform_tag = "multi" if len(pc) > 1 else (list(pc.keys())[0] if pc else "instagram")
            
            execution_mode = os.getenv("SEARCH_EXECUTION_MODE", "inline").strip().lower()
            if execution_mode == "worker":
                # Queue into durable system worker.
                job_payload = {
                    "search_run_id": search_run_id,
                    "creator_id": payload.creator_id,
                    "creator_handle": creator_handle,
                    "platform_configs": pc,
                    "source_url": source_url or f"creator:{payload.creator_id}",
                    "platform_tag": platform_tag,
                }
                db.execute_insert(
                    """
                    INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
                    VALUES (%s, 'SCRAPE', %s::jsonb, 'queued', 0, 'Search job enqueued')
                    RETURNING id
                    """,
                    (payload.creator_id, json.dumps(job_payload)),
                )
            else:
                # Run search pipeline in-process as a background task.
                background_tasks.add_task(
                    _run_search_background,
                    search_run_id,
                    payload.creator_id,
                    creator_handle,
                    pc,
                    source_url or f"creator:{payload.creator_id}",
                    platform_tag,
                )

            # Return immediately with search_id
            return {
                "search_id": search_run_id,
                "items": [],  # Empty initially, fetch via /search/{search_id}/items when complete
                "creator_id": payload.creator_id,
                "platform_statuses": {},
            }
        if payload.url:
            # Legacy: single Instagram URL
            limit = min(payload.limit, 10)
            parsed = parse_instagram_url(payload.url)
            if not parsed:
                raise HTTPException(status_code=400, detail="Invalid Instagram URL. Provide a valid profile or reel URL.")
            handle = parsed["handle"]
            reel_id = parsed.get("reel_id")
            mode = parsed.get("mode") or "profile"
            if not settings.APIFY_TOKEN:
                raise HTTPException(status_code=500, detail="APIFY_TOKEN is not set.")
            creator_id = get_or_create_creator_for_handle(handle, current_user["id"], platform="instagram")
            try:
                normalized_items = search_instagram_reels(handle, reel_id, limit)
            except Exception as e:
                raise _internal_server_error(e, "Apify scraping failed")
            if not normalized_items:
                raise HTTPException(status_code=404, detail=f"No Instagram reels found for @{handle}")
            search_run_id, response_items, failed_items = _execute_search_run(
                creator_id, handle, normalized_items, payload.url, "instagram", mode
            )
            return {
                "search_id": search_run_id, 
                "items": response_items, 
                "creator_id": creator_id,
                "success_count": len(response_items),
                "failed_count": len(failed_items),
                "failed_items": failed_items
            }
        raise HTTPException(status_code=400, detail="Provide either url or creator_id.")
    except HTTPException:
        raise
    except Exception as e:
        # Update progress on error if search_id exists
        error_msg = str(e)
        if search_run_id:
            prog = _get_search_progress(search_run_id)
            if prog is not None:
                prog.update({"status": "error", "error": error_msg})
                _set_search_progress(search_run_id, prog)
        raise HTTPException(status_code=500, detail=f"Scraping failed: {error_msg}")

@app.get("/search/{search_id}/progress")
async def get_search_progress(search_id: str, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Get search progress for a search run.
    Returns: { status, percent, stage, current_platform, completed_platforms, message, ... }
    Progress is persisted to DB so it survives backend restarts.
    """
    # print(f"[SEARCH] GET /search/{search_id}/progress", flush=True)
    progress = _get_search_progress(search_id)
    if not progress:
        raise HTTPException(status_code=404, detail="Search run not found or progress expired")
    
    # Use stored weighted percent if available, otherwise calculate simple ratio
    if "percent" in progress:
        percentage = progress["percent"]
    else:
        percentage = int((progress.get("completed", 0) / progress.get("total", 1) * 100)) if progress.get("total", 0) > 0 else 0
        
    counts = {
        "platforms_done": progress.get("completed", 0),
        "platforms_total": progress.get("total", 0),
        "items_total": progress.get("items_found", 0),
        "transcripts_done": progress.get("transcripts_done", 0),
        "failures": progress.get("failed_count", 0)
    }
        
    return {
        **progress,
        "percentage": percentage,
        "percent": percentage,
        "phase": progress.get("phase", "search"),
        "counts": counts
    }


@app.get("/search/{search_id}/items", response_model=SearchResponse)
async def get_search_items(search_id: str, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get all items for a search run"""
    try:
        query = """
            SELECT id, source_url, caption, transcript, transcript_status, 
                   published_at, metadata, review_status, creator_handle
            FROM scrape_items
            WHERE scrape_run_id = %s
            ORDER BY created_at DESC
        """
        results = db.execute_query(query, (search_id,))
        
        items = []
        for row in results:
            preview_text = row.get("transcript") or row.get("caption", "") or ""
            preview = preview_text[:200] + "..." if len(preview_text) > 200 else preview_text
            
            metadata = row.get("metadata")
            if isinstance(metadata, str):
                metadata = json.loads(metadata) if metadata else {}
            if not isinstance(metadata, dict):
                metadata = {}
            platform = metadata.get("platform")

            items.append({
                "item_id": str(row["id"]),
                "source_url": row["source_url"],
                "caption": row.get("caption"),
                "creator_handle": row.get("creator_handle"),
                "status": row.get("review_status", "pending"),
                "item_status": row.get("review_status", "pending"),
                "transcript_status": row.get("transcript_status", "missing"),
                "published_at": row.get("published_at").isoformat() if row.get("published_at") and hasattr(row.get("published_at"), "isoformat") else str(row.get("published_at")) if row.get("published_at") else None,
                "platform": platform,
                "metadata": metadata,
                "preview": preview
            })
        
        # Include platform_statuses from progress so frontend can show what happened per platform
        progress = _get_search_progress(search_id)
        platform_statuses = None
        if progress and progress.get("platform_summary"):
            # Convert platform_summary to format frontend expects
            platform_statuses = {}
            for key, s in progress["platform_summary"].items():
                platform_statuses[key] = {
                    "last_scrape_status": s.get("status", "unknown"),
                    "items_found": s.get("items_found", 0),
                    "last_error": s.get("error"),
                }
        elif progress and progress.get("platform_statuses"):
            platform_statuses = progress["platform_statuses"]

        return {
            "search_id": search_id,
            "scrape_id": search_id,  # Frontend expects scrape_id
            "items": items,
            "platform_statuses": platform_statuses or {},
        }
    except Exception as e:
        raise _internal_server_error(e, "Failed to load search items")

# ============================================================================
# Approval & Ingestion Endpoints
# ============================================================================



def _search_run_has_pending_transcripts(search_id: Optional[str]) -> bool:
    if not search_id:
        return False
    row = db.execute_one(
        """
        SELECT COUNT(*) AS count
        FROM scrape_items
        WHERE scrape_run_id = %s
          AND transcript_status IN ('processing', 'queued', 'pending', 'not_started')
        """,
        (search_id,),
    )
    return int((row or {}).get("count", 0) or 0) > 0


def _compose_ingest_text(caption: str, transcript: str, title: str = "", platform: str = "", source_url: str = "") -> str:
    caption_text = str(caption or "").strip()
    transcript_text = clean_transcript_for_ingestion(transcript)

    if not caption_text and not transcript_text:
        return ""
    if not caption_text:
        body = transcript_text
    elif not transcript_text:
        body = caption_text
    else:
        cap_norm = " ".join(caption_text.split()).casefold()
        transcript_norm = " ".join(transcript_text.split()).casefold()
        if cap_norm == transcript_norm:
            body = transcript_text if len(transcript_text) >= len(caption_text) else caption_text
        elif cap_norm in transcript_norm:
            body = transcript_text
        elif transcript_norm in cap_norm:
            body = caption_text
        else:
            body = f"{caption_text}\n\n---\n\n{transcript_text}"

    # Prepend a compact provenance anchor so retrieved chunks carry source
    # context inline. This boosts recall for title/topic queries and gives the
    # LLM the source label without needing an extra DB join in the prompt.
    title_clean = str(title or "").strip()
    platform_clean = str(platform or "").strip().lower()
    anchor_bits = []
    if title_clean:
        anchor_bits.append(title_clean)
    if platform_clean:
        anchor_bits.append(platform_clean)
    if anchor_bits:
        anchor = "[" + " · ".join(anchor_bits) + "]"
        return f"{anchor}\n\n{body}"
    return body

@app.post("/approve_ingest", response_model=ApproveIngestResponseNew)
async def approve_ingest(request: ApproveIngestRequestNew, current_user: Dict[str, Any] = Depends(require_auth)):
    """Ingest items from queue - insert documents from search_queue, then chunk and embed (legacy endpoint)"""
    try:
        ensure_creator_access(request.creator_id, current_user["id"])

        # Fetch rows to ingest
        queue_rows = fetch_queue_items(None, request.creator_id, request.queue_ids)

        ingested = []

        # These are the core helpers for chunking + embedding
        from backend.ingest import chunk_text_structured, embed_chunks

        # Process each row
        for row in queue_rows:
            queue_id = row["id"]
            try:
                raw_text = row["raw_text"]

                # Insert document directly from search_queue with type='content'
                doc_query = """
                    INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
                    SELECT
                        creator_id,
                        COALESCE(title, %s),
                        raw_text,
                        source,
                        COALESCE(source_id, %s),
                        jsonb_build_object('type', 'content')
                    FROM scrape_queue
                    WHERE creator_id = %s AND id = %s
                    RETURNING id
                """
                title = f"{request.title_prefix}: {row.get('title') or f'Queue {queue_id}'}"
                source_id = f"queue_{queue_id}"

                doc_result = db.execute_query(doc_query, (title, source_id, request.creator_id, queue_id))
                if not doc_result:
                    continue

                document_id = doc_result[0]["id"]

                # chunk the document
                chunks = chunk_text_structured(
                    text=raw_text,
                    creator_id=request.creator_id,
                    document_id=document_id,
                )

                # store chunks (no fallback needed, using correct schema)
                chunk_ids = []
                for chunk in chunks:
                    chunk_id = db.execute_insert(
                        """
                        INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                            chunk_text = EXCLUDED.chunk_text,
                            creator_id = EXCLUDED.creator_id
                        RETURNING id
                        """,
                        (request.creator_id, document_id, chunk["index"], chunk["text"]),
                    )
                    if chunk_id:
                        chunk_ids.append(chunk_id)

                # embed chunks
                embed_chunks(chunk_ids)

                ingested.append(
                    ApproveIngestItem(
                        queue_id=queue_id,
                        document_id=document_id,
                        chunks_inserted=len(chunk_ids),
                    )
                )
            except Exception:
                # Skip failed items, continue with others
                continue

        # Mark all as ingested in batch (only for successfully processed ids)
        if ingested:
            ingested_ids = [item.queue_id for item in ingested]
            mark_queue_ingested(None, request.creator_id, ingested_ids)

        return ApproveIngestResponseNew(approved=len(request.queue_ids), ingested=ingested)
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to approve ingest queue")

@app.post("/approvals/{creator_id}/commit")
async def commit_approvals_endpoint(creator_id: int, request: ApproveIngestRequestV2, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Approve items from search_items staging table and enqueue INGEST job.
    """
    try:
        ensure_creator_access(creator_id, current_user["id"])
        # Separate approved and denied, handling doc_ prefixes
        approved_item_ids = []
        denied_item_ids = []
        doc_ids_to_delete = []
        confirmed_doc_ids = []
        
        for d in request.decisions:
            raw_id = str(d["item_id"])
            decision = d.get("decision")
            
            if raw_id.startswith("doc_"):
                # Existing document - approve means "keep this in the KB", deny means delete it.
                if decision == "approve":
                    try:
                        confirmed_doc_ids.append(int(raw_id.split("_")[1]))
                    except:
                        pass
                elif decision == "deny":
                    try:
                        doc_ids_to_delete.append(int(raw_id.split("_")[1]))
                    except:
                        pass
            else:
                # Scrape item (UUID)
                if decision == "approve":
                    approved_item_ids.append(raw_id)
                elif decision == "deny":
                    denied_item_ids.append(raw_id)

        changed_existing_docs = False

        # Delete existing documents synchronously since it's fast
        if doc_ids_to_delete:
            delete_document_corpus(doc_ids_to_delete)
            changed_existing_docs = True
        
        sid = request.search_id or request.scrape_id
        if denied_item_ids:
            deny_query = """
                UPDATE scrape_items
                SET review_status = 'denied'
                WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
            """
            db.execute_update(deny_query, (denied_item_ids, sid))
        
        if not approved_item_ids:
            if confirmed_doc_ids or denied_item_ids or doc_ids_to_delete:
                db.execute_update(
                    "UPDATE creators SET last_approved_version = config_version WHERE id = %s",
                    (creator_id,)
                )
                refresh_creator_corpus_state(creator_id, sync_fingerprint=True)
            return {"job_id": None, "approved": len(confirmed_doc_ids)}

        already_approved = db.execute_one(
            """
            SELECT COUNT(*) AS count
            FROM scrape_items
            WHERE id = ANY(%s::uuid[])
              AND scrape_run_id = %s
              AND review_status = 'approved'
            """,
            (approved_item_ids, sid)
        )
        already_approved_count = int((already_approved or {}).get("count", 0) or 0)
        pending_approved_item_ids = approved_item_ids
        if already_approved_count:
            rows = db.execute_query(
                """
                SELECT id
                FROM scrape_items
                WHERE id = ANY(%s::uuid[])
                  AND scrape_run_id = %s
                  AND COALESCE(review_status, 'pending_review') != 'approved'
                """,
                (approved_item_ids, sid),
            )
            pending_approved_item_ids = [str(row["id"]) for row in rows]

        if not pending_approved_item_ids:
            db.execute_update(
                "UPDATE creators SET last_approved_version = config_version WHERE id = %s",
                (creator_id,)
            )
            refresh_creator_corpus_state(
                creator_id,
                sync_fingerprint=bool(denied_item_ids or changed_existing_docs),
            )
            return {"job_id": None, "approved": len(approved_item_ids)}
        
        # Enqueue INGEST job
        job_payload = {
            "creator_id": creator_id,
            "search_id": sid,
            "approved_item_ids": pending_approved_item_ids
        }
        
        job_id = db.execute_insert(
            """
            INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
            VALUES (%s, 'INGEST', %s::jsonb, 'queued', 0, 'Ingest job enqueued')
            RETURNING id
            """,
            (creator_id, json.dumps(job_payload))
        )
            
        return {"job_id": job_id, "approved": len(pending_approved_item_ids)}
    except Exception as e:
        raise _internal_server_error(e, "Failed to commit approvals")


@app.get("/jobs/{job_id}/progress")
async def get_job_progress(job_id: str, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Universal polling endpoint for all system_jobs. Return status, progress, and error logs.
    """
    try:
        job = db.execute_one(
            "SELECT id, creator_id, job_type, status, progress_percent, message, error_log FROM system_jobs WHERE id = %s",
            (job_id,)
        )
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        ensure_creator_access(int(job["creator_id"]), current_user["id"])
            
        return {
            "job_id": str(job["id"]),
            "creator_id": job["creator_id"],
            "job_type": job["job_type"],
            "status": job["status"],  # queued, processing, completed, failed
            "progress_percent": job["progress_percent"],
            "message": job["message"] or "",
            "error_log": job["error_log"]
        }
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise _internal_server_error(e, "Failed to load job progress")

@app.post("/approve_ingest_v2/stream")
async def approve_ingest_v2_stream(request: ApproveIngestRequestV2, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Streaming version of approve_ingest_v2 with real-time progress updates via SSE.
    Returns Server-Sent Events with progress information.
    """
    import asyncio
    ensure_creator_access(request.creator_id, current_user["id"])
    async def event_generator():
        try:
            # Separate approved and denied, handling doc_ prefixes
            approved_item_ids = []
            denied_item_ids = []
            doc_ids_to_delete = []
            
            for d in request.decisions:
                raw_id = str(d["item_id"])
                decision = d.get("decision")
                
                if raw_id.startswith("doc_"):
                    # Existing document - only handle delete (deny)
                    if decision == "deny":
                        try:
                            doc_ids_to_delete.append(int(raw_id.split("_")[1]))
                        except: pass
                else:
                    # Scrape item (UUID)
                    if decision == "approve":
                        approved_item_ids.append(raw_id)
                    elif decision == "deny":
                        denied_item_ids.append(raw_id)

            # Delete existing documents if requested
            if doc_ids_to_delete:
                yield f"data: {json.dumps({'stage': 'deleting', 'current': 0, 'total': len(doc_ids_to_delete), 'message': f'Deleting {len(doc_ids_to_delete)} existing documents...'})}\n\n"
                delete_document_corpus(doc_ids_to_delete)
            
            total_items = len(approved_item_ids)
            
            # Send initial progress
            yield f"data: {json.dumps({'stage': 'starting', 'current': 0, 'total': total_items, 'message': 'Starting ingestion...'})}\n\n"
            
            # Update review_status for denied items
            sid = request.search_id or request.scrape_id
            if denied_item_ids:
                yield f"data: {json.dumps({'stage': 'denying', 'current': 0, 'total': total_items, 'message': f'Marking {len(denied_item_ids)} items as denied...'})}\n\n"
                deny_query = """
                    UPDATE scrape_items
                    SET review_status = 'denied'
                    WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
                """
                db.execute_update(deny_query, (denied_item_ids, sid))
            
            if not approved_item_ids:
                if denied_item_ids or doc_ids_to_delete:
                    db.execute_update(
                        "UPDATE creators SET last_approved_version = config_version WHERE id = %s",
                        (request.creator_id,)
                    )
                    refresh_creator_corpus_state(request.creator_id, sync_fingerprint=True)
                yield f"data: {json.dumps({'stage': 'complete', 'current': 0, 'total': 0, 'message': 'No items to approve'})}\n\n"
                return
            
            # Fetch approved items
            yield f"data: {json.dumps({'stage': 'fetching', 'current': 0, 'total': total_items, 'message': f'Fetching {total_items} approved items...'})}\n\n"
            
            fetch_query = """
                SELECT id, creator_handle, source_url, caption, transcript, 
                       transcript_status, published_at, metadata, content_type,
                       is_primary, duplicate_of_item_id
                FROM scrape_items
                WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
            """
            items = db.execute_query(fetch_query, (approved_item_ids, sid))
            
            if not items:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'No approved items found'})}\n\n"
                return
            
            creator_id = request.creator_id
            ingested = []
            changed_item_count = 0
            skipped_item_count = 0
            from backend.ingest import chunk_text_structured, embed_chunks
            try:
                from backend.lib.transcription import transcribe_video
            except ImportError:
                def transcribe_video(url):
                    return None
            
            # Process each item
            for item_index, item in enumerate(items):
                item_id = item["id"]
                current_item = item_index + 1
                
                yield f"data: {json.dumps({'stage': 'processing', 'current': current_item, 'total': total_items, 'current_item': item_index, 'message': f'Processing item {current_item}/{total_items}...'})}\n\n"
                
                try:
                    source_url = item["source_url"]
                    item_meta = item.get("metadata") or {}
                    if isinstance(item_meta, str):
                        try:
                            item_meta = json.loads(item_meta)
                        except:
                            item_meta = {}
                    
                    platform = item_meta.get("platform") or item.get("metadata", {}).get("platform") if isinstance(item.get("metadata"), dict) else None
                    if not platform:
                        if "instagram.com" in source_url:
                            platform = "instagram"
                        elif "youtube.com" in source_url or "youtu.be" in source_url:
                            platform = "youtube"
                        elif "twitter.com" in source_url or "x.com" in source_url:
                            platform = "twitter"
                        elif "tiktok.com" in source_url:
                            platform = "tiktok"
                        elif "linkedin.com" in source_url:
                            platform = "linkedin"
                        elif "facebook.com" in source_url:
                            platform = "facebook"
                        elif "reddit.com" in source_url:
                            platform = "reddit"
                        else:
                            platform = "unknown"
                    
                    # Cross-platform duplicate short-circuit. If this scrape_item
                    # was flagged at scrape-time as a duplicate of another item
                    # (canonical URL match or simhash distance <= 3), reuse the
                    # existing document instead of re-embedding the same content.
                    if not item.get("is_primary") and item.get("duplicate_of_item_id"):
                        primary_doc = db.execute_one(
                            """
                            SELECT d.id
                            FROM scrape_items s
                            JOIN documents d
                              ON d.source_id = COALESCE(NULLIF(s.metadata->>'content_id', ''), s.id::text)
                            WHERE s.id = %s::uuid
                            LIMIT 1
                            """,
                            (str(item["duplicate_of_item_id"]),),
                        )
                        if primary_doc and primary_doc.get("id"):
                            primary_doc_id = int(primary_doc["id"])
                            db.execute_update(
                                "INSERT INTO creator_documents (creator_id, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                                (creator_id, primary_doc_id),
                            )
                            db.execute_update(
                                "UPDATE scrape_items SET review_status = 'approved' WHERE id = %s::uuid",
                                (str(item_id),),
                            )
                            skipped_item_count += 1
                            ingested.append(
                                ApproveIngestItem(
                                    queue_id=str(item_id),
                                    document_id=primary_doc_id,
                                    chunks_inserted=0,
                                )
                            )
                            yield f"data: {json.dumps({'stage': 'duplicate_skipped', 'current': current_item, 'total': total_items, 'message': f'Item {current_item} is a cross-platform duplicate \u2014 reusing existing document.'})}\n\n"
                            continue

                    content_id = item_meta.get("content_id") or ""
                    title_from_meta = item_meta.get("title") or ""
                    
                    if not content_id:
                        from backend.apify_service import extract_content_id
                        content_id = extract_content_id(source_url, platform)
                    if not title_from_meta:
                        from backend.apify_service import extract_title_from_metadata
                        title_from_meta = extract_title_from_metadata(item_meta, platform, source_url)

                    title = str(title_from_meta) if title_from_meta else "Untitled"
                    source_id = str(content_id) if content_id else f"search_item_{item_id}"
                    source_platform = str(platform) if platform else "unknown"

                    transcript = item.get("transcript") or ""
                    transcript_status = item.get("transcript_status", "missing")
                    text_content = _compose_ingest_text(
                        item.get("caption"),
                        transcript,
                        title=title,
                        platform=source_platform,
                        source_url=source_url,
                    )
                    existing_doc = find_existing_document(
                        creator_id,
                        source=source_platform,
                        source_id=str(source_id),
                        source_url=source_url,
                    )
                    current_checksum = ""
                    if text_content:
                        current_checksum = compute_item_ingest_checksum(
                            platform=source_platform,
                            source_url=source_url,
                            source_id=str(source_id),
                            title=title,
                            text_content=text_content,
                            transcript_status=transcript_status,
                            published_at=item.get("published_at"),
                        )

                    if existing_doc and current_checksum and get_document_ingest_checksum(existing_doc.get("metadata")) == current_checksum:
                        db.execute_update(
                            "UPDATE scrape_items SET review_status = 'approved' WHERE id = %s::uuid",
                            (str(item_id),),
                        )
                        skipped_item_count += 1
                        ingested.append(
                            ApproveIngestItem(
                                queue_id=str(item_id),
                                document_id=existing_doc["id"],
                                chunks_inserted=0
                            )
                        )
                        continue

                    if settings.TRANSCRIBE_ON_INGEST and transcript_needs_recovery(
                        transcript,
                        caption=item.get("caption") or "",
                        title=title,
                    ):
                        yield f"data: {json.dumps({'stage': 'transcribing', 'current': current_item, 'total': total_items, 'message': f'Transcribing item {current_item}...'})}\n\n"

                        video_url = item_meta.get("video_url") or item_meta.get("videoUrl") or item_meta.get("video") or ""

                        if not video_url:
                            vid = item_meta.get("videoId") or item_meta.get("id")
                            if item_meta.get("platform") == "youtube" and vid:
                                video_url = f"https://www.youtube.com/watch?v={vid}"

                        if not video_url:
                            video_url = item.get("source_url") or ""

                        if video_url:
                            try:
                                transcript = ""
                                transcript = transcribe_video(video_url)
                                if transcript:
                                    transcript_status = "present"
                                    update_query = """
                                        UPDATE scrape_items
                                        SET transcript = %s, transcript_status = 'present'
                                        WHERE id = %s::uuid
                                    """
                                    db.execute_update(update_query, (str(transcript), str(item_id)))
                                else:
                                    transcript_status = "error"
                            except Exception as e:
                                print(f"Transcription failed for {item_id}: {e}")
                                transcript_status = "error"

                    text_content = _compose_ingest_text(
                        item.get("caption"),
                        transcript,
                        title=title,
                        platform=source_platform,
                        source_url=source_url,
                    )

                    if not text_content:
                        print(f"Skipping item {item_id}: no transcript, caption, or post text")
                        continue

                    ingest_checksum = compute_item_ingest_checksum(
                        platform=source_platform,
                        source_url=source_url,
                        source_id=str(source_id),
                        title=title,
                        text_content=text_content,
                        transcript_status=transcript_status,
                        published_at=item.get("published_at"),
                    )

                    if existing_doc and get_document_ingest_checksum(existing_doc.get("metadata")) == ingest_checksum:
                        db.execute_update(
                            "UPDATE scrape_items SET review_status = 'approved' WHERE id = %s::uuid",
                            (str(item_id),),
                        )
                        skipped_item_count += 1
                        ingested.append(
                            ApproveIngestItem(
                                queue_id=str(item_id),
                                document_id=existing_doc["id"],
                                chunks_inserted=0
                            )
                        )
                        continue
                    
                    # Create document
                    yield f"data: {json.dumps({'stage': 'creating_doc', 'current': current_item, 'total': total_items, 'message': f'Creating document for item {current_item}...'})}\n\n"
                    
                    doc_metadata = {
                        "type": "content",
                        "platform": platform,
                        "content_type": item.get("content_type", "unknown"),
                        "creator_handle": item["creator_handle"],
                        "source_url": source_url,
                        "content_id": content_id,
                        "canonical_url": source_url,
                        "search_run_id": sid,
                        "transcript_status": transcript_status,
                        "published_at": item.get("published_at"),
                        "ingest_checksum": ingest_checksum,
                    }
                    for k, v in item_meta.items():
                        if k not in ("platform", "content_id", "canonical_url", "title"):
                            doc_metadata[k] = v
                    
                    doc_query = """
                        INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                        ON CONFLICT (source, source_id) DO UPDATE SET
                            creator_id = EXCLUDED.creator_id,
                            title = EXCLUDED.title,
                            content = EXCLUDED.content,
                            metadata = EXCLUDED.metadata
                        RETURNING id
                    """
                    if existing_doc:
                        delete_document_chunks_and_embeddings([int(existing_doc["id"])])

                    document_id = db.execute_insert(
                        doc_query,
                        (creator_id, title, text_content, source_platform, str(source_id), json.dumps(doc_metadata, default=str))
                    )
                    
                    if not document_id:
                        continue

                    db.execute_update(
                        "INSERT INTO creator_documents (creator_id, document_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        (creator_id, document_id)
                    )
                    
                    # Chunk the document
                    yield f"data: {json.dumps({'stage': 'chunking', 'current': current_item, 'total': total_items, 'message': f'Breaking item {current_item} into chunks...'})}\n\n"
                    
                    chunks = chunk_text_structured(
                        text=text_content,
                        creator_id=creator_id,
                        document_id=document_id,
                        chunk_size=800,
                        overlap=120
                    )

                    # Prepend a tiny "title summary" chunk so semantic queries
                    # that match the topic/title (e.g. "why did u spend a million
                    # in vegas" -> "Spending 1 Million in Vegas") get a strong
                    # vector hit on a high-signal short chunk. Without this the
                    # title is diluted by 800 chars of body in chunk 0.
                    body_preview = (text_content or "").strip()
                    # Strip the existing [title · platform] anchor we prepended
                    # in _compose_ingest_text so we don't double-count it.
                    if body_preview.startswith("["):
                        nl = body_preview.find("\n\n")
                        if 0 < nl < 200:
                            body_preview = body_preview[nl + 2 :].strip()
                    body_preview = body_preview[:240]
                    title_chunk_text = f"{title}\n\n{platform} \u00b7 {item['creator_handle']}\n\n{body_preview}".strip()
                    if title_chunk_text:
                        # Shift body chunk indices up by 1 so the title chunk
                        # owns chunk_index 0.
                        for c in chunks:
                            c["index"] = c["index"] + 1
                        chunks.insert(0, {
                            "index": 0,
                            "text": title_chunk_text,
                            "creator_id": creator_id,
                            "document_id": document_id,
                        })
                    
                    # Store chunks
                    chunk_ids = []
                    for chunk in chunks:
                        source_ref = {
                            "platform": platform,
                            "content_id": content_id,
                            "canonical_url": source_url,
                            "title": title,
                            "published_at": item.get("published_at"),
                            "content_type": item.get("content_type", "unknown"),
                        }
                        
                        chunk_metadata = {
                            "platform": platform,
                            "type": item.get("content_type", "unknown"),
                            "creator_handle": item["creator_handle"],
                            "source_url": source_url,
                            "content_id": content_id,
                            "canonical_url": source_url,
                            "title": title,
                            "search_run_id": request.search_id,
                            "transcript_status": transcript_status,
                            "published_at": item.get("published_at"),
                            "source_ref": source_ref,
                            "is_title_chunk": chunk["index"] == 0,
                        }
                        
                        chunk_id = db.execute_insert(
                            """
                            INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text, metadata)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                                chunk_text = EXCLUDED.chunk_text,
                                metadata = EXCLUDED.metadata,
                                creator_id = EXCLUDED.creator_id
                            RETURNING id
                            """,
                            (creator_id, document_id, chunk["index"], chunk["text"], json.dumps(chunk_metadata, default=str))
                        )
                        if chunk_id:
                            chunk_ids.append(chunk_id)
                    
                    # Embed chunks with progress callback
                    def embedding_progress(current, total, stage):
                        event_data = {
                            'stage': f'embedding_{stage}',
                            'current': current_item,
                            'total': total_items,
                            'chunk_progress': current,
                            'chunk_total': total,
                            'message': f'Item {current_item}/{total_items}: {stage.capitalize()} embeddings ({current}/{total} chunks)...'
                        }
                        # Note: This won't work in async generator, but embed_chunks is sync
                        # We'll handle this differently
                        pass
                    
                    yield f"data: {json.dumps({'stage': 'embedding', 'current': current_item, 'total': total_items, 'message': f'Creating embeddings for item {current_item} ({len(chunk_ids)} chunks)...'})}\n\n"
                    
                    embed_chunks(chunk_ids)  # Now uses batch API - much faster!

                    # Cross-platform paraphrase linking. Compares this doc's
                    # title-chunk embedding to other docs' title chunks for the
                    # same creator and stamps documents.metadata.related_document_ids
                    # so paraphrased reposts (e.g. YouTube vs LinkedIn version of
                    # the same idea) get linked even when simhash misses them.
                    try:
                        from backend.services.paraphrase_link import link_cross_platform_paraphrases
                        related_links = link_cross_platform_paraphrases(int(document_id), int(creator_id))
                        if related_links:
                            yield f"data: {json.dumps({'stage': 'paraphrase_linked', 'current': current_item, 'total': total_items, 'related_count': len(related_links), 'message': f'Item {current_item} linked to {len(related_links)} cross-platform paraphrase(s).'})}\n\n"
                    except Exception as _link_exc:
                        logger.warning(f"paraphrase link skipped for doc {document_id}: {_link_exc}")

                    # Content archetype detection: classify what kind of
                    # content this is (podcast / music / documentary / vlog /
                    # short / etc). Drives the per-creator fingerprint policy
                    # so we don't (e.g.) run web research on a music video or
                    # treat song lyrics as conversational voice.
                    try:
                        from backend.services.content_archetype import classify_and_persist_smart
                        item_meta_for_arch = item.get("metadata") or {}
                        if not isinstance(item_meta_for_arch, dict):
                            item_meta_for_arch = {}
                        arch_input = {
                            "title": title,
                            "transcript": item.get("raw_text") or text_content or "",
                            "caption": item.get("caption") or item_meta_for_arch.get("caption") or "",
                            "platform": platform,
                            "content_type": item.get("content_type"),
                            "duration_sec": (
                                item.get("duration_sec")
                                or item_meta_for_arch.get("duration_sec")
                                or item_meta_for_arch.get("duration")
                                or item_meta_for_arch.get("video_duration")
                            ),
                            "hashtags": item.get("hashtags") or item_meta_for_arch.get("hashtags") or [],
                            "metadata": item_meta_for_arch,
                        }
                        arch_result = await classify_and_persist_smart(str(item_id), arch_input)
                        yield f"data: {json.dumps({'stage': 'archetype_classified', 'current': current_item, 'total': total_items, 'archetype': arch_result.get('archetype'), 'confidence': arch_result.get('confidence'), 'source': arch_result.get('source', 'rule')})}\n\n"
                    except Exception as _arch_exc:
                        logger.warning(f"archetype classify skipped for item {item_id}: {_arch_exc}")
                    
                    # Update search_items status
                    update_status_query = """
                        UPDATE scrape_items
                        SET review_status = 'approved'
                        WHERE id = %s::uuid
                    """
                    db.execute_update(update_status_query, (str(item_id),))
                    changed_item_count += 1
                    
                    ingested.append(
                        ApproveIngestItem(
                            queue_id=str(item_id),
                            document_id=document_id,
                            chunks_inserted=len(chunk_ids)
                        )
                    )
                    
                except Exception as e:
                    print(f"Error processing item {item_id}: {e}")
                    error_query = """
                        UPDATE scrape_items
                        SET review_status = 'denied', transcript_status = 'error'
                        WHERE id = %s::uuid
                    """
                    db.execute_update(error_query, (str(item_id),))
                    yield f"data: {json.dumps({'stage': 'error', 'current': current_item, 'total': total_items, 'message': f'Error processing item {current_item}: {str(e)}'})}\n\n"
                    continue
            
            # Send completion event
            if ingested or denied_item_ids or doc_ids_to_delete:
                db.execute_update(
                    "UPDATE creators SET last_approved_version = config_version WHERE id = %s",
                    (creator_id,)
                )
                refresh_creator_corpus_state(creator_id, sync_fingerprint=(changed_item_count == 0))

            # Recompute creator archetype now that fresh items are classified.
            # Cheap aggregation query — safe to run even when nothing changed.
            try:
                from backend.services.content_archetype import compute_and_persist_creator_archetype_smart
                creator_arch = await compute_and_persist_creator_archetype_smart(int(creator_id))
                profile = creator_arch.get('llm_profile') or {}
                yield f"data: {json.dumps({'stage': 'creator_archetype', 'archetype': creator_arch.get('creator_archetype'), 'confidence': creator_arch.get('confidence'), 'distribution': creator_arch.get('distribution'), 'descriptive_label': profile.get('descriptive_label'), 'format_blend': profile.get('format_blend')})}\n\n"
            except Exception as _carch_exc:
                logger.warning(f"creator archetype recompute skipped for {creator_id}: {_carch_exc}")

            result = {
                'stage': 'complete',
                'current': total_items,
                'total': total_items,
                'message': f'Successfully processed {len(ingested)} items ({changed_item_count} changed, {skipped_item_count} unchanged)!',
                'result': {
                    'approved': len(approved_item_ids),
                    'changed': changed_item_count,
                    'unchanged': skipped_item_count,
                    'ingested': [{'queue_id': i.queue_id, 'document_id': i.document_id, 'chunks_inserted': i.chunks_inserted} for i in ingested]
                }
            }
            if changed_item_count > 0:
                from backend.services.fingerprint_service import fingerprint_service
                # Use asyncio.create_task since BackgroundTasks inside a generator won't execute after StreamingResponse
                asyncio.create_task(fingerprint_service.generate_fingerprint_async(request.creator_id, mode="incremental"))
            
            yield f"data: {json.dumps(result)}\n\n"
            
        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'stage': 'error', 'message': f'Error: {error_msg}'})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================================
# Persona Endpoints
# ============================================================================

@app.get("/creator/{creator_id}/persona", response_model=PersonaResponse)
async def get_persona_endpoint(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get persona document for a creator"""
    ensure_creator_access(creator_id, current_user["id"])
    persona_content = get_persona(creator_id)
    return PersonaResponse(
        creator_id=creator_id,
        persona=persona_content or "",
        found=persona_content is not None
    )

@app.post("/creator/{creator_id}/persona", response_model=PersonaResponse)
async def save_persona_endpoint(creator_id: int, request: PersonaRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    """Save persona document for a creator"""
    try:
        ensure_creator_access(creator_id, current_user["id"])
        persona_text = request.persona
        if not persona_text:
            raise HTTPException(status_code=400, detail="Persona text is required")
        
        # Delete existing persona documents (using source='persona')
        delete_query = """
            DELETE FROM documents 
            WHERE creator_id = %s AND source = 'persona'
        """
        db.execute_update(delete_query, (creator_id,))
        
        # Insert new persona document
        # Note: metadata column might not exist, relying on source='persona'
        insert_query = """
            INSERT INTO documents (creator_id, title, content, source, source_id, url)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        # Use simple empty string for URL if not needed
        doc_id = db.execute_insert(
            insert_query,
            (
                creator_id,
                "Persona",
                persona_text,
                "persona",
                f"persona_{creator_id}",
                "", # URL
            )
        )
        
        return PersonaResponse(
            creator_id=creator_id,
            persona=persona_text,
            found=True
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _internal_server_error(e, "Failed to load persona")

@app.get("/creator/{creator_id}/queue")
async def get_queue_items(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get all queue items for a creator. Merges legacy scrape_queue and actual documents."""
    try:
        ensure_creator_access(creator_id, current_user["id"])
        # 1. Legacy scrape_queue items
        query_legacy = """
            SELECT 
                sq.id, 
                sq.title, 
                sq.url, 
                sq.raw_text, 
                sq.status,
                COUNT(c.id) as chunks_inserted
            FROM scrape_queue sq
            LEFT JOIN documents d ON d.source_id = CONCAT('queue_', sq.id) AND d.creator_id = sq.creator_id
            LEFT JOIN chunks c ON c.document_id = d.id
            WHERE sq.creator_id = %s
            GROUP BY sq.id, sq.title, sq.url, sq.raw_text, sq.status
            ORDER BY sq.created_at DESC
        """
        results_legacy = db.execute_query(query_legacy, (creator_id,))
        
        items = []
        # Add legacy items
        for row in results_legacy:
            preview = row["raw_text"][:200] + "..." if len(row["raw_text"] or "") > 200 else (row["raw_text"] or "")
            chunks_count = row.get("chunks_inserted", 0) or 0
            legacy_url = row.get("url")
            legacy_platform = _platform_from_url(legacy_url or "")
            items.append({
                "item_id": str(row["id"]),
                "queue_id": str(row["id"]),
                "title": row.get("title"),
                "caption": row.get("title"),
                "url": legacy_url,
                "source_url": legacy_url,
                "preview": preview,
                "status": row.get("status", "pending"),
                "transcript_status": "present" if row.get("status") == "ingested" else "missing",
                "chunks_inserted": chunks_count if row.get("status") == "ingested" else 0,
                "platform": legacy_platform,
                "creator_handle": "",
                "metadata": {"platform": legacy_platform},
            })

        # 2. V2 Flow Documents (The actual knowledge base)
        # Fetch actual documents (excluding persona and legacy queue wrappers if any)
        # Note: source_id usually starts with 'queue_' for legacy items, but we want all content.
        # We order by ID if created_at is missing.
        query_docs = """
            SELECT id, title, content, url, source, source_id, metadata
            FROM documents
            WHERE creator_id = %s AND source != 'persona'
            ORDER BY id DESC
            LIMIT 100
        """
        try:
            results_docs = db.execute_query(query_docs, (creator_id,))
        except Exception as e:
            # Fallback if url column doesn't exist (older schema)
            print(f"[WARN] get_queue_items V2 query failed, trying fallback: {e}")
            query_docs = """
                SELECT id, title, content, source, source_id, metadata
                FROM documents
                WHERE creator_id = %s AND source != 'persona'
                ORDER BY id DESC
                LIMIT 100
            """
            results_docs = db.execute_query(query_docs, (creator_id,))
        
        for row in results_docs:
            content_text = row.get("content") or ""
            preview = content_text[:200] + "..." if len(content_text) > 200 else content_text

            doc_metadata = row.get("metadata") or {}
            if isinstance(doc_metadata, str):
                try:
                    doc_metadata = json.loads(doc_metadata) if doc_metadata else {}
                except Exception:
                    doc_metadata = {}
            if not isinstance(doc_metadata, dict):
                doc_metadata = {}

            doc_platform = doc_metadata.get("platform") or row.get("source") or "unknown"
            doc_creator_handle = doc_metadata.get("creator_handle") or doc_metadata.get("channelName") or ""
            doc_url = row.get("url") or doc_metadata.get("source_url") or doc_metadata.get("canonical_url") or ""
            transcript_status = doc_metadata.get("transcript_status") or "present"
            
            items.append({
                "item_id": f"doc_{row['id']}",
                "queue_id": f"doc_{row['id']}",
                "title": row.get("title"),
                "caption": row.get("title"),
                "url": doc_url,
                "source_url": doc_url,
                "preview": preview,
                "status": "ingested", 
                "chunks_inserted": 1, 
                "item_status": "ingested",
                "transcript_status": transcript_status,
                "platform": doc_platform,
                "creator_handle": doc_creator_handle,
                "metadata": doc_metadata,
            })

        return {"search_id": str(creator_id), "items": items}
    except Exception as e:
        print(f"[ERROR] get_queue_items: {e}", flush=True)
        # Return empty list on error to avoid crashing UI
        return {"search_id": str(creator_id), "items": []}

@app.post("/items/{item_id}/retry-transcript")
def retry_transcript(item_id: str, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Manually retries processing the transcript for a given scrape_item.
    """
    row = db.execute_one(
        "SELECT id, source_url, platform, caption, is_primary, creator_id FROM scrape_items WHERE id = %s",
        (item_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    if row.get("creator_id"):
        ensure_creator_access(row["creator_id"], current_user["id"])

    if not row.get("is_primary"):
        raise HTTPException(status_code=400, detail="Cannot retry transcript on duplicate item")

    db.execute_update(
        "UPDATE scrape_items SET transcript_status = 'queued' WHERE id = %s",
        (item_id,)
    )

    from backend.services.transcript_worker import process_transcript_job
    background_tasks.add_task(
        process_transcript_job,
        row["id"],
        row["source_url"],
        row.get("platform") or "unknown",
        row.get("caption") or ""
    )
    return {"status": "queued"}

# --- Thread Management Endpoints ---

@app.post("/threads", response_model=ThreadResponse)
def create_thread_endpoint(req: CreateThreadRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    status_obj = get_creator_status(req.creator_id)
    if not status_obj["ready_to_chat"]:
        raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})

    ensure_creator_access(req.creator_id, current_user["id"])
    user_id = current_user["id"]
    
    # Insert new thread
    row = db.execute_one("""
        INSERT INTO chat_threads (user_id, creator_id, title)
        VALUES (%s, %s, 'New conversation')
        RETURNING id, user_id, creator_id, title, last_preview, created_at, last_message_at
    """, (user_id, req.creator_id))
    
    # Update user's last active thread for this creator
    db.execute_update("""
        INSERT INTO user_creator_preferences (user_id, creator_id, last_active_thread_id)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, creator_id) 
        DO UPDATE SET last_active_thread_id = EXCLUDED.last_active_thread_id, updated_at = NOW()
    """, (user_id, req.creator_id, row['id']))
    
    return ThreadResponse(
        id=str(row['id']),
        user_id=row['user_id'],
        creator_id=row['creator_id'],
        title=row['title'],
        last_preview=row['last_preview'],
        created_at=row['created_at'],
        last_message_at=row['last_message_at']
    )

@app.put("/threads/{thread_id}", response_model=ThreadResponse)
def update_thread_endpoint(thread_id: str, req: UpdateThreadRequest, current_user: Dict[str, Any] = Depends(require_auth)):
    user_id = current_user["id"]
    updates = []
    params = []
    
    if req.title is not None:
        updates.append("title = %s")
        updates.append("title_locked = true")
        params.append(req.title)
    
    if req.is_archived is not None:
        updates.append("is_archived = %s")
        params.append(req.is_archived)
        
    if not updates:
        # Fetch current state to return
        sql = "SELECT id, user_id, creator_id, title, last_preview, created_at, last_message_at FROM chat_threads WHERE id = %s AND user_id = %s"
        row = db.execute_one(sql, (thread_id, user_id))
    else:
        params.append(thread_id)
        params.append(user_id)
        sql = f"UPDATE chat_threads SET {', '.join(updates)} WHERE id = %s AND user_id = %s RETURNING id, user_id, creator_id, title, last_preview, created_at, last_message_at"
        row = db.execute_one(sql, tuple(params))
    
    if not row:
        raise HTTPException(status_code=404, detail="Thread not found")
        
    return ThreadResponse(
        id=str(row['id']),
        user_id=row['user_id'],
        creator_id=row['creator_id'],
        title=row['title'],
        last_preview=row['last_preview'],
        created_at=row['created_at'],
        last_message_at=row['last_message_at']
    )

@app.get("/creators/{creator_id}/threads", response_model=List[ThreadResponse])
def list_threads_endpoint(creator_id: int, archived: bool = False, current_user: Dict[str, Any] = Depends(require_auth)):
    ensure_creator_access(creator_id, current_user["id"])
    user_id = current_user["id"]
    # Filter by archived status. handling NULL as false.
    archived_clause = "is_archived = true" if archived else "(is_archived = false OR is_archived IS NULL)"
    
    query = f"""
        SELECT id, user_id, creator_id, title, last_preview, created_at, last_message_at
        FROM chat_threads
        WHERE user_id = %s AND creator_id = %s AND is_active = true AND {archived_clause}
        ORDER BY last_message_at DESC
    """
    rows = db.execute_query(query, (user_id, creator_id))
    
    return [
        ThreadResponse(
            id=str(r['id']),
            user_id=r['user_id'],
            creator_id=r['creator_id'],
            title=r['title'],
            last_preview=r['last_preview'],
            created_at=r['created_at'],
            last_message_at=r['last_message_at']
        ) for r in rows
    ]

@app.get("/threads/{thread_id}/messages", response_model=List[MessageResponse])
def list_thread_messages_endpoint(thread_id: str, current_user: Dict[str, Any] = Depends(require_auth)):
    thread = db.execute_one("SELECT id FROM chat_threads WHERE id = %s AND user_id = %s", (thread_id, current_user["id"]))
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")
    
    rows = db.execute_query("""
        SELECT id, role, content, created_at, metadata
        FROM chat_messages
        WHERE thread_id = %s
        ORDER BY created_at ASC
    """, (thread_id,))
    
    results = []
    for r in rows:
        meta = r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except:
                meta = {}
                
        results.append(MessageResponse(
            id=str(r['id']),
            role=r['role'] or "user",
            content=r['content'] or "",
            created_at=r['created_at'],
            images=meta.get("images"),
            cards=meta.get("cards"),
            citations=meta.get("citations"),
        ))
        
    return results

@app.delete("/threads/{thread_id}")
def delete_thread_endpoint(thread_id: str, current_user: Dict[str, Any] = Depends(require_auth)):
    user_id = current_user["id"]
    # Hard delete (Permanent removal as requested)
    # First verify ownership
    thread = db.execute_one("SELECT id FROM chat_threads WHERE id = %s AND user_id = %s", (thread_id, user_id))
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Delete messages first (cascade usually handles this but being explicit is safer)
    db.execute_update("DELETE FROM chat_messages WHERE thread_id = %s", (thread_id,))
    
    # Nullify preferences to avoid foreign key constraints
    db.execute_update(
        "UPDATE user_creator_preferences SET last_active_thread_id = NULL WHERE user_id = %s AND last_active_thread_id = %s",
        (user_id, thread_id),
    )
    
    # Delete thread
    db.execute_update("DELETE FROM chat_threads WHERE id = %s", (thread_id,))
    
    return {"status": "deleted"}



@app.get("/creators/{creator_id}/last_active_thread")
def get_last_active_thread(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    ensure_creator_access(creator_id, current_user["id"])
    user_id = current_user["id"]
    row = db.execute_one("""
        SELECT last_active_thread_id 
        FROM user_creator_preferences
        WHERE user_id = %s AND creator_id = %s
    """, (user_id, creator_id))
    
    if row and row.get('last_active_thread_id'):
        return {"thread_id": str(row['last_active_thread_id'])}
    return {"thread_id": None}


def _update_thread_title_background(thread_id: str):
    """
    Attempt to generate a title from conversation history using LLM.
    Only if title is 'New conversation' and not locked.
    """
    try:
        # LLM based generation
        from backend.rag import get_client
        from backend.settings import settings
        
        # Verify checking again to be sure (in case of race condition)
        thread = db.execute_one("SELECT title, title_locked FROM chat_threads WHERE id = %s", (thread_id,))
        if not thread or thread['title_locked'] or thread['title'] != 'New conversation':
            return
            
        # Fetch conversation history (limit 6 messages) to determine intent
        msgs = db.execute_query("""
            SELECT role, content FROM chat_messages 
            WHERE thread_id = %s 
            ORDER BY created_at ASC 
            LIMIT 6
        """, (thread_id,))
        
        if not msgs:
            return

        # Check conditions: 2 user messages OR 1 user + 1 assistant
        user_msgs = [m for m in msgs if m['role'] == 'user']
        assistant_msgs = [m for m in msgs if m['role'] == 'assistant']
        
        has_enough_context = False
        if len(user_msgs) >= 2:
            has_enough_context = True
        elif len(user_msgs) >= 1 and len(assistant_msgs) >= 1:
            has_enough_context = True
            
        if not has_enough_context:
            return

        # Prepare context for LLM
        history_text = ""
        for m in msgs:
            role = "User" if m['role'] == 'user' else "Assistant"
            content = m['content'][:300] # Truncate 
            history_text += f"{role}: {content}\n"

        system_prompt = (
            "Generate a short TOPIC SUMMARY title for this conversation.\n"
            "RULES:\n"
            "1. 3 to 6 words.\n"
            "2. Max 42 chars.\n"
            "3. NO trailing punctuation.\n"
            "4. NO hyphens (-), en dashes, or em dashes. Use spaces/commas.\n"
            "5. NO greetings ('Hello'). Summarize the SUBJECT.\n"
            "6. Output ONLY the title text."
        )
        
        try:
            title = rag.generate_chat_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Conversation:\n{history_text}\n\nTitle:"}
                ],
                model=settings.CHAT_MODEL,
                temperature=0.3,
                max_tokens=25
            )
            
            # Cleanup & Enforce Constraints
            title = title.replace('"', '').replace("'", "").replace("\n", "")
            # Remove hyphens/dashes as requested
            title = title.replace("-", " ").replace("\u2013", " ").replace("\u2014", " ")
            # Remove trailing punctuation
            if title and title[-1] in ".,?!;":
                title = title[:-1]
                
            # Truncate to 42 chars strict
            if len(title) > 42:
                title = title[:42].strip()
                
            if len(title) >= 3 and title.lower() != "new conversation":
                db.execute_update("UPDATE chat_threads SET title = %s WHERE id = %s", (title, thread_id))
        except Exception as e:
            print(f"LLM Title Generation Failed: {e}")

    except Exception as e:
        print(f"Error updating thread title: {e}")

# ============================================================================
# Advanced Scrape Pipeline Endpoints
# ============================================================================

from backend.services.scrape_orchestrator import ScrapeOrchestrator
from backend.services.ingest_worker import IngestWorker
from pydantic import BaseModel

class ScrapeRunRequest(BaseModel):
    creator_id: int
    platforms: Optional[List[str]] = None
    force_full: bool = False

@app.post("/scrape/run")
async def run_scrape(request: ScrapeRunRequest, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """
    Trigger an incremental scrape for a creator.
    """
    try:
        ensure_creator_access(request.creator_id, current_user["id"])
        # Verify creator
        creator = db.execute_one(
            "SELECT id, platform_configs FROM creators WHERE id = %s AND user_id = %s", 
            (request.creator_id, current_user["id"])
        )
        if not creator:
            raise HTTPException(status_code=404, detail="Creator not found")
            
        configs = []
        pc = creator.get("platform_configs") or {}
        
        # Parse platform configs from JSON if string
        if isinstance(pc, str):
            pc = json.loads(pc)
        
        # Filter platforms if requested
        if request.platforms:
            target_platforms = request.platforms
        else:
            target_platforms = pc.keys()
        
        for key in target_platforms:
            cfg = pc.get(key)
            if isinstance(cfg, dict) and cfg.get("enabled"):
                # Add platform key to config for Orchestrator
                cfg_copy = dict(cfg)
                cfg_copy["platform_key"] = key
                configs.append(cfg_copy)
                
        if not configs:
            return {"message": "No enabled platforms found to scrape."}

        # Run in background to not block response
        orchestrator = ScrapeOrchestrator(request.creator_id)
        # Note: background_tasks runs purely async; ensuring db connection safety within it
        background_tasks.add_task(orchestrator.run, configs)
        
        return {"message": "Scrape started", "platforms": [c["platform_key"] for c in configs]}
    except Exception as e:
        print(f"[ScrapeRun] Error: {e}")
        raise _internal_server_error(e, "Failed to start scrape")

@app.get("/scrape/runs")
async def get_scrape_runs(creator_id: int, limit: int = 10, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get recent scrape runs for observability."""
    try:
        ensure_creator_access(creator_id, current_user["id"])
        runs = db.execute_query(
            """
            SELECT * FROM scrape_runs 
            WHERE creator_id = %s 
            ORDER BY started_at DESC 
            LIMIT %s
            """,
            (creator_id, limit)
        )
        return {"runs": runs}
    except Exception as e:
        raise _internal_server_error(e, "Failed to load scrape runs")

@app.get("/ingest/jobs")
async def get_ingest_jobs(creator_id: int, status: Optional[str] = None, limit: int = 50, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get ingestion job queue status."""
    try:
        ensure_creator_access(creator_id, current_user["id"])
        query = "SELECT * FROM ingest_jobs WHERE creator_id = %s"
        params = [creator_id]
        
        if status:
            query += " AND status = %s"
            params.append(status)
            
        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)
        
        jobs = db.execute_query(query, tuple(params))
        return {"jobs": jobs}
    except Exception as e:
        raise _internal_server_error(e, "Failed to load ingest jobs")

@app.get("/creators/{creator_id}/fingerprint/status")
async def get_fingerprint_status(creator_id: int, current_user: Dict[str, Any] = Depends(require_auth)):
    """Get the current fingerprinting status and timestamps."""
    ensure_creator_access(creator_id, current_user["id"])
    row = db.execute_one(
        "SELECT fingerprint_status, fingerprint_progress, fingerprint_updated_at, style_fingerprint, identity_fingerprint FROM creators WHERE id = %s AND user_id = %s",
        (creator_id, current_user["id"])
    )
    if not row:
        raise HTTPException(status_code=404, detail="Creator not found")

    progress = row.get("fingerprint_progress") or {}
    if isinstance(progress, str):
        try:
            progress = json.loads(progress)
        except Exception:
            progress = {}
    if not isinstance(progress, dict):
        progress = {}

    status = row.get("fingerprint_status") or "idle"
    default_progress = {
        "status": status,
        "percent": 100 if bool(row.get("style_fingerprint") or row.get("identity_fingerprint")) and status != "processing" else 0,
        "stage": "complete" if bool(row.get("style_fingerprint") or row.get("identity_fingerprint")) and status != "processing" else status,
        "message": "Fingerprint ready." if bool(row.get("style_fingerprint") or row.get("identity_fingerprint")) and status != "processing" else "Waiting to start.",
    }
    progress = {**default_progress, **progress, "status": status}
    stage_meta = _fingerprint_stage_meta(progress.get("stage"))
    stage_list = _build_fingerprint_stage_list(progress.get("stage"), int(progress.get("percent") or 0), status)
    current_stage_index = next((item["index"] for item in stage_list if item["key"] == stage_meta["key"]), 1)
    progress = {
        **progress,
        "stage_label": stage_meta["label"],
        "stage_description": stage_meta["description"],
        "fun_line": _fingerprint_fun_line(progress.get("stage")),
        "stage_index": current_stage_index,
        "stage_total": len(stage_list),
        "stages": stage_list,
    }

    return {
        "status": status,
        "progress": progress,
        "updated_at": row.get("fingerprint_updated_at"),
        "has_fingerprint": bool(row.get("style_fingerprint") or row.get("identity_fingerprint")),
        "style": row.get("style_fingerprint") or {},
        "identity": row.get("identity_fingerprint") or {}
    }

@app.post("/creators/{creator_id}/fingerprint/generate")
async def trigger_fingerprint_generation(creator_id: int, background_tasks: BackgroundTasks, current_user: Dict[str, Any] = Depends(require_auth)):
    """Manually trigger or force refresh the Style Fingerprint."""
    ensure_creator_access(creator_id, current_user["id"])
    from backend.services.fingerprint_service import fingerprint_service
    background_tasks.add_task(fingerprint_service.generate_fingerprint_async, creator_id)
    return {"message": "Fingerprint generation started"}
