import logging
import re
from typing import List, Dict, Any, Optional
from services.search_engine import SearchEngine
from dateutil import parser as date_parser
from datetime import datetime
import numpy as np

logger = logging.getLogger(__name__)

class ContentFinder:
    """
    Implements STRICT confidence scoring for content retrieval.
    Phase 1: Ingest-First Retrieval
    Phase 2: Silent Web Fallback
    Phase 3: Strict Scoring & Decision (Return Card vs Defer)
    """
    
    HIGH_THRESHOLD = 0.82
    MEDIUM_THRESHOLD = 0.68
    AMBIGUITY_MARGIN = 0.08
    
    def __init__(self, db_client=None, embedding_client=None):
        self.search_engine = SearchEngine()
        self.db = db_client # Should be passed or imported from db
        from db import db
        self.db = db
        self.embedding_client = embedding_client
        # If embedding_client is None, we need to get it via rag.get_client() when needed
        
    def find_content_card(
        self, 
        creator_id: int, 
        query: str, 
        resource_type: str = "any", 
        specificity: str = "recommendation"
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
            return self._format_defer("LOW", 0.0)

        # 1. Ingest Search (Strictly Scoped)
        ingest_candidates = self._search_ingested_multi(creator_id, query)
        
        # 2. Web Fallback if ingest is thin
        web_candidates = []
        if not ingest_candidates or ingest_candidates[0]["score"] < self.HIGH_THRESHOLD:
            logger.info("ContentFinder: Ingest coverage low. Trying web fallback...")
            creator_name = creator_profile["name"] if creator_profile else "Creator"
            web_candidates = self._search_web_multi(creator_name, query)
            
        # 3. Verify Ownership & Filter
        all_candidates = ingest_candidates + web_candidates
        verified_candidates = []
        
        for c in all_candidates:
            ownership = self._verify_ownership(c, creator_profile)
            c["creator_relation"] = ownership["relation"]
            c["creator_match_confidence"] = ownership["confidence"]

            # SELF Boost: If we know it's theirs, it's highly likely what they want
            if ownership["relation"] == "SELF":
                c["score"] = min(1.0, c["score"] + 0.20)
                # Extra weight for ingested content (provably theirs)
                if c.get("source_opt") == "Ingested Content":
                    c["score"] = min(1.0, c["score"] + 0.10)
            elif ownership["relation"] == "AFFILIATED":
                c["score"] = min(1.0, c["score"] + 0.10)
                
            # Strict Gating: Only SELF (or AFFILIATED if Broad recommendation)
            is_valid = ownership["relation"] == "SELF"
            if not is_valid and specificity == "recommendation" and ownership["relation"] == "AFFILIATED":
                is_valid = True # Allow guest appearances for recommendations
                
            if is_valid:
                verified_candidates.append(c)

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

        # 5. Strict Scoring Gating
        high_matches = [c for c in filtered if c["score"] >= self.HIGH_THRESHOLD]
        
        if high_matches and high_matches[0].get("is_ambiguous"):
             logger.info("ContentFinder: Best match is ambiguous. Deferring.")
             high_matches = []

        if high_matches:
            cards = []
            for c in high_matches[:3]:
                source_name = c.get("source_opt", "Content")
                if c["creator_relation"] == "AFFILIATED":
                    source_name = f"Guest • {source_name}"
                
                cards.append({
                    "type": "preview_card",
                    "resource_type": c.get("type", "video"),
                    "title": c["title"],
                    "subtitle": f"{source_name} • {c.get('date', 'Recent')}",
                    "thumbnail_url": c.get("thumbnail", ""),
                    "url": c["url"],
                    "short_snippet": c.get("snippet", "")[:150],
                    "action_label": "Watch" if c.get("type") == "video" else "Read",
                    "creator_relation": c["creator_relation"] # Internal metadata
                })
            
            logger.info(f"ContentFinder: Found {len(cards)} verified high-confidence matches.")
            return {
                "status": "FOUND",
                "cards": cards,
                "confidence_score": high_matches[0]["score"]
            }
            
        best_score = verified_candidates[0]["score"] if verified_candidates else 0.0
        label = "MEDIUM" if best_score >= self.MEDIUM_THRESHOLD else "LOW"
        logger.info(f"ContentFinder: No verified high confidence match. Attempting fallback cards...")

        # --- Fallback Cards (Creator-Specific) ---
        fallback_cards = []
        name = creator_profile.get("name") or creator_profile.get("handle") or "the creator"
        avatar = creator_profile.get("profile_picture_url", "")
        yt_handle = creator_profile.get("youtube_handle")
        yt_channel_id = creator_profile.get("youtube_channel_id")
        official_domains = creator_profile.get("official_domains") or []
        
        # 1. YouTube Search Fallback or Channel Fallback
        if yt_handle or yt_channel_id:
            import urllib.parse
            if query and len(query.split()) > 1:
                # Channel Search Card
                handle_part = f"@{yt_handle}" if yt_handle else yt_channel_id
                fallback_cards.append({
                    "type": "channel_search_card",
                    "platform": "youtube",
                    "title": f"Search {name} for this topic",
                    "subtitle": f"Topic: {query}",
                    "thumbnail_url": avatar,
                    "url": f"https://www.youtube.com/{handle_part}/search?query={urllib.parse.quote(query)}",
                    "action_label": "Search Channel"
                })
            else:
                # Channel Card
                fallback_cards.append({
                    "type": "channel_card",
                    "platform": "youtube",
                    "title": f"Watch {name} on YouTube",
                    "subtitle": "Official Channel",
                    "thumbnail_url": avatar,
                    "url": f"https://www.youtube.com/@{yt_handle}" if yt_handle else f"https://www.youtube.com/channel/{yt_channel_id}",
                    "action_label": "Open Channel"
                })

        # 2. Official Site Fallback
        if official_domains:
            fallback_cards.append({
                "type": "preview_card", # Use preview_card layout for site
                "resource_type": "article",
                "title": f"Visit {name}'s Official Site",
                "subtitle": official_domains[0],
                "thumbnail_url": avatar,
                "url": f"https://{official_domains[0]}",
                "action_label": "Visit Site"
            })

        if fallback_cards:
            logger.info(f"ContentFinder: Returning {len(fallback_cards)} fallback cards.")
            return {
                "status": "FOUND",
                "cards": fallback_cards[:2], # Limit fallbacks
                "confidence_score": 0.5, # Indicator of fallback
                "is_fallback": True
            }

        return self._format_defer(label, best_score)

    def _format_success(self, candidate, source, creator_name=""):
        return {
            "status": "FOUND",
            "card": {
                "type": "preview_card",
                "resource_type": candidate.get("type", "video"),
                "title": candidate["title"],
                "subtitle": f"{candidate.get('source_opt', source.title())} • {candidate.get('date', '')}",
                "thumbnail_url": candidate.get("thumbnail", ""),
                "short_snippet": candidate.get("snippet", "")[:150] + "...",
                "url": candidate["url"],
                "action_label": "Watch" if candidate.get("type") == "video" else "Read"
            },
            "confidence_score": candidate["score"],
            "confidence_label": "HIGH",
            "source": source
        }

    def _format_defer(self, label, score):
        return {
            "status": "DEFER",
            "card": None,
            "confidence_score": score,
            "confidence_label": label,
            "defer_message": "Unfortunately I don’t have that information confidently. You may want to check my official YouTube channel / website directly."
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

    def _format_success(self, candidate, source, creator_name=""):
        return {
            "status": "FOUND",
            "card": {
                "type": "preview_card",
                "resource_type": candidate.get("type", "video"),
                "title": candidate["title"],
                "subtitle": f"{candidate.get('source_opt', source.title())} • {candidate.get('date', '')}",
                "thumbnail_url": candidate.get("thumbnail", ""),
                "short_snippet": candidate.get("snippet", "")[:150] + "...",
                "url": candidate["url"],
                "action_label": "Watch" if candidate.get("type") == "video" else "Read"
            },
            "confidence_score": candidate["score"],
            "confidence_label": "HIGH",
            "source": source
        }

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
        try:
            emb_resp = get_client().embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=query
            )
            q_emb = emb_resp.data[0].embedding
        except:
            return []
            
        emb_str = "[" + ",".join(map(str, q_emb)) + "]"
        
        # We need semantic similarity (S)
        sql = """
            SELECT 
                d.title, d.url, d.metadata, c.chunk_text, (e.embedding <=> %s::vector) as distance
            FROM chunks c
            JOIN embeddings e ON c.id = e.chunk_id
            JOIN documents d ON c.document_id = d.id
            WHERE d.creator_id = %s
            AND e.model = %s
            ORDER BY distance ASC
            LIMIT 10
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
            
            # 5. Recency (R)
            R = 1.0 
                
            # Final Score
            # Adjusted weights to allow high confidence without explicit quotes
            # S=0.55, K=0.25, P=0.05, T=0.15
            score = (0.55 * S) + (0.25 * K) + (0.05 * P) + (0.15 * T) * R
            
            logger.debug(f"Ingest Score debug: {title[:20]}... S={S:.2f} K={K:.2f} P={P:.2f} T={T:.2f} Score={score:.3f}")
            
            candidates.append({
                "title": title,
                "url": r["url"],
                "snippet": text,
                "score": score,
                "type": "video" if "youtube" in (r["url"] or "") else "article",
                "date": "Recent",
                "source_opt": "Ingested Content"
            })
            
        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates

    def _search_web_multi(self, creator_name: str, query: str) -> List[Dict]:
        search_query = f"{creator_name} {query}"
        results = self.search_engine.search(search_query, num_results=5)
        
        if not results:
            return []
            
        scored_results = []
        
        # Keywords
        keywords = self._normalize_keywords(query)
        if not keywords: keywords = [""]

        for r in results:
            title = r.get("title") or ""
            snippet = r.get("snippet") or ""
            link = r.get("link") or ""
            
            # 1. Domain Trust (D)
            D = 0.20
            if "youtube.com" in link or "youtu.be" in link:
                D = 1.0 
            elif "wikipedia" in link:
                D = 0.75
            else:
                clean_name = creator_name.lower().replace(" ", "")
                if clean_name in link.lower():
                    D = 1.0
                else:
                    D = 0.35
            
            # 2. Title Match (TM)
            tm_matches = 0
            title_lower = title.lower()
            for k in keywords:
                if k in title_lower:
                    tm_matches += 1
                elif k.endswith('s') and k[:-1] in title_lower:
                    tm_matches += 1
                elif k + "s" in title_lower:
                    tm_matches += 1
            
            TM = tm_matches / max(1, len(keywords))
            if query.lower() in title_lower:
                TM = min(1.0, TM + 0.25)
                
            # 3. Snippet Match (SM)
            sm_matches = 0
            snippet_lower = snippet.lower()
            for k in keywords:
                 if k in snippet_lower:
                    sm_matches += 1
                 elif k.endswith('s') and k[:-1] in snippet_lower:
                    sm_matches += 1
            
            SM = sm_matches / max(1, len(keywords))
            
            # 4. Creator Identity Match (CM)
            CM = 0.5
            if creator_name.lower() in title_lower or creator_name.lower() in snippet_lower:
                CM = 1.0
            elif D == 1.0:
                CM = 1.0
                
            # 5. Ambiguity (A)
            A = 1.0 
            
            # Rescaled Web Weights
            # D=0.30, TM=0.30, SM=0.20, CM=0.15, A=0.05
            score = (0.30 * D) + (0.30 * TM) + (0.20 * SM) + (0.15 * CM) + (0.05 * A)
            
            logger.debug(f"Web Score debug: {title[:20]}... D={D} TM={TM} SM={SM} CM={CM} Score={score}")
            
            scored_results.append({
                "title": title,
                "url": link,
                "snippet": snippet,
                "score": score,
                "type": "video" if "youtube" in link else "article",
                "source_opt": r.get("source") or "Web",
                "metadata": r.get("rich_snippet", {}) or r.get("video_result", {}) or r
            })
            
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        
        # Ambiguity Penalty & Safety Check
        if len(scored_results) > 1:
            best = scored_results[0]
            second = scored_results[1]
            diff = best["score"] - second["score"]
            if diff < self.AMBIGUITY_MARGIN:
                # Mark candidates as ambiguous
                for s in scored_results:
                    s["is_ambiguous"] = True
                
        return scored_results
