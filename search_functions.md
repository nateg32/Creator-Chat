# Research Provider Key Functions

## 1. COG Enforcer (`_enforce_cog`)

The Creator Ownership Gate (COG) verifies if an external search result actually belongs to the creator by checking their social handles, channel IDs, domains, or their name in the source title. If they are just mentioned, it classifies it as `"AFFILIATED"`.

```python
    def _enforce_cog(self, candidates: List[Dict[str, Any]], creator_profile: Dict[str, Any]) -> List[Dict[str, Any]]:
        # Extract YouTube identifiers from the creator's profile
        yt_id = (creator_profile.get('youtube_channel_id') or "").lower()
        yt_handle = (creator_profile.get('youtube_handle') or "").lower().strip("@")
        
        configs = creator_profile.get('platform_configs') or {}
        yt_config = configs.get('youtube', {})
        if not yt_handle:
            yt_handle = (yt_config.get('handle') or yt_config.get('username') or "").lower().strip("@")
        if not yt_id:
            yt_id = (yt_config.get('channel_id') or yt_config.get('id') or "").lower()

        official_domains = [d.lower() for d in (creator_profile.get('official_domains') or [])]
        course_base_urls = [u.lower() for u in (creator_profile.get('course_base_urls') or [])]
        creator_name = (creator_profile.get('name') or '').strip().lower()
        if not creator_name:
            creator_name = (creator_profile.get('handle') or '').strip().lstrip('@').replace('_', ' ').lower()
        
        verified = []
        # Keywords suggesting a collaboration/interview rather than owned content
        collab_markers = ["interview", "podcast", "guest", "featuring", "presents", "collab", "conversation", "mentorship"]

        for c in candidates:
            url = c.get('url', "").lower()
            if not url: continue
            
            relation = "OTHER"
            score = 0.0
            
            # PHASE 1: Direct Ownership Check (URL/Domain)
            is_self = False
            if "youtube.com" in url or "youtu.be" in url:
                if yt_id and yt_id in url: is_self = True
                elif yt_handle and (f"@{yt_handle}" in url or f"/{yt_handle}" in url): is_self = True
            
            domain = urlparse(url).netloc.lower()
            if any(d in domain for d in official_domains): is_self = True
            if any(url.startswith(u) for u in course_base_urls): is_self = True

            # PHASE 2: Indirect Verification (Source/Channel Name)
            source = c.get('source', '').lower()
            if not is_self and source and creator_name:
                if creator_name in source: is_self = True

            if is_self:
                relation = "SELF"
                score = 1.0
            else:
                title = c.get('title', '').lower()
                snippet = c.get('snippet', '').lower()
                has_name = creator_name and creator_name in title
                has_marker = any(m in title or m in snippet for m in collab_markers)
                
                # Check if LLM already verified it as PUBLIC_FACTS
                llm_relation = c.get('relation', '').upper()
                
                # Map external facts/collaborations into AFFILIATED for the user
                if llm_relation == "PUBLIC_FACTS" and c.get('confidence', 0) >= 0.5:
                    relation = "AFFILIATED" 
                    score = 0.7
                elif has_name and has_marker:
                    relation = "AFFILIATED"
                    score = 0.8
                elif has_name:
                    relation = "AFFILIATED" # Fallback if we know it's them but can't verify channel ID
                    score = 0.75 
                else:
                    relation = "OTHER"
                    score = 0.1
            
            # Keep only items owned by the creator or explicitly affiliated
            if relation in ("SELF", "AFFILIATED"):
                c['relation'] = relation
                c['ownership_score'] = score
                c['confidence'] = min(1.0, c.get('confidence', 0.5) * score)
                verified.append(c)
        
        # Sort so owned ('SELF') content is recommended above 'AFFILIATED' content
        verified.sort(key=lambda x: (x['relation'] == 'SELF', x['confidence']), reverse=True)
        return verified
```

## 2. Exact Match Filter

This logic sits inside the `search` method. Its job is to score and heavily filter video results based on keyword matches from the user's intent. If the user explicitly asks for "ads", the filter ensures video titles must contain "ad" or "ads".

