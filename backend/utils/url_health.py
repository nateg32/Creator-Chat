"""
URL Health-Check Utility
========================
Validates that URLs are reachable before attaching them to user-facing
cards and citations.  Uses an in-memory TTL cache so the same URL is
only checked once per process lifetime (default 30 min).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────
_CACHE_TTL_SECONDS = 1800          # 30 minutes
_REQUEST_TIMEOUT_SECONDS = 6       # Fast fail — don't block the response
_MAX_REDIRECTS = 5
_CONCURRENT_LIMIT = 4              # Max parallel checks

# ── In-memory cache: url → (is_alive, checked_at) ─────────────
_cache: Dict[str, Tuple[bool, float]] = {}

# URLs on these platforms are assumed reachable (API-gated, no HEAD support, etc.)
_SKIP_CHECK_DOMAINS = frozenset({
    "youtube.com", "www.youtube.com", "youtu.be",
    "m.youtube.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com",
    "twitter.com", "www.twitter.com", "x.com",
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "reddit.com", "www.reddit.com",
    "open.spotify.com",
})


def _should_skip(url: str) -> bool:
    """Return True if the URL is on a platform we trust without pinging."""
    try:
        host = urlparse(url).hostname or ""
        return host.lower() in _SKIP_CHECK_DOMAINS
    except Exception:
        return False


def _cache_get(url: str) -> Optional[bool]:
    entry = _cache.get(url)
    if entry is None:
        return None
    is_alive, ts = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        del _cache[url]
        return None
    return is_alive


def _cache_set(url: str, alive: bool) -> None:
    _cache[url] = (alive, time.monotonic())


async def check_url_alive(url: str) -> bool:
    """Return True if the URL responds with a non-error status (< 400)."""
    if not url or not url.startswith(("http://", "https://")):
        return False

    if _should_skip(url):
        return True

    cached = _cache_get(url)
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            resp = await client.head(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CreatorBot/1.0; link-check)"
            })
            alive = resp.status_code < 400
    except Exception as e:
        logger.debug("URL health-check failed for %s: %s", url, e)
        alive = False

    _cache_set(url, alive)
    return alive


async def check_urls_alive(urls: list[str]) -> Dict[str, bool]:
    """Check multiple URLs concurrently, respecting concurrency limit."""
    sem = asyncio.Semaphore(_CONCURRENT_LIMIT)

    async def _bounded(u: str) -> Tuple[str, bool]:
        async with sem:
            return u, await check_url_alive(u)

    results = await asyncio.gather(*[_bounded(u) for u in urls])
    return dict(results)


def check_url_alive_sync(url: str) -> bool:
    """Synchronous wrapper — runs the async check in a new loop if needed."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    if _should_skip(url):
        return True
    cached = _cache_get(url)
    if cached is not None:
        return cached
    try:
        import httpx as _httpx
        with _httpx.Client(
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            resp = client.head(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CreatorBot/1.0; link-check)"
            })
            alive = resp.status_code < 400
    except Exception as e:
        logger.debug("URL health-check (sync) failed for %s: %s", url, e)
        alive = False
    _cache_set(url, alive)
    return alive


# ── Hot-path helpers (no blocking network I/O) ────────────────
import threading

_BG_INFLIGHT: set = set()
_BG_LOCK = threading.Lock()


def is_url_known_dead(url: str) -> bool:
    """Non-blocking check: True only if we've previously verified the URL dead.

    Unknown URLs return False (treat-as-alive) so the request path never
    waits on an HTTP HEAD. A background check is scheduled to populate
    the cache for next time.
    """
    if not url or not url.startswith(("http://", "https://")):
        return True  # malformed -> treat as dead
    if _should_skip(url):
        return False
    cached = _cache_get(url)
    if cached is None:
        # Unknown -> assume alive, but warm the cache in the background.
        _schedule_background_check(url)
        return False
    return not cached


def _schedule_background_check(url: str) -> None:
    """Fire-and-forget HEAD so the in-memory cache is populated for future calls."""
    with _BG_LOCK:
        if url in _BG_INFLIGHT:
            return
        _BG_INFLIGHT.add(url)

    def _runner(u: str) -> None:
        try:
            check_url_alive_sync(u)
        except Exception:
            pass
        finally:
            with _BG_LOCK:
                _BG_INFLIGHT.discard(u)

    try:
        t = threading.Thread(target=_runner, args=(url,), daemon=True)
        t.start()
    except Exception:
        with _BG_LOCK:
            _BG_INFLIGHT.discard(url)
