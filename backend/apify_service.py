import os
import json
import re
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from backend.settings import settings
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


def extract_title_from_metadata(item: Dict[str, Any], platform: str, source_url: str, caption_override: Optional[str] = None) -> str:
    """Extract title from item metadata or derive from URL/caption."""
    # Priority 1: Use caption/text as the primary source for social posts (Twitter, FB, Insta, LinkedIn)
    # Social posts rarely have "titles", and if they do, they are often generic or IDs.
    caption = (
        caption_override or
        item.get("caption") or 
        item.get("text") or 
        item.get("desc") or 
        item.get("description") or 
        item.get("full_text") or 
        item.get("message") or
        item.get("display_text") or
        ""
    )
    if isinstance(caption, dict):
        caption = caption.get("text") or ""
        
    title_from_item = item.get("title") or item.get("name") or ""
    
    # Check if the title from item is generic or just an ID
    is_generic = False
    if title_from_item:
        clean_tit = str(title_from_item).strip().lower()
        # Numeric or hex-like IDs or platform-named generic strings
        # Increase robustness for long numeric strings often used in tweets
        if clean_tit.isdigit() or len(clean_tit) > 15 and re.match(r'^[a-f0-9_\-]+$', clean_tit):
            is_generic = True
        elif clean_tit in [platform.lower(), f"{platform.lower()} post", f"{platform.lower()} content", f"{platform.lower()} reel"]:
            is_generic = True

    # Use caption if title is missing or generic
    title = ""
    if not is_generic:
        title = title_from_item

    if not title and caption:
        # Clean up and truncate caption for title use
        # Remove hashtags and excessive whitespace
        clean_caption = re.sub(r'#\w+\s*', '', str(caption))
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
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            raw_count += 1
            # Extract caption
            caption = item.get("caption", "") or item.get("text", "") or item.get("description", "") or ""
            
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
                "transcript": "", # Will be filled in second pass
                "transcript_status": "missing",
                "published_at": published_at,
                "metadata": metadata,
            }
            
            items.append(normalized_item)
            video_urls.append(source_url)
            
            if len(items) >= limit:
                break
        
        # 2. Second Pass: Batch Extract Transcripts (Actual Spoken Word)
        if video_urls and not skip_transcripts:
            print(f"[APIFY] Instagram handle={handle} attempting transcript recovery for {len(video_urls)} Reels...")
            transcripts = _extract_social_transcripts(video_urls, token, platform="instagram")
            for it in items:
                vurl = it["source_url"]
                if vurl in transcripts:
                    it["transcript"] = transcripts[vurl]
                    it["transcript_status"] = "present"
        elif video_urls and skip_transcripts:
            print(f"[APIFY] Instagram handle={handle} skipping transcripts (deferred batch mode)")
                    
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


def _extract_transcripts_invideoiq(video_urls: List[str], token: str, language: str = "") -> Dict[str, str]:
    """
    Extract transcripts from multiple video URLs using invideoiq/video-transcript-scraper.
    Supports YouTube, TikTok, X/Twitter, Facebook, Instagram, Dailymotion, etc.
    Returns a dict mapping video_url -> transcript text.
    """
    transcripts = {}
    if not video_urls:
        return transcripts

    try:
        client = ApifyClient(token)
        run_input = {
            "video_urls": video_urls,
            "language": language,
            "best_effort": False,
            "proxy_country": "US",
            "get_yt_original_metadata": False,
        }

        print(f"[TRANSCRIPT] Extracting transcripts for {len(video_urls)} videos via invideoiq/video-transcript-scraper...")
        run = client.actor("invideoiq/video-transcript-scraper").call(
            run_input=run_input,
            timeout_secs=300,
        )

        for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
            vurl = (
                result_item.get("video_url") or
                result_item.get("url") or
                result_item.get("videoUrl") or
                ""
            )

            transcript = (
                result_item.get("transcript") or
                result_item.get("text") or
                result_item.get("captions") or
                result_item.get("subtitles") or
                result_item.get("content") or
                ""
            )

            # Handle list format (timestamped segments)
            if isinstance(transcript, list):
                if transcript and isinstance(transcript[0], dict):
                    transcript = " ".join([str(seg.get("text", "")) for seg in transcript])
                else:
                    transcript = " ".join([str(seg) for seg in transcript])

            t_str = str(transcript).strip()

            if vurl and t_str:
                if len(t_str) < 500 and ("sign in" in t_str.lower() or "confirm you're not a bot" in t_str.lower()):
                    print(f"[TRANSCRIPT] Skipping bot-block message for {vurl}")
                    continue
                transcripts[vurl] = t_str

        print(f"[TRANSCRIPT] invideoiq extracted {len(transcripts)}/{len(video_urls)} transcripts")
    except Exception as e:
        print(f"[TRANSCRIPT] invideoiq extraction failed: {e}")

    return transcripts


