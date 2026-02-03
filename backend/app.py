from fastapi import FastAPI, HTTPException, Cookie, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import json
import bcrypt
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import asyncio
from fastapi import BackgroundTasks
from .models import (
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
    ApproveIngestRequestV2
)
from .rag import get_persona
from .creator_engine import ask as creator_ask
from .grounded_rag import grounded_rag_ask
from .ingest import ingest_document
from .apify_client import search_all, search_instagram_reels
from .lib.instagram_parser import parse_instagram_url
from .config.platforms import (
    PLATFORMS,
    get_platform,
    validate_url,
    normalize_url,
    extract_handle,
    validate_time_filter,
)
from .scraper_router import run_search_router, PLATFORM_MAPPERS
from .db import db
from .settings import settings

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
    db.connect()

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
    from .config.platforms import TIME_MODES, LAST_DAYS_OPTIONS
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
    """List all creators (default to creator_id=1)"""
    try:
        query = """
            SELECT id, name, handle, platforms, created_at
            FROM creators
            WHERE id = 1
            ORDER BY created_at DESC
        """
        results = db.execute_query(query, ())
        
        creators = []
        for row in results:
            platforms = row.get("platforms") or []
            if isinstance(platforms, str):
                platforms = json.loads(platforms) if platforms else []
            creators.append(Creator(
                id=row["id"],
                name=row["name"],
                handle=row.get("handle"),
                platforms=platforms if isinstance(platforms, list) else [],
                created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"])
            ))
        
        return CreatorsListResponse(creators=creators)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/creators", response_model=Creator)
