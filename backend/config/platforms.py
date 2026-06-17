"""
Platforms-as-data config. Add a new platform = add one entry to PLATFORMS.
"""
from __future__ import annotations

import re
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse, urlunparse

# Time filter modes; exactly one active per platform.
TIME_MODES = ("since", "last_days", "all")
LAST_DAYS_OPTIONS = (7, 30, 90)

PLATFORMS: List[Dict[str, Any]] = [
    {
        "key": "instagram",
        "label": "Instagram",
        "icon": "instagram",
        "placeholder": "https://instagram.com/username or @username",
        "url_pattern": r"^(https?://(www\.)?instagram\.com/[\w.-]+/?|https?://instagram\.com/reel/[\w-]+/?|@?[\w.]+)$",
        "apify_actor": "apify/instagram-reel-scraper",
        "supports_since_date": False,
        "default_max_items": 10,
        "url_to_handle": "instagram",
    },
    {
        "key": "youtube",
        "label": "YouTube Videos",
        "icon": "youtube",
        "placeholder": "https://youtube.com/@handle or @handle",
        "url_pattern": r"^(https?://(www\.)?(youtube\.com|youtu\.be)/[^\s]+|@?[\w-]+)$",
        "apify_actor": "apidojo/youtube-scraper",
        "supports_since_date": True,
        "default_max_items": 10,
        "url_to_handle": "youtube",
    },
    {
        "key": "twitter",
        "label": "Twitter / X",
        "icon": "twitter",
        "placeholder": "https://x.com/username or @username",
        "url_pattern": r"^(https?://(www\.)?(twitter|x)\.com/[\w]+/?|@?[\w]+)$",
        "apify_actor": "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest",
        "supports_since_date": True,
        "default_max_items": 20,
        "url_to_handle": "twitter",
    },
    {
        "key": "linkedin",
        "label": "LinkedIn",
        "icon": "linkedin",
        "placeholder": "https://linkedin.com/in/username or https://ca.linkedin.com/in/username",
        "url_pattern": r"^https?://(?:[\w-]+\.)?linkedin\.com/(?:in|company)/[\w-]+/?$",
        "apify_actor": "supreme_coder/linkedin-post",
        "supports_since_date": False,
        "default_max_items": 20,
        "url_to_handle": "linkedin",
    },
    {
        "key": "facebook",
        "label": "Facebook",
        "icon": "facebook",
        "placeholder": "https://facebook.com/pagename",
        "url_pattern": r"^https?://(www\.)?(fb\.com|facebook\.com|m\.facebook\.com)/[\w.]+/?",
        "apify_actor": "apify/facebook-posts-scraper",
        "supports_since_date": True,
        "default_max_items": 20,
        "url_to_handle": "facebook",
    },
    {
        "key": "tiktok",
        "label": "TikTok",
        "icon": "tiktok",
        "placeholder": "https://www.tiktok.com/@username or @username",
        "url_pattern": r"^(https?://((www|m)\.)?tiktok\.com/@[\w.-]+/?|@?[\w.]+)$",
        "apify_actor": "thenetaji/tiktok-post-scraper",
        "supports_since_date": False,
        "default_max_items": 20,
        "url_to_handle": "tiktok",
    },
    {
        "key": "custom",
        "label": "Custom Links",
        "icon": "link",
        "placeholder": "Paste links to videos or posts (one per line)",
        "url_pattern": r".*",
        "apify_actor": "multiple",
        "supports_since_date": False,
        "default_max_items": 50,
        "url_to_handle": "custom",
    },
]


def get_platform(key: str) -> Optional[Dict[str, Any]]:
    for p in PLATFORMS:
        if p["key"] == key:
            return p
    return None


def _strip_tracking(u: str, *, preserve_trailing_slash: bool = False) -> str:
    try:
        parsed = urlparse(u)
        if parsed.path == "/":
            path = "/"
        elif preserve_trailing_slash and parsed.path.endswith("/"):
            path = parsed.path
        else:
            path = parsed.path.rstrip("/")
        return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))
    except Exception:
        return u


def _unwrap_instagram_redirect(u: str) -> str:
    try:
        parsed = urlparse(u if u.startswith("http") else "https://" + u)
        host = (parsed.netloc or "").lower()
        if "instagram.com" not in host:
            return u

        segments = [seg for seg in (parsed.path or "").split("/") if seg]
        if len(segments) < 2 or segments[0].lower() != "accounts" or segments[1].lower() != "login":
            return u

        query = parse_qs(parsed.query or "")
        next_target = next((str(value).strip() for value in query.get("next", []) if str(value).strip()), "")
        if not next_target:
            return u

        if next_target.startswith("/"):
            return f"https://{parsed.netloc}{next_target}"
        if not re.match(r"^https?://", next_target, re.IGNORECASE):
            next_target = f"https://{parsed.netloc}/{next_target.lstrip('/')}"

        target_parsed = urlparse(next_target)
        if "instagram.com" not in (target_parsed.netloc or "").lower():
            return u
        return next_target
    except Exception:
        return u


