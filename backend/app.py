import logging
from fastapi import FastAPI, HTTPException, Cookie, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
import os
import json
import bcrypt
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import asyncio
from fastapi import BackgroundTasks
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
    CreateThreadRequest, ThreadResponse, MessageResponse, UpdateThreadRequest
)
from backend.rag import get_persona
import backend.rag as rag
from backend.creator_engine import ask as creator_ask
from backend.grounded_rag import grounded_rag_ask, grounded_rag_stream
from backend.ingest import ingest_document
from backend.services.identity_manager import autofill_creator_identity
from backend.apify_service import search_all, search_instagram_reels
from backend.lib.instagram_parser import parse_instagram_url
from backend.config.platforms import (
    PLATFORMS,
    get_platform,
    validate_url,
    normalize_url,
    extract_handle,
    validate_time_filter,
)
from backend.scraper_router import run_search_router, PLATFORM_MAPPERS
from backend.db import db
from backend.settings import settings
from backend.personality_analyzer import PersonalityAnalyzer
from backend.core.interaction_engine import interaction_engine
from backend.utils.name_formatter import normalize_creator_name

logger = logging.getLogger(__name__)

app = FastAPI(title="Creator Bot API")


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Log all unhandled exceptions so they appear in the terminal."""
    from fastapi import HTTPException
    if isinstance(exc, HTTPException):
        raise exc  # Let FastAPI handle HTTPException normally
    import traceback
    print(f"[ERROR] Unhandled exception: {exc}", flush=True)
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.on_event("startup")
def startup_event():
    """Minimal startup - DB table created on first use to avoid blocking app start."""
    print("[STARTUP] Backend ready", flush=True)


# In-memory progress tracking for search (key: search_id, value: progress dict)
# Also persisted to DB so progress survives backend restarts (e.g. uvicorn --reload)
_search_progress: Dict[str, Dict[str, Any]] = {}


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
    """Get progress from memory or DB."""
    if search_id in _search_progress:
        return _search_progress[search_id]
    try:
        row = db.execute_one(
            "SELECT progress_data FROM search_progress WHERE search_id = %s",
            (search_id,),
        )
        if row and row.get("progress_data"):
            data = row["progress_data"]
            if isinstance(data, str):
                data = json.loads(data)
            return dict(data) if isinstance(data, dict) else None
    except Exception:
        pass
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

# TEMP DEBUG: verify what env vars the running backend process sees
@app.get("/debug/env")
def debug_env():
    return {
        "has_apify": bool(os.getenv("APIFY_TOKEN")),
        "has_openai": bool(os.getenv("OPENAI_API_KEY")),
        "has_db_password": bool(os.getenv("DB_PASSWORD")),
    }

# CORS middleware - allow common development ports
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

def require_auth(session_id: Optional[str] = Cookie(None)) -> Dict[str, Any]:
    """Dependency to require authentication"""
    user = get_user_from_session(session_id)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user

@app.on_event("startup")
async def startup():
    """Initialize database connection"""
    try:
        db.execute_query("SELECT 1")
        print("[STARTUP] DB connection OK")
    except Exception as e:
        print(f"[STARTUP] DB connection warning: {e}")
    # Migration: Add soul and fingerprint columns if missing
    try:
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS profile_picture_url TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS identity_fingerprint JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS style_fingerprint JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS soul_md TEXT")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS research_summary JSONB")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_status TEXT DEFAULT 'idle'")
        db.execute_update("ALTER TABLE creators ADD COLUMN IF NOT EXISTS fingerprint_updated_at TIMESTAMPTZ")
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


def get_or_create_creator_for_handle(handle: str, platform: str = "instagram") -> int:
    """
    Find or create a creator row for the given handle.
    This lets us have separate personas and stats per creator instead of hardcoding id=1.
    """
    has_platforms = _creators_has_platforms_column()

    # Try to find existing creator by handle
    existing = None
    try:
        if has_platforms:
            existing = db.execute_one(
                "SELECT id, platforms FROM creators WHERE handle = %s LIMIT 1",
                (handle,),
            )
        else:
            existing = db.execute_one(
                "SELECT id FROM creators WHERE handle = %s LIMIT 1",
                (handle,),
            )
    except Exception:
        # If schema differs unexpectedly, fall back to minimal select
        existing = db.execute_one(
            "SELECT id FROM creators WHERE handle = %s LIMIT 1",
            (handle,),
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

    # No existing creator: attach to first user (or 1 as fallback)
    user_row = db.execute_one("SELECT id FROM users ORDER BY id LIMIT 1", ())
    user_id = user_row["id"] if user_row and "id" in user_row else 1
    if has_platforms:
        platforms_json = json.dumps([platform])
        creator_id = db.execute_insert(
            """
            INSERT INTO creators (user_id, name, handle, platforms)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, handle, handle, platforms_json),
        )
    else:
        creator_id = db.execute_insert(
            """
            INSERT INTO creators (user_id, handle, display_name)
            VALUES (%s, %s, %s)
            RETURNING id
            """,
            (user_id, handle, handle),
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
async def login(request: LoginRequest, response: Response):
    """Login and create session"""
    try:
        query = "SELECT id, password_hash FROM users WHERE email = %s"
        user = db.execute_one(query, (request.email,))
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        if not verify_password(request.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        
        session_id = create_session(user["id"])
        
        response.set_cookie(
            key="session_id",
            value=session_id,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite="lax"
        )
        
        return LoginResponse(session_id=session_id, user_id=user["id"])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/auth/register")
async def register(request: LoginRequest, response: Response):
    """Register a new user"""
    try:
        query = "SELECT id FROM users WHERE email = %s"
        existing = db.execute_one(query, (request.email,))
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")
        
        password_hash = hash_password(request.password)
        query = "INSERT INTO users (email, password_hash) VALUES (%s, %s) RETURNING id"
        user_id = db.execute_insert(query, (request.email, password_hash))
        
        session_id = create_session(user_id)
        
        response.set_cookie(
            key="session_id",
            value=session_id,
            max_age=30 * 24 * 60 * 60,
            httponly=True,
            samesite="lax"
        )
        
        return LoginResponse(session_id=session_id, user_id=user_id)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/auth/session", response_model=SessionResponse)
async def get_session(session_id: Optional[str] = Cookie(None)):
    """Get current session info"""
    user = get_user_from_session(session_id)
    if not user:
        return SessionResponse(user_id=0, email="", valid=False)
    return SessionResponse(user_id=user["id"], email=user["email"], valid=True)

@app.post("/auth/logout")
async def logout(response: Response, session_id: Optional[str] = Cookie(None)):
    """Logout and delete session"""
    if session_id:
        query = "DELETE FROM sessions WHERE id = %s"
        db.execute_update(query, (session_id,))
    response.delete_cookie(key="session_id")
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
        raise HTTPException(status_code=500, detail=f"Failed to load platforms: {str(e)}")


@app.get("/platforms/{key}/validate")
def validate_platform_url(key: str, url: str = ""):
    """Validate URL for a platform. Returns { valid, error?, normalized?, handle? }."""
    # Normalize first so query params like ?lang=en don't fail validation
    norm = normalize_url(url, key)
    ok, err = validate_url(norm, key)
    if not ok:
        return {"valid": False, "error": err or "Invalid"}
    h = extract_handle(norm, key)
    out = {"valid": True, "normalized": norm}
    if h:
        out["handle"] = h
    return out


# ============================================================================
# Creator Management Endpoints
# ============================================================================

@app.get("/creators", response_model=CreatorsListResponse)
async def list_creators():
    """List all creators"""
    try:
        dcol = _creator_display_column()
        query = f"""
            SELECT c.id, c.{dcol} as name, c.handle, c.created_at, c.profile_picture_url, c.visual_config, c.style_fingerprint,
                   c.search_mode,
                   (SELECT COUNT(*) FROM scrape_queue q WHERE q.creator_id = c.id AND q.status = 'ingested') as item_count
            FROM creators c
            ORDER BY c.created_at DESC
        """
        results = db.execute_query(query, ())
        
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
async def create_creator(request: CreateCreatorRequest):
    """Create a new creator (not used in simplified UI)"""
    try:
        # Name validation and normalization
        name_raw = request.name
        norm_res = normalize_creator_name(name_raw)
        if not norm_res.is_valid:
            raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
        name = norm_res.normalized
        
        platforms_json = json.dumps(request.platforms or [])
        query = """
            INSERT INTO creators (user_id, name, handle, platforms)
            VALUES (1, %s, %s, %s)
            RETURNING id, name, handle, platforms, created_at
        """
        result = db.execute_query(query, (name, request.handle, platforms_json))
        
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
        raise HTTPException(status_code=500, detail=str(e))

        raise HTTPException(status_code=500, detail=str(e))

def get_creator_status(creator_id: int) -> dict:
    row = db.execute_one(
        "SELECT config_version, last_approved_version, fingerprint_status FROM creators WHERE id = %s",
        (creator_id,)
    )
    if not row:
        return {"ready_to_chat": False, "block_reason": "Creator not found."}
    
    config_version = row.get("config_version", 1)
    last_approved = row.get("last_approved_version", 0)
    fingerprint_status = row.get("fingerprint_status", "empty")
    
    # Needs reapproval if config incremented past last approved
    needs_reapproval = last_approved < config_version
    
    # Get approved item count
    approved_count = db.execute_one(
        "SELECT COUNT(*) as count FROM scrape_items WHERE creator_handle = (SELECT handle FROM creators WHERE id = %s) AND review_status = 'approved'",
        (creator_id,)
    )
    approved_item_count = approved_count["count"] if approved_count else 0
    
    # Get ingested doc count
    doc_count = db.execute_one(
        "SELECT COUNT(*) as count FROM documents WHERE creator_id = %s",
        (creator_id,)
    )
    ingested_doc_count = doc_count["count"] if doc_count else 0
    
    ready_to_chat = (
        not needs_reapproval 
        and approved_item_count >= 1 
        and (ingested_doc_count >= 1 or fingerprint_status == "ready")
    )
    
    block_reason = ""
    if needs_reapproval:
        block_reason = "Changes detected. Approve content to continue."
    elif approved_item_count == 0:
        block_reason = "Approve content to build the fingerprint."
    elif ingested_doc_count == 0 and fingerprint_status != "ready":
        block_reason = "Waiting for content to be ingested."
    elif fingerprint_status == "error":
        block_reason = "Fingerprint failed to build. Try approving again."
        
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


@app.post("/creators/config", response_model=CreatorWithConfigResponse)
async def create_creator_with_config(request: CreateCreatorWithConfigRequest):
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

        handle = request.handle or _derive_handle_from_configs(configs)
        if not handle:
            raise HTTPException(status_code=400, detail="Could not derive handle from URLs. Provide handle or fix platform URLs.")
        
        name_raw = request.name
        if not name_raw:
            raise HTTPException(status_code=400, detail={"field": "name", "message": "Creator name is required."})
        norm_res = normalize_creator_name(name_raw)
        if not norm_res.is_valid:
            raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
        name = norm_res.normalized

        user_row = db.execute_one("SELECT id FROM users ORDER BY id LIMIT 1", ())
        user_id = user_row["id"] if user_row and user_row.get("id") else 1

        try:
            row = db.execute_one(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name = %s",
                ("creators", "platform_configs"),
            )
        except Exception:
            row = None
        has_pc = bool(row)

        dcol = _creator_display_column()
        # If creator already exists for this handle, update config instead of failing on unique constraint.
        existing = db.execute_one("SELECT id FROM creators WHERE handle = %s LIMIT 1", (handle,))
        if existing and existing.get("id"):
            creator_id = existing["id"]
            updates = [f"{dcol} = %s"]
            params = [name]
            if has_pc:
                updates.append("platform_configs = %s")
                params.append(json.dumps(configs))
            params.append(creator_id)
            db.execute_update(f"UPDATE creators SET {', '.join(updates)} WHERE id = %s", tuple(params))
        else:
            try:
                if has_pc:
                    creator_id = db.execute_insert(
                        f"""
                        INSERT INTO creators (user_id, handle, {dcol}, profile_picture_url, platform_configs, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, handle, name, request.profile_picture_url, json.dumps(configs), request.youtube_channel_id, request.youtube_handle, request.official_domains, request.course_domains, request.course_base_urls),
                    )
                else:
                    creator_id = db.execute_insert(
                        f"""
                        INSERT INTO creators (user_id, handle, {dcol}, profile_picture_url, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, handle, name, request.profile_picture_url, request.youtube_channel_id, request.youtube_handle, request.official_domains, request.course_domains, request.course_base_urls),
                    )
            except Exception as e:
                # Handle races / uniqueness: if handle already exists, update it instead.
                msg = str(e)
                if "duplicate key value" in msg and "handle" in msg:
                    existing = db.execute_one("SELECT id FROM creators WHERE handle = %s LIMIT 1", (handle,))
                    if existing and existing.get("id"):
                        creator_id = existing["id"]
                        updates = [f"{dcol} = %s"]
                        params = [name]
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
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/creators/{creator_id}", response_model=CreatorWithConfigResponse)
async def update_creator(creator_id: int, request: UpdateCreatorRequest):
    """Update creator name, handle, and/or platform_configs."""
    try:
        dcol = _creator_display_column()
        existing = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, style_fingerprint, visual_config, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls FROM creators WHERE id = %s", (creator_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="Creator not found.")

        print(f"[DEBUG] update_creator id={creator_id} request={request.dict(exclude={'profile_picture_url'})} has_pic={bool(request.profile_picture_url)}", flush=True)

        updates = []
        params = []
        name_raw = None
        norm_res = None
        if request.name is not None:
            name_raw = request.name
            norm_res = normalize_creator_name(name_raw)
            if not norm_res.is_valid:
                raise HTTPException(status_code=400, detail={"field": "name", "message": norm_res.error})
            updates.append(f"{dcol} = %s")
            params.append(norm_res.normalized)
        if request.handle is not None:
            updates.append("handle = %s")
            params.append(request.handle.strip())
        if request.profile_picture_url is not None:
            updates.append("profile_picture_url = %s")
            params.append(request.profile_picture_url)
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
                updates.append("platform_configs = %s")
                params.append(json.dumps(configs))
        if request.visual_config is not None:
             updates.append("visual_config = %s")
             params.append(json.dumps(request.visual_config))

        if request.youtube_channel_id is not None:
            updates.append("youtube_channel_id = %s")
            params.append(request.youtube_channel_id)
        if request.youtube_handle is not None:
            updates.append("youtube_handle = %s")
            params.append(request.youtube_handle)
        if request.official_domains is not None:
            updates.append("official_domains = %s")
            params.append(request.official_domains)
        if request.course_domains is not None:
            updates.append("course_domains = %s")
            params.append(request.course_domains)
        if request.course_base_urls is not None:
            updates.append("course_base_urls = %s")
            params.append(request.course_base_urls)
        if request.search_mode is not None:
            updates.append("search_mode = %s")
            params.append(request.search_mode)

        if not updates:
            configs_out = existing.get("platform_configs") or {}
            if hasattr(configs_out, "copy"):
                configs_out = dict(configs_out) if configs_out else {}
            else:
                configs_out = json.loads(configs_out) if isinstance(configs_out, str) else {}
            return CreatorWithConfigResponse(
                id=existing["id"],
                name=existing.get("display_name") or existing.get("handle") or "",
                handle=existing.get("handle"),
                profile_picture_url=existing.get("profile_picture_url"),
                platform_configs=configs_out,
                visual_config=existing.get("visual_config") or {},
                style_fingerprint=existing.get("style_fingerprint") or {},
                youtube_channel_id=existing.get("youtube_channel_id"),
                youtube_handle=existing.get("youtube_handle"),
                official_domains=existing.get("official_domains") or [],
                course_domains=existing.get("course_domains") or [],
                course_base_urls=existing.get("course_base_urls") or [],
                search_mode=existing.get("search_mode") or "hybrid",
                created_at=None,
            )
        updates.append("config_version = config_version + 1")
        
        params.append(creator_id)
        db.execute_update(
            f"UPDATE creators SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        row = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, visual_config, style_fingerprint, created_at, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, search_mode FROM creators WHERE id = %s", (creator_id,))
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
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/creators/{creator_id}")
async def delete_creator(creator_id: int):
    """Delete a creator and all associated data."""
    try:
        # Check if creator exists (simple check)
        existing = db.execute_one("SELECT id FROM creators WHERE id = %s", (creator_id,))
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
        count = db.execute_update("DELETE FROM creators WHERE id = %s", (creator_id,))
        if count == 0:
             raise HTTPException(status_code=404, detail="Creator not found during delete")

        return {"ok": True, "message": f"Creator {creator_id} deleted"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/creators/{creator_id}/config", response_model=CreatorWithConfigResponse)
async def get_creator_config(creator_id: int):
    """Get creator with platform_configs."""
    dcol = _creator_display_column()
    row = db.execute_one(
        f"SELECT id, handle, {dcol} AS display_name, profile_picture_url, platform_configs, visual_config, style_fingerprint, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, search_mode, created_at FROM creators WHERE id = %s",
        (creator_id,),
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
        created_at=row["created_at"].isoformat() if row.get("created_at") and hasattr(row["created_at"], "isoformat") else None,
    )


@app.get("/creators/{creator_id}/stats", response_model=CreatorStats)
async def get_creator_stats(creator_id: int):
    """Get stats for a creator"""
    try:
        query = "SELECT id, name, handle, platforms FROM creators WHERE id = %s"
        creator = db.execute_one(query, (creator_id,))
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
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Core Endpoints
# ============================================================================

@app.get("/user/settings", response_model=UserSettings)
async def get_user_settings():
    row = db.execute_one(
        "SELECT display_name, profile_picture_url, response_preferences FROM users WHERE id = 1"
    )
    if not row:
        return UserSettings()
    
    prefs = row.get("response_preferences") or {}
    if hasattr(prefs, "copy"):
        prefs = dict(prefs) if prefs else {}
    else:
        prefs = json.loads(prefs) if isinstance(prefs, str) else {}

    return UserSettings(
        display_name=row.get("display_name"),
        profile_picture_url=row.get("profile_picture_url"),
        response_preferences=prefs
    )

@app.put("/user/settings", response_model=UserSettings)
async def update_user_settings(request: UpdateUserSettingsRequest):
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
        params.append(json.dumps(request.response_preferences))
        
    if not updates:
        return await get_user_settings()
        
    params.append(1) # user_id
    
    db.execute_update(
        f"UPDATE users SET {', '.join(updates)} WHERE id = %s",
        tuple(params)
    )
    return await get_user_settings()

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
async def ask_stream_endpoint(request: AskRequest, background_tasks: BackgroundTasks):
    """
    Streaming version of /ask. 
    Bypasses deep classification/planning for immediate time-to-first-token.
    """
    try:
        status_obj = get_creator_status(request.creator_id)
        if not status_obj["ready_to_chat"]:
            raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})
            
        import asyncio
        
        # 1. Fetch creator soul metadata + Check fingerprint (Async)
        def _get_creator_meta():
            creator_row = db.execute_one("SELECT soul_md, fingerprint_status FROM creators WHERE id = %s", (request.creator_id,))
            if creator_row and not creator_row.get("soul_md") and creator_row.get("fingerprint_status") != "processing":
                print(f"[CHAT] Missing soul for creator {request.creator_id}, enqueueing FINGERPRINT job...")
                db.execute_insert(
                    "INSERT INTO system_jobs (creator_id, job_type, payload, message) VALUES (%s, 'FINGERPRINT', %s::jsonb, 'Auto-enqueued from chat')",
                    (request.creator_id, json.dumps({"creator_id": request.creator_id}))
                )
            return creator_row
            
        # 2. Fetch user prefs & history (Async)
        def _get_user_meta():
            user_row = db.execute_one("SELECT response_preferences, display_name FROM users WHERE id = 1")
            user_prefs = None
            user_name = None
            if user_row:
                up = user_row.get("response_preferences")
                user_name = user_row.get("display_name")
                if isinstance(up, str):
                    try: user_prefs = json.loads(up)
                    except: pass
                elif isinstance(up, dict): user_prefs = up
            return user_prefs, user_name

        # 3. Thread Logic & History (Async)
        def _get_thread_history():
            conversation_history = []
            if request.thread_id:
                try:
                    uuid.UUID(str(request.thread_id))
                    
                    # Auto-initialize thread if missing
                    db.execute_update("""
                        INSERT INTO chat_threads (id, user_id, creator_id, title)
                        VALUES (%s, 1, %s, 'New conversation')
                        ON CONFLICT (id) DO NOTHING
                    """, (request.thread_id, request.creator_id))

                    msgs_rows = db.execute_query("""
                        SELECT role, content FROM chat_messages 
                        WHERE thread_id = %s 
                        ORDER BY created_at DESC 
                        LIMIT 30
                    """, (request.thread_id,))
                    if msgs_rows:
                        msgs_rows.reverse()
                        conversation_history = [{"role": m["role"], "content": m["content"]} for m in msgs_rows]
                except ValueError:
                    request.thread_id = None
            return conversation_history

        # Execute DB calls sequentially to prevent psycopg connection threading issues
        # (psycopg single connections are not thread-safe for concurrent queries)
        _get_creator_meta()
        user_prefs, user_name = _get_user_meta()
        conversation_history = _get_thread_history()

        # 3. Generator Wrapper to capture full answer
        async def stream_wrapper():
            import copy
            full_answer = ""
            try:
                # Explicitly deepcopy conversation history to prevent frozenset cache poisoning
                safe_history = copy.deepcopy(conversation_history) if conversation_history else []
                async for chunk in grounded_rag_stream(
                    creator_id=request.creator_id,
                    question=request.question,
                    thread_id=request.thread_id,
                    conversation_history=safe_history,
                    user_preferences=user_prefs,
                    user_name=user_name,
                    user_id=1 # Default user
                ):
                    if chunk == " ":
                        # Early TTFB heartbeat
                        yield f"data: {json.dumps({'content': ' '})}\n\n"
                        continue
                        
                    full_answer += chunk
                    yield f"data: {json.dumps({'content': chunk})}\n\n"
                
                # 4. Finalize (Post-stream)
                # After the stream is exhausted, we do the background work
                if request.thread_id:
                    finalize_stream_interaction(request.thread_id, request.question, full_answer)
                    # Check for title update
                    thread = db.execute_one("SELECT title, title_locked FROM chat_threads WHERE id = %s", (request.thread_id,))
                    if thread and thread['title'] == 'New conversation' and not thread['title_locked']:
                        background_tasks.add_task(_update_thread_title_background, request.thread_id)
                
                yield "data: [DONE]\n\n"
            except Exception as stream_err:
                import traceback
                tb = traceback.format_exc()
                logger.error(f"Error mid-stream: {stream_err}")
                logger.debug(tb)
                yield f"data: {json.dumps({'error': str(stream_err), 'traceback': tb})}\n\n"

        return StreamingResponse(stream_wrapper(), media_type="text/event-stream")

    except Exception as e:
        import traceback
        logger.error(f"Streaming failed before started: {e}")
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(e))