async def create_creator(request: CreateCreatorRequest):
    """Create a new creator (not used in simplified UI)"""
    try:
        platforms_json = json.dumps(request.platforms or [])
        query = """
            INSERT INTO creators (user_id, name, handle, platforms)
            VALUES (1, %s, %s, %s)
            RETURNING id, name, handle, platforms, created_at
        """
        result = db.execute_query(query, (request.name, request.handle, platforms_json))
        
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
            created_at=row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"])
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        handle = request.handle or _derive_handle_from_configs(configs)
        if not handle:
            raise HTTPException(status_code=400, detail="Could not derive handle from URLs. Provide handle or fix platform URLs.")
        name = (request.name or handle).strip()
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
                        INSERT INTO creators (user_id, handle, {dcol}, platform_configs)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, handle, name, json.dumps(configs)),
                    )
                else:
                    creator_id = db.execute_insert(
                        f"""
                        INSERT INTO creators (user_id, handle, {dcol})
                        VALUES (%s, %s, %s)
                        RETURNING id
                        """,
                        (user_id, handle, name),
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

        creator = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, created_at FROM creators WHERE id = %s", (creator_id,))
        if has_pc:
            pc = db.execute_one("SELECT platform_configs FROM creators WHERE id = %s", (creator_id,))
            configs_out = pc.get("platform_configs") if pc else configs
            if hasattr(configs_out, "copy"):
                configs_out = dict(configs_out) if configs_out else {}
            else:
                configs_out = json.loads(configs_out) if isinstance(configs_out, str) else (configs_out or {})
        else:
            configs_out = configs

        return CreatorWithConfigResponse(
            id=creator_id,
            name=creator.get("display_name") or creator.get("handle") or name,
            handle=creator.get("handle"),
            platform_configs=configs_out,
            created_at=creator["created_at"].isoformat() if creator.get("created_at") and hasattr(creator["created_at"], "isoformat") else None,
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
        existing = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, platform_configs FROM creators WHERE id = %s", (creator_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="Creator not found.")

        updates = []
        params = []
        if request.name is not None:
            updates.append(f"{dcol} = %s")
            params.append(request.name.strip())
        if request.handle is not None:
            updates.append("handle = %s")
            params.append(request.handle.strip())
        if request.platform_configs is not None:
            configs = _validate_and_normalize_platform_configs(request.platform_configs)
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
                platform_configs=configs_out,
                created_at=None,
            )

        params.append(creator_id)
        db.execute_update(
            f"UPDATE creators SET {', '.join(updates)} WHERE id = %s",
            tuple(params),
        )
        row = db.execute_one(f"SELECT id, handle, {dcol} AS display_name, platform_configs, created_at FROM creators WHERE id = %s", (creator_id,))
        pc = row.get("platform_configs") or {}
        if hasattr(pc, "copy"):
            pc = dict(pc) if pc else {}
        else:
            pc = json.loads(pc) if isinstance(pc, str) else {}
        return CreatorWithConfigResponse(
            id=row["id"],
            name=row.get("display_name") or row.get("handle") or "",
            handle=row.get("handle"),
            platform_configs=pc,
            created_at=row["created_at"].isoformat() if row.get("created_at") and hasattr(row["created_at"], "isoformat") else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/creators/{creator_id}/config", response_model=CreatorWithConfigResponse)
async def get_creator_config(creator_id: int):
    """Get creator with platform_configs."""
    dcol = _creator_display_column()
    row = db.execute_one(
        f"SELECT id, handle, {dcol} AS display_name, platform_configs, created_at FROM creators WHERE id = %s",
        (creator_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Creator not found.")
    pc = row.get("platform_configs") or {}
    if hasattr(pc, "copy"):
        pc = dict(pc) if pc else {}
    else:
        pc = json.loads(pc) if isinstance(pc, str) else {}
    return CreatorWithConfigResponse(
        id=row["id"],
        name=row.get("display_name") or row.get("handle") or "",
        handle=row.get("handle"),
        platform_configs=pc,
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

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(request: AskRequest):
    """
    Ask a question using Grounded-RAG Loop algorithm.
    Uses broad retrieval + re-ranking + answer contract + grounding validation.
    """
    try:
        # Use grounded RAG algorithm for better grounding
        result = grounded_rag_ask(
            creator_id=request.creator_id,
            question=request.question,
            conversation_history=request.messages,
            top_k=request.top_k or 6,
            max_distance=request.max_distance or 1.15,
            debug=request.debug or False,
        )
        
        # Ensure response matches AskResponse format
        return {
            "answer": result["answer"],
            "retrieved": result.get("retrieved", []),
            "sources": result.get("sources", []),
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


def _execute_search_run(creator_id: int, creator_handle: str, normalized_items: List[Dict[str, Any]], source_url: str, platform: str, mode: str, search_run_id: Optional[str] = None):
    """Create scrape_run + scrape_items, return (search_run_id, response_items)."""
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
    for item in normalized_items:
        base_meta = item.get("metadata") or {}
        if not isinstance(base_meta, dict):
            base_meta = {}
        meta = {**base_meta, "platform": item.get("platform"), "matched_time_filter": item.get("matched_time_filter", True)}
        metadata_json = json.dumps(meta, default=str)
        published_at_raw = item.get("published_at")
        published_at = _normalize_timestamp(published_at_raw)
        insert_query = """
            INSERT INTO scrape_items (
                id, scrape_run_id, creator_handle, content_type, source_url,
                caption, transcript, transcript_status, published_at, metadata, review_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_url) DO UPDATE SET
                scrape_run_id = EXCLUDED.scrape_run_id,
                creator_handle = EXCLUDED.creator_handle,
                content_type = EXCLUDED.content_type,
                caption = EXCLUDED.caption,
                transcript = EXCLUDED.transcript,
                transcript_status = EXCLUDED.transcript_status,
                published_at = EXCLUDED.published_at,
                metadata = EXCLUDED.metadata,
                review_status = 'pending_review'
            RETURNING id
        """
        db_item_id = db.execute_insert(
            insert_query,
            (
                str(uuid.uuid4()), search_run_id, item["creator_handle"], item["content_type"],
                item["source_url"], item.get("caption"), item.get("transcript"),
                item["transcript_status"], published_at, metadata_json, "pending_review"
            )
        )
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
            "transcript_status": item["transcript_status"],
            "published_at": published_at_str,
            "platform": item.get("platform"),
            "metadata": meta,
            "preview": preview
        })
    return search_run_id, response_items


def _run_search_background(
    search_run_id: str,
    creator_id: int,
    creator_handle: str,
    pc: Dict[str, Any],
    source_url: str,
    platform_tag: str,
):
    """Background task to run scraping and update progress."""
    try:
        # Ensure progress exists (may already be created by main handler)
        enabled_count = sum(1 for cfg in pc.values() if isinstance(cfg, dict) and cfg.get("enabled"))
        current = _get_search_progress(search_run_id)
        if not current:
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
        
        def progress_callback(platform_key: str, status: str, current: int, total: int):
            """Update progress for this search run."""
            prog = _get_search_progress(search_run_id)
            if prog is not None:
                plat = get_platform(platform_key)
                label = plat.get("label", platform_key) if plat else platform_key
                platform_statuses_progress = prog.get("platform_statuses", {})
                if platform_key not in platform_statuses_progress:
                    platform_statuses_progress[platform_key] = {}
                platform_statuses_progress[platform_key].update({
                    "status": status,
                    "label": label,
                })
                prog.update({
                    "current_platform": platform_key,
                    "current_platform_label": label,
                    "completed": current,
                    "total": total,
                    "status": "running",
                    "platform_statuses": platform_statuses_progress,
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
        
        # Save items to database
        _execute_search_run(
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
                "status": "completed",
                "items_found": len(normalized_items),
                "platform_statuses": platform_statuses,
                "platform_summary": platform_summary,
                "completed": enabled_count,
            })
            _set_search_progress(search_run_id, prog)
            print(f"[SEARCH] Final summary for search {search_run_id}:")
            for key, summary in platform_summary.items():
                print(f"  {summary['label']}: {summary['status']} ({summary['items_found']} items)" + (f" - {summary['error']}" if summary['error'] else ""))
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
            prog.update({"status": "error", "error": msg})
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
            
            # Start background scraping task
            background_tasks.add_task(
                _run_search_background,
                search_run_id,
                request.creator_id,
                creator_handle,
                pc,
                source_url or f"creator:{request.creator_id}",
                platform_tag,
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
            search_run_id, response_items = _execute_search_run(
                creator_id, handle, normalized_items, request.url, "instagram", mode
            )
            return {"search_id": search_run_id, "items": response_items, "creator_id": creator_id}
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
    Returns: { status, current_platform, current_platform_label, completed, total, items_found, error }
    Progress is persisted to DB so it survives backend restarts.
    """
    print(f"[SEARCH] GET /search/{search_id}/progress", flush=True)
    progress = _get_search_progress(search_id)
    if progress:
        print(f"[DEBUG] Returning progress for {search_id}: {progress.get('status')} err={progress.get('error')}", flush=True)
    if not progress:
        raise HTTPException(status_code=404, detail="Search run not found or progress expired")
    
    percentage = int((progress["completed"] / progress["total"] * 100)) if progress["total"] > 0 else 0
    return {
        **progress,
        "percentage": percentage,
    }


@app.get("/search/{search_id}/items", response_model=SearchResponse)
async def get_search_items(search_id: str):
    """Get all items for a search run"""
    try:
        query = """
            SELECT id, source_url, caption, transcript, transcript_status, 
                   published_at, metadata, review_status
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
        from .ingest import chunk_text_structured, embed_chunks

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

                # store chunks (prefer newer schema, fall back to legacy schema)
                chunk_ids = []
                for chunk in chunks:
                    try:
                        chunk_id = db.execute_insert(
                            """
                            INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text)
                            VALUES (%s, %s, %s, %s)
                            RETURNING id
                            """,
                            (request.creator_id, document_id, chunk["index"], chunk["text"]),
                        )
                    except Exception:
                        # legacy schema fallback
                        chunk_id = db.execute_insert(
                            """
                            INSERT INTO chunks (document_id, chunk_index, content)
                            VALUES (%s, %s, %s)
                            RETURNING id
                            """,
                            (document_id, chunk["index"], chunk["text"]),
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

@app.post("/approve_ingest_v2", response_model=ApproveIngestResponseNew)
async def approve_ingest_v2(request: ApproveIngestRequestV2):
    """
    Approve/deny items from search_items staging table and ingest approved items.
    Handles transcript fallback if TRANSCRIBE_ON_INGEST is enabled.
    """
    try:
        # Separate approved and denied item IDs
        approved_item_ids = [
            d["item_id"] for d in request.decisions 
            if d.get("decision") == "approve"
        ]
        denied_item_ids = [
            d["item_id"] for d in request.decisions 
            if d.get("decision") == "deny"
        ]
        
        # Update review_status for denied items
        sid = request.search_id or request.scrape_id
        if denied_item_ids:
            deny_query = """
                UPDATE scrape_items
                SET review_status = 'denied'
                WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
            """
            db.execute_update(deny_query, (denied_item_ids, sid))
        
        if not approved_item_ids:
            return ApproveIngestResponseNew(approved=0, ingested=[])
        
        # Fetch approved items
        fetch_query = """
            SELECT id, creator_handle, source_url, caption, transcript, 
                   transcript_status, published_at, metadata, content_type
            FROM scrape_items
            WHERE id = ANY(%s::uuid[]) AND scrape_run_id = %s
        """
        items = db.execute_query(fetch_query, (approved_item_ids, sid))
        
        if not items:
            raise HTTPException(status_code=404, detail="No approved items found")
        
        # Use creator_id provided by the caller (per-handle creator records)
        creator_id = request.creator_id
        
        ingested = []
        from .ingest import chunk_text_structured, embed_chunks
        try:
            from .lib.transcription import transcribe_video
        except ImportError:
            # Fallback if transcription module not available
            def transcribe_video(url):
                return None
        
        for item in items:
            item_id = item["id"]
            try:
                # Handle transcript fallback if needed
                transcript = item.get("transcript") or ""
                transcript_status = item.get("transcript_status", "missing")
                
                if transcript_status == "missing" and settings.TRANSCRIBE_ON_INGEST:
                    # Try to transcribe
                    metadata = item.get("metadata") or {}
                    if isinstance(metadata, str):
                        metadata = json.loads(metadata) if metadata else {}
                    video_url = metadata.get("video_url") or metadata.get("videoUrl", "")
                    
                    if video_url:
                        try:
                            transcript = transcribe_video(video_url)
                            if transcript:
                                transcript_status = "present"
                                # Update search_items with transcript
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
                
                # Use transcript if available, otherwise caption
                text_content = transcript if transcript and transcript.strip() else (item.get("caption") or "")
                
                if not text_content:
                    print(f"Skipping item {item_id}: no transcript or caption")
                    continue
                
                # Extract source metadata (content_id, platform, title) from item metadata
                source_url = item["source_url"]
                item_meta = item.get("metadata") or {}
                if isinstance(item_meta, str):
                    try:
                        item_meta = json.loads(item_meta)
                    except:
                        item_meta = {}
                
                # Get platform and content_id from metadata (set by scrapers)
                platform = item_meta.get("platform") or item.get("metadata", {}).get("platform") if isinstance(item.get("metadata"), dict) else None
                if not platform:
                    # Fallback: detect from URL
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
                
                # Get content_id and title from metadata (set by search)
                content_id = item_meta.get("content_id") or ""
                title_from_meta = item_meta.get("title") or ""
                
                # Fallback extraction if not in metadata
                if not content_id:
                    from .apify_client import extract_content_id
                    content_id = extract_content_id(source_url, platform)
                if not title_from_meta:
                    from .apify_client import extract_title_from_metadata
                    title_from_meta = extract_title_from_metadata({}, platform, source_url)
                
                # Create document with full source metadata
                doc_metadata = {
                    "type": "content",
                    "platform": platform,
                    "content_type": item.get("content_type", "unknown"),
                    "creator_handle": item["creator_handle"],
                    "source_url": source_url,
                    "content_id": content_id,  # Video/post ID for linking
                    "canonical_url": source_url,  # Full URL for linking
                    "search_run_id": sid,
                    "transcript_status": transcript_status,
                    "published_at": item.get("published_at"),
                }
                # Merge any additional metadata (but don't overwrite our source fields)
                for k, v in item_meta.items():
                    if k not in ("platform", "content_id", "canonical_url", "title"):
                        doc_metadata[k] = v
                
                doc_query = """
                    INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (source, source_id) DO UPDATE SET
                        title = EXCLUDED.title,
                        content = EXCLUDED.content,
                        metadata = EXCLUDED.metadata
                    RETURNING id
                """
                title = str(title_from_meta) if title_from_meta else "Untitled"
                source_id = str(content_id) if content_id else f"search_item_{item_id}"
                # Use platform as source for documents table
                source_platform = str(platform) if platform else "unknown"
                
                document_id = db.execute_insert(
                    doc_query,
                    (creator_id, title, text_content, source_platform, str(source_id), json.dumps(doc_metadata, default=str))
                )
                
                if not document_id:
                    continue
                
                # Chunk the document
                chunks = chunk_text_structured(
                    text=text_content,
                    creator_id=creator_id,
                    document_id=document_id,
                    chunk_size=800,
                    overlap=120
                )
                
                # Store chunks with full source_ref metadata
                chunk_ids = []
                for chunk in chunks:
                    # Build source_ref for this chunk (links back to parent document)
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
                        "source_ref": source_ref,  # Full source reference for linking
                    }
                    
                    try:
                        chunk_id = db.execute_insert(
                            """
                            INSERT INTO chunks (creator_id, document_id, chunk_index, chunk_text, metadata)
                            VALUES (%s, %s, %s, %s, %s::jsonb)
                            RETURNING id
                            """,
                            (creator_id, document_id, chunk["index"], chunk["text"], json.dumps(chunk_metadata, default=str))
                        )
                    except Exception:
                        # Legacy schema fallback
                        chunk_id = db.execute_insert(
                            """
                            INSERT INTO chunks (document_id, chunk_index, content)
                            VALUES (%s, %s, %s)
                            RETURNING id
                            """,
                            (document_id, chunk["index"], chunk["text"])
                        )
                    if chunk_id:
                        chunk_ids.append(chunk_id)
                
                # Embed chunks
                embed_chunks(chunk_ids)
                
                # Update search_items status to approved
                update_status_query = """
                    UPDATE scrape_items
                    SET review_status = 'approved'
                    WHERE id = %s::uuid
                """
                db.execute_update(update_status_query, (str(item_id),))
                
                ingested.append(
                    ApproveIngestItem(
                        queue_id=str(item_id),  # Using item_id as queue_id for compatibility
                        document_id=document_id,
                        chunks_inserted=len(chunk_ids)
                    )
                )
            except Exception as e:
                print(f"Error processing item {item_id}: {e}")
                # Mark as error but continue
                error_query = """
                    UPDATE scrape_items
                    SET review_status = 'denied', transcript_status = 'error'
                    WHERE id = %s::uuid
                """
                db.execute_update(error_query, (str(item_id),))
                continue
        
        return ApproveIngestResponseNew(approved=len(approved_item_ids), ingested=ingested)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
        
        # Delete existing persona documents
        delete_query = """
            DELETE FROM documents 
            WHERE creator_id = %s AND metadata->>'type' = 'persona'
        """
        db.execute_update(delete_query, (creator_id,))
        
        # Insert new persona document
        insert_query = """
            INSERT INTO documents (creator_id, title, content, source, source_id, metadata)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        doc_id = db.execute_insert(
            insert_query,
            (
                creator_id,
                "Persona",
                persona_text,
                "persona",
                f"persona_{creator_id}",
                json.dumps({"type": "persona"}),
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

@app.get("/creator/{creator_id}/queue", response_model=SearchResponse)
async def get_queue_items(creator_id: int):
    """Get all queue items for a creator with their status and chunk counts (legacy endpoint)"""
    try:
        query = """
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
        results = db.execute_query(query, (creator_id,))
        
        items = []
        for row in results:
            preview = row["raw_text"][:200] + "..." if len(row["raw_text"]) > 200 else row["raw_text"]
            chunks_count = row.get("chunks_inserted", 0) or 0
            items.append({
                "queue_id": row["id"],
                "title": row.get("title"),
                "url": row.get("url"),
                "preview": preview,
                "status": row.get("status", "pending"),
                "chunks_inserted": chunks_count if row.get("status") == "ingested" else 0
            })
        
        return {"search_id": str(creator_id), "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
