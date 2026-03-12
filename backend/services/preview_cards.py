import html
import re
from functools import lru_cache
from typing import Dict, List, Optional
from urllib.parse import urlparse

MARKDOWN_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', re.IGNORECASE)
HTTP_URL_RE = re.compile(r'''https?://[^\s)\]>\'\"]+''', re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(
    r'''(?<![@/\w])(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s)\]>\'\"]*)?''',
    re.IGNORECASE,
)
TRAILING_PUNCT = '.,!?;:)'
GENERIC_TITLES = {'check', 'here', 'link', 'website', 'site', 'resource', 'go', 'visit'}
GENERIC_CARD_TITLES = {
    'external resource',
    'youtube video',
    'youtube short',
    'instagram reel',
    'instagram post',
    'tiktok video',
    'facebook video',
    'tweet',
    'video',
    'article',
}
GENERIC_TITLE_PATTERNS = (
    re.compile(r'^(?:watch|read|open|visit)(?: this| the)?(?: one| link| video| article| resource)?(?: first| now)?$', re.IGNORECASE),
    re.compile(r'^here(?: it is| you go)?$', re.IGNORECASE),
    re.compile(r'^this(?: one| link| video)?$', re.IGNORECASE),
)
MAX_CARDS = 3
TITLE_FETCH_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; CreatorBotPreview/1.0; +https://creator-bot.local)',
    'Accept-Language': 'en-US,en;q=0.9',
}
TITLE_META_PATTERNS = (
    re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL),
    re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']', re.IGNORECASE | re.DOTALL),
    re.compile(r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'](.*?)["\']', re.IGNORECASE | re.DOTALL),
    re.compile(r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']twitter:title["\']', re.IGNORECASE | re.DOTALL),
)
TITLE_TAG_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)


def _trim_url(url: str) -> str:
    cleaned = (url or '').strip()
    while cleaned and cleaned[-1] in TRAILING_PUNCT:
        cleaned = cleaned[:-1]
    return cleaned


def _normalize_url(url: str) -> str:
    cleaned = _trim_url(url)
    if not cleaned:
        return ''
    if not cleaned.lower().startswith(('http://', 'https://')):
        cleaned = f'https://{cleaned}'
    return cleaned


def _looks_like_public_url(url: str) -> bool:
    parsed = urlparse(url or '')
    host = (parsed.netloc or '').strip().lower()
    return bool(host and '.' in host and not host.endswith('.local'))


def _video_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if 'youtube.com' in host:
        query = parsed.query or ''
        for part in query.split('&'):
            if part.startswith('v='):
                return part.split('=', 1)[1]
        if '/shorts/' in parsed.path:
            return parsed.path.split('/shorts/', 1)[1].split('/', 1)[0]
    if 'youtu.be' in host:
        return parsed.path.lstrip('/').split('/', 1)[0]
    return ''


def _platform_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if 'youtube.com' in host or 'youtu.be' in host:
        return 'youtube'
    if 'instagram.com' in host:
        return 'instagram'
    if 'tiktok.com' in host:
        return 'tiktok'
    if 'facebook.com' in host or 'fb.watch' in host:
        return 'facebook'
    if 'twitter.com' in host or 'x.com' in host:
        return 'twitter'
    return 'web'


def _domain_key(url: str) -> str:
    host = (urlparse(url or '').netloc or '').lower().replace('www.', '')
    if host in {'youtu.be'} or 'youtube.com' in host:
        return 'youtube'
    if host in {'x.com'} or 'twitter.com' in host:
        return 'twitter'
    if host == 'fb.watch' or 'facebook.com' in host:
        return 'facebook'
    return host


def _url_identity(url: str) -> str:
    normalized = _normalize_url(url)
    platform = _platform_from_url(normalized)
    if platform == 'youtube':
        video_id = _video_id_from_url(normalized)
        if video_id:
            return f'youtube:{video_id}'
    return normalized.lower()