def finalize_stream_interaction(thread_id: str, question: str, answer: str):
    """Save the final interaction to DB after stream completion."""
    try:
        # Save User Message
        db.execute_update("""
            INSERT INTO chat_messages (thread_id, role, content)
            VALUES (%s, 'user', %s)
        """, (thread_id, question))

        # Save Assistant Message
        db.execute_update("""
            INSERT INTO chat_messages (thread_id, role, content)
            VALUES (%s, 'assistant', %s)
        """, (thread_id, answer))

        # Update thread preview
        preview = answer[:60] + "..." if len(answer) > 60 else answer
        db.execute_update("""
            UPDATE chat_threads 
            SET last_message_at = NOW(), last_preview = %s 
            WHERE id = %s
        """, (preview, thread_id))
        
        # Sync memory in background
        from db import interaction_engine
        interaction_engine.store_interaction("1", "1", thread_id, question, answer)
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
async def generate_fingerprint_endpoint(creator_id: int):
    """
    Manually trigger or regenerate a creator fingerprint via background worker queue.
    """
    try:
        creator_row = db.execute_one("SELECT id FROM creators WHERE id = %s", (creator_id,))
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(request: AskRequest, background_tasks: BackgroundTasks):
    # Pre-chat check: Ensure soul assets exist
    creator_row = db.execute_one("SELECT soul_md, fingerprint_status FROM creators WHERE id = %s", (request.creator_id,))
    if creator_row and not creator_row.get("soul_md") and creator_row.get("fingerprint_status") != "processing":
        print(f"[ASK] Missing soul for creator {request.creator_id}, enqueueing FINGERPRINT job...")
        db.execute_insert(
            "INSERT INTO system_jobs (creator_id, job_type, payload, message) VALUES (%s, 'FINGERPRINT', %s::jsonb, 'Auto-enqueued from chat')",
            (request.creator_id, json.dumps({"creator_id": request.creator_id}))
        )

    """
    Ask a question using Grounded-RAG Loop algorithm.
    Uses broad retrieval + re-ranking + answer contract + grounding validation.
    Handles thread persistence if thread_id is provided.
    """
    try:
        status_obj = get_creator_status(request.creator_id)
        if not status_obj["ready_to_chat"]:
            raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})
            
        # Get user preferences
        user_row = db.execute_one("SELECT response_preferences, display_name FROM users WHERE id = 1")
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
        
        # Thread Logic (Session Persistence)
        conversation_history = request.messages
        thread = None
        
        if request.thread_id:
             # Validate UUID format
             try:
                 uuid.UUID(str(request.thread_id))
                 
                 # Auto-initialize thread if missing
                 db.execute_update("""
                     INSERT INTO chat_threads (id, user_id, creator_id, title)
                     VALUES (%s, 1, %s, 'New conversation')
                     ON CONFLICT (id) DO NOTHING
                 """, (request.thread_id, request.creator_id))
                 
                 # Verify thread exists
                 thread = db.execute_one("SELECT id, user_id, title, title_locked FROM chat_threads WHERE id = %s", (request.thread_id,))
             except ValueError:
                 print(f"[WARN] Invalid UUID received for thread_id: {request.thread_id}. Treating as new thread.")
                 request.thread_id = None
                 thread = None

             if thread:
                 # Update last active thread preference
                 db.execute_update("""
                    INSERT INTO user_creator_preferences (user_id, creator_id, last_active_thread_id)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (user_id, creator_id) 
                    DO UPDATE SET last_active_thread_id = EXCLUDED.last_active_thread_id, updated_at = NOW()
                 """, (1, request.creator_id, request.thread_id))
                 
                 # Save user message with images (persisted in metadata)
                 user_metadata = {}
                 if request.images and len(request.images) > 0:
                     # Store images in metadata JSON so they persist on refresh
                     # Note: Storing base64 strings in DB can be heavy, but required for persistence without S3.
                     user_metadata["images"] = [
                         {"data_url": img.data_url, "detail": img.detail} 
                         for img in request.images
                     ]

                 db.execute_update("""
                    INSERT INTO chat_messages (thread_id, role, content, metadata)
                    VALUES (%s, 'user', %s, %s::jsonb)
                 """, (request.thread_id, request.question, json.dumps(user_metadata)))
                 
                 # Fetch history from DB for RAG context (last 20 messages)
                 # We want the messages BEFORE the one we just inserted.
                 # So we fetch limit 21 desc, and look at them.
                 msgs_rows = db.execute_query("""
                    SELECT role, content FROM chat_messages 
                    WHERE thread_id = %s 
                    ORDER BY created_at DESC 
                    LIMIT 21
                 """, (request.thread_id,))
                 
                 if msgs_rows:
                     # Reverse to chronological order [oldest ... newest]
                     msgs_rows.reverse()
                     
                     # The last message in msgs_rows should be the one we just inserted (user question).
                     # We want history *excluding* the current question for the RAG 'conversation_history' param.
                     # (grounded_rag_ask treats 'question' as new, 'conversation_history' as past)
                     if msgs_rows[-1]['role'] == 'user' and msgs_rows[-1]['content'] == request.question:
                          msgs_rows.pop() 
                     
                     conversation_history = [{"role": m["role"], "content": m["content"]} for m in msgs_rows]
        
        # Get creator name
        creator_name = "Creator"
        try:
            cr = db.execute_one("SELECT name, handle FROM creators WHERE id = %s", (request.creator_id,))
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
        if request.images and len(request.images) > 0:
            images_payload = [{"data_url": img.data_url, "detail": img.detail} for img in request.images[:4]]
            print(f"[ASK] {len(images_payload)} image(s) attached, using vision model")
        
        # Auto-inject default question for image-only messages
        question = request.question
        if images_payload and (not question or not question.strip()):
            question = "Describe this image and point out anything important."
        
        # Use grounded RAG algorithm for better grounding
        result = grounded_rag_ask(
            creator_id=request.creator_id,
            question=question,
            conversation_history=conversation_history,
            top_k=request.top_k or 6,
            max_distance=request.max_distance or 1.15,
            debug=request.debug or False,
            user_preferences=user_prefs,
            user_name=user_name,
            creator_name=creator_name,
            images=images_payload,
            user_id=thread.get("user_id", 1) if thread else 1,
            thread_id=request.thread_id
        )
        
        answer_text = result["answer"]
        
        # Post-Processing: Save Assistant Message & Update Thread
        if request.thread_id and thread:
             # Save assistant message with cards in metadata
             assistant_metadata = {}
             cards = result.get("cards") or ([] if result.get("card") is None else [result.get("card")])
             if cards:
                 assistant_metadata["cards"] = cards

             db.execute_update("""
                INSERT INTO chat_messages (thread_id, role, content, metadata)
                VALUES (%s, 'assistant', %s, %s::jsonb)
             """, (request.thread_id, answer_text, json.dumps(assistant_metadata)))
             
             # Update thread metadata
             preview = answer_text[:60] + "..." if len(answer_text) > 60 else answer_text
             db.execute_update("""
                UPDATE chat_threads 
                SET last_message_at = NOW(), last_preview = %s 
                WHERE id = %s
             """, (preview, request.thread_id))
             
             # Trigger title update if needed (only if 'New conversation' and unlocked)
             if thread['title'] == 'New conversation' and not thread['title_locked']:
                  background_tasks.add_task(_update_thread_title_background, request.thread_id)

        # Ensure response matches AskResponse format
        return {
            "answer": answer_text,
            "retrieved": result.get("retrieved", []),
            "sources": result.get("sources", []),
            "cards": result.get("cards") or ([] if result.get("card") is None else [result.get("card")]),
            "debug_info": result.get("debug") if request.debug else None,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/ingest", response_model=IngestResponse)
async def ingest(request: IngestRequest):
    """Ingest a single document"""
    try:
        result = ingest_document(
            creator_id=request.creator_id,
            title=request.title,
            content=request.content,
            source=request.source,
            source_id=request.source_id,
            doc_type=request.doc_type
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Scraping Endpoints
# ============================================================================

def _normalize_timestamp(ts: Any) -> Optional[datetime]:
    """
    Normalize timestamp to Python datetime for PostgreSQL TIMESTAMPTZ.
    Handles: Unix timestamps (int/float), ISO strings, datetime objects, None.
    """
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts
    if isinstance(ts, (int, float)):
        try:
            # Handle Unix timestamps (seconds since epoch)
            # If timestamp is > year 2100, assume milliseconds
            if ts > 4102444800:  # Jan 1, 2100 in seconds
                ts = ts / 1000.0
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            return dt
        except (ValueError, OSError) as e:
            print(f"Warning: Failed to parse timestamp {ts}: {e}")
            return None
    if isinstance(ts, str):
        try:
            # Try ISO format first
            if ts.endswith("Z"):
                ts = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            try:
                # Try parsing as Unix timestamp string
                ts_float = float(ts)
                if ts_float > 4102444800:
                    ts_float = ts_float / 1000.0
                return datetime.fromtimestamp(ts_float, tz=timezone.utc)
            except (ValueError, OSError):
                print(f"Warning: Failed to parse timestamp string {ts}")
                return None
    return None


_allowed_transcript_statuses = None

def normalize_transcript_status(input_status: str) -> str:
    global _allowed_transcript_statuses
    if _allowed_transcript_statuses is None:
        try:
            from backend.db import db
            res = db.execute_query("SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conname = 'scrape_items_transcript_status_check'")
            if res and res[0].get('def'):
                def_str = res[0]['def']
                import re
                matches = re.findall(r"'([^']+)'::text", def_str)
                if matches:
                    _allowed_transcript_statuses = set(matches)
        except Exception as e:
            print(f"Warning: failed to load transcript_status constraint: {e}", flush=True)
            
    if not _allowed_transcript_statuses:
        _allowed_transcript_statuses = {"present", "missing", "error", "not_started", "queued", "processing"}
        
    s = str(input_status).lower()
    if s in _allowed_transcript_statuses:
        return s
        
    print(f"Warning: Normalizing invalid transcript_status '{s}'", flush=True)
    
    if "not_started" in _allowed_transcript_statuses:
        return "not_started"
    elif "queued" in _allowed_transcript_statuses:
        return "queued"
    elif "missing" in _allowed_transcript_statuses:
        return "missing"
    elif "pending" in _allowed_transcript_statuses:
        return "pending"
    else:
        return sorted(list(_allowed_transcript_statuses))[0]

from backend.services.duplicate_detection import generate_canonical_key, compute_normalized_text, simhash64, find_duplicate

def _execute_search_run(creator_id: int, creator_handle: str, normalized_items: List[Dict[str, Any]], source_url: str, platform: str, mode: str, search_run_id: Optional[str] = None):
    """Create scrape_run + scrape_items, return (search_run_id, response_items, failed_items)."""
    if not search_run_id:
        search_run_id = str(uuid.uuid4())
    scrape_run_query = """
        INSERT INTO scrape_runs (id, source_url, platform, mode, creator_handle, items_found)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id
    """
    db.execute_insert(
        scrape_run_query,
        (search_run_id, source_url, platform, mode, creator_handle, len(normalized_items))
    )
    response_items = []
    failed_items = []
    for item in normalized_items:
        base_meta = item.get("metadata") or {}
        if not isinstance(base_meta, dict):
            base_meta = {}
            
        # Ensure we have a valid platform and creator_handle
        item_platform = item.get("platform") or base_meta.get("platform")
        
        # If still missing or generic "multi", try to derive from individual URL
        if not item_platform or item_platform in ("multi", "unknown"):
            surl = (item.get("source_url") or "").lower()
            if "youtube.com" in surl or "youtu.be" in surl:
                item_platform = "youtube"
            elif "instagram.com" in surl:
                item_platform = "instagram"
            elif "twitter.com" in surl or "x.com" in surl:
                item_platform = "twitter"
            elif "tiktok.com" in surl:
                item_platform = "tiktok"
            elif "facebook.com" in surl or "fb.com" in surl:
                item_platform = "facebook"
            elif "linkedin.com" in surl:
                item_platform = "linkedin"
            elif "reddit.com" in surl:
                item_platform = "reddit"
            else:
                item_platform = platform or "unknown"

        item_creator_handle = item.get("creator_handle") or base_meta.get("creator_handle") or creator_handle or "unknown"
        
        # Clean up creator_handle if it's explicitly "unknown" string but we have a better fallback
        if str(item_creator_handle).lower() == "unknown" and creator_handle:
            item_creator_handle = creator_handle

        meta = {
            **base_meta,
            "platform": item_platform,
            "matched_time_filter": item.get("matched_time_filter", True)
        }
        metadata_json = json.dumps(meta, default=str)
        published_at_raw = item.get("published_at")
        published_at = _normalize_timestamp(published_at_raw)
        
        # Duplicate detection
        canon_key = generate_canonical_key(item["source_url"], item_platform)
        norm_text = compute_normalized_text(
            item.get("title", ""),
            item.get("description", ""),
            item.get("caption", "")
        )
        fingerprint = simhash64(norm_text)
        is_primary, dup_item_id, dup_method, dup_confidence = find_duplicate(
            canon_key, fingerprint, item_creator_handle
        )
        
        insert_query = """
            INSERT INTO scrape_items (
                id, scrape_run_id, creator_handle, content_type, source_url,
                caption, transcript, transcript_status, published_at, metadata, review_status,
                canonical_key, content_fingerprint, is_primary, duplicate_of_item_id, duplicate_method, duplicate_confidence
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_url) DO UPDATE SET
                scrape_run_id = EXCLUDED.scrape_run_id,
                creator_handle = EXCLUDED.creator_handle,
                content_type = EXCLUDED.content_type,
                caption = EXCLUDED.caption,
                transcript = EXCLUDED.transcript,
                transcript_status = EXCLUDED.transcript_status,
                published_at = EXCLUDED.published_at,
                metadata = EXCLUDED.metadata,
                review_status = 'pending_review',
                canonical_key = EXCLUDED.canonical_key,
                content_fingerprint = EXCLUDED.content_fingerprint,
                is_primary = EXCLUDED.is_primary,
                duplicate_of_item_id = EXCLUDED.duplicate_of_item_id,
                duplicate_method = EXCLUDED.duplicate_method,
                duplicate_confidence = EXCLUDED.duplicate_confidence
            RETURNING id
        """
        
        norm_status = normalize_transcript_status(item.get("transcript_status", "missing"))
        
        try:
            db_item_id = db.execute_insert(
                insert_query,
                (
                    str(uuid.uuid4()), search_run_id, item_creator_handle, item["content_type"],
                    item["source_url"], item.get("caption"), item.get("transcript"),
                    norm_status, published_at, metadata_json, "pending_review",
                    canon_key, fingerprint, is_primary, dup_item_id, dup_method, dup_confidence
                )
            )
        except Exception as e:
            print(f"Failed to insert scrape item {item.get('source_url')}: {e}", flush=True)
            failed_items.append({
                "url": item.get("source_url"),
                "reason_sanitized": "Database insertion failed for this item."
            })
            continue

        preview_text = item.get("transcript") or item.get("caption", "") or ""
        preview = preview_text[:200] + "..." if len(preview_text) > 200 else preview_text
        # Serialize published_at for JSON response
        published_at_str = None
        if published_at:
            if isinstance(published_at, datetime):
                published_at_str = published_at.isoformat()
            else:
                published_at_str = str(published_at)
                
        response_items.append({
            "item_id": str(db_item_id),
            "source_url": item["source_url"],
            "caption": item.get("caption"),
            "creator_handle": item_creator_handle,
            "transcript_status": norm_status,
            "published_at": published_at_str,
            "platform": item_platform,
            "metadata": meta,
            "preview": preview,
            "is_primary": is_primary,
            "duplicate_of_item_id": dup_item_id
        })
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
            "message": "Preparing search..."
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
                    "stage": "scraping",
                    "percent": round(percent, 1),
                    "platform_statuses": platform_statuses_progress,
                    "message": msg
                })
                _set_search_progress(search_run_id, prog)
        
        # Run search router with progress callback
        normalized_items, platform_statuses = run_search_router(
            creator_id, creator_handle, pc, progress_callback=progress_callback
        )
        
        # Merge statuses into platform_configs and persist
        pc_updated = {}
        for k, cfg in (pc or {}).items():
            c = dict(cfg) if isinstance(cfg, dict) else {}
            st = platform_statuses.get(k)
            if st:
                c["last_search_status"] = st.get("last_search_status")
                c["last_search_at"] = st.get("last_search_at")
                c["last_error"] = st.get("last_error")
            pc_updated[k] = c
        
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
            
        # 3. Finalizing Stage (90-95%)
        # Skip Transcripts stage (80-90%) as we don't have explicit enrichment step here currently
        _set_search_progress(search_run_id, {
            **(_get_search_progress(search_run_id) or {}),
            "stage": "finalizing",
            "percent": 90.0,
            "message": "Finalizing..."
        })
        
        # Save items to database
        _, response_items, failed_items = _execute_search_run(
            creator_id, creator_handle, normalized_items,
            source_url or f"creator:{creator_id}", platform_tag, "profile",
            search_run_id=search_run_id
        )
        
        # Update final progress with detailed platform info
        prog = _get_search_progress(search_run_id)
        if prog is not None:
            # Calculate per-platform summary
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
            
            prog.update({
                "status": "running",
                "stage": "finalizing",
                "phase": "transcripts",
                "percent": 70.0,
                "items_found": len(response_items),
                "failed_count": len(failed_items),
                "platform_statuses": platform_statuses,
                "platform_summary": platform_summary,
                "completed": enabled_count,
                "message": "Scrape complete, processing transcripts..."
            })
            _set_search_progress(search_run_id, prog)
            print(f"[SEARCH] Final summary for search {search_run_id}:")
            for key, summary in platform_summary.items():
                print(f"  {summary['label']}: {summary['status']} ({summary['items_found']} items)" + (f" - {summary['error']}" if summary['error'] else ""))
                
        # Sequence transcripts pipeline
        from backend.services.transcript_worker import run_transcripts_for_search
        run_transcripts_for_search(search_run_id)
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
async def search_endpoint(request: SearchRequest, background_tasks: BackgroundTasks):
    """
    Search via Apify. Two modes:
    - Legacy: provide `url` (Instagram) + optional `limit`. Creates creator by handle.
    - Config: provide `creator_id`. Loads platform_configs from DB (or override via `platform_configs`), runs router.
    
    Returns immediately with search_id. Use /search/{search_id}/progress to track progress.
    """
    try:
        payload = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    except Exception:
        payload = {"creator_id": request.creator_id}
    print("[SEARCH] request payload:", payload, flush=True)
    print("[APIFY] token present:", bool(settings.APIFY_TOKEN), flush=True)
    search_run_id = None  # Initialize to avoid UnboundLocalError
    try:
        if request.creator_id is not None:
            # Config-based flow: load creator + platform_configs, run search router async
            if not settings.APIFY_TOKEN:
                raise HTTPException(status_code=500, detail="APIFY_TOKEN is not set.")
            dcol = _creator_display_column()
            row = db.execute_one(
                f"SELECT id, handle, {dcol} AS display_name, platform_configs FROM creators WHERE id = %s",
                (request.creator_id,),
            )
            if not row:
                raise HTTPException(status_code=404, detail="Creator not found.")
            creator_handle = row.get("handle") or row.get("display_name") or "creator"
            pc = row.get("platform_configs") or {}
            if request.platform_configs is not None:
                pc = _validate_and_normalize_platform_configs(request.platform_configs)
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
            
            # Start background scraping task by enqueuing to system_jobs
            # We store the required params in the payload
            job_payload = {
                "search_id": search_run_id,
                "creator_id": request.creator_id,
                "creator_handle": creator_handle,
                "platform_configs": pc,
                "source_url": source_url or f"creator:{request.creator_id}",
                "platform_tag": platform_tag
            }
            
            db.execute_insert(
                """
                INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
                VALUES (%s, 'SCRAPE', %s::jsonb, 'queued', 0, 'Scrape job enqueued')
                RETURNING id
                """,
                (request.creator_id, json.dumps(job_payload))
            )
            
            # Return immediately with search_id
            return {
                "search_id": search_run_id,
                "items": [],  # Empty initially, fetch via /search/{search_id}/items when complete
                "creator_id": request.creator_id,
                "platform_statuses": {},
            }
        if request.url:
            # Legacy: single Instagram URL
            limit = min(request.limit, 10)
            parsed = parse_instagram_url(request.url)
            if not parsed:
                raise HTTPException(status_code=400, detail="Invalid Instagram URL. Provide a valid profile or reel URL.")
            handle = parsed["handle"]
            reel_id = parsed.get("reel_id")
            mode = parsed.get("mode") or "profile"
            if not settings.APIFY_TOKEN:
                raise HTTPException(status_code=500, detail="APIFY_TOKEN is not set.")
            creator_id = get_or_create_creator_for_handle(handle, platform="instagram")
            try:
                normalized_items = search_instagram_reels(handle, reel_id, limit)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Apify scraping failed: {str(e)}.")
            if not normalized_items:
                raise HTTPException(status_code=404, detail=f"No Instagram reels found for @{handle}")
            search_run_id, response_items, failed_items = _execute_search_run(
                creator_id, handle, normalized_items, request.url, "instagram", mode
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
async def get_search_progress(search_id: str):
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
        "phase": progress.get("phase", "scrape"),
        "counts": counts
    }