def normalize_url(url: str, platform_key: str) -> str:
    """Normalize URL: strip tracking params, enforce https, handle @handle input."""
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if not u:
        return ""

    # Custom platform: handle multi-line
    if platform_key == "custom":
        lines = [line.strip() for line in u.split('\n') if line.strip()]
        norm_lines = []
        for line in lines:
            if not line.startswith("http"):
                line = "https://" + line
            norm_lines.append(_strip_tracking(line))
        return "\n".join(norm_lines)

    # --- Bare @handle / handle → full URL (per-platform) ---
    if platform_key == "instagram" and re.match(r"^@?[\w.]+$", u):
        h = u.lstrip("@")
        return f"https://www.instagram.com/{h}/"

    if platform_key == "youtube" and re.match(r"^@?[\w.-]+$", u):
        h = u.lstrip("@")
        return f"https://www.youtube.com/@{h}/videos"

    if platform_key == "twitter" and re.match(r"^@?[\w]+$", u):
        h = u.lstrip("@")
        return f"https://x.com/{h}"

    if platform_key == "tiktok" and re.match(r"^@?[\w.]+$", u):
        h = u.lstrip("@")
        return f"https://www.tiktok.com/@{h}"

    # --- Platform-specific URL fixups ---
    if platform_key == "instagram":
        u = _unwrap_instagram_redirect(u)

    if platform_key == "tiktok":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if path:
                first = path.split("/", 1)[0]
                if first.startswith("@"):
                    return f"https://www.tiktok.com/{first}"
        except Exception:
            pass

    if platform_key == "twitter":
        # Unify twitter.com → x.com
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            host = (parsed.netloc or "").lower()
            if "twitter.com" in host:
                u = urlunparse((
                    parsed.scheme or "https",
                    host.replace("twitter.com", "x.com"),
                    parsed.path, "", "", "",
                ))
        except Exception:
            pass

    if platform_key == "youtube":
        # Ensure @handle URLs have the right tab suffix
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            segments = [seg for seg in path.split("/") if seg]
            if segments and segments[0].startswith("@"):
                handle_seg = segments[0]
                if len(segments) == 1:
                    u = f"https://www.youtube.com/{handle_seg}/videos"
                elif len(segments) == 2 and segments[1] in {"videos", "shorts", "featured", "streams", "playlists"}:
                    u = f"https://www.youtube.com/{handle_seg}/videos"
        except Exception:
            pass

    if not u.startswith("http"):
        u = "https://" + u
    # Instagram canonical profile URLs end in a trailing slash
    # (e.g. https://www.instagram.com/blakefakhoury/). Preserve it so we don't
    # accidentally produce a non-canonical form that fails downstream equality
    # checks against Instagram's own redirect targets.
    return _strip_tracking(u, preserve_trailing_slash=(platform_key == "instagram"))


def choose_valid_normalized_url(platform_key: str, requested_url: str, resolved_url: str = "") -> str:
    """
    Prefer the resolved URL when it can be normalized into a valid profile URL,
    otherwise keep the normalized requested URL.
    """
    normalized_requested = normalize_url(requested_url, platform_key)
    normalized_resolved = normalize_url(resolved_url, platform_key) if resolved_url else ""
    if normalized_resolved:
        ok, _ = validate_url(normalized_resolved, platform_key)
        if ok:
            return normalized_resolved
    return normalized_requested


def _path_matches_platform(parsed, platform_key: str) -> bool:
    host = (parsed.netloc or '').lower()
    path = (parsed.path or '').strip('/')

    if platform_key == 'youtube':
        if not path:
            return False
        segments = [seg for seg in path.split('/') if seg]
        if not segments:
            return False
        first = segments[0]
        if first.startswith('@'):
            if len(segments) == 1:
                return True
            if len(segments) == 2 and segments[1] in {'videos', 'shorts', 'featured', 'streams', 'playlists'}:
                return True
            return False
        if first in {'channel', 'user', 'c'} and len(segments) == 2:
            return True
        return False

    if platform_key == 'instagram':
        if not path:
            return False
        first = path.split('/')[0].lower()
        return first not in {'reel', 'reels', 'p', 'tv', 'stories', 'explore', 'accounts'}

    if platform_key == 'twitter':
        if not path:
            return False
        segments = [seg for seg in path.split('/') if seg]
        return len(segments) == 1 and segments[0].lower() not in {'home', 'explore', 'search', 'i', 'settings'}

    if platform_key == 'linkedin':
        segments = [seg for seg in path.split('/') if seg]
        return len(segments) == 2 and segments[0].lower() in {'in', 'company'}

    if platform_key == 'reddit':
        segments = [seg for seg in path.split('/') if seg]
        return len(segments) == 2 and segments[0].lower() in {'user', 'u'}

    if platform_key == 'facebook':
        if 'profile.php' in path.lower():
            return 'id=' in (parsed.query or '').lower()
        segments = [seg for seg in path.split('/') if seg]
        if not segments:
            return False
        if segments[0].lower() in {'watch', 'reel', 'share', 'events', 'groups', 'marketplace', 'gaming', 'login', 'checkpoint', 'recover', 'help', 'settings'}:
            return False
        return len(segments) == 1 or (len(segments) == 2 and segments[0].lower() == 'people')

    if platform_key == 'tiktok':
        segments = [seg for seg in path.split('/') if seg]
        return len(segments) == 1 and segments[0].startswith('@')

    return True


