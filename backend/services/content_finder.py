import logging
import re
from typing import List, Dict, Any, Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)

class ContentFinder:
    """
    Creator-only video recommender for "best next video" decisions.
    - Never returns off-channel recommendations.
    - Uses confidence + margin gates before returning specific videos.
    - Falls back to creator-owned channel search card when uncertain.
    """
    
    HIGH_THRESHOLD = 0.82
    MEDIUM_THRESHOLD = 0.68
    AMBIGUITY_MARGIN = 0.08
    
    def __init__(self, db_client=None, embedding_client=None):
        if db_client is not None:
            self.db = db_client
        else:
            from db import db
            self.db = db
        self.embedding_client = embedding_client

    def _parse_duration_seconds(self, raw: Any) -> Optional[int]:
        if raw is None:
            return None
        try:
            if isinstance(raw, int):
                return raw
            if isinstance(raw, float):
                return int(raw)
            txt = str(raw).strip()
            if txt.isdigit():
                return int(txt)
            if ":" in txt:
                parts = [int(p) for p in txt.split(":") if p.isdigit()]
                if len(parts) == 3:
                    return parts[0] * 3600 + parts[1] * 60 + parts[2]
                if len(parts) == 2:
                    return parts[0] * 60 + parts[1]
        except Exception:
            return None
        return None

    def _interpret_user_need(
        self,
        user_message: str,
        history_messages: Optional[List[Dict[str, Any]]] = None,
        user_level_estimate: Optional[str] = None,
    ) -> Dict[str, str]:
        text = f"{(user_message or '').lower()} " + " ".join(
            (m.get("content") or "").lower() for m in (history_messages or []) if isinstance(m, dict)
        )
        topic = "strategy"
        topic_rules = {
            "market structure": ["market structure", "bos", "choch"],
            "entries": ["entry", "entries", "entry model", "setup"],
            "risk": ["risk", "1%", "stop loss", "position size"],
            "psychology": ["psychology", "mindset", "emotion", "discipline"],
            "strategy": ["strategy", "system", "plan", "framework"],
        }
        for k, words in topic_rules.items():
            if any(w in text for w in words):
                topic = k
                break

        task = "learn"
        if any(w in text for w in ["mistake", "wrong", "fix", "failing"]):
            task = "fix mistake"
        elif any(w in text for w in ["which", "recommend", "best", "what should i watch"]):
            task = "choose strategy"
        elif any(w in text for w in ["proof", "evidence", "did you say", "where did"]):
            task = "find proof"
        elif any(w in text for w in ["next", "after", "what now"]):
            task = "next steps"

        specificity = "recommendation"
        if any(w in text for w in ["video?", "specific", "exact", "which video", "did you say"]):
            specificity = "specific"
        if "proof" in text or "evidence" in text:
            specificity = "evidence"

        user_level = (user_level_estimate or "").lower() or "unknown"
        if user_level not in ("beginner", "intermediate", "advanced"):
            if any(w in text for w in ["i'm new", "beginner", "starting", "start trading"]):
                user_level = "beginner"
            elif any(w in text for w in ["advanced", "institutional", "already trade", "experienced"]):
                user_level = "advanced"
            elif any(w in text for w in ["intermediate", "improving", "decent"]):
                user_level = "intermediate"
            else:
                user_level = "beginner" if "start trading" in text else "unknown"

        time_preference = "unknown"
        if any(w in text for w in ["quick", "short", "brief", "under 10"]):
            time_preference = "short"
        elif any(w in text for w in ["deep dive", "long", "full", "detailed"]):
            time_preference = "long"

        asset_class = "unknown"
        for ac in ["forex", "crypto", "stocks", "options"]:
            if ac in text:
                asset_class = ac
                break

        style = "unknown"
        if "day" in text and "trade" in text:
            style = "daytrade"
        elif "swing" in text:
            style = "swing"
        elif "invest" in text:
            style = "investing"

        return {
            "topic": topic,
            "task": task,
            "specificity": specificity,
            "user_level": user_level,
            "time_preference": time_preference,
            "asset_class": asset_class,
            "style": style,
        }
        
    def find_content_card(
        self, 
        creator_id: int, 
        query: str, 
        resource_type: str = "any", 
        specificity: str = "recommendation",
        history_messages: Optional[List[Dict[str, Any]]] = None,
        user_level_estimate: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point. Returns a result dict with possible multiple cards.
        Enforces Creator Ownership Gate (COG).
        """
        logger.info(f"ContentFinder: Query '{query}' (Type: {resource_type}, Specificity: {specificity})")
        
        # Fetch creator identity profile
        creator_profile = self.db.execute_one(
            "SELECT id, name, handle, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, profile_picture_url FROM creators WHERE id = %s",
            (creator_id,)
        )
        if not creator_profile:
            return self._format_defer("LOW", 0.0, creator_profile=None, query=query)
        if not creator_profile.get("youtube_channel_id") and not creator_profile.get("youtube_handle"):
            return self._format_defer("LOW", 0.0, creator_profile=creator_profile, query=query)

        need = self._interpret_user_need(query, history_messages, user_level_estimate)
        if specificity:
            need["specificity"] = specificity

        # 1/2) Creator inventory retrieval only (no web).
        ingest_candidates = self._search_ingested_multi(creator_id, query)
        verified_candidates = [
            c for c in ingest_candidates
            if self._verify_ownership(c, creator_profile)["relation"] == "SELF" and c.get("url")
        ]

        # 4. Filter by Resource Type
        if resource_type != "any":
            if resource_type == "course_lesson":
                filtered = [c for c in verified_candidates if "lesson" in c["title"].lower() or "module" in c["title"].lower()]
                if not filtered: filtered = verified_candidates
            else:
                filtered = [c for c in verified_candidates if c.get("type") == resource_type]
                if not filtered: filtered = verified_candidates
        else:
            filtered = verified_candidates

        # 5) Best-next scoring + safety gate.
        if not filtered:
            return self._format_defer("LOW", 0.0, creator_profile=creator_profile, query=need["topic"])

        for c in filtered:
            c["score"] = self._best_next_score(c, need)
        filtered.sort(key=lambda x: x["score"], reverse=True)

        top = filtered[0]
        second = filtered[1] if len(filtered) > 1 else None
        margin = top["score"] - (second["score"] if second else 0.0)

        if top["score"] >= self.HIGH_THRESHOLD and margin >= self.AMBIGUITY_MARGIN:
            return {"status": "FOUND", "cards": [self._to_preview_card(top)], "confidence_score": top["score"]}

        if top["score"] >= self.HIGH_THRESHOLD and margin < self.AMBIGUITY_MARGIN:
            top_high = [c for c in filtered[:3] if c["score"] >= self.HIGH_THRESHOLD]
            if top_high:
                return {
                    "status": "FOUND",
                    "cards": [self._to_preview_card(c) for c in top_high],
                    "confidence_score": top["score"],
                }

        return {
            "status": "FOUND",
            "cards": [self._channel_search_card(creator_profile, need["topic"])],
            "confidence_score": top["score"],
            "is_fallback": True,
        }

    def _best_next_score(self, candidate: Dict[str, Any], need: Dict[str, str]) -> float:
        topic_weight = 0.40
        intent_weight = 0.25
        level_weight = 0.15
        continuity_weight = 0.10
        format_weight = 0.05
        recency_weight = 0.05

        if need.get("specificity") == "specific":
            topic_weight = 0.55
            format_weight = 0.0
            recency_weight = 0.0
        if need.get("user_level") == "beginner":
            level_weight += 0.10

        score = (
            topic_weight * candidate.get("topic_match_score", 0.0)
            + intent_weight * candidate.get("intent_match_score", 0.0)
            + level_weight * candidate.get("level_fit_score", 0.0)
            + continuity_weight * candidate.get("continuity_score", 0.0)
            + format_weight * candidate.get("format_fit_score", 0.0)
            + recency_weight * candidate.get("recency_score", 0.0)
        )
        return max(0.0, min(1.0, score))

    def _to_preview_card(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "preview_card",
            "resource_type": candidate.get("type", "video"),
            "title": candidate["title"],
            "subtitle": candidate.get("subtitle", "YouTube"),
            "thumbnail_url": candidate.get("thumbnail", ""),
            "url": candidate.get("url") or "",
            "short_snippet": (candidate.get("snippet") or "")[:150],
            "action_label": "Watch",
        }

    def _channel_search_card(self, creator_profile: Dict[str, Any], topic: str) -> Dict[str, Any]:
        handle = creator_profile.get("youtube_handle")
        channel_id = creator_profile.get("youtube_channel_id")
        if handle:
            url = f"https://www.youtube.com/@{handle}/search?query={quote(topic)}"
        else:
            url = f"https://www.youtube.com/channel/{channel_id}/search?query={quote(topic)}"
        return {
            "type": "channel_search_card",
            "platform": "youtube",
            "title": "Search my channel for this topic",
            "subtitle": topic,
            "thumbnail_url": creator_profile.get("profile_picture_url", ""),
            "url": url,
            "action_label": "Search Channel",
        }

    def _format_defer(self, label, score, creator_profile: Optional[Dict[str, Any]], query: str):
        cards = []
        if creator_profile and (creator_profile.get("youtube_handle") or creator_profile.get("youtube_channel_id")):
            cards = [self._channel_search_card(creator_profile, query or "trading")]
        return {
            "status": "DEFER" if not cards else "FOUND",
            "card": None,
            "cards": cards,
            "confidence_score": score,
            "confidence_label": label,
            "is_fallback": bool(cards),
            "defer_message": "I can't confidently name a specific creator video right now."
        }

    def _verify_ownership(self, candidate: Dict, creator_profile: Dict) -> Dict:
        """
        COG Verification.
        Returns { "relation": "SELF"|"AFFILIATED"|"OTHER"|"UNKNOWN", "confidence": float }
        """
        url = (candidate.get("url") or "").lower()
        title = (candidate.get("title") or "").lower()
        snippet = (candidate.get("snippet") or "").lower()
        
        # Ingested is always SELF
        if candidate.get("source_opt") == "Ingested Content":
            return {"relation": "SELF", "confidence": 1.0}

        # Identity fields
        yt_channel = creator_profile.get("youtube_channel_id")
        yt_handle = creator_profile.get("youtube_handle")
        official_domains = creator_profile.get("official_domains") or []
        course_domains = creator_profile.get("course_domains") or []
        creator_name = creator_profile.get("name", "").lower()
        
        # Fail-safe: if no identity fields, everything is UNKNOWN
        if not yt_channel and not yt_handle and not official_domains and not course_domains:
            return {"relation": "UNKNOWN", "confidence": 0.0}

        # 1. YouTube Verification
        if "youtube.com" in url or "youtu.be" in url:
            # Check metadata for channel ID first (reliable)
            meta = candidate.get("metadata", {})
            channel_id_meta = meta.get("channel_id") or meta.get("id") if "UC" in str(meta.get("id", "")) else None
            
            if yt_channel and (yt_channel.lower() in url or (channel_id_meta and yt_channel.lower() == channel_id_meta.lower())):
                return {"relation": "SELF", "confidence": 1.0}
            if yt_handle and (f"@{yt_handle.lower()}" in url or f"/{yt_handle.lower()}" in url):
                return {"relation": "SELF", "confidence": 0.95}
            if creator_name and (creator_name in title or creator_name in candidate.get("source_opt", "").lower()):
                # Still likely self if name matches title or channel name
                return {"relation": "SELF", "confidence": 0.85}
            
            # Affiliated check
            pockets = ["interview", "podcast", "guest", "featuring", "presents"]
            if creator_name in title and any(p in title or p in snippet for p in pockets):
                return {"relation": "AFFILIATED", "confidence": 0.8}
            
            return {"relation": "OTHER", "confidence": 0.2}

        # 2. Domain/Website Verification
        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc.lower()
        except:
            domain = ""

        for d in official_domains:
            if d.lower() in domain:
                return {"relation": "SELF", "confidence": 1.0}
                
        for d in course_domains:
            if d.lower() in domain:
                return {"relation": "SELF", "confidence": 1.0}

        # 3. Identity name match in reputable source
        if creator_name in title:
             pockets = ["interview", "podcast", "guest", "featured"]
             if any(p in title or p in snippet for p in pockets):
                 return {"relation": "AFFILIATED", "confidence": 0.7}

        return {"relation": "OTHER", "confidence": 0.1}

    def _normalize_keywords(self, text: str) -> List[str]:
        # Broader stopword list for strict matching
        stopwords = {
            "the", "a", "an", "of", "to", "in", "for", "on", "with", "is", "are", 
            "video", "videos", "about", "show", "me", "give", "link", "url", 
            "watch", "watching", "good", "best", "great", "recommend", "recommendation",
            "start", "starting", "tutorial", "guide", "learn", "learning", "explain",
            "explanation", "how", "what", "where", "when", "why", "who", "youtube",
            "content", "episode", "clip", "channel"
        }
        
        # Simple tokenization
        tokens = re.findall(r'\w+', text.lower())
        
        keywords = []
        for t in tokens:
            if t not in stopwords:
                # Simple singularization (very basic)
                if t.endswith('s') and len(t) > 3:
                     keywords.append(t[:-1]) # structure(s) -> structure
                     keywords.append(t)      # keep original too? No, let's just keep root or check both?
                else:
                    keywords.append(t)
                    
        # Filter duplicates while preserving order
        return list(dict.fromkeys(keywords))

    # --- Ingest Scoring ---
    def _search_ingested_multi(self, creator_id: int, query: str) -> List[Dict]:
        from rag import get_client, settings
        
        # Get query embedding
        client = self.embedding_client or get_client()
        try:
            emb_resp = client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=query
            )
            q_emb = emb_resp.data[0].embedding
        except Exception:
            return []
            
        emb_str = "[" + ",".join(map(str, q_emb)) + "]"
        
        # We need semantic similarity (S)
        sql = """
            SELECT 
                d.title,
                d.url,
                d.metadata,
                c.chunk_text,
                (e.embedding <=> %s::vector) as distance
            FROM chunks c
            JOIN embeddings e ON c.id = e.chunk_id
            JOIN documents d ON c.document_id = d.id
            WHERE d.creator_id = %s
            AND e.model = %s
            ORDER BY distance ASC
            LIMIT 30
        """
        results = self.db.execute_query(sql, (emb_str, creator_id, settings.EMBEDDING_MODEL))
        
        if not results:
            return []
            
        candidates = []
        
        # Keywords for K score
        keywords = self._normalize_keywords(query)
        if not keywords: keywords = [""] # Avoid div zero
        
        for r in results:
            # 1. Semantic Similarity (S) = 1 - distance
            S = max(0, 1 - float(r["distance"]))
            
            title = r["title"] or ""
            text = r["chunk_text"] or ""
            content_lower = (title + " " + text).lower()
            
            # 2. Keyword Match (K)
            # Check for root matches
            matches = 0
            for k in keywords:
                # Basic check: k in content OR k+"s" in content OR k[:-1] in content
                if k in content_lower:
                    matches += 1
                elif k.endswith('s') and k[:-1] in content_lower:
                    matches += 1
                elif k + "s" in content_lower:
                    matches += 1
                    
            K = matches / max(1, len(keywords))
            
            # 3. Exact Phrase Bonus (P)
            # Check for full query phrase match (implicit) or quoted phrases (explicit)
            P = 0.0
            query_clean = re.sub(r'[^\w\s]', '', query).lower()
            if len(query_clean.split()) > 1 and query_clean in content_lower:
                P = 1.0
            else:
                 # Standard quoted check
                 quoted = re.findall(r'"([^"]*)"', query)
                 if quoted:
                    for phrase in quoted:
                        if phrase.lower() in content_lower:
                            P = 1.0
                            break
            
            # 4. Topic/Tag Alignment (T)
            T = 1.0 if any(k in title.lower() for k in keywords) else 0.5
            
            # Derived feature scores for best-next ranking
            topic_match = max(0.0, min(1.0, (0.75 * S) + (0.25 * K)))
            intent_match = max(0.0, min(1.0, (0.70 * K) + (0.30 * P)))

            level_fit = 0.6
            level_text = f"{title} {text}".lower()
            if any(w in level_text for w in ["beginner", "basics", "start", "foundation"]):
                level_fit = 1.0
            elif any(w in level_text for w in ["advanced", "pro", "institutional"]):
                level_fit = 0.4

            duration_seconds = self._parse_duration_seconds((r.get("metadata") or {}).get("duration_seconds"))
            format_fit = 0.5
            if duration_seconds is not None:
                format_fit = 1.0 if duration_seconds <= 900 else 0.7

            recency = 0.6
            continuity = T

            meta = r.get("metadata") or {}
            subtitle_bits = []
            if meta.get("published_at"):
                subtitle_bits.append(str(meta.get("published_at"))[:10])
            if duration_seconds is not None:
                subtitle_bits.append(f"{duration_seconds//60}m")
            subtitle = " • ".join(subtitle_bits) or "YouTube"

            if not r.get("url"):
                continue

            candidates.append({
                "title": title,
                "url": r["url"],
                "snippet": text,
                "topic_match_score": topic_match,
                "intent_match_score": intent_match,
                "level_fit_score": level_fit,
                "recency_score": recency,
                "format_fit_score": format_fit,
                "continuity_score": continuity,
                "creator_match_score": 1.0,
                "type": "video" if "youtube" in (r["url"] or "") else "article",
                "source_opt": "Ingested Content",
                "thumbnail": meta.get("thumbnail_url") or meta.get("thumbnail") or "",
                "subtitle": subtitle,
            })
            
        candidates.sort(key=lambda x: x.get("topic_match_score", 0.0), reverse=True)
        return candidates
