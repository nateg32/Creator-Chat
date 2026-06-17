import os
import json
import html
import re
import time
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
from backend.settings import settings
from backend.services.transcript_quality import assess_transcript_quality, transcript_needs_recovery
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from apify_client import ApifyClient
    APIFY_AVAILABLE = True
except ImportError:
    APIFY_AVAILABLE = False

def get_apify_token() -> str:
    # Use settings which properly loads from backend/.env
    token = (settings.APIFY_TOKEN or "").strip()
    if not token:
        raise ValueError("APIFY_TOKEN is not set. Please set it in backend/.env")
    return token


def _apify_run_value(run: Any, *keys: str) -> Any:
    """Read Apify run fields from either dict responses or SDK Run objects."""
    if run is None:
        return None

    for key in keys:
        if isinstance(run, dict) and key in run:
            return run.get(key)
        if hasattr(run, "get"):
            try:
                value = run.get(key)
                if value is not None:
                    return value
            except Exception:
                pass
        if hasattr(run, key):
            value = getattr(run, key)
            return value() if callable(value) else value

    for converter in ("model_dump", "dict", "to_dict"):
        if hasattr(run, converter):
            try:
                data = getattr(run, converter)()
                if isinstance(data, dict):
                    for key in keys:
                        if key in data:
                            return data.get(key)
            except Exception:
                pass

    data = getattr(run, "data", None)
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data.get(key)

    return None


def _apify_dataset_id(run: Any) -> str:
    dataset_id = _apify_run_value(run, "defaultDatasetId", "default_dataset_id")
    if not dataset_id:
        raise RuntimeError("Apify run did not return a default dataset id.")
    return str(dataset_id)


def _apify_run_id(run: Any) -> str:
    run_id = _apify_run_value(run, "id", "runId", "run_id")
    if not run_id:
        raise RuntimeError("Apify run did not return a run id.")
    return str(run_id)


def extract_content_id(url: str, platform: str) -> str:
    """
    Extract content ID from URL for source fidelity.
    Returns content_id (video ID, post ID, etc.) or empty string.
    """
    if not url:
        return ""
    
    try:
        if platform == "youtube":
            # Extract video ID from YouTube URLs
            match = re.search(r'(?:v=|/)([a-zA-Z0-9_-]{11})', url)
            return match.group(1) if match else ""
        elif platform == "instagram":
            # Extract reel/post shortcode
            match = re.search(r'/reel/([^/?]+)', url) or re.search(r'/p/([^/?]+)', url)
            return match.group(1) if match else ""
        elif platform == "twitter":
            # Extract tweet ID
            match = re.search(r'/status/(\d+)', url)
            return match.group(1) if match else ""
        elif platform == "tiktok":
            # TikTok URLs are complex, use last path segment
            path = urlparse(url).path.strip("/")
            parts = path.split("/")
            if parts:
                return parts[-1].split("?")[0]
        elif platform == "reddit":
            # Extract post ID from Reddit URL
            match = re.search(r'/comments/([a-z0-9]+)', url)
            return match.group(1) if match else ""
        elif platform == "linkedin":
            # Extract post ID (usually in path)
            path = urlparse(url).path.strip("/")
            parts = path.split("/")
            if "activity" in parts:
                idx = parts.index("activity")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
    except Exception:
        pass
    
    return ""


def _get_platform_from_url(url: str, platform_hint: str = "") -> str:
    platform = (platform_hint or "").lower().replace(" ", "_")
    if platform in {"youtube", "instagram", "tiktok", "twitter", "x", "linkedin", "reddit"}:
        return platform

    host = (urlparse(url or "").netloc or "").lower()
    if "youtu" in host:
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    if "twitter.com" in host or "x.com" in host:
        return "twitter"
    if "linkedin.com" in host:
        return "linkedin"
    if "reddit.com" in host:
        return "reddit"
    return platform


def _transcript_lookup_keys(url: str, platform_hint: str = "") -> List[str]:
    if not url:
        return []

    raw = str(url).strip()
    if not raw:
        return []

    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    platform = _get_platform_from_url(raw, platform_hint)

    keys: List[str] = []
    seen = set()

    def add(value: str):
        if not value or value in seen:
            return
        seen.add(value)
        keys.append(value)

    add(raw)
    add(raw.rstrip("/"))
    if host:
        add(f"{host}{path}")
        add(f"https://{host}{path}")
        add(f"http://{host}{path}")

    if platform == "youtube":
        content_id = extract_content_id(raw, "youtube")
        if content_id:
            add(f"youtube:{content_id}")
    elif platform == "instagram":
        content_id = extract_content_id(raw, "instagram")
        if content_id:
            add(f"instagram:{content_id}")
    elif platform == "tiktok":
        content_id = extract_content_id(raw, "tiktok")
        if content_id:
            add(f"tiktok:{content_id}")
    elif platform == "twitter":
        content_id = extract_content_id(raw, "twitter")
        if content_id:
            add(f"twitter:{content_id}")
    elif platform == "linkedin":
        content_id = extract_content_id(raw, "linkedin")
        if content_id:
            add(f"linkedin:{content_id}")
    elif platform == "reddit":
        content_id = extract_content_id(raw, "reddit")
        if content_id:
            add(f"reddit:{content_id}")

    return keys


def _build_transcript_alias_map(video_urls: List[str], platform_hint: str = "") -> Dict[str, List[str]]:
    alias_map: Dict[str, List[str]] = {}
    for original_url in video_urls:
        for alias in _transcript_lookup_keys(original_url, platform_hint):
            alias_map.setdefault(alias, []).append(original_url)
    return alias_map


def _resolve_transcript_matches(
    alias_map: Dict[str, List[str]],
    candidate_urls: List[str],
    platform_hint: str = "",
) -> List[str]:
    matches: List[str] = []
    seen = set()
    for candidate_url in candidate_urls:
        for alias in _transcript_lookup_keys(candidate_url, platform_hint):
            for original_url in alias_map.get(alias, []):
                if original_url in seen:
                    continue
                seen.add(original_url)
                matches.append(original_url)
    return matches


def _get_nested_value(data: Any, *path: Any) -> Any:
    current = data
    for key in path:
        if isinstance(current, dict):
            if key not in current:
                return None
            current = current[key]
        elif isinstance(current, list) and isinstance(key, int):
            if key < 0 or key >= len(current):
                return None
            current = current[key]
        else:
            return None
    return current


def _normalize_text_whitespace(value: str) -> str:
    return re.sub(r'\s+', ' ', html.unescape(value or '')).strip()


def _flatten_text_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return _normalize_text_whitespace(value)
    if isinstance(value, (int, float)):
        return _normalize_text_whitespace(str(value))
    if isinstance(value, list):
        parts = [_flatten_text_value(part) for part in value]
        parts = [part for part in parts if part]
        return _normalize_text_whitespace(' '.join(parts))
    if isinstance(value, dict):
        for nested_key in ('node', 'item', 'media', 'data'):
            if nested_key in value:
                nested_text = _flatten_text_value(value.get(nested_key))
                if nested_text:
                    return nested_text

        edge_texts = []
        if isinstance(value.get('edges'), list):
            for edge in value.get('edges', []):
                edge_text = _flatten_text_value(edge)
                if edge_text:
                    edge_texts.append(edge_text)
        if edge_texts:
            return _normalize_text_whitespace(' '.join(edge_texts))

        candidates = []
        for key in (
            'text', 'full_text', 'content', 'description', 'caption', 'message', 'body',
            'subtitle', 'subtitles', 'title', 'desc', 'display_text', 'note', 'value'
        ):
            if key in value:
                candidate = _flatten_text_value(value.get(key))
                if candidate:
                    candidates.append(candidate)
        if candidates:
            return max(candidates, key=len)
    return ''