```python
        # Extract meaningful keywords from the topic query
        # This strips out common stopwords to find the core subject
        topic_keywords = [w for w in re.split(r'\W+', topic_lower) if w and w not in STOP_WORDS and len(w) > 1]
        
        if not topic_keywords:
            return results
        
        scored = []
        for r in results:
            # Prefer _real_title (oEmbed-validated) over generic extracted title
            # (e.g., getting the real title avoids a video named "YouTube Video" from matching 0 keywords)
            title_lower = (r.get('_real_title') or r.get('title') or '').lower()
            snippet_lower = (r.get('snippet') or '').lower()
            
            # Count keyword hits in title (weighted 2x) and snippet (weighted 1x)
            title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
            snippet_hits = sum(1 for kw in topic_keywords if kw in snippet_lower)
            relevance_score = (title_hits * 2) + snippet_hits
            
            r['_relevance_score'] = relevance_score
            scored.append(r)
        
        # Sort by relevance score (highest first), then by confidence
        scored.sort(key=lambda x: (-x.get('_relevance_score', 0), -x.get('confidence', 0)))
        
        # For VIDEO intent, apply strict filtering to drop generically matched videos
        if search_intent == 'VIDEO':
            strict_relevant = []
            requires_ads = any(kw in topic_keywords for kw in ['ads', 'advertising'])
            
            for r in scored:
                title_lower = (r.get('_real_title') or r.get('title') or '').lower()
                title_hits = sum(1 for kw in topic_keywords if kw in title_lower)
                
                # Enforce that the video title has at least one of the topic keywords
                if title_hits >= 1:
                    # If they asked for ads, the video MUST be about ads
                    # (this prevents dropping an "ads" video and settling for a generic business video)
                    if requires_ads and 'ad' not in title_lower:
                        continue
                        
                    strict_relevant.append(r)
            
            # Either keep the strictly filtered results, or reject everything to force a fallback
            if strict_relevant:
                scored = strict_relevant
            else:
                scored = []
```

## 3. URL Extraction -> Result Objects

This is `_extract_urls_from_text`, which reads the raw, natural-language response from the GPT model and extracts URLs, formatted markdown titles, and nearby descriptive text into structured JSON-like `result` objects.

```python
    def _extract_urls_from_text(self, text: str, creator_name: str) -> List[Dict[str, Any]]:
        """Extract URLs from natural language response. Handles markdown links and bare URLs."""
        import re as _re
        
        # Fake IDs commonly hallucinated by OpenAI models when they guess URLs
        FAKE_ID_PATTERNS = {'abc123', 'def456', 'ghi789', 'jkl012', 'mno345', 'xyz789',
                            'test123', '1234567', 'abcdefg', 'example', 'placeholder'}
        
        results = []
        seen = set()
        
        def _is_fake_youtube(url: str) -> bool:
            """Check if a YouTube URL has a hallucinated video ID."""
            vid_id = ''
            if 'v=' in url:
                vid_id = url.split('v=')[1].split('&')[0].split('?')[0]
            elif 'youtu.be/' in url:
                vid_id = url.split('youtu.be/')[-1].split('?')[0]
            # Real YouTube IDs are 11 characters. If it's shorter or in the fake list, it's a hallucination.
            if vid_id and (len(vid_id) < 8 or vid_id.lower() in FAKE_ID_PATTERNS):
                return True
            return False
        
        def _add_result(url: str, title: str, snippet: str = ""):
            url = url.rstrip('.,;:)')
            if url in seen:
                return
            seen.add(url)
            
            is_youtube = 'youtube.com' in url or 'youtu.be' in url
            if is_youtube and _is_fake_youtube(url):
                return
            
            resource_type = "video" if is_youtube else "web"
            # Attempt a weak relation if the creator's name is simply mentioned nearby
            # (Note: COG enforcer runs later to do proper validation)
            relation = "SELF" if creator_name.lower() in text.lower() and is_youtube else "PUBLIC_FACTS"
            
            results.append({
                "title": title or ("YouTube Video" if is_youtube else url.split('/')[-1][:80]),
                "url": url,
                "snippet": snippet[:300].strip() if snippet else "",
                "resource_type": resource_type,
                "relation": relation,
                "confidence": 0.8  # Start with high confidence, lowered by COG/relevance later if needed
            })
        
        # Phase 1: Extract markdown-style links [Title](URL) — these include proper titles
        md_links = _re.findall(r'\[([^\]]+)\]\((https?://[^\s\)]+)\)', text)
        for title, url in md_links:
            _add_result(url, title)
        
        # Phase 2: Extract numbered/bulleted list items with URLs
        # Matches patterns like: "1. Video Title - https://..." or "- Title: https://..."
        list_items = _re.findall(r'(?:^|\n)\s*(?:\d+[\.\)]\s*|[-•]\s*)(.+?)\s*[-–—:]\s*(https?://[^\s\)]+)', text)
        for title, url in list_items:
            _add_result(url, title.strip())
        
        # Phase 3: Extract bare URLs not already captured
        # Scrapes the rest of the text, attempting to infer a title from the surrounding line text
        bare_urls = _re.findall(r'https?://[^\s\)\]"\']+', text)
        for url in bare_urls:
            url = url.rstrip('.,;:)')
            if url not in seen:
                # Try to find a title near the URL in the text
                nearby_title = ""
                url_pos = text.find(url)
                if url_pos > 0:
                    # Look at the line containing the URL for context
                    line_start = text.rfind('\n', max(0, url_pos - 200), url_pos)
                    line_text = text[line_start + 1:url_pos].strip() if line_start >= 0 else text[:url_pos].strip()
                    # Clean up common prefixes like numbers or bullet points
                    line_text = _re.sub(r'^[\d+\.\)\-•\s]+', '', line_text).strip()
                    line_text = _re.sub(r'[\-–—:]+$', '', line_text).strip()
                    if len(line_text) > 5 and len(line_text) < 150:
                        nearby_title = line_text
                _add_result(url, nearby_title)
        
        return results
```
