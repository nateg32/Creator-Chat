"""
Grounded-RAG Loop (GRL) Algorithm
Forces the assistant to stay close to retrieved DB chunks with evidence mapping and validation.
"""

from __future__ import annotations

import re
import json
import logging
from typing import List, Dict, Any, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from db import db
from settings import settings
import rag
from prompts.creator_base_prompt import CREATOR_BASE_SYSTEM_PROMPT
from services.style_distiller import StyleDistiller
from services.style_scorer import StyleScorer
from services.content_finder import ContentFinder


logger = logging.getLogger(__name__)


def _platform_from_url(url: str) -> str:
    """Derive platform key from URL. Ensures platform always matches canonical_url domain."""
    if not url or not isinstance(url, str):
        return "unknown"
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if "instagram.com" in u:
        return "instagram"
    if "linkedin.com" in u:
        return "linkedin"
    if "twitter.com" in u or "x.com" in u:
        return "twitter"
    if "tiktok.com" in u:
        return "tiktok"
    if "reddit.com" in u:
        return "reddit"
    if "facebook.com" in u or "fb.com" in u:
        return "facebook"
    return "unknown"


# Algorithm settings
K_RETRIEVE = 25  # Broad retrieval
K_FINAL = 12  # Final support set after re-ranking
MIN_SUPPORT = 1  # Minimum chunks supporting key claims
MAX_REPAIR = 1  # Max repair attempts

# Source quality weights (higher = better)
SOURCE_QUALITY_MAP = {
    "video": 1.0,  # Full videos/podcasts
    "reel": 0.9,  # Short videos
    "post": 0.8,  # Social posts
    "tweet": 0.7,  # Tweets
    "comment": 0.5,  # Comments
    "caption": 0.6,  # Captions only
}


def get_enabled_platforms_for_creator(creator_id: int) -> Optional[List[str]]:
    """
    Return enabled platform keys from creator.platform_configs, or None if not restricted.
    Used to filter retrieval to only chunks from enabled platforms.
    """
    try:
        row = db.execute_one(
            "SELECT platform_configs FROM creators WHERE id = %s LIMIT 1",
            (creator_id,),
        )
    except Exception:
        return None
    pc = row.get("platform_configs") if row else None
    if not pc:
        return None
    if isinstance(pc, str):
        try:
            pc = json.loads(pc)
        except Exception:
            return None
    if not isinstance(pc, dict):
        return None
    enabled = [
        k for k, cfg in pc.items()
        if isinstance(cfg, dict) and cfg.get("enabled") is True
    ]
    return enabled if enabled else None


def build_search_query(question: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    """
    Step 1: Build a search query from question + minimal recent context keywords.
    When user asks for "links for both/those", augment with last assistant message
    so retrieval favors the same items we just recommended.
    """
    query_parts = [question]

    # Follow-up "links for both/those": bias retrieval toward same items as last reply
    if history and is_follow_up_requesting_links(question, history):
        last_assistant = None
        for m in reversed(history):
            if (m.get("role") or "").lower() == "assistant":
                last_assistant = (m.get("content") or m.get("text") or "").strip()
                break
        if last_assistant:
            # Strip markdown, truncate; add to query so we retrieve same videos/posts
            clean = re.sub(r"\*\*", "", last_assistant)
            clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)  # keep link text, drop URL
            query_parts.append(clean[:500])

    elif history:
        # Extract 3-6 keywords from last few user turns
        recent_text = " ".join([
            msg.get("content", "") for msg in history[-3:]
            if msg.get("role") == "user"
        ])
        words = re.findall(r"\b[a-z]{4,}\b", recent_text.lower())
        stop_words = {"that", "this", "what", "when", "where", "which", "with", "from", "have", "been", "will", "would"}
        keywords = [w for w in words if w not in stop_words][:6]
        if keywords:
            query_parts.extend(keywords)

    return " ".join(query_parts)