def _title_from_line(line: str, url_fragment: str, title_hint: str = '') -> str:
    if title_hint:
        return title_hint.strip()[:140]
    cleaned = line.replace(url_fragment, ' ')
    cleaned = MARKDOWN_LINK_RE.sub(lambda m: m.group(1), cleaned)
    cleaned = HTTP_URL_RE.sub(' ', cleaned)
    cleaned = re.sub(r'^\s*(?:\d+[.)]\s*|[-*]\s*)', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:\t')
    if cleaned and not _is_generic_title(cleaned, url_fragment) and len(cleaned) <= 140:
        return cleaned
    normalized_url = _normalize_url(url_fragment)
    parsed = urlparse(normalized_url)
    domain = parsed.netloc.replace('www.', '')
    if domain:
        return domain
    return 'External Resource'


def _normalize_title(value: str, url: str = '') -> str:
    cleaned = html.unescape((value or '').strip())
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:\t|')
    host = (urlparse(url or '').netloc or '').lower()
    suffixes = []
    if 'youtube.com' in host or 'youtu.be' in host:
        suffixes.append(' - YouTube')
    if 'facebook.com' in host:
        suffixes.append(' | Facebook')
    if 'instagram.com' in host:
        suffixes.append(' - Instagram')
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[:-len(suffix)].rstrip(' -:|')
    return cleaned


def _extract_remote_title(body: str, url: str) -> str:
    if not body:
        return ''
    for pattern in TITLE_META_PATTERNS:
        match = pattern.search(body)
        if match:
            title = _normalize_title(match.group(1), url)
            if title:
                return title
    match = TITLE_TAG_RE.search(body)
    if not match:
        return ''
    return _normalize_title(match.group(1), url)


@lru_cache(maxsize=128)
def _lookup_remote_title(url: str) -> str:
    import requests

    normalized = _normalize_url(url)
    if not normalized:
        return ''
    try:
        response = requests.get(
            normalized,
            headers=TITLE_FETCH_HEADERS,
            timeout=(3.05, 4.5),
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = (response.headers.get('Content-Type') or '').lower()
        if content_type and 'html' not in content_type and 'xml' not in content_type:
            return ''
        return _extract_remote_title(response.text or '', response.url or normalized)
    except Exception:
        return ''


def _build_card(line: str, raw_url: str, title_hint: str = '') -> Optional[Dict[str, str]]:
    url = _normalize_url(raw_url)
    if not url or not _looks_like_public_url(url):
        return None
    platform = _platform_from_url(url)
    thumbnail = ''
    if platform == 'youtube':
        video_id = _video_id_from_url(url)
        if video_id:
            thumbnail = f'https://img.youtube.com/vi/{video_id}/mqdefault.jpg'
    return {
        'type': 'preview_card',
        'title': _title_from_line(line, raw_url, title_hint=title_hint),
        'url': url,
        'thumbnail_url': thumbnail,
    }


def _enrich_card_title(card: Dict[str, str], prefer_remote: bool = False) -> Dict[str, str]:
    if not card:
        return card
    current_title = card.get('title', '')
    if not prefer_remote and not _is_generic_title(current_title, card.get('url', '')):
        return card
    remote_title = _lookup_remote_title(card.get('url', ''))
    if not remote_title:
        return card
    if _is_generic_title(remote_title, card.get('url', '')) and not _is_generic_title(current_title, card.get('url', '')):
        return card
    enriched = dict(card)
    enriched['title'] = remote_title[:140]
    return enriched


def _is_generic_title(title: str, url: str = '') -> bool:
    lowered = re.sub(r'\s+', ' ', (title or '').strip().lower())
    if lowered in {'', 'external resource'} or lowered in GENERIC_TITLES or lowered in GENERIC_CARD_TITLES:
        return True
    if any(pattern.match(lowered) for pattern in GENERIC_TITLE_PATTERNS):
        return True
    domain = _domain_key(url)
    return bool(domain and lowered == domain)


def _path_depth(url: str) -> int:
    path = urlparse(url or '').path.strip('/')
    return len(path.split('/')) if path else 0


def _prefer_card(existing: Dict[str, str], candidate: Dict[str, str]) -> bool:
    existing_title = existing.get('title', '')
    candidate_title = candidate.get('title', '')
    if _is_generic_title(existing_title, existing.get('url', '')) and not _is_generic_title(candidate_title, candidate.get('url', '')):
        return True
    return _path_depth(candidate.get('url', '')) < _path_depth(existing.get('url', ''))


def _append_card(cards: List[Dict[str, str]], card: Optional[Dict[str, str]], by_url: dict, by_domain: dict) -> None:
    if not card:
        return
    url_key = _url_identity(card['url'])
    if url_key in by_url:
        return
    domain_key = _domain_key(card['url'])
    existing = by_domain.get(domain_key)
    if existing:
        existing_title = (existing.get('title') or '').strip().lower()
        candidate_title = (card.get('title') or '').strip().lower()
        if existing_title == candidate_title or _is_generic_title(candidate_title, card.get('url', '')):
            return
        if _prefer_card(existing, card):
            idx = cards.index(existing)
            cards[idx] = card
            del by_url[_url_identity(existing['url'])]
            by_url[url_key] = card
            by_domain[domain_key] = card
        return
    cards.append(card)
    by_url[url_key] = card
    by_domain[domain_key] = card


def extract_preview_cards(text: str, enrich_titles: bool = False) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    by_url = {}
    by_domain = {}
    content = text or ''
    lines = content.splitlines() or [content]

    for line in lines:
        markdown_spans = [match.group(0) for match in MARKDOWN_LINK_RE.finditer(line)]
        for title, raw_url in MARKDOWN_LINK_RE.findall(line):
            _append_card(cards, _build_card(line, raw_url, title_hint=title), by_url, by_domain)

        masked_line = line
        for span in markdown_spans:
            masked_line = masked_line.replace(span, ' ')

        bare_candidates = []
        bare_candidates.extend(match.group(0) for match in HTTP_URL_RE.finditer(masked_line))
        bare_candidates.extend(match.group(0) for match in BARE_DOMAIN_RE.finditer(masked_line))
        for raw in bare_candidates:
            _append_card(cards, _build_card(line, raw), by_url, by_domain)

    cards = cards[:MAX_CARDS]
    if enrich_titles:
        return [_enrich_card_title(card, prefer_remote=True) for card in cards]
    return cards


def merge_preview_cards(*groups: Optional[List[Dict[str, str]]], enrich_titles: bool = False) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    by_url = {}
    by_domain = {}
    for group in groups:
        for card in group or []:
            normalized = _build_card(card.get('title') or '', card.get('url') or '', title_hint=card.get('title') or '')
            if normalized:
                normalized['thumbnail_url'] = card.get('thumbnail_url') or normalized.get('thumbnail_url') or ''
                if enrich_titles:
                    normalized = _enrich_card_title(
                        normalized,
                        prefer_remote=_is_generic_title(normalized.get('title', ''), normalized.get('url', '')),
                    )
            _append_card(merged, normalized, by_url, by_domain)
    return merged[:MAX_CARDS]