def _pick_richest_text(candidates: List[Any]) -> str:
    cleaned: List[str] = []
    seen = set()
    for candidate in candidates:
        text = _flatten_text_value(candidate)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    if not cleaned:
        return ''
    cleaned.sort(key=lambda value: (len(value), value.count(' ')), reverse=True)
    return cleaned[0]


def _has_meaningful_text(*values: Any) -> bool:
    for value in values:
        if _flatten_text_value(value):
            return True
    return False


def _extract_platform_caption(item: Dict[str, Any], platform: str) -> str:
    platform = (platform or '').lower()
    candidate_paths = {
        'instagram': [
            ('caption',), ('text',), ('description',), ('captionText',),
            ('edgeMediaToCaption', 'edges'), ('edge_media_to_caption', 'edges'),
        ],
        'youtube': [
            ('description',), ('snippet', 'description'), ('desc',), ('text',), ('title',),
        ],
        'twitter': [
            ('noteTweet', 'noteTweetResults', 'result', 'text'),
            ('note_tweet', 'note_tweet_results', 'result', 'text'),
            ('legacy', 'full_text'), ('full_text',), ('text',), ('content',), ('display_text',), ('tweet_text',),
        ],
        'linkedin': [
            ('commentary', 'text', 'text'), ('commentary', 'text'),
            ('shareCommentary', 'text', 'text'), ('shareCommentary', 'text'),
            ('postText',), ('text',), ('caption',), ('message',), ('description',),
            ('content', 'text'),
        ],
        'tiktok': [
            ('text',), ('desc',), ('caption',), ('captionText',), ('descText',),
        ],
    }
    candidates = [_get_nested_value(item, *path) for path in candidate_paths.get(platform, [])]
    return _pick_richest_text(candidates)


def _extract_platform_transcript_candidate(item: Dict[str, Any], platform: str) -> str:
    platform = (platform or '').lower()
    candidate_paths = {
        'instagram': [
            ('transcript',), ('subtitles',), ('captionText',), ('caption_text',), ('videoSubtitles',),
        ],
        'youtube': [
            ('transcript',), ('subtitles',), ('captions',), ('subtitle',),
        ],
        'tiktok': [
            ('transcript',), ('subtitles',), ('captionText',), ('caption_text',), ('subtitle',), ('autoCaptions',),
        ],
        'twitter': [
            ('noteTweet', 'noteTweetResults', 'result', 'text'),
            ('note_tweet', 'note_tweet_results', 'result', 'text'),
            ('legacy', 'full_text'), ('full_text',), ('text',), ('content',),
        ],
        'linkedin': [
            ('commentary', 'text', 'text'), ('commentary', 'text'),
            ('shareCommentary', 'text', 'text'), ('shareCommentary', 'text'),
            ('postText',), ('text',), ('caption',), ('message',),
        ],
    }
    candidates = [_get_nested_value(item, *path) for path in candidate_paths.get(platform, [])]
    return _pick_richest_text(candidates)


def fetch_youtube_oembed_title(video_url: str, timeout: int = 6) -> str:
    """
    Fetch a YouTube video's authentic title via the public oEmbed endpoint.

    No auth required, no rate limit issues for low volume. Used as a last-resort
    fallback when the scraper actor returns an item without a title field.
    Returns empty string on any failure (caller falls back to generic label).
    """
    if not video_url:
        return ""
    try:
        from urllib.parse import quote
        oembed_url = f"https://www.youtube.com/oembed?url={quote(video_url, safe='')}&format=json"
        req = Request(
            oembed_url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CreatorChat/1.0)",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8", errors="ignore"))
        title = str(data.get("title") or "").strip()
        return title
    except Exception as exc:
        print(f"[YOUTUBE-OEMBED] title fetch failed for {video_url}: {exc}", flush=True)
        return ""


def _is_generic_title(value: Any, platform: str) -> bool:
    title = _normalize_text_whitespace(str(value or ""))
    if not title:
        return True

    platform_key = (platform or "").lower()
    lower = title.lower()
    generic_labels = {
        platform_key,
        f"{platform_key} post",
        f"{platform_key} content",
        f"{platform_key} reel",
        f"{platform_key} video",
        "video",
        "post",
        "content",
    }
    if lower in generic_labels:
        return True

    if platform_key == "youtube":
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", title):
            return True
        if re.fullmatch(r"youtube\s+video[:\s-]+[A-Za-z0-9_-]{6,}", lower):
            return True

    if lower.isdigit():
        return True
    if len(lower) > 15 and re.fullmatch(r"[a-f0-9_-]+", lower):
        return True
    return False


def _first_specific_title(item: Dict[str, Any], candidate_paths: List[Tuple[Any, ...]], platform: str) -> str:
    for path in candidate_paths:
        value = _get_nested_value(item, *path)
        title = _flatten_text_value(value)
        if title and not _is_generic_title(title, platform):
            return title
    return ""


def extract_title_from_metadata(item: Dict[str, Any], platform: str, source_url: str, caption_override: Optional[str] = None) -> str:
    """Extract title from item metadata or derive from URL/caption."""
    platform = (platform or "").lower()
    caption = caption_override or _extract_platform_caption(item, platform)

    # Per-platform candidate fields, ordered most-authentic first.
    candidate_paths_by_platform = {
        "youtube": [
            ("title",), ("videoTitle",), ("snippet", "title"), ("video", "title"),
            ("metadata", "title"), ("name",), ("headline",), ("displayTitle",),
        ],
        "tiktok": [("title",), ("desc",), ("description",), ("name",), ("metadata", "title")],
        "instagram": [("title",), ("name",), ("caption_title",), ("metadata", "title")],
        "twitter": [("title",), ("name",), ("metadata", "title")],
        "linkedin": [("title",), ("headline",), ("name",), ("metadata", "title")],
        "facebook": [("title",), ("name",), ("headline",), ("metadata", "title")],
        "reddit": [("title",), ("name",), ("link_title",), ("metadata", "title")],
    }
    candidates = candidate_paths_by_platform.get(platform, [("title",), ("name",), ("headline",), ("metadata", "title")])
    title = _first_specific_title(item, candidates, platform)

    # If Apify returns a bare YouTube id or a generic label, the public oEmbed
    # endpoint usually gives the real watch-page title without needing an API key.
    if platform == "youtube" and not title:
        oembed_title = fetch_youtube_oembed_title(source_url)
        if oembed_title and not _is_generic_title(oembed_title, platform):
            title = oembed_title

    if not title and caption:
        # Clean up and truncate caption for title use
        # Remove hashtags and excessive whitespace
        clean_caption = re.sub(r'#\w+\s*', '', str(caption))
        clean_caption = re.sub(r'https?://\S+', '', clean_caption)
        if platform == "twitter":
            clean_caption = re.sub(r'^RT\s+@\w+:\s*', '', clean_caption, flags=re.IGNORECASE)
            clean_caption = re.sub(r'^(?:@\w+\s+){1,5}', '', clean_caption)
        clean_caption = re.sub(r'\s+', ' ', clean_caption).strip()
        if len(clean_caption) > 60:
            title = clean_caption[:57] + "..."
        else:
            title = clean_caption

    # Final Fallbacks
    if not title:
        content_id = extract_content_id(source_url, platform)
        if platform == "youtube":
            title = f"YouTube video: {content_id}" if content_id else "YouTube video"
        elif platform == "instagram":
            title = f"Instagram reel: {content_id}" if content_id else "Instagram content"
        elif platform == "twitter":
            title = f"Tweet: {content_id}" if content_id else "Twitter post"
        elif platform == "tiktok":
            title = f"TikTok: {content_id}" if content_id else "TikTok video"
        else:
            title = f"{platform.title()} content"
            
    return title