@app.get("/search/{search_id}/items", response_model=SearchResponse)
async def get_search_items(search_id: str):
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
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================================
# Approval & Ingestion Endpoints
# ============================================================================

@app.post("/approve_ingest", response_model=ApproveIngestResponseNew)
async def approve_ingest(request: ApproveIngestRequestNew):
    """Ingest items from queue - insert documents from search_queue, then chunk and embed (legacy endpoint)"""
    try:
        conn = db.connect()

        # Fetch rows to ingest
        queue_rows = fetch_queue_items(conn, request.creator_id, request.queue_ids)

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
            mark_queue_ingested(conn, request.creator_id, ingested_ids)

        return ApproveIngestResponseNew(approved=len(request.queue_ids), ingested=ingested)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/approvals/{creator_id}/commit")
async def commit_approvals_endpoint(creator_id: int, request: ApproveIngestRequestV2):
    """
    Approve items from search_items staging table and enqueue INGEST job.
    """
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

        # Delete existing documents synchronously since it's fast
        if doc_ids_to_delete:
            db.execute_update("DELETE FROM chunks WHERE document_id = ANY(%s)", (doc_ids_to_delete,))
            db.execute_update("DELETE FROM documents WHERE id = ANY(%s)", (doc_ids_to_delete,))
            # DB cascades will clean up creator_documents
        
        sid = request.search_id or request.scrape_id
        if denied_item_ids:
            deny_query = """
                UPDATE scrape_items
                SET review_status = 'denied'
                WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
            """
            db.execute_update(deny_query, (denied_item_ids, sid))
        
        if not approved_item_ids:
            # If nothing to ingest but things were deleted, we might want to regenerate fingerprint
            if doc_ids_to_delete:
                job_id = db.execute_insert(
                    """
                    INSERT INTO system_jobs (creator_id, job_type, payload, message)
                    VALUES (%s, 'FINGERPRINT', %s::jsonb, 'Regenerating fingerprint after deletions')
                    RETURNING id
                    """,
                    (creator_id, json.dumps({"creator_id": creator_id}))
                )
                return {"job_id": job_id, "approved": 0}
            return {"job_id": None, "approved": 0}
        
        # Enqueue INGEST job
        job_payload = {
            "creator_id": creator_id,
            "search_id": sid,
            "approved_item_ids": approved_item_ids
        }
        
        job_id = db.execute_insert(
            """
            INSERT INTO system_jobs (creator_id, job_type, payload, status, progress_percent, message)
            VALUES (%s, 'INGEST', %s::jsonb, 'queued', 0, 'Ingest job enqueued')
            RETURNING id
            """,
            (creator_id, json.dumps(job_payload))
        )
            
        return {"job_id": job_id, "approved": len(approved_item_ids)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}/progress")