def batch_extract_all_transcripts(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Post-scrape batch transcript extraction for ALL platforms in ONE call.
    
    This is the key performance optimization: instead of each platform making
    separate invideoiq actor calls (4+ runs × ~3s startup = 12s+ overhead),
    we batch ALL video URLs into a SINGLE invideoiq call.
    
    Flow:
    1. Collect all video URLs from items that need transcripts
    2. ONE invideoiq call for ALL URLs (YouTube, TikTok, Instagram, etc.)
    3. YouTube-only: karamelo fallback for any still-missing YouTube URLs
    4. Distribute transcripts back to items
    
    Args:
        items: List of scraped items from all platforms
    
    Returns:
        The same items list with transcripts filled in
    """
    if not items:
        return items
    
    token = get_apify_token()
    if not token:
        print("[BATCH-TRANSCRIPT] No Apify token, skipping transcript extraction")
        return items
    
    # 1. Collect all video URLs that need transcripts
    video_platforms = {"youtube", "youtube_shorts", "tiktok", "instagram"}
    video_items = []
    all_video_urls = []
    seen_urls = set()
    
    for it in items:
        platform = it.get("platform", "")
        # Normalize platform for matching
        plat_key = platform.lower().replace(" ", "_")
        
        # Only attempt transcript extraction for video-based platforms
        if plat_key in video_platforms:
            url = it.get("source_url", "")
            if url and it.get("transcript_status") != "present" and url not in seen_urls:
                video_items.append(it)
                all_video_urls.append(url)
                seen_urls.add(url)
    
    if not all_video_urls:
        print("[BATCH-TRANSCRIPT] No video URLs need transcripts")
        return items
    
    # Adaptive timeout: ~30s per video, min 60s, max 300s
    timeout = min(max(len(all_video_urls) * 30, 60), 300)
    print(f"[BATCH-TRANSCRIPT] Extracting transcripts for {len(all_video_urls)} videos across all platforms (timeout={timeout}s)...")
    
    # 2. ONE invideoiq call for everything
    all_transcripts = {}
    try:
        all_transcripts = _extract_transcripts_invideoiq(all_video_urls, token)
        print(f"[BATCH-TRANSCRIPT] invideoiq: {len(all_transcripts)}/{len(all_video_urls)} transcripts")
    except Exception as e:
        print(f"[BATCH-TRANSCRIPT] invideoiq failed: {e}")
    
    # 3. YouTube-only fallback: karamelo for any still-missing YouTube URLs
    youtube_missing = [
        url for url in all_video_urls
        if url not in all_transcripts and ("youtube.com" in url or "youtu.be" in url)
    ]
    
    if youtube_missing:
        print(f"[BATCH-TRANSCRIPT] Karamelo fallback for {len(youtube_missing)} YouTube URLs...")
        try:
            client = ApifyClient(token)
            run = client.actor("karamelo/youtube-transcripts").call(
                run_input={"urls": youtube_missing, "subtitlesLanguage": "en"},
                timeout_secs=120,
            )
            for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
                vurl = result_item.get("videoUrl") or result_item.get("url") or ""
                vid = result_item.get("videoId") or result_item.get("id") or ""
                
                transcript = (
                    result_item.get("transcript") or
                    result_item.get("text") or 
                    result_item.get("captions") or
                    result_item.get("body") or ""
                )
                if isinstance(transcript, list):
                    if transcript and isinstance(transcript[0], dict):
                        transcript = " ".join([str(c.get("text", "")) for c in transcript])
                    else:
                        transcript = " ".join([str(c) for c in transcript])
                
                t_str = str(transcript).strip()
                if t_str and (len(t_str) >= 500 or "sign in" not in t_str.lower()):
                    # Match by video ID since karamelo may return different URL format
                    if not vid and vurl:
                        vid = extract_content_id(vurl, "youtube")
                    matched = False
                    if vid:
                        for orig_url in youtube_missing:
                            orig_vid = extract_content_id(orig_url, "youtube")
                            if orig_vid and orig_vid == vid:
                                all_transcripts[orig_url] = t_str
                                matched = True
                                break
                    if not matched and vurl and vurl in youtube_missing:
                        all_transcripts[vurl] = t_str
            
            yt_recovered = sum(1 for u in youtube_missing if u in all_transcripts)
            if yt_recovered:
                print(f"[BATCH-TRANSCRIPT] Karamelo recovered {yt_recovered}/{len(youtube_missing)} YouTube transcripts")
        except Exception as e:
            print(f"[BATCH-TRANSCRIPT] Karamelo fallback failed: {e}")
    
    # 4. Distribute transcripts back to items
    transcripts_applied = 0
    for it in items:
        url = it.get("source_url", "")
        if url in all_transcripts:
            it["transcript"] = all_transcripts[url]
            it["transcript_status"] = "present"
            transcripts_applied += 1
    
    print(f"[BATCH-TRANSCRIPT] Applied {transcripts_applied}/{len(all_video_urls)} transcripts total")
    return items


def _extract_youtube_transcripts(video_urls: List[str], token: str) -> Dict[str, str]:
    """
    Extract transcripts from multiple YouTube videos.
    Primary: invideoiq/video-transcript-scraper, Fallbacks: karamelo, pintostudio.
    Returns a dict mapping video_url -> transcript.
    """
    # Map from video_id -> transcript for robust matching
    transcripts_by_id = {}
    if not video_urls:
        return {}
    
    # helper to get ID from any URL
    def get_vid(u):
        return extract_content_id(u, "youtube")

    # Primary: invideoiq/video-transcript-scraper (multi-platform, high quality)
    try:
        ivq = _extract_transcripts_invideoiq(video_urls, token)
        for url in video_urls:
            vid = get_vid(url)
            if url in ivq and vid:
                transcripts_by_id[vid] = ivq[url]
        if transcripts_by_id:
            print(f"[YOUTUBE] invideoiq primary: {len(transcripts_by_id)}/{len(video_urls)} transcripts")
    except Exception as e:
        print(f"[YOUTUBE] invideoiq primary failed, falling back: {e}")

    # Fallback 1: karamelo/youtube-transcripts (only for videos still missing)
    _karamelo_urls = [u for u in video_urls if get_vid(u) not in transcripts_by_id]
    if not _karamelo_urls:
        return {url: transcripts_by_id[get_vid(url)] for url in video_urls if get_vid(url) in transcripts_by_id}

    try:
        client = ApifyClient(token)
        run_input = {
            "urls": _karamelo_urls,
            "subtitlesLanguage": "en",
        }
        print(f"[YOUTUBE] Fallback karamelo for {len(_karamelo_urls)} remaining videos...")
        run = client.actor("karamelo/youtube-transcripts").call(run_input=run_input, timeout_secs=180)
        
        count = 0
        for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
            # Try to find video ID from result fields
            vurl = result_item.get("videoUrl") or result_item.get("url") or ""
            vid = result_item.get("videoId") or result_item.get("id") or get_vid(vurl)
            
            # Extract transcript text - check all possible names
            transcript = (
                result_item.get("transcript") or 
                result_item.get("text") or 
                result_item.get("captions") or 
                result_item.get("body") or 
                result_item.get("content") or 
                ""
            )
            
            if isinstance(transcript, list):
                if transcript and isinstance(transcript[0], dict):
                    transcript = " ".join([str(c.get("text", "")) for c in transcript])
                else:
                    transcript = " ".join([str(c) for c in transcript])
            
            if vid and transcript and str(transcript).strip():
                t_str = str(transcript).strip()
                # Skip error messages that look like transcripts (usually short sentences)
                if len(t_str) < 500 and ("sign in" in t_str.lower() or "confirm you're not a bot" in t_str.lower()):
                    print(f"[YOUTUBE] Skipping bot-block message for {vid}")
                    continue
                transcripts_by_id[vid] = t_str
                count += 1
            
        if count > 0:
            print(f"[YOUTUBE] Extracted {count}/{len(video_urls)} transcripts via karamelo")
    except Exception as e:
        print(f"[YOUTUBE] Karamelo fallback failed: {e}")

    # Fallback 2: pintostudio for any still missing IDs
    remaining_urls = [u for u in video_urls if get_vid(u) not in transcripts_by_id]
    if remaining_urls:
        try:
            print(f"[YOUTUBE] Falling back to pintostudio for missing IDs: {[get_vid(u) for u in remaining_urls]}")
            client = ApifyClient(token)
            for url in remaining_urls:
                try:
                    # pintostudio takes "videoUrl"
                    run = client.actor("pintostudio/youtube-transcript-scraper").call(
                        run_input={"videoUrl": url, "language": "en"}, 
                        timeout_secs=60
                    )
                    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                        vid = item.get("videoId") or item.get("id") or get_vid(url)
                        t = (
                            item.get("transcript") or 
                            item.get("text") or 
                            item.get("captions") or 
                            item.get("body") or 
                            ""
                        )
                        if t:
                            if isinstance(t, list):
                                t = " ".join([str(c.get("text", c)) if isinstance(c, dict) else str(c) for c in t])
                            
                            t_str = str(t).strip()
                            if len(t_str) < 500 and ("sign in" in t_str.lower() or "confirm you're not a bot" in t_str.lower()):
                                continue
                                
                            transcripts_by_id[vid] = t_str
                            print(f"[YOUTUBE] Successfully recovered transcript for {vid} via pintostudio")
                            break
                except Exception as e:
                    print(f"[YOUTUBE] Fallback failed for {url}: {e}")
        except Exception as e:
            print(f"[YOUTUBE] Fallback extraction loop failed: {e}")
    
    # Convert back to mapping from input URLs to transcripts
    final_mapping = {}
    for url in video_urls:
        vid = get_vid(url)
        if vid in transcripts_by_id:
            final_mapping[url] = transcripts_by_id[vid]
            
    return final_mapping


def search_youtube_channel(
    url: str,
    handle: Optional[str],
    limit: int = 10,
    time_filter: Optional[Dict[str, Any]] = None,
    youtube_shorts_only: bool = False,
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
    
    # If targeting shorts only, ensure the URL points to the shorts tab
    target_url = url
    if youtube_shorts_only:
        if "/videos" in target_url:
            target_url = target_url.replace("/videos", "/shorts")
        elif "/shorts" not in target_url:
            target_url = target_url.rstrip("/") + "/shorts"
            
    # Optimization: Use the actor's native filtering.
    run_input = {
        "startUrls": [target_url],
        "maxResults": limit if not youtube_shorts_only else 0,
        "maxResultsShorts": limit if youtube_shorts_only else 0,
        "maxResultStreams": 0,
        "maxItems": limit,
        "sortVideosBy": "NEWEST",
    }
    if date_expr:
        run_input["oldestPostDate"] = date_expr
    
    print(f"[YOUTUBE] Starting apidojo/youtube-scraper (Surgical) with limit={limit} shorts_only={youtube_shorts_only}")
        
    client = ApifyClient(token)
    # Start the actor but don't wait for it to finish (surgical abort strategy)
    run = client.actor("apidojo/youtube-scraper").start(run_input=run_input)
    run_id = run["id"]
    dataset_id = run["defaultDatasetId"]
    
    # Poll for results and abort as soon as we have enough
    video_data = []
    start_time = time.time()
    while len(video_data) < limit:
        # Check if run finished naturally
        run_info = client.run(run_id).get()
        status = run_info.get("status")
        
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
    
    # Batch extract transcripts for all videos (unless deferred)
    video_urls = [vsurl for _, vsurl in video_data]
    if skip_transcripts:
        print(f"[YOUTUBE] Skipping transcripts (deferred batch mode) for {len(video_urls)} videos")
        transcripts_dict = {}
    else:
        transcripts_dict = _extract_youtube_transcripts(video_urls, token)
    
    # Second pass: build items with transcripts
    items = []
    for item, source_url in video_data:
        title = item.get("title") or ""
        desc = item.get("description") or ""
        caption = desc or title
        
        # Get transcript from batch extraction or fallback
        transcript = transcripts_dict.get(source_url, "")
        if not transcript:
            # Fallback to any transcript/subtitle from search
            transcript = item.get("transcript") or item.get("subtitles") or item.get("caption", "") or ""
        
        # When transcripts were deferred, mark as "pending" so batch extraction picks them up
        if skip_transcripts:
            transcript_status = "pending"
        else:
            transcript_status = "present" if transcript and str(transcript).strip() else "missing"
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
        
        # Extract content_id and title for source fidelity
        content_id = extract_content_id(source_url, "youtube")
        # Ensure we have a descriptive title
        final_title = title or f"YouTube Video {content_id}"
        if "/shorts/" in source_url:
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
        if youtube_shorts_only and not is_shorts:
            continue
        if not youtube_shorts_only and is_shorts:
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
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        tid = item.get("id") or item.get("tweetId") or ""
        user = item.get("userName") or item.get("username") or item.get("author") or h
        source_url = item.get("url") or (f"https://twitter.com/{user}/status/{tid}" if tid else "")
        text = item.get("text") or item.get("full_text") or item.get("content", "") or ""
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
        "username": url,
        "totalPostsToScrape": limit,
    }
    
    client = ApifyClient(token)
    run = client.actor("apimaestro/linkedin-profile-posts").call(run_input=run_input)
    items: List[Dict[str, Any]] = []
    
    # Iterate dataset. Note: Output might be Single Item containing 'data.posts' array, or multiple items.
    for dataset_item in client.dataset(run["defaultDatasetId"]).iterate_items():
        # Check if this item is a wrapper containing posts
        posts = []
        if dataset_item.get("data") and isinstance(dataset_item["data"].get("posts"), list):
             posts = dataset_item["data"]["posts"]
        else:
             # Assume item IS the post (if actor behavior differs)
             posts = [dataset_item]

        for item in posts:
            source_url = item.get("url") or item.get("postUrl") or ""
            text = item.get("text") or item.get("caption") or ""
            
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
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        source_url = item.get("url") or item.get("postUrl") or item.get("link", "") or ""
        text = item.get("text") or item.get("message") or item.get("caption", "") or ""
        transcript = item.get("transcript") or item.get("captionText", "") or text
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
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        permalink = item.get("permalink") or item.get("url") or ""
        if permalink and not permalink.startswith("http"):
            permalink = f"https://reddit.com{permalink}"
        source_url = permalink or ""
        text = item.get("selftext") or item.get("body") or item.get("title", "") or ""
        title = item.get("title") or ""
        caption = f"{title}\n\n{text}".strip() if text else title
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
    """
    Extract transcripts from multiple social videos (TikTok, Instagram, FB, etc.).
    Primary: invideoiq/video-transcript-scraper, Fallback: tictechid/anoxvanzi-Transcriber.
    Returns a dict mapping video_url -> transcript.
    """
    transcripts = {}
    if not video_urls:
        return transcripts

    # Primary: invideoiq/video-transcript-scraper (multi-platform)
    try:
        transcripts = _extract_transcripts_invideoiq(video_urls, token)
        if transcripts:
            print(f"[{platform.upper()}] invideoiq primary: {len(transcripts)}/{len(video_urls)} transcripts")
    except Exception as e:
        print(f"[{platform.upper()}] invideoiq primary failed: {e}")

    # Fallback: tictechid/anoxvanzi-Transcriber (for any still missing)
    remaining = [u for u in video_urls if u not in transcripts]
    if remaining:
        try:
            client = ApifyClient(token)
            run_input = {
                "start_urls": "\n".join(remaining),
            }
            print(f"[{platform.upper()}] Fallback tictechid for {len(remaining)} remaining videos...")
            run = client.actor("tictechid/anoxvanzi-Transcriber").call(run_input=run_input, timeout_secs=180)

            for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
                video_url = result_item.get("videoUrl") or result_item.get("url") or ""
                transcript = result_item.get("transcript") or result_item.get("text") or result_item.get("subtitle") or ""
                if video_url and transcript and str(transcript).strip():
                    transcripts[video_url] = str(transcript).strip()
            print(f"[{platform.upper()}] Total transcripts recovered: {len(transcripts)}/{len(video_urls)}")
        except Exception as e:
            print(f"[{platform.upper()}] tictechid fallback failed: {e}")

    return transcripts


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
    
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        source_url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url") or ""
        text = item.get("text") or item.get("desc") or ""
        
        # Initial status
        transcript_status = "missing"
        
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
            "transcript": text, # Fallback to caption
            "transcript_status": transcript_status,
            "published_at": published_at,
            "metadata": metadata,
        })
        video_urls.append(source_url)
        if len(items) >= limit:
            break
            
    # Batch Extract Transcripts for TikTok (unless deferred)
    if video_urls and not skip_transcripts:
        print(f"[APIFY] TikTok attempting transcript recovery for {len(video_urls)} videos...")
        transcripts_recovered = _extract_social_transcripts(video_urls, token, platform="tiktok")
        for it in items:
            vurl = it["source_url"]
            if vurl in transcripts_recovered:
                it["transcript"] = transcripts_recovered[vurl]
                it["transcript_status"] = "present"
    elif video_urls and skip_transcripts:
        print(f"[APIFY] TikTok skipping transcripts (deferred batch mode)")

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
        "startUrls": run_urls,
        "maxResults": len(run_urls),
        "maxResultStreams": 0,
        "maxItems": len(run_urls),
    }
    
    print(f"[CUSTOM] Scraping {len(run_urls)} YouTube videos...")
    client = ApifyClient(token)
    run = client.actor("apidojo/youtube-scraper").call(run_input=run_input, timeout_secs=300)
    
    # Parse results
    video_data = [] # (item, source_url)
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        vurl = item.get("url") or ""
        vid = item.get("id") or item.get("videoId") or extract_content_id(vurl, "youtube")
        
        if not vid: continue
        
        source_url = vurl or f"https://www.youtube.com/watch?v={vid}"
        video_data.append((item, source_url))

        
    # Get transcripts
    found_urls = [u for _, u in video_data]
    transcripts = _extract_youtube_transcripts(found_urls, token)
    
    items = []
    for item, source_url in video_data:
         title = item.get("title") or ""
         desc = item.get("description") or ""
         caption = desc or title
         transcript = transcripts.get(source_url, "") or (item.get("caption") if item.get("caption") else "")
         if not transcript:
             transcript = item.get("transcript") or item.get("subtitles") or ""
             
         transcript_status = "present" if transcript and str(transcript).strip() else "missing"
         
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
             "title": title or f"YouTube Video {content_id}",
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
    
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
         source_url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url") or ""
         text = item.get("text") or item.get("desc") or ""
         
         items.append({
             "creator_handle": item.get("authorMeta", {}).get("name") or creator_handle,
             "platform": "tiktok",
             "content_type": "video",
             "source_url": source_url,
             "caption": text,
             "transcript": text,
             "transcript_status": "missing",
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
         print(f"[CUSTOM-TIKTOK] Attempting transcript recovery for {len(video_urls)} videos...")
         trans_map = _extract_social_transcripts(video_urls, token, platform="tiktok")
         for it in items:
             if it["source_url"] in trans_map:
                 it["transcript"] = trans_map[it["source_url"]]
                 it["transcript_status"] = "present"
                 
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
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        # Reuse logic from search_instagram_reels normalization
        transcript = item.get("transcript", "") or item.get("subtitles", "") or item.get("captionText", "") or ""
        caption = item.get("caption", "") or item.get("text", "") or item.get("description", "") or ""
        shortcode = item.get("shortCode", "") or item.get("shortcode", "") or item.get("id", "")
        
        if shortcode: source_url = f"https://instagram.com/reel/{shortcode}"
        else: surl_raw = item.get("url") or item.get("postUrl") or ""
        
        if not source_url: continue
            
        items.append({
            "creator_handle": item.get("ownerUsername") or creator_handle,
            "platform": "instagram",
            "content_type": "reel",
            "source_url": source_url,
            "caption": caption,
            "transcript": transcript,
            "transcript_status": "present" if transcript else "missing",
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
         print(f"[CUSTOM-INSTA] Attempting transcript recovery for {len(video_urls)} Reels...")
         trans_map = _extract_social_transcripts(video_urls, token, platform="instagram")
         for it in items:
             if it["source_url"] in trans_map:
                 it["transcript"] = trans_map[it["source_url"]]
                 it["transcript_status"] = "present"
                 
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