def search_instagram_reels(handle: str, reel_id: Optional[str] = None, limit: int = 10, skip_transcripts: bool = False) -> List[Dict[str, Any]]:
    """
    Scrape Instagram reels using Apify instagram-reel-scraper actor.
    
    Args:
        handle: Instagram username
        reel_id: Optional specific reel ID to search
        limit: Max number of reels (enforced to 10)
    
    Returns:
        List of normalized reel items with transcript handling
    """
    # No limit enforced for testing
    limit = max(1, limit)

    token = get_apify_token()
    
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    
    try:
        print("[APIFY] Instagram token present:", bool(token), flush=True)
        print("[APIFY] actor: apify/instagram-reel-scraper", flush=True)
        client = ApifyClient(token)
        
        # Prepare input for apify/instagram-reel-scraper
        run_input = {
            "username": [handle],
            "resultsLimit": limit,
        }
        
        # If specific reel ID provided, add it to startUrls
        if reel_id:
            run_input["startUrls"] = [{"url": f"https://instagram.com/reel/{reel_id}"}]
        
        # 1. First Pass: Scrape metadata
        run = client.actor("apify/instagram-reel-scraper").call(run_input=run_input)
        
        # Wait for the run to finish and get results
        items = []
        video_urls = []
        raw_count = 0
        for item in client.dataset(_apify_dataset_id(run)).iterate_items():
            raw_count += 1
            caption = _extract_platform_caption(item, "instagram")
            
            # Extract metadata
            shortcode = item.get("shortCode", "") or item.get("shortcode", "") or item.get("id", "")
            reel_id_from_item = item.get("id", "") or item.get("reelId", "") or shortcode
            
            # Build URL
            if shortcode:
                source_url = f"https://instagram.com/reel/{shortcode}"
            elif reel_id_from_item:
                source_url = f"https://instagram.com/reel/{reel_id_from_item}"
            else:
                source_url = f"https://instagram.com/{handle}"
            
            # Extract content_id and title for source fidelity
            content_id = shortcode or reel_id_from_item or ""
            title = extract_title_from_metadata(item, "instagram", source_url, caption_override=caption)
            
            # Extract published date
            published_at = None
            if item.get("timestamp"):
                try:
                    published_at = datetime.fromtimestamp(item.get("timestamp")).isoformat()
                except:
                    pass
            
            # Build metadata JSON with source fidelity
            metadata = {
                "likes": item.get("likesCount", 0) or item.get("likes", 0),
                "views": item.get("viewsCount", 0) or item.get("views", 0),
                "comments": item.get("commentsCount", 0) or item.get("comments", 0),
                "duration": item.get("duration", 0),
                "hashtags": item.get("hashtags", []),
                "mentions": item.get("mentions", []),
                "audio": item.get("audio", {}),
                "video_url": item.get("videoUrl", "") or item.get("video", ""),
                "platform": "instagram",
                "content_id": content_id,
                "canonical_url": source_url,
                "title": title,
            }
            
            # Normalized item schema
            normalized_item = {
                "creator_handle": handle,
                "platform": "instagram",
                "content_type": "reel",
                "source_url": source_url,
                "caption": caption,
                "transcript": "",
                "transcript_status": "pending",
                "published_at": published_at,
                "metadata": metadata,
            }
            
            items.append(normalized_item)
            video_urls.append(source_url)
            
            if len(items) >= limit:
                break
        
        if video_urls:
            print(f"[APIFY] Instagram handle={handle} deferred {len(video_urls)} Reels to Whisper + AssemblyAI")
                    
        print(f"[APIFY] Instagram handle={handle} raw_items={raw_count} normalized={len(items)}", flush=True)
        return items
    except Exception as e:
        print(f"[APIFY] Instagram scrape error: {e}", flush=True)
        raise


def _time_filter_to_date_expr(time_filter: Optional[Dict[str, Any]]) -> Optional[str]:
    """Convert our time_filter to actor date expression: YYYY-MM-DD or 'N days'."""
    if not time_filter or not isinstance(time_filter, dict):
        return None
    mode = time_filter.get("mode") or "all"
    if mode == "all":
        return None
    if mode == "since":
        since = (time_filter.get("since") or "").strip()
        return since if since else None
    if mode == "last_days":
        d = time_filter.get("days") or 30
        return f"{int(d)} days"
    return None


def _fetch_url_text(url: str, timeout: int = 20) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="ignore")


def _resolve_youtube_feed_url(channel_url: str) -> Optional[str]:
    url = str(channel_url or "").strip()
    if not url:
        return None

    direct_channel = re.search(r"/channel/([A-Za-z0-9_-]+)", url)
    if direct_channel:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={direct_channel.group(1)}"

    try:
        html_text = _fetch_url_text(url)
    except Exception as exc:
        print(f"[YOUTUBE-FALLBACK] Could not fetch channel HTML: {exc}", flush=True)
        return None

    patterns = [
        r"https://www\.youtube\.com/feeds/videos\.xml\?channel_id=([A-Za-z0-9_-]+)",
        r'"rssUrl":"https://www\.youtube\.com/feeds/videos\.xml\?channel_id=([A-Za-z0-9_-]+)"',
        r'"externalId":"(UC[A-Za-z0-9_-]+)"',
        r'"browseId":"(UC[A-Za-z0-9_-]+)"',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text)
        if match:
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}"

    return None


def _jsonish_unescape(value: str) -> str:
    try:
        text = json.loads(f'"{str(value or "")}"')
    except Exception:
        text = str(value or "")
    text = html.unescape(text)
    return text.replace("\\u0026", "&").replace('\\"', '"').strip()


def _parse_relative_date(value: str) -> Optional[str]:
    text = str(value or "").strip().lower()
    match = re.search(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", text)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    days_per_unit = {
        "minute": 0,
        "hour": 0,
        "day": 1,
        "week": 7,
        "month": 30,
        "year": 365,
    }
    if unit == "minute":
        dt = datetime.now(timezone.utc) - timedelta(minutes=amount)
    elif unit == "hour":
        dt = datetime.now(timezone.utc) - timedelta(hours=amount)
    else:
        dt = datetime.now(timezone.utc) - timedelta(days=amount * days_per_unit[unit])
    return dt.isoformat()