async def get_job_progress(job_id: str):
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
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/approve_ingest_v2/stream")
async def approve_ingest_v2_stream(request: ApproveIngestRequestV2, background_tasks: BackgroundTasks):
    """
    Streaming version of approve_ingest_v2 with real-time progress updates via SSE.
    Returns Server-Sent Events with progress information.
    """
    import asyncio
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
                db.execute_update("DELETE FROM chunks WHERE document_id = ANY(%s)", (doc_ids_to_delete,))
                db.execute_update("DELETE FROM documents WHERE id = ANY(%s)", (doc_ids_to_delete,))
            
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
                yield f"data: {json.dumps({'stage': 'complete', 'current': 0, 'total': 0, 'message': 'No items to approve'})}\n\n"
                return
            
            # Fetch approved items
            yield f"data: {json.dumps({'stage': 'fetching', 'current': 0, 'total': total_items, 'message': f'Fetching {total_items} approved items...'})}\n\n"
            
            fetch_query = """
                SELECT id, creator_handle, source_url, caption, transcript, 
                       transcript_status, published_at, metadata, content_type
                FROM scrape_items
                WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
            """
            items = db.execute_query(fetch_query, (approved_item_ids, sid))
            
            if not items:
                yield f"data: {json.dumps({'stage': 'error', 'message': 'No approved items found'})}\n\n"
                return
            
            creator_id = request.creator_id
            ingested = []
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
                    # Handle transcript fallback if needed
                    transcript = item.get("transcript") or ""
                    transcript_status = item.get("transcript_status", "missing")
                    
                    if transcript_status == "missing" and settings.TRANSCRIBE_ON_INGEST:
                        yield f"data: {json.dumps({'stage': 'transcribing', 'current': current_item, 'total': total_items, 'message': f'Transcribing item {current_item}...'})}\n\n"
                        
                        metadata = item.get("metadata") or {}
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata) if metadata else {}
                        
                        video_url = metadata.get("video_url") or metadata.get("videoUrl") or metadata.get("video") or ""
                        
                        if not video_url:
                            vid = metadata.get("videoId") or metadata.get("id")
                            if metadata.get("platform") == "youtube" and vid:
                                video_url = f"https://www.youtube.com/watch?v={vid}"
                        
                        if not video_url:
                            video_url = item.get("source_url") or ""
                        
                        if video_url:
                            try:
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
                    
                    text_content = transcript if transcript and transcript.strip() else (item.get("caption") or "")
                    
                    if not text_content:
                        print(f"Skipping item {item_id}: no transcript or caption")
                        continue
                    
                    # Extract source metadata
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
                    
                    content_id = item_meta.get("content_id") or ""
                    title_from_meta = item_meta.get("title") or ""
                    
                    if not content_id:
                        from backend.apify_service import extract_content_id
                        content_id = extract_content_id(source_url, platform)
                    if not title_from_meta:
                        from backend.apify_service import extract_title_from_metadata
                        title_from_meta = extract_title_from_metadata({}, platform, source_url)
                    
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
                    title = str(title_from_meta) if title_from_meta else "Untitled"
                    source_id = str(content_id) if content_id else f"search_item_{item_id}"
                    source_platform = str(platform) if platform else "unknown"
                    
                    document_id = db.execute_insert(
                        doc_query,
                        (creator_id, title, text_content, source_platform, str(source_id), json.dumps(doc_metadata, default=str))
                    )
                    
                    if not document_id:
                        continue
                    
                    # Chunk the document
                    yield f"data: {json.dumps({'stage': 'chunking', 'current': current_item, 'total': total_items, 'message': f'Breaking item {current_item} into chunks...'})}\n\n"
                    
                    chunks = chunk_text_structured(
                        text=text_content,
                        creator_id=creator_id,
                        document_id=document_id,
                        chunk_size=800,
                        overlap=120
                    )
                    
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
                    
                    # Update search_items status
                    update_status_query = """
                        UPDATE scrape_items
                        SET review_status = 'approved'
                        WHERE id = %s::uuid
                    """
                    db.execute_update(update_status_query, (str(item_id),))
                    
                    db.execute_update(
                        "UPDATE creators SET last_approved_version = config_version WHERE id = %s",
                        (creator_id,)
                    )
                    
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
            result = {
                'stage': 'complete',
                'current': total_items,
                'total': total_items,
                'message': f'Successfully ingested {len(ingested)} items!',
                'result': {
                    'approved': len(approved_item_ids),
                    'ingested': [{'queue_id': i.queue_id, 'document_id': i.document_id, 'chunks_inserted': i.chunks_inserted} for i in ingested]
                }
            }
            if ingested or doc_ids_to_delete:
                from backend.services.fingerprint_service import fingerprint_service
                # Use asyncio.create_task since BackgroundTasks inside a generator won't execute after StreamingResponse
                asyncio.create_task(fingerprint_service.generate_fingerprint_async(request.creator_id))
            
            yield f"data: {json.dumps(result)}\n\n"
            
        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'stage': 'error', 'message': f'Error: {error_msg}'})}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================================
