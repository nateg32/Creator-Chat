import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

MARKDOWN_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', re.IGNORECASE)
HTTP_URL_RE = re.compile(r'https?://[^\s)\]>\'"]+', re.IGNORECASE)
BARE_DOMAIN_RE = re.compile(
    r'(?<![@/\w])(?:www\.)?(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s)\]>\'"]*)?',
    re.IGNORECASE,
)
TRAILING_PUNCT = '.,!?;:)'
GENERIC_TITLES = {'check', 'here', 'link', 'website', 'site', 'resource', 'go', 'visit'}


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


def _title_from_line(line: str, url_fragment: str, title_hint: str = '') -> str:
    if title_hint:
        return title_hint.strip()[:140]
    cleaned = line.replace(url_fragment, ' ')
    cleaned = MARKDOWN_LINK_RE.sub(lambda m: m.group(1), cleaned)
    cleaned = HTTP_URL_RE.sub(' ', cleaned)
    cleaned = re.sub(r'^\s*(?:\d+[.)]\s*|[-*]\s*)', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip(' -:\t')
    if cleaned and cleaned.lower() not in GENERIC_TITLES and len(cleaned) <= 140:
        return cleaned
    normalized_url = _normalize_url(url_fragment)
    parsed = urlparse(normalized_url)
    domain = parsed.netloc.replace('www.', '')
    if domain:
        return domain
    return 'External Resource'


def extract_preview_cards(text: str) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    seen = set()
    content = text or ''
    lines = content.splitlines() or [content]

    for line in lines:
        markdown_spans = [match.group(0) for match in MARKDOWN_LINK_RE.finditer(line)]
        for title, raw_url in MARKDOWN_LINK_RE.findall(line):
            url = _normalize_url(raw_url)
            if not url or url.lower() in seen:
                continue
            seen.add(url.lower())
            platform = _platform_from_url(url)
            thumbnail = ''
            if platform == 'youtube':
                video_id = _video_id_from_url(url)
                if video_id:
                    thumbnail = f'https://img.youtube.com/vi/{video_id}/mqdefault.jpg'
            cards.append({
                'type': 'preview_card',
                'title': _title_from_line(line, raw_url, title_hint=title),
                'url': url,
                'thumbnail_url': thumbnail,
            })

        masked_line = line
        for span in markdown_spans:
            masked_line = masked_line.replace(span, ' ')

        bare_candidates = []
        bare_candidates.extend(match.group(0) for match in HTTP_URL_RE.finditer(masked_line))
        bare_candidates.extend(match.group(0) for match in BARE_DOMAIN_RE.finditer(masked_line))
        for raw in bare_candidates:
            url = _normalize_url(raw)
            if not url or url.lower() in seen:
                continue
            if not _looks_like_public_url(url):
                continue
            seen.add(url.lower())
            platform = _platform_from_url(url)
            thumbnail = ''
            if platform == 'youtube':
                video_id = _video_id_from_url(url)
                if video_id:
                    thumbnail = f'https://img.youtube.com/vi/{video_id}/mqdefault.jpg'
            cards.append({
                'type': 'preview_card',
                'title': _title_from_line(line, raw),
                'url': url,
                'thumbnail_url': thumbnail,
            })

    return cards[:6]


def merge_preview_cards(*groups: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen = set()
    for group in groups:
        for card in group or []:
            url = _normalize_url(card.get('url') or '')
            if not url or url.lower() in seen:
                continue
            if not _looks_like_public_url(url):
                continue
            seen.add(url.lower())
            merged.append({
                'type': 'preview_card',
                'title': (card.get('title') or 'External Resource')[:140],
                'url': url,
                'thumbnail_url': card.get('thumbnail_url') or '',
            })
    return merged[:6]