def _fallback_youtube_html_items(
    channel_url: str,
    handle: Optional[str],
    limit: int,
    skip_transcripts: bool = False,
) -> List[Dict[str, Any]]:
    try:
        html_text = _fetch_url_text(channel_url)
    except Exception as exc:
        print(f"[YOUTUBE-FALLBACK] Could not fetch channel HTML items: {exc}", flush=True)
        return []

    items: List[Dict[str, Any]] = []
    seen_ids = set()
    creator = handle or "youtube"

    for match in re.finditer(r'"contentId":"([A-Za-z0-9_-]{11})","contentType":"LOCKUP_CONTENT_TYPE_VIDEO"', html_text):
        video_id = match.group(1)
        if video_id in seen_ids:
            continue

        window = html_text[max(0, match.start() - 6000):match.start()]
        title_match = list(re.finditer(r'"lockupMetadataViewModel":\{"title":\{"content":"(.*?)"\}', window))
        title = _jsonish_unescape(title_match[-1].group(1)) if title_match else f"YouTube video: {video_id}"

        age_match = re.search(r'"accessibilityLabel":"([^"]+ ago)"', window)
        published_at = _parse_relative_date(age_match.group(1)) if age_match else None
        source_url = f"https://www.youtube.com/watch?v={video_id}"

        items.append({
            "creator_handle": creator,
            "platform": "youtube",
            "content_type": "video",
            "source_url": source_url,
            "caption": title,
            "transcript": "",
            "transcript_status": "pending" if skip_transcripts else "missing",
            "published_at": published_at,
            "metadata": {
                "platform": "youtube",
                "content_id": video_id,
                "canonical_url": source_url,
                "title": title,
                "fallback_source": "youtube_html",
            },
        })
        seen_ids.add(video_id)
        if len(items) >= limit:
            break

    if items:
        print(f"[YOUTUBE-FALLBACK] HTML fallback returned {len(items)} items from {channel_url}", flush=True)
    return items


def _fallback_youtube_feed_items(
    channel_url: str,
    handle: Optional[str],
    limit: int,
    skip_transcripts: bool = False,
) -> List[Dict[str, Any]]:
    feed_url = _resolve_youtube_feed_url(channel_url)
    if not feed_url:
        return []

    try:
        xml_text = _fetch_url_text(feed_url)
        root = ET.fromstring(xml_text)
    except Exception as exc:
        print(f"[YOUTUBE-FALLBACK] Could not parse feed: {exc}", flush=True)
        return _fallback_youtube_html_items(channel_url, handle, limit, skip_transcripts=skip_transcripts)

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
        "media": "http://search.yahoo.com/mrss/",
    }

    items: List[Dict[str, Any]] = []
    creator = handle or "youtube"

    for entry in root.findall("atom:entry", ns):
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
        if not video_id:
            continue
        source_url = f"https://www.youtube.com/watch?v={video_id}"
        published_at = entry.findtext("atom:published", default=None, namespaces=ns)
        description = entry.findtext("media:group/media:description", default="", namespaces=ns)
        channel_name = entry.findtext("atom:author/atom:name", default=creator, namespaces=ns)
        title = extract_title_from_metadata(
            {"title": entry.findtext("atom:title", default="", namespaces=ns), "description": description},
            "youtube",
            source_url,
            caption_override=description,
        )

        items.append({
            "creator_handle": channel_name or creator,
            "platform": "youtube",
            "content_type": "video",
            "source_url": source_url,
            "caption": description or title,
            "transcript": "",
            "transcript_status": "pending" if skip_transcripts else "missing",
            "published_at": published_at,
            "metadata": {
                "platform": "youtube",
                "content_id": video_id,
                "canonical_url": source_url,
                "title": title,
                "channelName": channel_name or creator,
                "fallback_source": "youtube_rss",
            },
        })
        if len(items) >= limit:
            break

    print(f"[YOUTUBE-FALLBACK] RSS fallback returned {len(items)} items from {feed_url}", flush=True)
    return items


def _extract_transcripts_invideoiq(video_urls: List[str], token: str, language: str = "", platform_hint: str = "") -> Dict[str, str]:
    """Disabled by design: Apify scraping must not produce transcript text."""
    if video_urls:
        print("[APIFY] invideoiq transcript actor disabled; deferring to Whisper + AssemblyAI.")
    return {}


def _flatten_transcript_segments(payload: Any) -> str:
    if not payload:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts: List[str] = []
        for segment in payload:
            if isinstance(segment, dict):
                text = str(segment.get("text") or "").strip()
            else:
                text = str(segment).strip()
            if text:
                parts.append(text)
        return " ".join(parts).strip()
    return str(payload).strip()


def _flatten_transcript_with_timestamps(payload: Any) -> Tuple[str, List[Dict[str, Any]]]:
    """Flatten transcript segments AND preserve per-segment timing data.

    Returns:
        (flat_text, timing_map)
        where timing_map is a list of dicts:
        [{"start": 0.0, "end": 2.5, "char_start": 0, "char_end": 45}, ...]
    """
    if not payload:
        return "", []
    if isinstance(payload, str):
        return payload.strip(), []
    if not isinstance(payload, list):
        return str(payload).strip(), []

    parts: List[str] = []
    timing_map: List[Dict[str, Any]] = []
    char_offset = 0

    for segment in payload:
        if isinstance(segment, dict):
            text = str(segment.get("text") or "").strip()
            start = segment.get("start")
            duration = segment.get("duration") or segment.get("dur")
        else:
            text = str(segment).strip()
            start = None
            duration = None

        if not text:
            continue

        # Account for the space separator between segments
        if parts:
            char_offset += 1  # for the " " join separator

        char_start = char_offset
        char_end = char_offset + len(text)

        entry: Dict[str, Any] = {
            "char_start": char_start,
            "char_end": char_end,
        }
        if start is not None:
            try:
                entry["start"] = float(start)
                if duration is not None:
                    entry["end"] = float(start) + float(duration)
            except (ValueError, TypeError):
                pass

        timing_map.append(entry)
        parts.append(text)
        char_offset = char_end

    return " ".join(parts).strip(), timing_map


def _extract_youtube_native_transcripts(video_urls: List[str], max_workers: Optional[int] = None) -> Dict[str, str]:
    """Disabled by design: Whisper creates raw transcripts for YouTube videos."""
    if video_urls:
        print("[YOUTUBE] Native caption recovery disabled; deferring to Whisper + AssemblyAI.")
    return {}