# Persona Endpoints
# ============================================================================

@app.get("/creator/{creator_id}/persona", response_model=PersonaResponse)
async def get_persona_endpoint(creator_id: int):
    """Get persona document for a creator"""
    persona_content = get_persona(creator_id)
    return PersonaResponse(
        creator_id=creator_id,
        persona=persona_content or "",
        found=persona_content is not None
    )

@app.post("/creator/{creator_id}/persona", response_model=PersonaResponse)
async def save_persona_endpoint(creator_id: int, request: PersonaRequest):
    """Save persona document for a creator"""
    try:
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/creator/{creator_id}/queue")
async def get_queue_items(creator_id: int):
    """Get all queue items for a creator. Merges legacy scrape_queue and actual documents."""
    try:
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
            items.append({
                "item_id": str(row["id"]),
                "queue_id": str(row["id"]),
                "title": row.get("title"),
                "caption": row.get("title"),
                "url": row.get("url"),
                "source_url": row.get("url"),
                "preview": preview,
                "status": row.get("status", "pending"),
                "transcript_status": "present" if row.get("status") == "ingested" else "missing",
                "chunks_inserted": chunks_count if row.get("status") == "ingested" else 0
            })

        # 2. V2 Flow Documents (The actual knowledge base)
        # Fetch actual documents (excluding persona and legacy queue wrappers if any)
        # Note: source_id usually starts with 'queue_' for legacy items, but we want all content.
        # We order by ID if created_at is missing.
        query_docs = """
            SELECT id, title, content, url, source, source_id
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
                SELECT id, title, content, source, source_id
                FROM documents
                WHERE creator_id = %s AND source != 'persona'
                ORDER BY id DESC
                LIMIT 100
            """
            results_docs = db.execute_query(query_docs, (creator_id,))
        
        for row in results_docs:
            # Check if this document is already covered by legacy queue logic 
            # (simple dedupe by checking if we have a queue item with same title/content? Hard to map perfectly)
            # For now, we list everything. 
            # If it's a legacy item, it might appear twice (once as Queue Item Pending/Ingested, once as Doc).
            # This is acceptable for "Manager Mode".
            
            content_text = row.get("content") or ""
            preview = content_text[:200] + "..." if len(content_text) > 200 else content_text
            
            # Use 'url' column if present, otherwise try to extract or empty
            doc_url = row.get("url") or ""
            
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
                "transcript_status": "present"
            })

        return {"search_id": str(creator_id), "items": items}
    except Exception as e:
        print(f"[ERROR] get_queue_items: {e}", flush=True)
        # Return empty list on error to avoid crashing UI
        return {"search_id": str(creator_id), "items": []}

