"""
Instagram URL parser - extracts handle, reel ID, and determines scrape mode
"""
import re
from typing import Dict, Optional
from urllib.parse import urlparse, parse_qs

def parse_instagram_url(url: str) -> Optional[Dict[str, str]]:
    """
    Parse Instagram URL to extract:
    - handle: username
    - reel_id: if it's a reel URL
    - mode: 'reel' or 'profile'
    
    Returns None if not a valid Instagram URL
    """
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()
    
    # Remove @ if present at start
    if url.startswith("@"):
        return {
            "handle": url[1:],
            "reel_id": None,
            "mode": "profile"
        }
    
    # Try to parse as URL
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname.lower() if parsed.hostname else ""
        
        # Must be instagram.com
        if "instagram.com" not in hostname:
            return None
        
        path = parsed.path.strip("/")
        path_parts = [p for p in path.split("/") if p]
        
        if not path_parts:
            return None
        
        # Check for reel URL: instagram.com/reel/{shortcode}/
        if path_parts[0] == "reel" and len(path_parts) > 1:
            reel_id = path_parts[1]
            # Try to extract username from next part or use reel_id as identifier
            handle = path_parts[2] if len(path_parts) > 2 else None
            return {
                "handle": handle,
                "reel_id": reel_id,
                "mode": "reel"
            }
        
        # Check for post URL: instagram.com/p/{shortcode}/
        if path_parts[0] == "p" and len(path_parts) > 1:
            post_id = path_parts[1]
            handle = path_parts[2] if len(path_parts) > 2 else None
            return {
                "handle": handle,
                "reel_id": None,  # Not a reel
                "mode": "profile"  # Will scrape profile, not this specific post
            }
        
        # Profile URL: instagram.com/{username}/
        if len(path_parts) == 1:
            handle = path_parts[0]
            return {
                "handle": handle,
                "reel_id": None,
                "mode": "profile"
            }
        
        # If we have username as first part, use it
        handle = path_parts[0]
        return {
            "handle": handle,
            "reel_id": None,
            "mode": "profile"
        }
        
    except Exception:
        # If URL parsing fails, try regex
        pass
    
    # Regex fallback for handle extraction
    handle_match = re.search(r'instagram\.com/([^/\s?]+)', url, re.IGNORECASE)
    if handle_match:
        handle = handle_match.group(1)
        # Check if it's a reel
        reel_match = re.search(r'/reel/([^/\s?]+)', url, re.IGNORECASE)
        if reel_match:
            return {
                "handle": handle,
                "reel_id": reel_match.group(1),
                "mode": "reel"
            }
        return {
            "handle": handle,
            "reel_id": None,
            "mode": "profile"
        }
    
    return None