def extract_youtube_native_with_timestamps(video_url: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Disabled by design: timestamp/caption generation belongs to AssemblyAI."""
    if video_url:
        print("[YOUTUBE] Native timestamp captions disabled; deferring to Whisper + AssemblyAI.")
    return "", []


def batch_extract_all_transcripts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mark scraped video items for Whisper transcription plus AssemblyAI captions.

    Apify is intentionally kept out of transcript recovery. It scrapes posts,
    source URLs, captions, and metadata; the transcript worker owns media
    resolution, Whisper transcription, and AssemblyAI enrichment/captions.
    """
    if not items:
        return items

    video_platforms = {"youtube", "tiktok", "instagram"}
    deferred = 0
    for it in items:
        platform = str(it.get("platform") or "").lower().replace(" ", "_")
        if platform not in video_platforms:
            continue
        metadata = it.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        title = metadata.get("title") or ""
        transcript = it.get("transcript") or ""
        source = str(metadata.get("transcript_source") or "").lower()
        transcriber_owned = bool(
            metadata.get("assemblyai_transcript_id")
            or "assemblyai" in source
            or "whisper" in source
            or "openai_whisper" in source
        )
        if transcriber_owned and not transcript_needs_recovery(transcript, caption=it.get("caption") or "", title=title):
            diagnostics = assess_transcript_quality(transcript, caption=it.get("caption") or "", title=title)
            metadata["transcript_quality_score"] = diagnostics.get("score")
            metadata["transcript_quality_reason"] = diagnostics.get("reason")
            metadata["transcript_coverage"] = diagnostics.get("coverage")
            metadata["transcript_word_count"] = diagnostics.get("word_count")
            it["metadata"] = metadata
            it["transcript_status"] = "present"
            continue
        it["transcript"] = ""
        it["transcript_status"] = "pending"
        metadata["transcript_source"] = "whisper_assemblyai_deferred"
        metadata["transcript_quality_reason"] = "pending_whisper_assemblyai"
        it["metadata"] = metadata
        deferred += 1

    print(f"[BATCH-TRANSCRIPT] Deferred {deferred} video transcripts to Whisper + AssemblyAI")
    return items


def _extract_youtube_transcripts(video_urls: List[str], token: str) -> Dict[str, str]:
    """Disabled by design: Apify does scraping only; transcript worker uses Whisper + AssemblyAI."""
    if video_urls:
        print("[YOUTUBE] Apify transcript actors disabled; deferring to Whisper + AssemblyAI.")
    return {}


def search_youtube_channel(
    url: str,
    handle: Optional[str],
    limit: int = 10,
    time_filter: Optional[Dict[str, Any]] = None,
    skip_transcripts: bool = False,
) -> List[Dict[str, Any]]:
    """
    Scrape YouTube channel/videos using apidojo/youtube-scraper.
    Input: startUrls (list of strings), maxResults, maxResultsShorts.
    """
    limit = max(1, int(limit))
    token = get_apify_token()
    # Import time locally to ensure it's defined during parallel execution/reloads
    import time
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    date_expr = _time_filter_to_date_expr(time_filter)
    
    target_url = url
    actor_start_url = target_url.split("?", 1)[0].rstrip("/")
            
    # Optimization: Use the actor's native filtering.
    run_input = {
        "startUrls": [{"url": actor_start_url}],
        "maxResults": limit,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
        "maxItems": limit,
        "sortVideosBy": "NEWEST",
    }
    if date_expr:
        run_input["oldestPostDate"] = date_expr
    
    print(f"[YOUTUBE] Starting apidojo/youtube-scraper (Surgical) with limit={limit}")
        
    client = ApifyClient(token)
    # Start the actor but don't wait for it to finish (surgical abort strategy)
    run = client.actor("apidojo/youtube-scraper").start(run_input=run_input)
    run_id = _apify_run_id(run)
    dataset_id = _apify_dataset_id(run)
    
    # Poll for results and abort as soon as we have enough
    video_data = []
    start_time = time.time()
    while len(video_data) < limit:
        # Check if run finished naturally
        run_info = client.run(run_id).get()
        status = _apify_run_value(run_info, "status") or "UNKNOWN"
        
        # Check dataset
        items = list(client.dataset(dataset_id).iterate_items())
        for item in items:
            vid = item.get("id") or item.get("videoId") or ""
            vurl = item.get("url") or ""
            if not (vid and len(str(vid)) == 11):
                vid = extract_content_id(vurl, "youtube")
            if not vid or vid == "watch": continue
            
            source_url = vurl or f"https://www.youtube.com/watch?v={vid}"
            # Deduplicate by ID
            if not any(v[1] == source_url for v in video_data):
                video_data.append((item, source_url))
        
        if len(video_data) >= limit:
            print(f"[YOUTUBE] Found {len(video_data)} items. Aborting actor early to save time.")
            try:
                client.run(run_id).abort()
            except:
                pass
            break
            
        if status in ["SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"]:
            break
            
        if time.time() - start_time > 90: # Backup safety timeout
            print("[YOUTUBE] Polling timeout. Proceeding with what we found.")
            break
            
        time.sleep(2) # Poll every 2 seconds
    
    # Apify scrapes video metadata only; Whisper + AssemblyAI handle transcripts/captions.
    video_urls = [vsurl for _, vsurl in video_data]
    print(f"[YOUTUBE] Deferred transcripts for {len(video_urls)} videos to Whisper + AssemblyAI")
    
    # Second pass: build items with transcripts
    items = []
    for item, source_url in video_data:
        title = extract_title_from_metadata(item, "youtube", source_url)
        caption = _extract_platform_caption(item, "youtube") or title

        transcript = ""
        transcript_status = "pending"
        published_at = None
        if item.get("uploadDate"):
            published_at = item["uploadDate"]
        elif item.get("publishedAt"):
            published_at = item["publishedAt"]
        if isinstance(published_at, (int, float)):
            try:
                published_at = datetime.fromtimestamp(float(published_at)).isoformat()
            except Exception:
                published_at = None
        
        content_id = extract_content_id(source_url, "youtube")
        final_title = title or (f"YouTube video: {content_id}" if content_id else "YouTube video")
        if "/shorts/" in source_url and not final_title.lower().startswith("short:"):
            final_title = f"Short: {final_title}"
        
        metadata = {
            "likes": item.get("likes") or item.get("likeCount", 0),
            "views": item.get("views") or item.get("viewCount", 0),
            "comments": item.get("comments") or item.get("commentCount", 0),
            "duration": item.get("duration") or item.get("lengthSeconds", 0),
            "channelId": item.get("channelId"),
            "channelName": item.get("channelName") or item.get("channelTitle"),
            "platform": "youtube",
            "content_id": content_id,
            "canonical_url": source_url,
            "title": final_title,
        }
        
        is_shorts = "/shorts/" in source_url
        if is_shorts:
            continue
            
        creator = handle or item.get("channelName") or item.get("channelTitle") or "youtube"
        items.append({
            "creator_handle": creator,
            "platform": "youtube",
            "content_type": "video" if not is_shorts else "short",
            "source_url": source_url,
            "caption": caption,
            "transcript": transcript,
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        if len(items) >= limit:
            break

    if len(items) < limit:
        fallback_items = _fallback_youtube_feed_items(target_url, handle, limit, skip_transcripts=skip_transcripts)
        seen_urls = {item.get("source_url") for item in items}
        for fallback_item in fallback_items:
            fallback_url = fallback_item.get("source_url")
            if not fallback_url or fallback_url in seen_urls:
                continue
            items.append(fallback_item)
            seen_urls.add(fallback_url)
            if len(items) >= limit:
                break

    return items


def search_twitter_profile(
    handle: str,
    url: Optional[str] = None,
    limit: int = 20,
    time_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Search Twitter/X profile using kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest.
    Uses advanced search fields like `from` and `within_time` / `since`.
    """
    limit = max(1, int(limit))
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    h = handle.strip().lstrip("@")
    tf = time_filter or {}
    run_input: Dict[str, Any] = {
        "maxItems": limit,
        "queryType": "Latest",
        "from": h,
    }
    # Time filter mapping
    if tf.get("mode") == "since" and tf.get("since"):
        # Actor expects: YYYY-MM-DD_00:00:00_UTC
        since = str(tf["since"]).strip()
        run_input["since"] = f"{since}_00:00:00_UTC"
    elif tf.get("mode") == "last_days":
        d = int(tf.get("days") or 30)
        run_input["within_time"] = f"{d}d"
    client = ApifyClient(token)
    run = client.actor("kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest").call(run_input=run_input)
    items = []
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        tid = item.get("id") or item.get("tweetId") or ""
        user = item.get("userName") or item.get("username") or item.get("author") or h
        source_url = item.get("url") or (f"https://twitter.com/{user}/status/{tid}" if tid else "")
        text = _extract_platform_caption(item, "twitter")
        if not _has_meaningful_text(text):
            continue
        transcript_status = "present" if text and text.strip() else "missing"
        published_at = item.get("created_at") or item.get("postedAt") or item.get("date")
        if isinstance(published_at, (int, float)):
            try:
                published_at = datetime.fromtimestamp(float(published_at)).isoformat()
            except Exception:
                published_at = None
        
        # Extract content_id and title for source fidelity
        content_id = tid or extract_content_id(source_url, "twitter")
        title = extract_title_from_metadata(item, "twitter", source_url, caption_override=text)
        
        metadata = {
            "likes": item.get("likes") or item.get("favorite_count", 0) or item.get("likeCount", 0),
            "retweets": item.get("retweets") or item.get("retweet_count", 0) or item.get("retweetCount", 0),
            "replies": item.get("replies") or item.get("reply_count", 0) or item.get("replyCount", 0),
            "views": item.get("views", 0),
            "platform": "twitter",
            "content_id": content_id,
            "canonical_url": source_url,
            "title": title,
        }
        items.append({
            "creator_handle": handle,
            "platform": "twitter",
            "content_type": "tweet",
            "source_url": source_url,
            "caption": text,
            "transcript": text,
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        if len(items) >= limit:
            break
    return items


def search_linkedin_posts(
    url: str,
    limit: int = 20,
    deep_search: bool = True,
) -> List[Dict[str, Any]]:
    """
    Scrape LinkedIn posts using apimaestro/linkedin-profile-posts.
    Input: {"profileUrl": url}.
    """
    limit = min(max(1, int(limit)), 100)
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    
    # Ensure full URL normalization to prevent defaulting to examples.
    normalized_url = url
    if "linkedin.com" not in url:
        handle = url.strip("/")
        normalized_url = f"https://www.linkedin.com/in/{handle}/"

    # The apimaestro/linkedin-profile-posts actor expects 'username' according to the UI help.
    # To ensure it doesn't fall back to the default (Satya Nadella), we use 'username'.
    run_input = {
        "username": normalized_url,
        "totalPostsToScrape": limit,
    }
    
    client = ApifyClient(token)
    run = client.actor("apimaestro/linkedin-profile-posts").call(run_input=run_input)
    items: List[Dict[str, Any]] = []
    
    # Iterate dataset. Note: Output might be Single Item containing 'data.posts' array, or multiple items.
    for dataset_item in client.dataset(_apify_dataset_id(run)).iterate_items():
        # Check if this item is a wrapper containing posts
        posts = []
        if dataset_item.get("data") and isinstance(dataset_item["data"].get("posts"), list):
             posts = dataset_item["data"]["posts"]
        else:
             # Assume item IS the post (if actor behavior differs)
             posts = [dataset_item]

        for item in posts:
            source_url = item.get("url") or item.get("postUrl") or ""
            text = _extract_platform_caption(item, "linkedin")
            if not _has_meaningful_text(text):
                continue
            
            # Date handling
            published_at = None
            posted_at = item.get("posted_at")
            if isinstance(posted_at, dict):
                 # Try timestamp first
                 ts = posted_at.get("timestamp")
                 if ts:
                     try:
                        # Timestamp might be in ms
                        if ts > 1000000000000: ts = ts / 1000.0
                        published_at = datetime.fromtimestamp(ts).isoformat()
                     except: pass
                 if not published_at and posted_at.get("date"):
                     published_at = posted_at.get("date")
            else:
                 published_at = item.get("date") or item.get("time")

            # Stats
            stats = item.get("stats") or {}
            
            # Extract content_id and title
            raw_urn = item.get("urn")
            if raw_urn and not isinstance(raw_urn, str):
                raw_urn = str(raw_urn)
            content_id = raw_urn or extract_content_id(source_url, "linkedin")
            title = extract_title_from_metadata(item, "linkedin", source_url, caption_override=text)
            
            metadata = {
                "likes": stats.get("like") or stats.get("likes") or item.get("likes", 0),
                "comments": stats.get("comments") or item.get("comments", 0),
                "shares": stats.get("reposts") or item.get("shares", 0),
                "author": item.get("author", {}).get("username") or "linkedin",
                "platform": "linkedin",
                "content_id": content_id,
                "canonical_url": source_url,
                "title": title,
            }
            
            transcript_status = "present" if text and str(text).strip() else "missing"
            
            items.append({
                "creator_handle": metadata.get("author") or "linkedin",
                "platform": "linkedin",
                "content_type": "post",
                "source_url": source_url,
                "caption": text,
                "transcript": text,
                "transcript_status": transcript_status,
                "published_at": published_at,
                "metadata": metadata,
            })
            if len(items) >= limit:
                break
        if len(items) >= limit:
            break
            
    return items


def search_facebook_posts(
    url: str,
    handle: Optional[str],
    limit: int = 20,
    time_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Search Facebook page posts using apify/facebook-posts-scraper.
    Input: startUrls (page URL), resultsLimit, optional onlyPostsNewerThan.
    """
    limit = min(max(1, int(limit)), 100)
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    run_input = {
        "startUrls": [{"url": url}],
        "resultsLimit": limit,
        "captionText": True,
    }
    date_expr = _time_filter_to_date_expr(time_filter)
    if date_expr:
        run_input["onlyPostsNewerThan"] = date_expr
    client = ApifyClient(token)
    run = client.actor("apify/facebook-posts-scraper").call(run_input=run_input)
    items = []
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        source_url = item.get("url") or item.get("postUrl") or item.get("link", "") or ""
        text = _pick_richest_text([
            item.get("text"),
            item.get("message"),
            item.get("caption"),
        ])
        transcript = _pick_richest_text([
            item.get("transcript"),
            item.get("captionText"),
            text,
        ])
        if not _has_meaningful_text(text, transcript):
            continue
        transcript_status = "present" if (transcript and str(transcript).strip()) else "missing"
        published_at = item.get("time") or item.get("postedAt") or item.get("creationTime")
        if isinstance(published_at, (int, float)):
            try:
                published_at = datetime.fromtimestamp(float(published_at)).isoformat()
            except Exception:
                published_at = None
        # Extract content_id and title for source fidelity
        content_id = extract_content_id(source_url, "facebook")
        title = extract_title_from_metadata(item, "facebook", source_url, caption_override=text)
        
        metadata = {
            "likes": item.get("likes") or item.get("reactions", 0),
            "comments": item.get("comments") or item.get("commentsCount", 0),
            "shares": item.get("shares") or item.get("sharesCount", 0),
            "platform": "facebook",
            "content_id": content_id,
            "canonical_url": source_url,
            "title": title,
        }
        creator = handle or item.get("author") or item.get("pageName", "") or "facebook"
        items.append({
            "creator_handle": creator,
            "platform": "facebook",
            "content_type": "post",
            "source_url": source_url,
            "caption": text,
            "transcript": transcript,
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        if len(items) >= limit:
            break
    return items


def search_reddit_user(
    url: str,
    handle: Optional[str],
    limit: int = 20,
    time_filter: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Search Reddit user posts using harshmaur/reddit-scraper.
    Uses startUrls (user profile) and result limit. Schema may vary; we normalize.
    """
    limit = min(max(1, int(limit)), 100)
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    run_input = {
        "startUrls": [{"url": url}],
        "maxResults": limit,
    }
    client = ApifyClient(token)
    run = client.actor("harshmaur/reddit-scraper").call(run_input=run_input)
    items = []
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        permalink = item.get("permalink") or item.get("url") or ""
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"
        source_url = permalink or ""
        text = item.get("selftext") or item.get("body") or item.get("title", "") or ""
        title = item.get("title") or ""
        caption = f"{title}\n\n{text}".strip() if text else title
        if not _has_meaningful_text(caption):
            continue
        transcript_status = "present" if caption and caption.strip() else "missing"
        published_at = item.get("created_utc") or item.get("created") or item.get("postedAt")
        if isinstance(published_at, (int, float)):
            try:
                published_at = datetime.fromtimestamp(float(published_at)).isoformat()
            except Exception:
                published_at = None
        # Extract content_id and title for source fidelity
        content_id = extract_content_id(source_url, "reddit")
        title_for_meta = title or extract_title_from_metadata(item, "reddit", source_url, caption_override=caption)
        
        metadata = {
            "score": item.get("score", 0),
            "upvotes": item.get("ups", 0),
            "comments": item.get("num_comments", 0),
            "subreddit": item.get("subreddit"),
            "platform": "reddit",
            "content_id": content_id,
            "canonical_url": source_url,
            "title": title_for_meta,
        }
        creator = handle or item.get("author") or "reddit"
        items.append({
            "creator_handle": creator,
            "platform": "reddit",
            "content_type": "post",
            "source_url": source_url,
            "caption": caption,
            "transcript": caption,
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        if len(items) >= limit:
            break
    return items


def _extract_social_transcripts(video_urls: List[str], token: str, platform: str = "tiktok") -> Dict[str, str]:
    """Disabled by design: Apify does scraping only; transcript worker uses Whisper + AssemblyAI."""
    if video_urls:
        print(f"[{platform.upper()}] Apify transcript actors disabled; deferring to Whisper + AssemblyAI.")
    return {}


def scrape_tiktok_posts(
    url: str,
    handle: Optional[str],
    limit: int = 20,
    skip_transcripts: bool = False,
) -> List[Dict[str, Any]]:
    """
    Search TikTok posts using clockworks/tiktok-scraper.
    Input: {"profiles": [url], "resultsPerPage": limit}.
    """
    limit = min(max(1, int(limit)), 100)
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    
    run_input = {
        "profiles": [url],
        "resultsPerPage": limit,
        "resultsLimit": limit, # Optimization: Hard stop at limit
        "downloadSubtitles": False,
    }
    
    client = ApifyClient(token)
    run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input, timeout_secs=90)
    
    items: List[Dict[str, Any]] = []
    video_urls = []
    
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        source_url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url") or ""
        text = _extract_platform_caption(item, "tiktok")
        transcript = ""
        transcript_status = "pending"
        
        create_time_iso = item.get("createTimeISO")
        published_at = None
        if create_time_iso:
            published_at = create_time_iso
        elif item.get("createTime"):
             try:
                published_at = datetime.fromtimestamp(float(item["createTime"])).isoformat()
             except: pass

        content_id = item.get("id") or extract_content_id(source_url, "tiktok")
        title = extract_title_from_metadata(item, "tiktok", source_url, caption_override=text)
        
        user_meta = item.get("authorMeta") or {}
        creator_name = user_meta.get("name") or user_meta.get("nickName") or handle or "tiktok"
        
        metadata = {
            "likes": item.get("diggCount") or item.get("likes", 0),
            "comments": item.get("commentCount") or item.get("comments", 0),
            "shares": item.get("shareCount") or item.get("shares", 0),
            "views": item.get("playCount") or item.get("views", 0),
            "platform": "tiktok",
            "content_id": content_id,
            "canonical_url": source_url,
            "title": title,
            "author_id": user_meta.get("id"),
        }
        
        items.append({
            "creator_handle": creator_name,
            "platform": "tiktok",
            "content_type": "video",
            "source_url": source_url,
            "caption": text,
            "transcript": transcript,
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        video_urls.append(source_url)
        if len(items) >= limit:
            break
            
    if video_urls:
        print(f"[APIFY] TikTok deferred {len(video_urls)} videos to Whisper + AssemblyAI")

    return items


# Alias for scraper_router
search_tiktok_posts = scrape_tiktok_posts


# Legacy functions kept for backward compatibility
def search_instagram(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Legacy function - redirects to search_instagram_reels"""
    return search_instagram_reels(handle, limit=limit)

def search_youtube(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Scrape YouTube content for a creator handle"""
    url = f"https://youtube.com/@{handle.strip().lstrip('@')}"
    return search_youtube_channel(url, handle, limit=limit)

def scrape_youtube(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Alias for search_youtube"""
    return search_youtube(handle, limit)

def search_twitter(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search Twitter/X content for a creator handle"""
    return search_twitter_profile(handle, limit=limit)

def scrape_tiktok(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search TikTok content for a creator handle"""
    url = f"https://tiktok.com/@{handle.strip().lstrip('@')}"
    return scrape_tiktok_posts(url, handle, limit=limit)

def search_tiktok(handle: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Alias for scrape_tiktok"""
    return scrape_tiktok(handle, limit)

def search_all(handle: str, sources: List[str], limit: int = 10) -> List[Dict[str, Any]]:
    """Search from all requested sources in parallel"""
    # Enforce limit of 10
    limit = min(limit, 10)
    
    all_items = []
    
    # Define sources mapping
    search_funcs = {
        "instagram": search_instagram,
        "youtube": scrape_youtube,
        "twitter": search_twitter,
        "tiktok": search_tiktok,
    }
    
    futures_map = {}
    with ThreadPoolExecutor(max_workers=min(len(sources) or 1, 4)) as executor:
        for source in sources:
            if func := search_funcs.get(source):
                futures_map[executor.submit(func, handle, limit)] = source
                
        for future in as_completed(futures_map):
            source = futures_map[future]
            try:
                items = future.result()
                all_items.extend(items)
            except Exception as e:
                print(f"[SEARCH ERROR] {source} search failed: {e}")
                
    return all_items


def _scrape_youtube_videos(urls: List[str], creator_handle: str) -> List[Dict[str, Any]]:
    """Scrape specific YouTube videos."""
    token = get_apify_token()
    if not APIFY_AVAILABLE: return []
    
    # Normalize URLs to full watch URLs (apidojo often rejects youtu.be or other variants)
    run_urls = []
    for u in urls:
        vid = extract_content_id(u, "youtube")
        if vid:
            run_urls.append(f"https://www.youtube.com/watch?v={vid}")
        elif u.startswith("http"):
            run_urls.append(u)
            
    if not run_urls:
        return []

    # apidojo/youtube-scraper supports startUrls list
    run_input = {
        "startUrls": [{"url": run_url} for run_url in run_urls],
        "maxResults": len(run_urls),
        "maxResultStreams": 0,
        "maxItems": len(run_urls),
    }
    
    print(f"[CUSTOM] Scraping {len(run_urls)} YouTube videos...")
    client = ApifyClient(token)
    run = client.actor("apidojo/youtube-scraper").call(run_input=run_input, timeout_secs=300)
    
    # Parse results
    video_data = [] # (item, source_url)
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        vurl = item.get("url") or ""
        vid = item.get("id") or item.get("videoId") or extract_content_id(vurl, "youtube")
        
        if not vid: continue
        
        source_url = vurl or f"https://www.youtube.com/watch?v={vid}"
        video_data.append((item, source_url))

        
    items = []
    for item, source_url in video_data:
         title = extract_title_from_metadata(item, "youtube", source_url)
         caption = _extract_platform_caption(item, "youtube") or title
         transcript = ""
         transcript_status = "pending"
         
         published_at = item.get("uploadDate") or item.get("publishedAt")
         # Simple date check
         if isinstance(published_at, (int, float)):
            try: published_at = datetime.fromtimestamp(float(published_at)).isoformat()
            except: pass
         
         content_id = extract_content_id(source_url, "youtube")
         
         metadata = {
             "likes": item.get("likes") or item.get("likeCount", 0),
             "views": item.get("views") or item.get("viewCount", 0),
             "platform": "youtube",
             "content_id": content_id,
             "canonical_url": source_url,
             "title": title or (f"YouTube video: {content_id}" if content_id else "YouTube video"),
             "channelName": item.get("channelTitle") or item.get("channelName"),
         }
         
         items.append({
             "creator_handle": item.get("channelTitle") or creator_handle,
             "platform": "youtube",
             "content_type": "video",
             "source_url": source_url,
             "caption": caption,
             "transcript": transcript,
             "transcript_status": transcript_status,
             "published_at": published_at,
             "metadata": metadata
         })
         
    return items


def _scrape_tiktok_videos(urls: List[str], creator_handle: str) -> List[Dict[str, Any]]:
    token = get_apify_token()
    if not APIFY_AVAILABLE: return []
    
    print(f"[CUSTOM] Scraping {len(urls)} TikTok videos...")
    client = ApifyClient(token)
    # thenetaji/tiktok-post-scraper supports startUrls
    run_input = { "startUrls": urls, "resultsLimit": len(urls), "downloadSubtitles": False }
    run = client.actor("thenetaji/tiktok-post-scraper").call(run_input=run_input)
    
    items: List[Dict[str, Any]] = []
    # Collect URLs for recovery
    video_urls = []
    
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
         source_url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url") or ""
         text = _extract_platform_caption(item, "tiktok")
         
         items.append({
             "creator_handle": item.get("authorMeta", {}).get("name") or creator_handle,
             "platform": "tiktok",
             "content_type": "video",
             "source_url": source_url,
             "caption": text,
             "transcript": "",
             "transcript_status": "pending",
             "metadata": {
                 "platform": "tiktok", 
                 "title": text[:50] if text else "TikTok Video",
                 "content_id": item.get("id") or extract_content_id(source_url, "tiktok"),
                 "canonical_url": source_url,
                 "views": item.get("playCount", 0),
                 "likes": item.get("diggCount", 0)
             }
         })
         video_urls.append(source_url)
         
    if video_urls:
         print(f"[CUSTOM-TIKTOK] Deferred {len(video_urls)} videos to Whisper + AssemblyAI")
                 
    return items


def _scrape_instagram_reels_multi(urls: List[str], creator_handle: str) -> List[Dict[str, Any]]:
    token = get_apify_token()
    if not APIFY_AVAILABLE: return []
    
    print(f"[CUSTOM] Scraping {len(urls)} Instagram items...")
    client = ApifyClient(token)
    run_input = { "startUrls": [{"url": u} for u in urls], "resultsLimit": len(urls) }
    run = client.actor("apify/instagram-reel-scraper").call(run_input=run_input)
    
    items = []
    video_urls = []
    for item in client.dataset(_apify_dataset_id(run)).iterate_items():
        caption = _extract_platform_caption(item, "instagram")
        shortcode = item.get("shortCode", "") or item.get("shortcode", "") or item.get("id", "")

        if shortcode:
            source_url = f"https://instagram.com/reel/{shortcode}"
        else:
            source_url = item.get("url") or item.get("postUrl") or ""

        if not source_url:
            continue
            
        items.append({
            "creator_handle": item.get("ownerUsername") or creator_handle,
            "platform": "instagram",
            "content_type": "reel",
            "source_url": source_url,
            "caption": caption,
            "transcript": "",
            "transcript_status": "pending",
            "metadata": {
                "platform": "instagram",
                "title": caption[:50] if caption else "Instagram Content",
                "content_id": shortcode,
                "canonical_url": source_url,
                "views": item.get("viewsCount", 0),
                "likes": item.get("likesCount", 0)
            }
        })
        video_urls.append(source_url)
        
    if video_urls:
         print(f"[CUSTOM-INSTA] Deferred {len(video_urls)} Reels to Whisper + AssemblyAI")
                 
    return items


def scrape_custom_urls(urls: List[str], creator_handle: str = "custom", limit: int = 50) -> List[Dict[str, Any]]:
    """
    Scrape specific URLs by dispatching to appropriate platform logic.
    """
    if not urls:
        return []

    grouped = {"youtube": [], "instagram": [], "tiktok": [], "twitter": [], "linkedin": [], "unknown": []}
    
    for u in urls:
        if not u or not isinstance(u, str) or not u.startswith("http"):
            continue
        u = u.strip()
        if "youtube.com" in u or "youtu.be" in u:
            grouped["youtube"].append(u)
        elif "instagram.com" in u:
            grouped["instagram"].append(u)
        elif "tiktok.com" in u:
            grouped["tiktok"].append(u)
        elif "twitter.com" in u or "x.com" in u:
            grouped["twitter"].append(u)
        elif "linkedin.com" in u:
            grouped["linkedin"].append(u)
        else:
            grouped["unknown"].append(u)
            
    all_items = []
    
    # Process YouTube
    if grouped["youtube"]:
         try:
            items = _scrape_youtube_videos(grouped["youtube"], creator_handle)
            all_items.extend(items)
         except Exception as e:
            print(f"[CUSTOM] YouTube scrape failed: {e}")

    # Process TikTok
    if grouped["tiktok"]:
        try:
             items = _scrape_tiktok_videos(grouped["tiktok"], creator_handle)
             all_items.extend(items)
        except Exception as e:
             print(f"[CUSTOM] TikTok scrape failed: {e}")
             
    # Process Instagram
    if grouped["instagram"]:
        try:
             items = _scrape_instagram_reels_multi(grouped["instagram"], creator_handle)
             all_items.extend(items)
        except Exception as e:
             print(f"[CUSTOM] Instagram scrape failed: {e}")

    # For unknown, we could try a generic extractor or skip.
    if grouped["unknown"]:
        print(f"[CUSTOM] Skipping {len(grouped['unknown'])} unknown URLs: {grouped['unknown']}")

    return all_items