def retrieve_candidates(
    creator_id: int,
    query_embedding: List[float],
    k_retrieve: int = K_RETRIEVE,
    max_distance: float = 1.15,
    enabled_platforms: Optional[List[str]] = None,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """
    Step 2: Retrieve broadly (high recall).
    Always filtered by creator_id. Optionally filtered by enabled_platforms from
    creator.platform_configs. canonical_url is always from content item URL
    (source_url), never from author/profile URL.
    """
    embedding_str = "[" + ",".join(map(str, query_embedding)) + "]"
    
    base = """
        SELECT 
            c.id as chunk_id,
            c.chunk_index,
            c.chunk_text,
            c.metadata as chunk_metadata,
            (e.embedding <=> %s::vector) as distance,
            d.id as document_id,
            d.title as document_title,
            d.source as document_source,
            d.source_id as document_source_id,
            d.metadata as document_metadata
        FROM chunks c
        JOIN embeddings e ON c.id = e.chunk_id
        JOIN documents d ON c.document_id = d.id
        WHERE d.creator_id = %s
        AND (d.metadata->>'type' IS NULL OR d.metadata->>'type' != 'persona')
        AND e.model = %s
        AND (e.embedding <=> %s::vector) <= %s
    """
    order_limit = """
        ORDER BY e.embedding <=> %s::vector
        LIMIT %s
    """
    params: List[Any] = [
        embedding_str, creator_id, settings.EMBEDDING_MODEL,
        embedding_str, max_distance,
    ]
    if enabled_platforms:
        # Restrict to chunks whose document platform is in enabled set (case-insensitive)
        low = [str(p).lower() for p in enabled_platforms]
        base += " AND (LOWER(COALESCE(d.metadata->>'platform','')) = ANY(%s) OR LOWER(COALESCE(d.source,'')) = ANY(%s))"
        params.extend([low, low])
    base += order_limit
    params.extend([embedding_str, k_retrieve])

    results = db.execute_query(base, tuple(params))

    candidates = []
    for r in results:
        chunk_meta = r.get("chunk_metadata") or {}
        if isinstance(chunk_meta, str):
            try:
                chunk_meta = json.loads(chunk_meta)
            except Exception:
                chunk_meta = {}
        doc_meta = r.get("document_metadata") or {}
        if isinstance(doc_meta, str):
            try:
                doc_meta = json.loads(doc_meta)
            except Exception:
                doc_meta = {}

        # canonical_url: content item URL only (source_url). Never author/profile URL.
        source_url = chunk_meta.get("source_url") or doc_meta.get("source_url") or ""
        stored = chunk_meta.get("platform") or doc_meta.get("platform") or r.get("document_source") or ""
        # Always derive platform from URL so we never show "instagram: https://youtube.com/..."
        platform = _platform_from_url(source_url) if source_url else (stored or "unknown")
        platform = (platform or "unknown").lower()
        content_id = chunk_meta.get("content_id") or doc_meta.get("content_id") or r.get("document_source_id") or ""
        content_type = chunk_meta.get("type") or chunk_meta.get("content_type") or "unknown"
        published_at = chunk_meta.get("published_at") or doc_meta.get("published_at")

        cand = {
            "chunk_id": r["chunk_id"],
            "chunk_index": r["chunk_index"],
            "distance": float(r["distance"]),
            "content": r["chunk_text"],
            "source_ref": {
                "platform": platform,
                "content_id": content_id,
                "canonical_url": source_url,
                "title": r.get("document_title") or "",
                "published_at": published_at,
                "content_type": content_type,
            },
            "document_id": r["document_id"],
        }
        candidates.append(cand)

    # Platform purity: when enabled_platforms set, keep only chunks whose URL-derived platform is in that set
    if enabled_platforms:
        allowed = { str(p).lower() for p in enabled_platforms }
        filtered = [ c for c in candidates if (c["source_ref"].get("platform") or "").lower() in allowed ]
        if len(filtered) < len(candidates) and debug:
            logger.info("retrieval_debug platform_filter kept=%d dropped=%d enabled=%s", len(filtered), len(candidates) - len(filtered), list(allowed))
        candidates = filtered

    if debug and candidates:
        for i, c in enumerate(candidates[:10]):
            ref = c["source_ref"]
            logger.info(
                "retrieval_debug chunk_id=%s creator_id=%s platform=%s canonical_url=%s",
                c["chunk_id"], creator_id, ref.get("platform"), ref.get("canonical_url"),
            )

    return candidates


def needs_links(user_msg: str) -> bool:
    """
    True if the user is asking for links/sources/proof.
    Only include links in the final answer when this is True.
    """
    t = (user_msg or "").lower()
    triggers = [
        "link", "source", "where did", "which post", "which video", "which reel",
        "show me", "send me", "url", "proof", "prove it", "are you sure",
        "reference", "references", "cite", "citation", "from which",
        "best video", "best reel", "best post", "video is best", "reel to watch",
        "any other videos", "more videos", "other videos", "any more videos",
        "what else can i watch", "what else to watch", "any other video",
        "give me the links", "links for", "links to those", "links to both",
    ]
    return any(x in t for x in triggers)


def is_follow_up_requesting_links(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
) -> bool:
    """
    True if the user is asking for links to items we just recommended (e.g. "links for both", "those").
    When True, we must provide links only for those same items, not new recommendations.
    """
    t = (question or "").lower().strip()
    if not t:
        return False
    follow_up_patterns = [
        "links for both", "links for those", "links for them", "give me the links for both",
        "give me the links for those", "links for the two", "links for the videos you",
        "links for the ones you", "links for the two you", "can you give me the links",
        "send me those", "send me the links", "link for both", "link for those",
        "the links for both", "the links for those", "links for the ones",
        "links to both", "links to those", "both links", "those links",
    ]
    if not any(p in t for p in follow_up_patterns):
        return False
    # Must have a prior assistant message (user is referring to what we just said)
    if not history:
        return False
    last_assistant = None
    for m in reversed(history):
        if (m.get("role") or "").lower() == "assistant":
            last_assistant = m.get("content") or m.get("text") or ""
            break
    return bool(last_assistant and len(last_assistant.strip()) > 0)


_PLATFORM_DISPLAY_NAMES = {
    "instagram": "Instagram",
    "youtube": "YouTube",
    "linkedin": "LinkedIn",
    "twitter": "X",
    "tiktok": "TikTok",
    "reddit": "Reddit",
    "facebook": "Facebook",
}


def _platform_display_name(key: str) -> str:
    return _PLATFORM_DISPLAY_NAMES.get((key or "").lower()) or (key or "Instagram").strip().title()


def _cta_platform_instruction(enabled_platforms: Optional[List[str]] = None) -> str:
    """
    Instruction for making CTAs (message me COACH / Elite etc.) platform-specific.
    Use the platform of the source when the CTA comes from retrieved content;
    otherwise prefer Instagram when ingested, or the first enabled platform.
    """
    base = "When you mention 'message me X' (e.g. COACH, Elite), always add the platform: e.g. 'message me COACH on Instagram' or 'message me Elite on Instagram'. "
    if not enabled_platforms:
        return base + "Use the platform(s) where the creator's content was ingested; prefer 'on Instagram' when that is one of them. If the CTA appears in a specific retrieved source, use that source's platform (see [Source N - platform] in context)."
    low = [str(p).lower() for p in enabled_platforms]
    if "instagram" in low:
        default = "Instagram"
    else:
        default = _platform_display_name(enabled_platforms[0] or "instagram")
    return (
        base
        + f"If the CTA comes from a specific retrieved source, use that source's platform (see [Source N - platform] in context). Otherwise use only ingested platforms ({', '.join(enabled_platforms)}) and prefer 'on {default}'."
    )


def needs_cta(user_msg: str) -> bool:
    """
    True if the user is asking about coaching, programs, or working together.
    Only mention coaching/group/DM/COACH when this is True.
    """
    t = (user_msg or "").lower()
    triggers = [
        "coaching", "coach", "program", "programme", "work with you", "work together",
        "mentor", "mentorship", "join your", "your group", "your community", "your course",
        "hire you", "book you", "consulting", "offer",
    ]
    return any(x in t for x in triggers)


_INTENT_PATTERNS = [
    ([
        "what's your name", "what is your name", "who are you", "what do you do", "your name",
        "what's my name", "what is my name", "do you know my name", "my name"
    ], "identity"),
    (["how are you", "what's up", "hey", "hello", "hi there", "good morning", "good afternoon", "hi", "hey,"], "small_talk"),
    (["start a business", "start business", "starting a business", "want to start", "want to start a business", "i want to start"], "start_business"),
    (["how do i", "how to", "how can i", "steps to", "guide to", "tutorial"], "how_to"),
    (["strategy", "strategies", "framework", "breakdown", "explain ", "deep dive"], "deep_strategy"),
    (["link", "source", "which post", "which video", "show me", "send me", "url", "proof", "best video", "best reel", "best post", "video link", "post link", "whats the video", "that video", "that reel", "that post", "any other videos", "more videos", "other videos", "any more videos", "what else can i watch", "what else to watch", "any other video", "give me the links", "links for", "tools", "recommend"], "request_sources"),
]


def classify_intent(question: str) -> str:
    """Rule-based intent: identity | small_talk | start_business | how_to | deep_strategy | request_sources."""
    q = (question or "").lower().strip()
    if not q:
        return "small_talk"
    
    # 1. Check explicit patterns
    for patterns, intent in _INTENT_PATTERNS:
        if any(p in q for p in patterns):
            return intent
            
    # 2. Heuristic for low-intent: Very short messages (1-2 words) that don't match specific business triggers
    words = q.split()
    if len(words) <= 2:
        # If it's short but NOT asking for links/proof, it's probably a greeting or small talk
        return "small_talk"
        
    return "how_to"  # default


def classify_resource_intent(
    question: str,
    history: Optional[List[Dict[str, str]]] = None,
    creator_profile: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Semantic Intent Router to detect when user needs a resource (video, article, course).
    """
    import rag
    
    # Prepare profile context
    profile_info = "Available platforms: YouTube, Instagram, Website."
    if creator_profile:
        pc = creator_profile.get("platform_configs") or {}
        active_plats = [k for k, v in pc.items() if isinstance(v, dict) and v.get("enabled")]
        if active_plats:
            profile_info = f"Available platforms: {', '.join(active_plats)}."
        if creator_profile.get("has_course"):
            profile_info += " This creator HAS a paid course/modules."

    # Prepare history context
    history_context = ""
    if history:
        history_context = "\nConversation History:\n"
        for m in history[-10:]:
            role = m.get("role", "user").upper()
            content = m.get("content") or m.get("text") or ""
            history_context += f"{role}: {content}\n"

    system_prompt = f"""
You are an Intent Router for a Creator AI. Your goal is to detect when the user is requesting or would benefit from a specific piece of creator content (video, article, or course lesson).

Output ONLY a JSON object:
{{
  "needs_resource": true/false,
  "resource_type": "video" | "article" | "course_lesson" | "any",
  "specificity": "specific" | "recommendation" | "evidence",
  "query": "the search query to use",
  "reason": "short explanation",
  "confidence": 0.0-1.0
}}

Set needs_resource=true when the user intent implies:
- Finding where something exists (e.g., "where did you talk about X?")
- Requesting what to watch/read/do next (learning path/recommendations)
- Requesting proof, source, clip, episode, or lesson.
- Wanting a resource recommendation related to a topic.
- Asking "how to learn X" where the creator likely has content covering it.
- Asking a specific question that is best answered by pointing to a foundational video or lesson.

{profile_info}
"""

    user_prompt = f"User Message: {question}\n{history_context}"

    try:
        response_text = rag.generate_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            json_mode=True
        )
        return json.loads(response_text)
    except Exception as e:
        logger.error(f"Intent Router failed: {e}")
        return {
            "needs_resource": False,
            "resource_type": "any",
            "specificity": "recommendation",
            "query": question,
            "reason": "error",
            "confidence": 0.0
        }


def response_length_instruction(intent: str) -> str:
    """Instruction for model response length based on intent."""
    if intent == "identity":
        return "Respond naturally in 1–2 sentences. Then ask a question in the creator's style to learn about the USER."
    if intent == "small_talk":
        return (
            "Just greet them briefly in character using their specific hooks and small talk style. "
            "Do NOT use generic advisor phrases. Ask a unique question that matches the creator's persona to open the floor. "
            "Do NOT use retrieved content. 1-2 sentences max."
        )
    if intent == "start_business":
        return "Give a high-level response in character. Share one key piece of mindset advice from the creator's worldview, then ask 1-2 clarifying questions to understand their situation better."
    if intent == "how_to":
        return "Provide actionable steps in the creator's voice. Use their specific vocabulary and frameworks. Be thorough but don't overwhelm."
    if intent == "deep_strategy":
        return "Provide a detailed, structured strategy session. Use the creator's mental models. Be as long as necessary to be comprehensive."
    if intent == "request_sources":
        return (
            "Recommend 1–3 sources most relevant to their question. "
            "For each: (a) a brief summary of the video/post, "
            "(b) how it helps their specific request, and (c) the link. "
            "If details are missing from a transcript, be honest about it. "
            "Inline the links with your summaries."
        )
    return "Match the length and depth the creator usually provides in their content."


# Match URLs for stripping or collecting
_URL_RE = re.compile(
    r"https?://[^\s\]>\)\"']+",
    re.IGNORECASE,
)


def strip_urls_from_text(text: str) -> str:
    """Remove URLs from text. Used when needs_links is False."""
    if not text:
        return text
    out = _URL_RE.sub("", text)
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _filter_sources_by_platform(
    sources: List[Dict[str, Any]],
    enabled_platforms: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Keep only sources whose platform (URL-derived) is in enabled_platforms when set."""
    if not enabled_platforms:
        return sources
    allowed = { str(p).lower() for p in enabled_platforms }
    out = []
    for s in sources:
        ref = s.get("source_ref") if isinstance(s.get("source_ref"), dict) else {}
        plat = (ref.get("platform") or "").lower()
        if plat in allowed:
            out.append(s)
    return out


def _urls_in_text(text: str) -> Set[str]:
    """Extract URLs present in text (for deduping when appending Sources)."""
    if not text:
        return set()
    return set(_URL_RE.findall(text))


def sources_section(
    sources: List[Dict[str, Any]],
    max_links: int = 3,
    enabled_platforms: Optional[List[str]] = None,
    exclude_urls: Optional[Set[str]] = None,
) -> str:
    """Build 'Sources:' section with max_links URLs, deduped by content_id. Skip URLs in exclude_urls (e.g. already inline)."""
    sources = _filter_sources_by_platform(sources, enabled_platforms)
    exclude = exclude_urls or set()
    seen: Set[str] = set()
    out: List[str] = []
    for s in sources:
        ref = s.get("source_ref") if isinstance(s.get("source_ref"), dict) else {}
        cid = ref.get("content_id") or ref.get("canonical_url") or ""
        url = ref.get("canonical_url") or ""
        if not url or cid in seen or url in exclude:
            continue
        seen.add(cid)
        title = ref.get("title") or ""
        platform = ref.get("platform") or ""
        part = f"{platform}: {url}" + (f" ({title})" if title else "")
        out.append(part)
        if len(out) >= max_links:
            break
    if not out:
        return ""
    return "Sources:\n" + "\n".join(out)


def recency_boost(published_at: Optional[str], days_old_threshold: int = 90) -> float:
    """Calculate recency boost: newer content gets slight boost."""
    if not published_at:
        return 0.5  # Neutral if no date
    
    try:
        if isinstance(published_at, str):
            if published_at.endswith("Z"):
                published_at = published_at.replace("Z", "+00:00")
            dt = datetime.fromisoformat(published_at)
        else:
            dt = published_at
        
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        
        days_old = (datetime.now(timezone.utc) - dt).days
        if days_old < 0:
            return 1.0  # Future dates (shouldn't happen)
        if days_old <= 30:
            return 1.0  # Very recent
        if days_old <= days_old_threshold:
            return 0.8  # Recent
        return 0.6  # Older
    except Exception:
        return 0.5


def source_quality_score(content_type: str) -> float:
    """Calculate source quality score based on content type."""
    return SOURCE_QUALITY_MAP.get(content_type.lower(), 0.7)


def query_term_overlap(query: str, text: str) -> float:
    """Calculate overlap between query terms and chunk text."""
    query_words = set(re.findall(r'\b[a-z]{3,}\b', query.lower()))
    text_words = set(re.findall(r'\b[a-z]{3,}\b', text.lower()))
    
    if not query_words:
        return 0.0
    
    overlap = len(query_words & text_words) / len(query_words)
    return min(overlap, 1.0)


def rerank_candidates(
    candidates: List[Dict[str, Any]],
    query: str,
    k_final: int = K_FINAL
) -> List[Dict[str, Any]]:
    """
    Step 3: Re-rank tightly using composite score.
    Score = 0.55*semantic + 0.20*recency + 0.15*source_quality + 0.10*term_overlap
    """
    scored = []
    
    for cand in candidates:
        # Normalize distance to similarity (0-1, higher is better)
        # Distance 0 = perfect match, distance 1.15 = threshold
        similarity = max(0.0, 1.0 - (cand["distance"] / 1.15))
        
        # Recency boost
        recency = recency_boost(cand["source_ref"].get("published_at"))
        
        # Source quality
        quality = source_quality_score(cand["source_ref"].get("content_type", ""))
        
        # Term overlap
        overlap = query_term_overlap(query, cand["content"])
        
        # Composite score
        score = (
            0.55 * similarity +
            0.20 * recency +
            0.15 * quality +
            0.10 * overlap
        )
        
        scored.append({
            **cand,
            "rerank_score": score,
            "score_components": {
                "similarity": similarity,
                "recency": recency,
                "quality": quality,
                "overlap": overlap,
            }
        })
    
    # Sort by rerank_score descending
    scored.sort(key=lambda x: x["rerank_score"], reverse=True)
    
    return scored[:k_final]


def build_answer_contract(
    support_set: List[Dict[str, Any]],
    question: str
) -> Dict[str, Any]:
    """
    Step 4: Build an Answer Contract with facts[] and evidence mapping.
    """
    facts = []
    gaps = []
    
    # Group chunks by document/source for deduplication
    sources_by_id = {}
    for chunk in support_set:
        source_ref = chunk["source_ref"]
        source_id = source_ref.get("content_id") or source_ref.get("canonical_url", "")
        if source_id not in sources_by_id:
            sources_by_id[source_id] = {
                "source_ref": source_ref,
                "chunks": [],
            }
        sources_by_id[source_id]["chunks"].append(chunk["chunk_id"])
    
    # Extract key facts from chunks (simplified - in production, use LLM to extract)
    # For now, we'll mark all chunks as supporting facts
    fact_id = 1
    for source_id, source_data in sources_by_id.items():
        chunk_ids = source_data["chunks"]
        if len(chunk_ids) >= MIN_SUPPORT:
            facts.append({
                "id": f"F{fact_id}",
                "text": f"Content from {source_data['source_ref'].get('platform', 'unknown')}",
                "support": chunk_ids[:MIN_SUPPORT],  # Use first MIN_SUPPORT chunks
                "source_ref": source_data["source_ref"],
            })
            fact_id += 1
    
    # Identify gaps (areas not well supported)
    if len(support_set) < MIN_SUPPORT:
        gaps.append("Limited supporting content - may need to provide general advice")
    
    return {
        "facts": facts,
        "gaps": gaps,
        "sources": list(sources_by_id.values()),
        "total_chunks": len(support_set),
    }


def generate_meaning_draft(
    question: str,
    context: str,
    verified_facts: str,
    intent: str,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    """
    Step 3: Generate a Neutral Meaning Draft (Content Plan).
    This step focuses purely on WHAT to say, ignoring HOW to say it.
    """
    
    system_prompt = """
You are a Neutral Content Planner. 
Your goal is to extract the core information needed to answer the user's question based ONLY on the provided content.
Do NOT write the final answer. Do NOT use any persona.
Output a JSON object with the following structure:
{
    "answer_points": ["Point 1", "Point 2"],
    "required_facts": [{"claim": "...", "confidence": "HIGH/MEDIUM/LOW", "source": "Source 1"}],
    "uncertainty_handling": "exact_required" | "admit_unknown" | "general_advice",
    "followup_question": "Optional clarifying question if needed",
    "tone_guidance": "neutral"
}
"""
    history_text = ""
    if conversation_history:
        history_text = "\\nRecent History:\\n" + "\\n".join([f"{m.get('role', 'unknown')}: {m.get('content', '')}" for m in conversation_history[-3:]])

    user_prompt = f"""
Context:
{context}

Verified Facts:
{verified_facts}

Question: {question}
{history_text}

Draft the content plan.
"""

    # Use rag.generate_chat_completion (assuming it handles json_mode if accessible, or just instruct json)
    # The current rag module might not expose json_mode directly in generate_chat_completion signature based on how it's called elsewhere.
    # I'll rely on the prompt instructions.
    
    response = rag.generate_chat_completion(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0, # Strict facts
        json_mode=True
    )
    
    try:
        # Simple heuristic to extract JSON if specific block not returned
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0]
        elif "{" in response:
            start = response.find("{")
            end = response.rfind("}") + 1
            json_str = response[start:end]
            
        return json.loads(json_str)
    except Exception as e:
        logger.error(f"Failed to parse Meaning Draft JSON: {e}")
        return {
            "answer_points": ["Could not parse plan"], 
            "required_facts": [], 
            "uncertainty_handling": "admit_unknown",
            "tone_guidance": "neutral"
        }



def generate_grounded_answer(
    question: str,
    support_set: List[Dict[str, Any]],
    answer_contract: Dict[str, Any],
    persona: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    intent: str = "how_to",
    include_links_in_output: bool = False,
    allow_cta: bool = False,
    enabled_platforms: Optional[List[str]] = None,
    follow_up_requesting_links: bool = False,
    user_preferences: Optional[Dict[str, Any]] = None,
    user_name: Optional[str] = None,
    creator_name: Optional[str] = None,
    style_fingerprint: Optional[Dict[str, Any]] = None,
    images: Optional[List[Dict[str, Any]]] = None,
    creator_id: Optional[int] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    SDD-CVR Implementation:
    1. Meaning Draft (Neutral)
    2. Voice Render (Creator Persona + Style DNA)
    3. Verification & Repair Loop
    """
    
    # --- Dependencies ---
    distiller = StyleDistiller()
    scorer = StyleScorer(style_fingerprint)
    
    # --- Context Construction ---
    # Build context from support set
    context_parts = []
    for i, chunk in enumerate(support_set):
        source = chunk["source_ref"]
        platform = source.get("platform", "unknown")
        title = source.get("title", "")
        context_parts.append(
            f"[Source {i+1} - {platform}" + (f": {title}" if title else "") + "]:\n"
            + chunk["content"]
        )
    context = "\n\n".join(context_parts) if context_parts else "No relevant content found."

    # Fetch verified facts
    from services.fact_verification import FactVerificationService
    fv_service = FactVerificationService()
    verified_facts_str = "No verified facts loaded."
    if creator_id:
        verified_facts_str = fv_service.get_verified_facts_formatted(creator_id)

    # --- Step 1: Meaning Draft (Neutral) ---
    logger.info("Generating Meaning Draft...")
    draft = generate_meaning_draft(question, context, verified_facts_str, intent, conversation_history)
    
    # --- Step 2: Voice Render (Creator Persona) ---
    logger.info("Rendering Voice...")
    style_dna = distiller.get_style_dna(creator_id or 0, style_fingerprint or {})
    dna_instruction = distiller.format_for_prompt(style_dna)
    
    # Construct Render Prompt
    render_system_prompt = f"""
You are {creator_name or 'the creator'}.
{persona or ''}

MISSION:
Rewrite the NEUTRAL CONTENT PLAN below into your unique voice and style.
Strictly adhere to the STYLE DNA constraints.

{dna_instruction}

RULES:
1. NO SOURCES: Do NOT mention "Source 1" or include URLs. Speak as if you know this info.
2. NO FILLER: Do not say "Here is a plan" or "I hope this helps". Dive straight in.
3. HONESTY: If the plan says "uncertainty_handling: admit_unknown", admit you don't know in your voice.
4. FORMAT: Use the structure defined in the DNA (e.g. valid frameworks, list style).
5. USER: You are talking to {user_name or 'a friend'}.
6. NO LINKS: Do not output any http links.

NEUTRAL PLAN:
{json.dumps(draft, indent=2)}
"""

    messages = [{"role": "system", "content": render_system_prompt}]
    
    # Add history for continuity
    if conversation_history:
        for msg in conversation_history[-5:]: 
             if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg.get("content", "")})
    
    # Current User Input
    messages.append({"role": "user", "content": f"Question: {question}\n(Respond using the plan above in your voice.)"})
    
    # --- Generation & Repair Loop ---
    final_response = ""
    is_rewrite = False
    style_score = {}
    
    # Pass 1
    response_text = rag.generate_chat_completion(
        messages=messages,
        temperature=0.7,
        max_tokens=1000
    )
    
    # Verify
    score_result = scorer.score_response(response_text)
    style_score = score_result
    
    if score_result["passed"]:
        final_response = response_text
    else:
        logger.info(f"Style Check Failed: {score_result['final_score']}. Rewriting...")
        is_rewrite = True
        
        repair_prompt = f"""
CRITIQUE: The response failed the style check (Score: {score_result['final_score']}).
VIOLATIONS:
- Structure: {score_result['structural_score']} (Check sentence length/paragraphing)
- Lexical: {score_result['lexical_score']} (Check vocabulary)
- Behavioral: {score_result['behavioral_score']} (Check tone/frameworks)

REPAIR INSTRUCTION:
Rewrite the response to fix these violations. 
- Vary sentence structure.
- Remove generic filler.
- Be more authentic.
- KEEP THE INFORMATION THE SAME.
"""
        messages.append({"role": "assistant", "content": response_text})
        messages.append({"role": "user", "content": repair_prompt})
        
        final_response = rag.generate_chat_completion(
            messages=messages,
            temperature=0.75
        )

    # --- Step 4: Post-Processing ---
    # Strip URLs to enforce "No sources shown in chat" (just in case model hallucinated them)
    try:
        final_response = strip_urls_from_text(final_response)
    except:
        pass

    # Build sources list for UI (not for chat text)
    unique_sources = []
    seen_urls = set()
    for chunk in support_set:
        ref = chunk.get("source_ref", {})
        url = ref.get("canonical_url")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_sources.append({
                "platform": ref.get("platform", ""),
                "canonical_url": url,
                "title": ref.get("title", ""),
                "published_at": ref.get("published_at"),
                "content_type": ref.get("content_type", ""),
            })

    debug_info = {
        "draft": draft,
        "style_score": style_score,
        "is_rewrite": is_rewrite,
        "dna_used": style_dna,
        "retrieved_count": len(support_set),
        "sources": unique_sources[:5]
    }
    
    return final_response, debug_info


def validate_grounding(
    answer: str,
    answer_contract: Dict[str, Any],
    support_set: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Step 6: Validate grounding - check if claims are supported.
    """
    # Simple validation: check if answer mentions sources/links
    # In production, use LLM to extract claims and verify against facts
    
    has_sources = any(
        source["source_ref"].get("canonical_url") 
        for source in answer_contract["sources"]
    )
    
    # Check if answer contains unsupported claims (heuristic)
    # Look for definitive statements without source mentions
    definitive_patterns = [
        r"he (always|never|definitely|certainly)",
        r"the creator (always|never|definitely|certainly)",
    ]
    
    ungrounded_claims = []
    for pattern in definitive_patterns:
        matches = re.findall(pattern, answer.lower())
        if matches:
            # Check if nearby text mentions a source
            # Simplified check - in production, use more sophisticated NLP
            ungrounded_claims.extend(matches)
    
    is_grounded = len(ungrounded_claims) == 0 and has_sources
    
    return {
        "is_grounded": is_grounded,
        "ungrounded_claims": ungrounded_claims,
        "has_sources": has_sources,
        "support_strength": len(answer_contract["facts"]) / max(1, len(support_set)),
    }


def repair_answer(
    answer: str,
    answer_contract: Dict[str, Any],
    support_set: List[Dict[str, Any]],
    grounding_report: Dict[str, Any],
    question: str,
    persona: Optional[str] = None,
    allow_sources: bool = True,
    enabled_platforms: Optional[List[str]] = None,
    intent: str = "how_to",
) -> str:
    """
    Step 7: Repair answer if grounding validation failed.
    When allow_sources is False, do not add a Sources block (link gating).
    When intent is request_sources, skip Sources block (links are inline only).
    """
    if grounding_report["is_grounded"]:
        return answer

    repaired = answer

    # Add source citation only when links allowed, not request_sources, and missing; skip URLs already in answer
    if (
        allow_sources
        and intent != "request_sources"
        and not grounding_report["has_sources"]
        and answer_contract.get("sources")
    ):
        extra = sources_section(
            answer_contract["sources"], max_links=3,
            enabled_platforms=enabled_platforms,
            exclude_urls=_urls_in_text(repaired),
        )
        if extra:
            repaired = (repaired.rstrip() + "\n\n" + extra).strip()

    if grounding_report["support_strength"] < 0.3:
        repaired = (
            "Based on the available content, here's what I can share:\n\n" + repaired
            + "\n\nNote: Some aspects may be general advice rather than specific to this creator's documented content."
        )

    return repaired


def grounded_rag_ask(
    creator_id: int,
    question: str,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    top_k: int = K_FINAL,
    max_distance: float = 1.15,
    debug: bool = False,
    user_preferences: Optional[Dict[str, Any]] = None,
    user_name: Optional[str] = None,
    creator_name: Optional[str] = None,
    images: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Main Grounded-RAG Loop function.
    Returns answer with sources and debug info.
    """
    import json
    
    # Step 1: Build search query
    q_search = build_search_query(question, conversation_history)
    
    from db import db
    # Fetch creator personality metadata and configurations
    creator_row = db.execute_one("SELECT name, handle, style_fingerprint, platform_configs FROM creators WHERE id = %s", (creator_id,))
    sf = creator_row.get("style_fingerprint") if creator_row else {}
    if isinstance(sf, str): sf = json.loads(sf)
    
    # Get query embedding
    from rag import get_client
    try:
        embedding_response = get_client().embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=q_search
        )
        query_embedding = embedding_response.data[0].embedding
    except Exception as e:
        raise Exception(f"Failed to get query embedding: {str(e)}")
    
    # Step 2: Retrieve broadly
    enabled_platforms = get_enabled_platforms_for_creator(creator_id)
    candidates = retrieve_candidates(
        creator_id, query_embedding, K_RETRIEVE, max_distance,
        enabled_platforms=enabled_platforms,
        debug=debug,
    )

    if not candidates:
        return {
            "answer": "I don't have enough content about this creator yet. Please search and ingest some content first.",
            "retrieved": [],
            "sources": [],
            "debug": {"error": "No candidates found"} if debug else None,
        }
    
    # Step 3: Re-rank
    support_set = rerank_candidates(candidates, q_search, top_k)
    
    # Step 4: Build answer contract
    answer_contract = build_answer_contract(support_set, question)
    
    # Early intent classification
    has_images = images and len(images) > 0
    persona = rag.get_persona(creator_id)
    
    # --- Semantic Intent Router (Resource-First Policy) ---
    logger.info("ContentFinder: Running Semantic Intent Router...")
    resource_intent = classify_resource_intent(question, conversation_history, creator_row)
    logger.info(f"Intent Router Output: {json.dumps(resource_intent)}")
    
    is_strict_content = resource_intent.get("needs_resource") and resource_intent.get("confidence", 0) >= 0.70
    intent = "request_sources" if is_strict_content else classify_intent(question)

    if is_strict_content:
        logger.info(f"ContentFinder: Semantic trigger activated (Reason: {resource_intent.get('reason')})")
        from services.content_finder import ContentFinder
        finder = ContentFinder(db, get_client())
        
        # Use Router's query and types
        router_query = resource_intent.get("query") or question
        cf_result = finder.find_content_card(
            creator_id,
            router_query,
            resource_type=resource_intent.get("resource_type", "any"),
            specificity=resource_intent.get("specificity", "recommendation"),
            history_messages=conversation_history,
        )
        
        if cf_result["status"] == "DEFER":
             logger.info("ContentFinder: Deferring response.")
             return {
                 "answer": cf_result["defer_message"],
                 "retrieved": [],
                 "sources": [],
                 "cards": [],
                 "debug": {"cf_result": cf_result} if debug else None
             }
        
        # If FOUND, we still want to generate a persona response that introduces the card
        # We override support_set to be just the found item to force focus
        if cf_result["status"] == "FOUND" and cf_result.get("cards"):
             cards = cf_result["cards"]
             first_card = cards[0]
             snippet = first_card.get("short_snippet", "") or first_card.get("title", "")
             
             # Mock support set with the first result
             is_fallback = cf_result.get("is_fallback", False)
             content_desc = "Specific Video" if not is_fallback else "Creator Channel/Search"
             
             support_set = [{
                 "chunk_id": "content_finder_match",
                 "chunk_index": 0,
                 "distance": 0.0,
                 "rerank_score": 1.0,
                 "content": f"Resource Found: {content_desc}\nTitle: {first_card['title']}\nSnippet: {snippet}",
                 "source_ref": {
                     "platform": "youtube" if "youtube" in first_card["url"] else "web",
                     "title": first_card["title"],
                     "canonical_url": first_card["url"],
                     "published_at": None,
                     "content_type": first_card.get("resource_type", "video")
                 }
             }]
             
             # Force answering with this content
             answer_contract = build_answer_contract(support_set, question)
             
             answer, gen_debug = generate_grounded_answer(
                question, support_set, answer_contract, persona, conversation_history,
                intent="introduce_content", # Custom intent to guide style?
                include_links_in_output=False, # We use card, not text links
                allow_cta=False,
                enabled_platforms=enabled_platforms,
                user_preferences=user_preferences,
                creator_name=creator_name,
                style_fingerprint=sf,
                creator_id=creator_id,
            )
             
             return {
                 "answer": answer,
                 "retrieved": support_set,
                 "sources": [], 
                 "cards": cards,
                 "debug": gen_debug if debug else None 
             }
        
    # --- Standard RAG Path --- (if no strict content found or triggered)

    # When images are attached, override small_talk — images need full analysis
    if has_images and intent in ("small_talk", "identity"):
        intent = "how_to"
    
    # For small talk and identity (no images), don't retrieve content to avoid tempting the model
    if intent in ("small_talk", "identity"):
        support_set = []
        answer_contract = {
            "facts": [],
            "gaps": [],
            "sources": [],
            "total_chunks": 0,
        }
        
        answer, gen_debug = generate_grounded_answer(
            question, support_set, answer_contract, persona, conversation_history,
            intent=intent,
            include_links_in_output=False,
            allow_cta=False,
            enabled_platforms=enabled_platforms,
            follow_up_requesting_links=False,
            user_preferences=user_preferences,
            creator_name=creator_name,
            style_fingerprint=sf,
            creator_id=creator_id,
        )
        
        return {
            "answer": answer,
            "retrieved": [],
            "sources": [],
            "debug": gen_debug if debug else None,
        }
    
    # Normal retrieval flow for advice/help questions
    intent = classify_intent(question)
    want_links = needs_links(question) or debug
    allow_cta = needs_cta(question)
    follow_up_requesting_links = is_follow_up_requesting_links(question, conversation_history)

    # Step 5: Generate answer
    answer, gen_debug = generate_grounded_answer(
        question, support_set, answer_contract, persona, conversation_history,
        intent=intent,
        include_links_in_output=want_links,
        allow_cta=allow_cta,
        enabled_platforms=enabled_platforms,
        follow_up_requesting_links=follow_up_requesting_links,
        user_preferences=user_preferences,
        user_name=user_name,
        creator_name=creator_name,
        style_fingerprint=sf,
        images=images,
        creator_id=creator_id,
    )
    
    # Step 6: Validate grounding
    grounding_report = validate_grounding(answer, answer_contract, support_set)
    
    # Step 7: Repair if needed (no sources block when link gating off or request_sources)
    if not grounding_report["is_grounded"]:
        answer = repair_answer(
            answer, answer_contract, support_set, grounding_report, question, persona,
            allow_sources=want_links,
            enabled_platforms=enabled_platforms,
            intent=intent,
        )

    # Platform-pure sources only (filter by enabled_platforms when set)
    contract_sources = _filter_sources_by_platform(
        answer_contract.get("sources") or [], enabled_platforms
    )

    # Link gating: only include links when requested (or debug)
    if not want_links:
        answer = strip_urls_from_text(answer)
    elif want_links and contract_sources and intent != "request_sources":
        # For "which video/post/link" requests, the model links inline—no "Sources:" block.
        exclude_urls = _urls_in_text(answer)
        extra = sources_section(
            contract_sources, max_links=3,
            enabled_platforms=enabled_platforms,
            exclude_urls=exclude_urls,
        )
        if extra and "Sources:" not in answer:
            answer = (answer.rstrip() + "\n\n" + extra).strip()

    # Build unique sources for response (platform-filtered)
    sources = []
    seen_urls: Set[str] = set()
    for source_data in contract_sources:
        ref = source_data.get("source_ref") or {}
        url = ref.get("canonical_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            sources.append({
                "platform": ref.get("platform", ""),
                "canonical_url": url,
                "title": ref.get("title", ""),
                "published_at": ref.get("published_at"),
                "content_type": ref.get("content_type", ""),
            })
    
    result = {
        "answer": answer,
        "retrieved": [
            {
                "chunk_id": c["chunk_id"],
                "chunk_index": c["chunk_index"],
                "distance": round(c["distance"], 3),
                "rerank_score": round(c.get("rerank_score", 0), 3),
                "preview": c["content"][:200],
                "source_ref": c["source_ref"],
            }
            for c in support_set
        ],
        "sources": sources,
    }
    
    if debug:
        result["debug"] = {
            "search_query": q_search,
            "enabled_platforms": enabled_platforms,
            "intent": intent,
            "needs_links": want_links,
            "follow_up_requesting_links": follow_up_requesting_links,
            "candidates_count": len(candidates),
            "support_set_size": len(support_set),
            "answer_contract": answer_contract,
            "grounding_report": grounding_report,
            "generation_debug": gen_debug,
        }

    return result
