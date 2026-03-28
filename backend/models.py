from pydantic import BaseModel
from typing import List, Optional, Literal, Dict, Any, Union

# Request models
class ImageInput(BaseModel):
    """Image attached to a chat message."""
    data_url: str  # base64 data URL (data:image/jpeg;base64,...)
    detail: str = "auto"  # "auto", "low", or "high"

class AskRequest(BaseModel):
    creator_id: int
    question: str
    top_k: int = 5
    max_distance: float = 1.15
    messages: Optional[List[Dict[str, str]]] = None  # conversation history [{role, content}]
    thread_id: Optional[str] = None  # UUID of the chat thread
    images: Optional[List[ImageInput]] = None  # Attached images (max 4)
    debug: Optional[bool] = False

class CreateThreadRequest(BaseModel):
    creator_id: int

class UpdateThreadRequest(BaseModel):
    title: Optional[str] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None

class ThreadResponse(BaseModel):
    id: str
    user_id: int
    creator_id: int
    title: str
    last_preview: Optional[str] = None
    created_at: Any
    last_message_at: Any

class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: Any
    images: Optional[List[Dict[str, Any]]] = None # To return attached images
    cards: Optional[List[Dict[str, Any]]] = None  # To return recommendation cards
    citations: Optional[List[Dict[str, Any]]] = None  # Lightweight source provenance for the message

class IngestRequest(BaseModel):
    creator_id: int
    title: str
    content: str
    source: str
    source_id: str
    doc_type: Literal["knowledge", "persona"]

class SearchRequest(BaseModel):
    url: Optional[str] = None
    limit: int = 10  # Max 10 enforced in backend
    creator_id: Optional[int] = None
    platform_configs: Optional[Dict[str, Any]] = None  # { "instagram": { "enabled", "url", "timeFilter", "maxItems" }, ... }

class SearchRequestOld(BaseModel):  # Keep for backward compatibility
    creator_id: int
    handle: str
    source: str = "tiktok"
    limit: int = 10

class ApprovalItem(BaseModel):
    source: str
    source_id: str
    title: str
    content: str
    url: Optional[str] = None

class ApproveIngestRequest(BaseModel):
    creator_id: int
    approvals: List[ApprovalItem]

class ApproveIngestRequestNew(BaseModel):
    creator_id: int
    queue_ids: List[int]
    doc_type: str = "knowledge"
    title_prefix: str = "Approved"

class ApproveIngestRequestV2(BaseModel):
    search_id: Optional[str] = None
    scrape_id: Optional[str] = None # Added for compatibility with frontend
    decisions: List[Dict[str, str]]  # [{"item_id": "...", "decision": "approve"|"deny"}]
    # Which creator these items belong to; defaults to 1 for backward compatibility.
    creator_id: int = 1

class ApproveIngestItem(BaseModel):
    # Legacy response uses integer queue_id; Instagram Reels v2 uses UUID item ids.
    queue_id: Union[int, str, Any]
    document_id: int
    chunks_inserted: int

class ApproveIngestResponseNew(BaseModel):
    approved: int
    ingested: List[ApproveIngestItem]

class PersonaRequest(BaseModel):
    persona: str

class PersonaResponse(BaseModel):
    creator_id: int
    persona: str
    found: bool

# Response models
class RetrievedChunk(BaseModel):
    chunk_id: Union[int, str]
    chunk_index: int
    distance: float
    rerank_score: Optional[float] = None
    preview: Optional[str] = None
    source_ref: Optional[Dict[str, Any]] = None

class AskResponse(BaseModel):
    answer: str
    retrieved: List[RetrievedChunk]
    sources: Optional[List[Dict[str, Any]]] = None  # Source references with URLs
    cards: Optional[List[Dict[str, Any]]] = None  # Content cards for high confidence matches
    citations: Optional[List[Dict[str, Any]]] = None  # Lightweight source provenance for rendering
    debug_info: Optional[Dict[str, Any]] = None  # only when debug=true

class IngestResponse(BaseModel):
    document_id: int
    chunks_inserted: int
    chunk_ids: List[int]

class SearchedItem(BaseModel):
    item_id: str
    source_url: str
    caption: Optional[str] = None
    transcript_status: str  # 'present', 'missing', 'error'
    published_at: Optional[str] = None
    platform: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    preview: str