@app.post("/items/{item_id}/retry-transcript")
def retry_transcript(item_id: str, background_tasks: BackgroundTasks):
    """
    Manually retries processing the transcript for a given scrape_item.
    """
    row = db.execute_one(
        "SELECT id, source_url, platform, caption, is_primary FROM scrape_items WHERE id = %s",
        (item_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")
        
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
def create_thread_endpoint(req: CreateThreadRequest):
    status_obj = get_creator_status(req.creator_id)
    if not status_obj["ready_to_chat"]:
        raise HTTPException(status_code=409, detail={"error": "not_ready", "message": status_obj["block_reason"], "status": status_obj})

    # Assume default user_id = 1 for now
    user_id = 1
    
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
def update_thread_endpoint(thread_id: str, req: UpdateThreadRequest):
    user_id = 1
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
def list_threads_endpoint(creator_id: int, archived: bool = False):
    user_id = 1
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
def list_thread_messages_endpoint(thread_id: str):
    # Verify ownership (optional but good practice)
    user_id = 1
    
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
            cards=meta.get("cards")
        ))
        
    return results

@app.delete("/threads/{thread_id}")
def delete_thread_endpoint(thread_id: str):
    user_id = 1
    # Hard delete (Permanent removal as requested)
    # First verify ownership
    thread = db.execute_one("SELECT id FROM chat_threads WHERE id = %s AND user_id = %s", (thread_id, user_id))
    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    # Delete messages first (cascade usually handles this but being explicit is safer)
    db.execute_update("DELETE FROM chat_messages WHERE thread_id = %s", (thread_id,))
    
    # Nullify preferences to avoid foreign key constraints
    db.execute_update("UPDATE user_creator_preferences SET last_active_thread_id = NULL WHERE last_active_thread_id = %s", (thread_id,))
    
    # Delete thread
    db.execute_update("DELETE FROM chat_threads WHERE id = %s", (thread_id,))
    
    return {"status": "deleted"}