def extract_handle(url: str, platform_key: str) -> Optional[str]:
    """Extract platform handle from URL. Used during validation and stored in config."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    if not u:
        return None

    if platform_key == "custom":
        return "custom"

    # Bare @handle input — strip and return directly
    if re.match(r"^@?[\w.]+$", u) and platform_key in ("instagram", "youtube", "twitter", "tiktok"):
        return u.lstrip("@")

    if platform_key == "instagram":
        from backend.lib.instagram_parser import parse_instagram_url
        p = parse_instagram_url(u)
        return p.get("handle") if p else None

    if platform_key == "tiktok":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if not path:
                return None
            first = path.split("/", 1)[0]
            return first.lstrip("@") if first.startswith("@") else None
        except Exception:
            return None

    if platform_key == "youtube":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if not path:
                return None
            segments = [seg for seg in path.split("/") if seg]
            if not segments:
                return None
            first = segments[0]
            if first.startswith("@"):
                return first.lstrip("@")
            if first in ("channel", "user", "c") and len(segments) >= 2:
                return segments[1]
            return None
        except Exception:
            return None

    if platform_key == "twitter":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if not path:
                return None
            segments = [seg for seg in path.split("/") if seg]
            if len(segments) == 1 and segments[0].lower() not in ("home", "explore", "search", "i", "settings"):
                return segments[0]
            return None
        except Exception:
            return None

    if platform_key == "linkedin":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if not path:
                return None
            segments = [seg for seg in path.split("/") if seg]
            if len(segments) == 2 and segments[0].lower() in ("in", "company"):
                return segments[1]
            return None
        except Exception:
            return None

    if platform_key == "facebook":
        try:
            parsed = urlparse(u if u.startswith("http") else "https://" + u)
            path = (parsed.path or "").strip("/")
            if not path:
                return None
            segments = [seg for seg in path.split("/") if seg]
            if len(segments) == 1:
                return segments[0]
            if len(segments) == 2 and segments[0].lower() == "people":
                return segments[1]
            return None
        except Exception:
            return None

    # Fallback for unknown platforms
    try:
        parsed = urlparse(u if u.startswith("http") else "https://" + u)
        path = (parsed.path or "").strip("/")
        if not path:
            return None
        return path.split("/")[-1]
    except Exception:
        return None


def validate_url(url: str, platform_key: str) -> Tuple[bool, Optional[str]]:
    """
    Validate URL for platform. Returns (ok, error_message).
    """
    p = get_platform(platform_key)
    if not p:
        return False, f"Unknown platform: {platform_key}"
    u = (url or "").strip()
    if not u:
        return False, "URL is required"

    if platform_key == "custom":
        lines = [line.strip() for line in u.split('\n') if line.strip()]
        if not lines:
            return False, "At least one URL is required"
        return True, None

    # Normalize first — this converts bare @handle → full URL for all platforms
    u = normalize_url(u, platform_key)
    if not u:
        return False, "URL is required"

    pattern = p.get("url_pattern")
    if pattern and not re.match(pattern, u, re.IGNORECASE):
        return False, f"URL doesn't match {p['label']} format"
    try:
        parsed = urlparse(u if u.startswith("http") else "https://" + u)
        if not parsed.netloc:
            return False, "Invalid URL"
        if not _path_matches_platform(parsed, platform_key):
            return False, f"Enter a valid {p['label']} profile/page URL"
    except Exception:
        return False, "Invalid URL"
    return True, None


def validate_time_filter(tf: Dict[str, Any], platform_key: str) -> Tuple[bool, Optional[str]]:
    """
    Enforce exactly ONE active time mode per platform. Returns (ok, error_message).
    """
    if not tf or not isinstance(tf, dict):
        return True, None  # default to "all"
    mode = (tf.get("mode") or "all").strip().lower()
    since = tf.get("since")
    days = tf.get("days")
    if mode not in TIME_MODES:
        return False, f"timeFilter.mode must be one of: since, last_days, all"
    if mode == "all":
        if since or days is not None:
            return False, "timeFilter: use only mode 'all'; do not set since or days"
        return True, None
    if mode == "since":
        if days is not None:
            return False, "timeFilter: use either since or last_days, not both"
        if not since or not isinstance(since, str) or not since.strip():
            return False, "timeFilter: mode 'since' requires a valid since date (YYYY-MM-DD)"
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", since.strip()):
            return False, "timeFilter: since must be YYYY-MM-DD"
        return True, None
    if mode == "last_days":
        if since:
            return False, "timeFilter: use either since or last_days, not both"
        d = days if isinstance(days, int) else (int(days) if days is not None else None)
        if d is None:
            return False, "timeFilter: mode 'last_days' requires days (7, 30, or 90)"
        if d not in LAST_DAYS_OPTIONS:
            return False, f"timeFilter: days must be one of {LAST_DAYS_OPTIONS}"
        return True, None
    return True, None
