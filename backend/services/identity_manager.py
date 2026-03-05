import re
import logging
import httpx
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def _resolve_youtube_channel_id(url: str) -> Optional[str]:
    """
    Perform a fast lightweight fetch to resolve a canonical UC... channel ID
    from a youtube.com/@handle URL without BeautifulSoup.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=5.0) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                html = resp.text
                # Look for the canonical channel URL meta tag or internal JS variables
                match = re.search(r'\"channelId\":\"(UC[0-9A-Za-z_-]{20,})\"', html)
                if match:
                    return match.group(1)
                
                # Fallback to alternate meta tags
                match = re.search(r'<meta[^>]*itemprop=\"channelId\"[^>]*content=\"(UC[0-9A-Za-z_-]{20,})\"', html)
                if match:
                    return match.group(1)
    except Exception as e:
        logger.warning(f"Failed to fetch YouTube channel ID for URL {url}: {e}")
    return None


from urllib.parse import urlparse

def _normalize_social_url(url: str) -> str:
    # Normalized: strip, lowercase, no trailing slash, clear query params
    u = url.strip().lower()
    if "?" in u:
        u = u.split("?")[0]
    u = u.rstrip("/")
    u = u.replace("twitter.com", "x.com")
    return u

def _is_generic_handle(handle: str) -> bool:
    h = handle.lower().strip()
    if len(h) < 4:
        return True
    digits = sum(c.isdigit() for c in h)
    if digits / max(1, len(h)) > 0.5:
        return True
    common_words = {"official", "media", "team", "global", "page", "contact", "info", "admin"}
    if h in common_words:
        return True
    return False

def _grade_social_identity(candidate_url: str, platform: str, creator_profile: Dict[str, Any], candidate_title: str = "", candidate_snippet: str = "") -> Tuple[float, str]:
    """
    Returns (confidence_score, reason_string).
    Deterministic scoring only. No LLM calls.
    """
    url = _normalize_social_url(candidate_url)
    
    # 1. Platform Constraint Layer (Rejectlist / Allowlist)
    if platform == "instagram":
        if any(bad in url for bad in ["/p/", "/reel/", "/tv/"]): return (0.0, "Rejected: Post/Reel URL")
    elif platform == "tiktok":
        if "/video/" in url: return (0.0, "Rejected: Video URL")
    elif platform == "youtube":
        if "watch?v=" in url or "youtu.be/" in url: return (0.0, "Rejected: Watch URL")
    elif platform == "x":
        if "/status/" in url: return (0.0, "Rejected: Status URL")
    elif platform == "linkedin":
        if "/posts/" in url: return (0.0, "Rejected: Post URL")
    elif platform == "facebook":
        if "/posts/" in url or "/groups/" in url: return (0.0, "Rejected: Post/Group URL")

    score = 0
    reasons = []

    # Try extracting candidate handle
    parsed = urlparse(url if "://" in url else f"https://{url}")
    path_parts = [p for p in parsed.path.split("/") if p]
    candidate_handle = path_parts[-1].lstrip("@") if path_parts else ""
    if candidate_handle:
        if _is_generic_handle(candidate_handle):
            score -= 4
            reasons.append("Generic Handle (-4)")
        else:
            # Canonical Root Check
            if len(path_parts) <= 2:
                score += 2
                reasons.append("Canonical Root (+2)")
            else:
                score -= 3
                reasons.append("Non-Root Path (-3)")

    # 2. Exact Handle Match (+3)
    target_handle = (creator_profile.get("handle") or "").lower().strip()
    pc = creator_profile.get("platform_configs") or {}
    pc_handle = (pc.get(platform, {}).get("handle") or "").lower().strip()
    
    if candidate_handle and (candidate_handle == target_handle or candidate_handle == pc_handle):
        score += 3
        reasons.append("Exact Handle Match (+3)")

    # 3. Name Match (+2)
    name = (creator_profile.get("name") or target_handle).lower()
    name_tokens = [t for t in name.split() if len(t) > 2]
    text_corpus = (candidate_title + " " + candidate_snippet).lower()
    
    if name_tokens and all(tok in text_corpus for tok in name_tokens):
        score += 2
        reasons.append("Name Match (+2)")
        
    if "official" in text_corpus:
        score += 1
        reasons.append("Official Keyword (+1)")

    # 4. Cross-Link Evidence (+2 to +4)
    yt_config = pc.get("youtube", {})
    yt_verified = yt_config.get("verified_url")
    yt_confidence = yt_config.get("social_confidence", 0)
    
    if platform != "youtube" and yt_verified and yt_confidence >= 0.85:
        try:
            with httpx.Client(follow_redirects=True, timeout=3.0) as client:
                resp = client.get(yt_verified) # Might be /about in the future
                if resp.status_code == 200:
                    html = resp.text.lower()
                    if candidate_handle.lower() in html or parsed.netloc in html:
                        score += 2
                        reasons.append("Cross-Link from YouTube (+2)")
                        # Note: Deep reciprocal/bi-directional (+4) would require fetching the candidate URL too
                        # For speed, we just do one-way from the verified source.
        except Exception as e:
            pass # Silent fail for fetch

    # Normalize out of 10.0
    final_score = max(0.0, min(1.0, score / 10.0))
    return (final_score, ", ".join(reasons) or "Neutral")

def autofill_creator_identity(creator_id: int, creator_profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process `creator_profile['platform_configs']` and heuristically attempt to 
    auto-fill missing canonical IDs/handles for enabled platforms.
    """
    configs = creator_profile.get("platform_configs", {})
    if not isinstance(configs, dict): return creator_profile
    changes_made = False

    # 1) YOUTUBE
    yt_config = configs.get("youtube", {})
    if isinstance(yt_config, dict) and yt_config.get("enabled"):
        url = (yt_config.get("url") or "").strip()
        channel_id = (yt_config.get("channel_id") or "").strip()
        handle = (yt_config.get("handle") or yt_config.get("username") or "").strip().lstrip("@")
        
        if url and not channel_id:
            uc_match = re.search(r'youtube\.com/channel/(UC[0-9A-Za-z_-]{20,})', url)
            if uc_match:
                channel_id = uc_match.group(1)
                yt_config["channel_id"] = channel_id
                changes_made = True
            elif "@" in url:
                handle_match = re.search(r'youtube\.com/@([A-Za-z0-9_-]+)', url)
                if handle_match and not handle:
                    handle = handle_match.group(1)
                    yt_config["handle"] = handle
                    changes_made = True
                
                resolved_id = _resolve_youtube_channel_id(url)
                if resolved_id:
                    channel_id = resolved_id
                    yt_config["channel_id"] = channel_id
                    changes_made = True
        
        candidate_url = f"https://www.youtube.com/channel/{channel_id}" if channel_id else (f"https://www.youtube.com/@{handle}" if handle else url)
        if candidate_url:
            confidence, reasons = _grade_social_identity(candidate_url, "youtube", creator_profile, "Official YouTube Channel", "")
            # Base logic defaults to 0.85+ if handle/channel_id matches directly from user setup
            if channel_id or handle:
                confidence = max(confidence, 0.9)
                
            existing_conf = yt_config.get("social_confidence", 0.0)
            is_user_provided = yt_config.get("social_source") == "user_provided"

            if confidence >= 0.85:
                if not is_user_provided and (confidence > existing_conf + 0.15 or not yt_config.get("verified_url")):
                    yt_config["verified_url"] = candidate_url
                    yt_config["social_source"] = "verified_search"
                    yt_config["social_confidence"] = confidence
                    changes_made = True
                    logger.info(f"IdentityAutoFill: platform=youtube handle={handle} confidence={confidence:.2f} action=saved reasons=[{reasons}]")
            elif confidence >= 0.6:
                logger.info(f"IdentityAutoFill: platform=youtube handle={handle} confidence={confidence:.2f} action=low_confidence_not_saved reasons=[{reasons}]")
            else:
                logger.debug(f"IdentityAutoFill: platform=youtube action=skip confidence={confidence:.2f}")

        configs["youtube"] = yt_config

    if changes_made:
        creator_profile["platform_configs"] = configs

    return creator_profile