@app.get("/creators/{creator_id}/last_active_thread")
def get_last_active_thread(creator_id: int):
    user_id = 1
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
            title = title.replace("-", " ").replace("–", " ").replace("—", " ")
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
@app.delete("/creators/{creator_id}", status_code=204)
async def delete_creator_endpoint(creator_id: int):
    """
    Delete a creator and ALL their associated data:
    - Creator profile
    - Chat threads & messages
    - Documents & Chunks (Knowledge Base)
    - Scrape items / Search results
    - Vector embeddings (implied by chunks deletion)
    """
    try:
        # Verify creator exists
        exists = db.execute_one("SELECT id FROM creators WHERE id = %s", (creator_id,))
        if not exists:
            # If not found, perhaps already deleted? 204 is fine.
            return

        # 1. Delete Messages (via threads)
        try:
             db.execute_update(
                 "DELETE FROM messages WHERE thread_id IN (SELECT id FROM threads WHERE creator_id = %s)",
                 (creator_id,)
             )
        except Exception: 
            pass

        # 2. Delete Threads
        try:
            db.execute_update("DELETE FROM threads WHERE creator_id = %s", (creator_id,))
        except Exception:
            pass

        # 3. Delete Chunks (Vector Store Data) - This is critical for vector cleanup
        try:
             db.execute_update("DELETE FROM chunks WHERE creator_id = %s", (creator_id,))
        except Exception:
             pass

        # Also clean up chunks that might be linked via documents but missing creator_id (if any legacy issues)
        try:
             db.execute_update(
                 "DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE creator_id = %s)",
                 (creator_id,)
             )
        except Exception:
             pass

        # 4. Delete Documents (Knowledge Base)
        try:
             db.execute_update("DELETE FROM documents WHERE creator_id = %s", (creator_id,))
        except Exception:
             pass

        # 5. Delete Scrape Items / Queue (Raw Search Data)
        # Note: scrape_queue is legacy, scrape_items is current
        try:
             db.execute_update("DELETE FROM scrape_queue WHERE creator_id = %s", (creator_id,))
        except Exception:
             pass 
        
        # New table usage - scrape_items usually linked to search_run_id which is often ephemeral or missing proper linkage
        # However, we can TRY to delete if creator_id exists
        try:
             # Just in case `creator_id` column exists
             db.execute_update("DELETE FROM scrape_items WHERE creator_id = %s", (creator_id,)) 
        except Exception:
             # If column doesn't exist, we might have orphan scrape_items but that's less critical than KB/Vectors
             pass

        # 6. Delete Creator Profile
        db.execute_update("DELETE FROM creators WHERE id = %s", (creator_id,))

        return 
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting creator {creator_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete creator: {str(e)}")

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
async def run_scrape(request: ScrapeRunRequest, background_tasks: BackgroundTasks):
    """
    Trigger an incremental scrape for a creator.
    """
    try:
        # Verify creator
        creator = db.execute_one(
            "SELECT id, platform_configs FROM creators WHERE id = %s", 
            (request.creator_id,)
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/scrape/runs")
async def get_scrape_runs(creator_id: int, limit: int = 10):
    """Get recent scrape runs for observability."""
    try:
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ingest/jobs")
async def get_ingest_jobs(creator_id: int, status: Optional[str] = None, limit: int = 50):
    """Get ingestion job queue status."""
    try:
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/creators/{creator_id}/fingerprint/status")
async def get_fingerprint_status(creator_id: int):
    """Get the current fingerprinting status and timestamps."""
    row = db.execute_one(
        "SELECT fingerprint_status, fingerprint_updated_at, style_fingerprint, identity_fingerprint FROM creators WHERE id = %s",
        (creator_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Creator not found")
        
    return {
        "status": row.get("fingerprint_status") or "idle",
        "updated_at": row.get("fingerprint_updated_at"),
        "has_fingerprint": bool(row.get("style_fingerprint") or row.get("identity_fingerprint")),
        "style": row.get("style_fingerprint") or {},
        "identity": row.get("identity_fingerprint") or {}
    }

@app.post("/creators/{creator_id}/fingerprint/generate")
async def trigger_fingerprint_generation(creator_id: int, background_tasks: BackgroundTasks):
    """Manually trigger or force refresh the Style Fingerprint."""
    from backend.services.fingerprint_service import fingerprint_service
    background_tasks.add_task(fingerprint_service.generate_fingerprint_async, creator_id)
    return {"message": "Fingerprint generation started"}

