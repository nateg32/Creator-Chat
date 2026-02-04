import os
import json
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs
from .settings import settings

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


def extract_title_from_metadata(item: Dict[str, Any], platform: str, source_url: str) -> str:
    """Extract title from item metadata or derive from URL."""
    title = item.get("title") or item.get("name") or ""
    if not title:
        # Try to derive from URL or use platform-specific defaults
        if platform == "youtube":
            content_id = extract_content_id(source_url, platform)
            title = f"YouTube video: {content_id}" if content_id else "YouTube video"
        elif platform == "instagram":
            content_id = extract_content_id(source_url, platform)
            title = f"Instagram reel: {content_id}" if content_id else "Instagram content"
        elif platform == "twitter":
            content_id = extract_content_id(source_url, platform)
            title = f"Tweet: {content_id}" if content_id else "Twitter post"
        else:
            title = f"{platform.title()} content"
    return title

def search_instagram_reels(handle: str, reel_id: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Scrape Instagram reels using Apify instagram-reel-scraper actor.
    
    Args:
        handle: Instagram username
        reel_id: Optional specific reel ID to search
        limit: Max number of reels (enforced to 10)
    
    Returns:
        List of normalized reel items with transcript handling
    """
    # Enforce limit of 10
    limit = min(limit, 10)

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
        
        # Run the Instagram reel scraper actor
        run = client.actor("apify/instagram-reel-scraper").call(run_input=run_input)
        
        # Wait for the run to finish and get results
        items = []
        raw_count = 0
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            raw_count += 1
            # Extract transcript/subtitles
            transcript = item.get("transcript", "") or item.get("subtitles", "") or item.get("captionText", "") or ""
            transcript_status = "present" if transcript and transcript.strip() else "missing"
            
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
            title = extract_title_from_metadata(item, "instagram", source_url)
            
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
            if raw_count == 1:
                print(f"[APIFY] Instagram first raw item keys: {list(item.keys())[:15]}", flush=True)
            normalized_item = {
                "creator_handle": handle,
                "content_type": "reel",
                "source_url": source_url,
                "caption": caption,
                "transcript": transcript,
                "transcript_status": transcript_status,
                "published_at": published_at,
                "metadata": metadata,
            }
            
            items.append(normalized_item)
            
            if len(items) >= limit:
                break
        
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


def _extract_youtube_transcripts(video_urls: List[str], token: str) -> Dict[str, str]:
    """
    Extract transcripts from multiple YouTube videos using karamelo/youtube-transcripts.
    Returns a dict mapping video_url -> transcript.
    Uses video IDs for robust mapping.
    """
    # Map from video_id -> transcript for robust matching
    transcripts_by_id = {}
    if not video_urls:
        return {}
    
    # helper to get ID from any URL
    def get_vid(u):
        return extract_content_id(u, "youtube")

    try:
        client = ApifyClient(token)
        # Primary: karamelo/youtube-transcripts (supports multiple URLs)
        run_input = {
            "urls": video_urls,
            "subtitlesLanguage": "en",
        }
        print(f"[YOUTUBE] Extracting transcripts for {len(video_urls)} videos via karamelo...")
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
        print(f"[YOUTUBE] Batch extraction with karamelo failed: {e}")

    # Fallback to pintostudio for any still missing IDs
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
) -> List[Dict[str, Any]]:
    """
    Scrape YouTube channel/videos using apidojo/youtube-scraper.
    Input: startUrls (list of strings), maxResults, maxResultsShorts.
    """
    limit = min(max(1, int(limit)), 50)
    token = get_apify_token()
    if not APIFY_AVAILABLE:
        raise ImportError("apify-client package is not installed. Run: pip install apify-client")
    date_expr = _time_filter_to_date_expr(time_filter)
    
    # If targeting shorts only, ensure the URL points to the shorts tab
    target_url = url
    if youtube_shorts_only:
        if "/videos" in target_url:
            target_url = target_url.replace("/videos", "/shorts")
        elif "/shorts" not in target_url:
            # Append /shorts but handle trailing slash
            target_url = target_url.rstrip("/") + "/shorts"
            
    # apidojo/youtube-scraper expects startUrls as a list of strings
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
    
    print(f"[YOUTUBE] Calling apidojo/youtube-scraper with limit={limit} shorts_only={youtube_shorts_only} input={json.dumps(run_input)}")
        
    client = ApifyClient(token)
    # Adding a timeout to the call to prevent infinite hanging runs
    run = client.actor("apidojo/youtube-scraper").call(run_input=run_input, timeout_secs=180)
    
    # First pass: collect all video URLs
    video_data = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        vid = item.get("id") or item.get("videoId") or ""
        vurl = item.get("url") or ""
        
        # Robust ID validation: must be exactly 11 valid chars
        if not (vid and len(str(vid)) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', str(vid))):
            vid = extract_content_id(vurl, "youtube")
            
        source_url = vurl or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
        
        if not vid or vid == "watch":
            continue
            
        video_data.append((item, source_url))
        if len(video_data) >= int(limit * 1.5): # allow a tiny bit of headroom for filtering
            break
    
    # Batch extract transcripts for all videos
    video_urls = [vsurl for _, vsurl in video_data]
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
        
        is_shorts = "/shorts/" in source_url or "short" in str(item.get("type", "")).lower()
        if youtube_shorts_only and not is_shorts:
            continue
            
        creator = handle or item.get("channelName") or item.get("channelTitle") or "youtube"
        items.append({
            "creator_handle": creator,
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
    limit = min(max(1, int(limit)), 100)
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
        title = extract_title_from_metadata(item, "twitter", source_url)
        
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
            title = extract_title_from_metadata(item, "linkedin", source_url)
            
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
        title = extract_title_from_metadata(item, "facebook", source_url)
        
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
        title_for_meta = title or extract_title_from_metadata(item, "reddit", source_url)
        
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


def _extract_tiktok_transcripts(video_urls: List[str], token: str) -> Dict[str, str]:
    """
    Extract transcripts from multiple TikTok videos using agentx/tiktok-transcript.
    Returns a dict mapping video_url -> transcript.
    """
    transcripts = {}
    if not video_urls:
        return transcripts
    
    try:
        client = ApifyClient(token)
        run_input = {
            "videoUrls": video_urls,
        }
        run = client.actor("tictechid/anoxvanzi-Transcriber").call(run_input=run_input)
        # Map transcripts to video URLs
        for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
            video_url = result_item.get("videoUrl") or result_item.get("url") or ""
            transcript = result_item.get("transcript") or result_item.get("text") or result_item.get("subtitle") or ""
            if video_url and transcript and str(transcript).strip():
                transcripts[video_url] = str(transcript).strip()
        print(f"[TIKTOK] Extracted {len(transcripts)}/{len(video_urls)} transcripts")
    except Exception as e:
        print(f"[TIKTOK] Failed to extract transcripts: {e}")
        # Fallback: try individual calls
        for video_url in video_urls:
            try:
                client = ApifyClient(token)
                run_input = {"videoUrls": [video_url]}
                run = client.actor("tictechid/anoxvanzi-Transcriber").call(run_input=run_input)
                for result_item in client.dataset(run["defaultDatasetId"]).iterate_items():
                    transcript = result_item.get("transcript") or result_item.get("text") or result_item.get("subtitle") or ""
                    if transcript and str(transcript).strip():
                        transcripts[video_url] = str(transcript).strip()
                        break
            except Exception as e2:
                print(f"[TIKTOK] Failed to extract transcript for {video_url}: {e2}")
    
    return transcripts


def scrape_tiktok_posts(
    url: str,
    handle: Optional[str],
    limit: int = 20,
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
        "downloadSubtitles": False, # Text caption is usually sufficient
    }
    
    client = ApifyClient(token)
    run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
    
    items: List[Dict[str, Any]] = []
    
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        source_url = item.get("webVideoUrl") or item.get("videoUrl") or item.get("url") or ""
        text = item.get("text") or item.get("desc") or ""
        
        # Transcript handling (using caption as transcript for now)
        transcript = text
        transcript_status = "present" if transcript and str(transcript).strip() else "missing"
        
        create_time_iso = item.get("createTimeISO")
        published_at = None
        if create_time_iso:
            published_at = create_time_iso
        elif item.get("createTime"):
             try:
                published_at = datetime.fromtimestamp(float(item["createTime"])).isoformat()
             except: pass

        content_id = item.get("id") or extract_content_id(source_url, "tiktok")
        title = extract_title_from_metadata(item, "tiktok", source_url)
        
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
            "content_type": "video",
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
    """Search from all requested sources"""
    # Enforce limit of 10
    limit = min(limit, 10)
    
    all_items = []
    
    if "instagram" in sources:
        all_items.extend(search_instagram(handle, limit))
    
    if "youtube" in sources:
        all_items.extend(scrape_youtube(handle, limit))
    
    if "twitter" in sources:
        all_items.extend(search_twitter(handle, limit))
    
    if "tiktok" in sources:
        all_items.extend(search_tiktok(handle, limit))
    
    return all_items