class SearchedItemOld(BaseModel):  # Legacy format
    queue_id: int
    title: Optional[str] = None
    url: Optional[str] = None
    preview: str
    status: Optional[str] = "pending"

class SearchResponse(BaseModel):
    search_id: str
    items: List[SearchedItem]
    creator_id: Optional[int] = None
    platform_statuses: Optional[Dict[str, Dict[str, Any]]] = None

class SearchItemPreview(BaseModel):
    item_id: str
    source_url: str
    caption: Optional[str] = None
    transcript_status: str  # 'present', 'missing', 'error'
    published_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class ApproveIngestRequestNewV2(BaseModel):
    search_id: str
    decisions: List[Dict[str, str]]  # [{"item_id": "...", "decision": "approve"|"deny"}]

class ApproveIngestResponse(BaseModel):
    documents_inserted: int
    total_chunks_inserted: int
    document_ids: List[int]

class HealthResponse(BaseModel):
    ok: bool

# Auth models
class LoginRequest(BaseModel):
    email: str
    password: str

class LoginResponse(BaseModel):
    session_id: str
    user_id: int
    access_token: Optional[str] = None
    token_type: Optional[str] = "bearer"

class SessionResponse(BaseModel):
    user_id: int
    email: str
    valid: bool

# Creator models
class Creator(BaseModel):
    id: int
    name: str
    name_raw: Optional[str] = None
    name_suggested: Optional[str] = None
    name_flags: Optional[Dict[str, Any]] = None
    handle: Optional[str] = None
    platforms: List[str] = []
    item_count: int = 0
    profile_picture_url: Optional[str] = None
    created_at: str
    visual_config: Dict[str, Any] = {}
    style_fingerprint: Dict[str, Any] = {}
    youtube_channel_id: Optional[str] = None
    youtube_handle: Optional[str] = None
    official_domains: List[str] = []
    course_domains: List[str] = []
    course_base_urls: List[str] = []
    search_mode: str = "hybrid"


class CreateCreatorRequest(BaseModel):
    name: str
    handle: Optional[str] = None
    platforms: List[str] = []

class CreateCreatorWithConfigRequest(BaseModel):
    name: str
    handle: Optional[str] = None
    profile_picture_url: Optional[str] = None
    platform_configs: Dict[str, Any] = {}
    visual_config: Dict[str, Any] = {}
    youtube_channel_id: Optional[str] = None
    youtube_handle: Optional[str] = None
    official_domains: List[str] = []
    course_domains: List[str] = []
    course_base_urls: List[str] = []


class UpdateCreatorRequest(BaseModel):
    name: Optional[str] = None
    handle: Optional[str] = None
    profile_picture_url: Optional[str] = None
    platform_configs: Optional[Dict[str, Any]] = None
    visual_config: Optional[Dict[str, Any]] = None
    youtube_channel_id: Optional[str] = None
    youtube_handle: Optional[str] = None
    official_domains: Optional[List[str]] = None
    course_domains: Optional[List[str]] = None
    course_base_urls: Optional[List[str]] = None
    search_mode: Optional[str] = None


class CreatorWithConfigResponse(BaseModel):
    id: int
    name: str
    name_raw: Optional[str] = None
    name_suggested: Optional[str] = None
    name_flags: Optional[Dict[str, Any]] = None
    handle: Optional[str] = None
    profile_picture_url: Optional[str] = None
    platform_configs: Dict[str, Any] = {}
    visual_config: Dict[str, Any] = {}
    style_fingerprint: Dict[str, Any] = {}
    youtube_channel_id: Optional[str] = None
    youtube_handle: Optional[str] = None
    official_domains: List[str] = []
    course_domains: List[str] = []
    course_base_urls: List[str] = []
    search_mode: str = "hybrid"
    status: Optional[Dict[str, Any]] = None

    created_at: Optional[str] = None

class CreatorStats(BaseModel):
    creator_id: int
    name: str
    handle: Optional[str] = None
    platforms: List[str] = []
    last_search_time: Optional[str] = None
    items_ingested: int
    total_chunks: int

class CreatorsListResponse(BaseModel):
    creators: List[Creator]

class UserSettings(BaseModel):
    display_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    response_preferences: Dict[str, Any] = {}

class UpdateUserSettingsRequest(BaseModel):
    display_name: Optional[str] = None
    profile_picture_url: Optional[str] = None
    response_preferences: Optional[Dict[str, Any]] = None


