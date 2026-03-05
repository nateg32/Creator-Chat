import re
import urllib.parse
from typing import Optional, Dict, Any, Tuple
from backend.db import db

def generate_canonical_key(source_url: str, platform: str) -> str:
    if not source_url:
        return ""
        
    url_lower = source_url.lower()
    
    if platform == "youtube":
        match = re.search(r'(?:v=|youtu\.be/|/shorts/)([\w-]+)', url_lower)
        if match:
            return f"youtube:{match.group(1)}"
            
    elif platform == "instagram":
        match = re.search(r'/(?:reel|p|reels)/([\w-]+)', url_lower)
        if match:
            return f"instagram:{match.group(1)}"
            
    elif platform == "tiktok":
        match = re.search(r'/video/(\d+)', url_lower)
        if match:
            return f"tiktok:{match.group(1)}"
            
    elif platform == "twitter" or platform == "x":
        match = re.search(r'/status/(\d+)', url_lower)
        if match:
            return f"x:{match.group(1)}"
            
    parsed = urllib.parse.urlparse(url_lower)
    query_params = urllib.parse.parse_qs(parsed.query)
    preserved_params = {}
    for k, v in query_params.items():
        if k not in ["utm_source", "utm_medium", "utm_campaign", "fbclid", "gclid", "si", "feature", "t", "igsh", "utm_content", "utm_term"]:
            preserved_params[k] = v
    new_query = urllib.parse.urlencode(preserved_params, doseq=True)
    
    path = parsed.path.rstrip('/')
    normalized_url = f"{parsed.scheme}://{parsed.netloc}{path}"
    if new_query:
        normalized_url += f"?{new_query}"
        
    return f"url:{normalized_url}"

def compute_normalized_text(title: Optional[str], description: Optional[str], caption: Optional[str]) -> str:
    parts = []
    if title: parts.append(title)
    if description: parts.append(description)
    if caption: parts.append(caption)
    
    combined = " ".join(parts).lower()
    combined = re.sub(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', '', combined)
    combined = re.sub(r'[^\w\s]', '', combined)
    combined = re.sub(r'\s+', ' ', combined).strip()
    return combined

def simhash64(text: str) -> int:
    if not text:
        return 0
        
    tokens = text.split()
    if not tokens:
        return 0
        
    import hashlib
    v = [0] * 64
    for t in tokens:
        h = int(hashlib.md5(t.encode('utf-8')).hexdigest()[:16], 16)
        for i in range(64):
            bitmask = 1 << i
            if h & bitmask:
                v[i] += 1
            else:
                v[i] -= 1
                
    fingerprint = 0
    for i in range(64):
        if v[i] > 0:
            fingerprint |= (1 << i)
            
    if fingerprint >= 2**63:
        fingerprint -= 2**64
        
    return fingerprint

def hamming_distance(hash1: int, hash2: int) -> int:
    try:
        if hash1 is None or hash2 is None:
            return 64
        x = (int(hash1) ^ int(hash2)) & ((1 << 64) - 1)
        return bin(x).count('1')
    except Exception:
        return 64

def find_duplicate(canonical_key: str, content_fingerprint: int, creator_handle: str) -> Tuple[bool, Optional[str], Optional[str], float]:
    """
    Returns (is_primary, duplicate_of_item_id, duplicate_method, duplicate_confidence)
    """
    if canonical_key:
        query = "SELECT id FROM scrape_items WHERE canonical_key = %s LIMIT 1"
        res = db.execute_query(query, (canonical_key,))
        if res:
            return False, res[0]["id"], "canonical", 1.0
            
    if content_fingerprint and content_fingerprint != 0:
        query = """
            SELECT id, content_fingerprint
            FROM scrape_items
            WHERE creator_handle = %s
            AND content_fingerprint IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 200
        """
        recent_items = db.execute_query(query, (creator_handle,))
        
        best_match_id = None
        best_distance = 64
        
        for item in recent_items:
            dist = hamming_distance(content_fingerprint, item["content_fingerprint"])
            if dist <= 3 and dist < best_distance:
                best_distance = dist
                best_match_id = item["id"]
                
        if best_match_id:
            confidence = 1.0 - (best_distance / 64.0)
            return False, best_match_id, "fingerprint", confidence
            
    return True, None, None, 0.0
