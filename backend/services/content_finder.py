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
    
    STRONG_MATCH_THRESHOLD = 0.85
    MODERATE_MATCH_THRESHOLD = 0.65
    LOW_TRANSCRIPT_THRESHOLD_ADJUST = 0.05
    AMBIGUITY_MARGIN = 0.08
    
    def __init__(self, db_client=None, embedding_client=None):
        if db_client is not None:
            self.db = db_client
        else:
            from backend.db import db
            self.db = db
        self.embedding_client = embedding_client
        from backend.services.research_provider import get_research_provider
        self.research_provider = get_research_provider()
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
        exclude_titles: Optional[Set[str]] = None,
        exclude_ids: Optional[Set[str]] = None,
        intent_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Main entry point. Returns a result dict with possible multiple cards.
        Now prioritizes Gemini Research for exact matching.
        """
        logger.info(f"ContentFinder: Discovery Strategy = Gemini-First. Query: '{query}'")
        
        # Fetch creator identity profile
        creator_profile = self.db.execute_one(
            "SELECT id, name, handle, youtube_channel_id, youtube_handle, official_domains, course_domains, course_base_urls, platform_configs, profile_picture_url FROM creators WHERE id = %s",
            (creator_id,)
        )
        if not creator_profile:
            return self._format_defer("LOW", 0.0, creator_profile=None, query=query)
        need = self._interpret_user_need(query, history_messages, user_level_estimate)
        if specificity:
            need["specificity"] = specificity

        # --- Phase 1: Primary Gemini Research ---
        # Gemini with Search Grounding is the best at finding the "Exact" resource requested.
        logger.info("ContentFinder: Running Primary Gemini Research...")
        research_result = self._trigger_research(
            query, creator_profile, resource_type, 
            history_messages=history_messages,
            intent_metadata=intent_metadata
        )
        
        # If Gemini finds a very high confidence "Exact" match (1.0 or high), return it immediately
        if research_result["status"] == "FOUND" and research_result.get("confidence_score", 0) >= 0.85:
            # DEDUPLICATION CHECK: Ensure Gemini's first result isn't something we just saw
            first_card = research_result["cards"][0]
            c_url = (first_card.get("url") or "").lower()
            c_title = (first_card.get("title") or "").lower()
            c_vid_match = re.search(r"(?:v=|be/)([\w-]+)", c_url)
            c_vid_id = c_vid_match.group(1) if c_vid_match else c_url
            
            clean_c_title = re.sub(r'[^a-z0-9]', '', c_title)
            
            is_seen = (exclude_ids and c_vid_id in exclude_ids) or (exclude_titles and clean_c_title in exclude_titles)
            if not is_seen and exclude_titles:
                for t in exclude_titles:
                    if t in clean_c_title or clean_c_title in t:
                        is_seen = True; break
            
            if is_seen:
                logger.info("ContentFinder: Gemini returned a seen resource. Continuing to Phase 2.")
            else:
                logger.info(f"ContentFinder: High confidence Gemini match found ({research_result['confidence_score']}). Returning.")
                return research_result

        # --- Phase 2: Secondary RAG / Ingested Search ---
        # If Gemini is unsure or finds nothing, look through local ingested content.
        logger.info("ContentFinder: Running Secondary RAG discovery...")
        clean_query = query.lower()
        
        # Aggressively strip conversational prefixes
        prefixes = [
            r"^(what|which|show|find|give|tell|reccomend|recommend|suggest|can you|could you|would you|do you|any|do u|u|you).*(about|for|on|recommended|recommendation|video|videos|tutorial|lesson|content|regarding) ",
            r"^what video (would|should) (u|you) (reccomend|recommend) for ",
            r"^can (u|you) (reccomend|recommend) a video for ",
            r"^(reccomend|recommend) a video for ",
            r"^(show|find|give) (me|us) (a|the) video (about|for) ",
            r"^(what|which) video (is|are) best (for|about) "
        ]
        for p in prefixes:
            clean_query = re.sub(p, "", clean_query).strip()
        
        # Final pass: remove common noise words
        noise = ["reccomend", "recommend", "video", "videos", "tutorial", "lesson", "content", "please", "thanks"]
        for nw in noise:
            clean_query = re.sub(rf"\b{nw}\b", "", clean_query).strip()

        logger.info(f"ContentFinder: Original='{query}' -> Cleaned='{clean_query}'")
        
        # 1. Retrieve top K relevant chunks from multiple search angles
        search_terms = {clean_query, need.get("topic", "trading")}
        if intent_metadata and intent_metadata.get("topic_depth"):
            search_terms.add(intent_metadata["topic_depth"])

        all_retrieved_chunks = []
        for term in search_terms:
            if not term or len(term) < 3:
                continue
            logger.info(f"ContentFinder: Searching for chunks with term='{term}'")
            results = self._get_relevant_chunks(creator_id, term, limit=40)
            if results:
                all_retrieved_chunks.extend(results)

        if all_retrieved_chunks:
            # 2. Group by document_id and aggregate scores
            scored_docs = self._aggregate_document_scores(all_retrieved_chunks, query, intent_metadata=intent_metadata)

            # 3. Filter by Resource Type if requested
            if resource_type != "any":
                if resource_type == "course_lesson":
                    scored_docs = [d for d in scored_docs if "lesson" in d["title"].lower() or "module" in d["title"].lower()]
                else:
                    scored_docs = [d for d in scored_docs if d.get("type") == resource_type]

            if scored_docs:
                # Deduplicate scored_docs before picking best
                filtered_scored = []
                for d in scored_docs:
                    d_url = d["url"].lower()
                    d_title = d["title"].lower()
                    d_vid_match = re.search(r"(?:v=|be/)([\w-]+)", d_url)
                    d_vid_id = d_vid_match.group(1) if d_vid_match else d_url
                    clean_d_title = re.sub(r'[^a-z0-9]', '', d_title)

                    is_seen = (exclude_ids and d_vid_id in exclude_ids) or (exclude_titles and clean_d_title in exclude_titles)
                    if not is_seen and exclude_titles:
                        for t in exclude_titles:
                            if t in clean_d_title or clean_d_title in t:
                                if abs(len(clean_d_title) - len(t)) < 5:
                                    is_seen = True; break
                    if not is_seen:
                        filtered_scored.append(d)

                if filtered_scored:
                    top_matches = filtered_scored[:3]
                    cards = [self._to_preview_card(d) for d in top_matches]
                    logger.info(f"ContentFinder: Found {len(cards)} RAG matches. Best Score: {top_matches[0]['video_score']:.2f}")
                    return {
                        "status": "FOUND",
                        "cards": cards,
                        "confidence_score": top_matches[0]["video_score"]
                    }

        # --- Phase 3: Merging & Fallback ---
        if research_result["status"] == "FOUND":
             return research_result

        # Final Fallback to Channel Search Card
        logger.info("ContentFinder: All exact search methods failed. Returning channel search fallback.")
        return {
            "status": "FOUND",
            "cards": [self._channel_search_card(creator_profile, query if len(query.split()) > 1 else need["topic"])],
            "confidence_score": 0.4,
            "is_fallback": True,
        }

    def _trigger_research(self, query: str, creator_profile: Dict, resource_type: str, history_messages: Optional[List[Dict]] = None, intent_metadata: Optional[Dict] = None) -> Dict:
        results = self.research_provider.search(
            query, creator_profile, resource_type,
            conversation_history=history_messages,
            intent_metadata=intent_metadata
        )
        if not results:
            return self._format_defer("LOW", 0.0, creator_profile=creator_profile, query=query)

        cards = []
        for res in results[:3]:
            subtitle = res.get("resource_type", "video").title()
            if res.get("relation") == "AFFILIATED":
                subtitle = f"Guest Appearance • {subtitle}"
            elif res.get("is_playlist"):
                subtitle = "Playlist"
            elif res.get("series_index"):
                subtitle = f"Part {res.get('series_index')}"

            cards.append({
                "type": "preview_card",
                "resource_type": res.get("resource_type", "video"),
                "title": res["title"],
                "subtitle": subtitle,
                "thumbnail_url": self._get_thumbnail_url(res["url"]),
                "url": res["url"],
                "short_snippet": (res.get("snippet") or "")[:150],
                "action_label": "Watch" if res.get("resource_type") == "video" else "Read",
            })

        return {
            "status": "FOUND",
            "cards": cards,
            "confidence_score": results[0].get("confidence", 0.8)
        }

    def _get_relevant_chunks(self, creator_id: int, query: str, limit: int = 60) -> List[Dict]:
        from backend.rag import get_client, settings
        client = self.embedding_client or get_client()
        try:
            emb_resp = client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=query
            )
            q_emb = emb_resp.data[0].embedding
        except Exception as e:
            logger.error(f"ContentFinder: Error getting embedding for query '{query}': {e}")
            return []

        emb_str = "[" + ",".join(map(str, q_emb)) + "]"

        sql = """
            SELECT
                d.id as doc_id,
                d.title,
                d.url,
                d.source,
                d.source_id,
                d.metadata,
                c.chunk_index,
                c.chunk_text,
                (e.embedding <=> %s::vector) as distance
            FROM chunks c
            JOIN embeddings e ON c.id = e.chunk_id
            JOIN documents d ON c.document_id = d.id
            WHERE d.creator_id = %s
            AND e.model = %s
            ORDER BY distance ASC
            LIMIT %s
        """
        results = self.db.execute_query(sql, (emb_str, creator_id, settings.EMBEDDING_MODEL, limit))
        return results

    def _aggregate_document_scores(self, chunks: List[Dict], query: str, intent_metadata: Optional[Dict] = None) -> List[Dict]:
        """
        Group chunks by video and calculate a weighted quality score.
        """
        docs = {}
        query_keywords = set(self._normalize_keywords(query))
        user_level = (intent_metadata or {}).get("user_level", "unknown")
        learning_phase = (intent_metadata or {}).get("learning_phase", "unknown") # e.g., "foundational", "advanced"
        thematic_keywords = set(self._normalize_keywords((intent_metadata or {}).get("topic_depth", "")))

        for c in chunks:
            doc_id = c["doc_id"]
            if doc_id not in docs:
                # URL Reconstruction for YouTube if missing
                url = c["url"]
                # Use source and source_id from top-level if available
                source = c.get("source") or "youtube"
                sid = c.get("source_id")
                
                metadata = c["metadata"] or {}
                if isinstance(metadata, str):
                    try: metadata = json.loads(metadata)
                    except: metadata = {}

                if not url and sid:
                     if source == "youtube":
                         url = f"https://www.youtube.com/watch?v={sid}"

                if not url:
                    # Skip if no URL can be found or built
                    continue

                docs[doc_id] = {
                    "doc_id": doc_id,
                    "title": c["title"],
                    "url": url,
                    "metadata": metadata,
                    "similarities": [],
                    "chunk_indices": set(),
                    "texts": [],
                    "matches": 0
                }
            
            sim = max(0, 1 - float(c["distance"]))
            docs[doc_id]["similarities"].append(sim)
            docs[doc_id]["chunk_indices"].add(c["chunk_index"])
            docs[doc_id]["texts"].append(c["chunk_text"] or "")

        aggregated = []
        for doc_id, data in docs.items():
            sims = sorted(data["similarities"], reverse=True)
            
            # --- Similarity Stretching ---
            # OpenAI text-embedding-3-small cosine distances usually fall between 0.4 and 0.7 for relevant results.
            # We map the 0.3-0.6 similarity range to 0.6-1.0 for better threshold alignment.
            def stretch(s):
                return max(0.2, min(1.0, (s - 0.20) / (0.45 - 0.20) * 0.4 + 0.5))

            peak = stretch(sims[0])
            avg_top_5 = sum(sims[:5]) / min(len(sims), 5)
            density = stretch(avg_top_5)
            
            coverage = len(sims)
            
            # spread_score: count unique buckets of 6 chunks
            buckets = {idx // 6 for idx in data["chunk_indices"]}
            spread_score = min(1.0, len(buckets) / 4.0)
            
            # teaching_signal
            combined_text = " ".join(data["texts"]).lower()
            teaching_patterns = [
                r"here's how", r"how to", r"steps?", r"framework", r"rule",
                r"example", r"lesson", r"tutorial", r"guide", r"pattern",
                r"concept", r"principle", r"technique", r"method", r"first", r"second"
            ]
            matches = sum(1 for p in teaching_patterns if re.search(p, combined_text))
            teaching_signal = 1.0 if matches >= 3 else (0.5 if matches >= 1 else 0.0)

            # Level Match Signal
            level_match_score = 0.5 # Neutral
            if user_level == "beginner":
                beginner_patterns = [r"introduction", r"basics", r"beginner", r"starting", r"roadmap", r"entry level"]
                if any(re.search(p, combined_text + " " + data["title"].lower()) for p in beginner_patterns):
                    level_match_score = 1.0
                elif any(re.search(p, combined_text + " " + data["title"].lower()) for p in [r"advanced", r"complex", r"institutional"]):
                    level_match_score = 0.2
            elif user_level == "advanced":
                advanced_patterns = [r"advanced", r"complex", r"institutional", r"nuances", r"in-depth", r"advanced concept"]
                if any(re.search(p, combined_text + " " + data["title"].lower()) for p in advanced_patterns):
                    level_match_score = 1.0
                elif any(re.search(p, combined_text + " " + data["title"].lower()) for p in [r"beginner", r"intro", r"basics"]):
                    level_match_score = 0.2

            # video_score formula (EVOLVED: Added Level Match)
            coverage_score = min(1.0, coverage / 6.0)
            video_score = (
                0.35 * peak +
                0.20 * density +
                0.15 * coverage_score +
                0.10 * spread_score +
                0.10 * teaching_signal +
                0.10 * level_match_score
            )
            
            # Title overlap bonus
            title_keywords = set(self._normalize_keywords(data["title"]))
            overlap = len(query_keywords.intersection(title_keywords))
            if overlap > 0:
                video_score += min(0.12, 0.04 * overlap)
            
            # THEMATIC BONUS: if intent router found specific thematic keywords
            theme_overlap = len(thematic_keywords.intersection(title_keywords))
            if theme_overlap > 0:
                video_score += min(0.08, 0.02 * theme_overlap)

            # Duration formatting meta
            duration_seconds = self._parse_duration_seconds(data["metadata"].get("duration_seconds"))
            subtitle_bits = []
            if data["metadata"].get("published_at"):
                subtitle_bits.append(str(data["metadata"].get("published_at"))[:10])
            if duration_seconds:
                subtitle_bits.append(f"{duration_seconds//60}m")
            
            # Skip candidates with no URL - they cannot be previewed
            if not data.get("url"):
                continue

            aggregated.append({
                **data,
                "peak": peak,
                "density": density,
                "coverage": coverage,
                "spread_score": spread_score,
                "teaching_signal": teaching_signal,
                "video_score": min(1.0, video_score),
                "type": "video" if "youtube" in str(data["url"] or "").lower() or data["metadata"].get("type") == "video" else "article",
                "subtitle": " • ".join(subtitle_bits) or "Content",
                "thumbnail": data["metadata"].get("thumbnail_url") or "",
                "snippet": data["texts"][0] # Just for preview
            })

        aggregated.sort(key=lambda x: x["video_score"], reverse=True)
        return aggregated

    def _best_next_score(self, candidate: Dict[str, Any], need: Dict[str, str], query: str = "") -> float:
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

        if query:
            query_terms = set(self._normalize_keywords(query))
            title_terms = set(self._normalize_keywords(candidate.get("title", "")))
            overlap = len(query_terms.intersection(title_terms))
            if overlap > 0:
                score += min(0.08, 0.02 * overlap)

        foundational_topics = {"market structure", "risk", "entries", "strategy"}
        if need.get("user_level") == "beginner" and need.get("topic") in foundational_topics:
            level_text = f"{candidate.get('title', '')} {candidate.get('snippet', '')}".lower()
            if any(w in level_text for w in ["beginner", "basics", "foundation", "start"]):
                score += 0.05

        return max(0.0, min(1.0, score))

    def _to_preview_card(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        url = candidate.get("url") or ""
        thumbnail = candidate.get("thumbnail") or candidate.get("thumbnail_url") or self._get_thumbnail_url(url)
        return {            "type": "preview_card",
            "resource_type": candidate.get("type", "video"),
            "title": candidate["title"],
            "subtitle": candidate.get("subtitle", "YouTube"),
            "thumbnail_url": thumbnail,
            "url": url,
            "short_snippet": (candidate.get("snippet") or "")[:150],
            "action_label": "Watch",
        }

    def _channel_search_card(self, creator_profile: Dict[str, Any], topic: str) -> Dict[str, Any]:
        handle = creator_profile.get("youtube_handle")
        channel_id = creator_profile.get("youtube_channel_id")
        
        # Fallback to platform_configs
        configs = creator_profile.get("platform_configs") or {}
        yt_config = configs.get("youtube", {})
        if not handle: handle = yt_config.get("handle") or yt_config.get("username")
        if not channel_id: channel_id = yt_config.get("channel_id") or yt_config.get("id")

        if handle:
            handle_clean = handle.strip("@")
            url = f"https://www.youtube.com/@{handle_clean}/search?query={quote(topic)}"
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
        if creator_profile:
            handle = creator_profile.get("youtube_handle")
            channel_id = creator_profile.get("youtube_channel_id")
            configs = creator_profile.get("platform_configs") or {}
            yt_config = configs.get("youtube", {})
            
            if handle or channel_id or yt_config.get("handle") or yt_config.get("channel_id"):
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

        # Fallback to platform_configs
        configs = creator_profile.get("platform_configs") or {}
        yt_config = configs.get("youtube", {})
        if not yt_handle:
            yt_handle = yt_config.get("handle") or yt_config.get("username")
        if not yt_channel:
            yt_channel = yt_config.get("channel_id") or yt_config.get("id")

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

    # --- Reference ---    def _normalize_keywords(self, text: str) -> List[str]:
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

    def _get_thumbnail_url(self, url: str) -> str:
        if not url: return ""
        # YouTube
        if "youtube.com" in url or "youtu.be" in url:
            video_id = None
            if "v=" in url:
                video_id = url.split("v=")[1].split("&")[0]
            elif "youtu.be/" in url:
                video_id = url.split("youtu.be/")[1].split("?")[0]
            
            if video_id:
                # Try hqdefault or mqdefault
                return f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                
        return ""