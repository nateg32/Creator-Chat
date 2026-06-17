import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    import sys
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


platforms = _load_module("platforms", "config/platforms.py")
import sys
sys.modules.setdefault("backend.config.platforms", platforms)
sys.modules.setdefault("backend.settings", SimpleNamespace(settings=SimpleNamespace(APIFY_TOKEN="test-token")))
sys.modules.setdefault("backend.apify_service", SimpleNamespace(
    search_all=lambda *args, **kwargs: [],
    search_instagram_reels=lambda *args, **kwargs: [],
    search_youtube_channel=lambda *args, **kwargs: [],
    search_twitter_profile=lambda *args, **kwargs: [],
    search_facebook_posts=lambda *args, **kwargs: [],
    search_reddit_user=lambda *args, **kwargs: [],
    search_linkedin_posts=lambda *args, **kwargs: [],
    search_tiktok_posts=lambda *args, **kwargs: [],
    batch_extract_all_transcripts=lambda items: items,
))
scraper_router = _load_module("scraper_router", "scraper_router.py")
apify_service = _load_module("apify_service", "apify_service.py")


class TikTokPlatformTests(unittest.TestCase):
    def test_instagram_login_redirect_normalizes_to_profile_url(self):
        url = platforms.normalize_url(
            "https://www.instagram.com/accounts/login/?next=https%3A%2F%2Fwww.instagram.com%2Fblakefakhoury%2F&is_from_rle=1",
            "instagram",
        )
        self.assertEqual(url, "https://www.instagram.com/blakefakhoury/")

    def test_resolved_instagram_login_redirect_does_not_replace_profile_url(self):
        normalized = platforms.choose_valid_normalized_url(
            "instagram",
            "https://www.instagram.com/blakefakhoury/",
            "https://www.instagram.com/accounts/login/",
        )
        self.assertEqual(normalized, "https://www.instagram.com/blakefakhoury/")

    def test_tiktok_profile_url_is_valid(self):
        url = platforms.normalize_url("https://www.tiktok.com/@ahormozi", "tiktok")
        self.assertEqual(platforms.validate_url(url, "tiktok"), (True, None))

    def test_tiktok_video_url_normalizes_to_profile_url(self):
        url = platforms.normalize_url("https://www.tiktok.com/@ahormozi/video/1234567890", "tiktok")
        self.assertEqual(url, "https://www.tiktok.com/@ahormozi")
        self.assertEqual(platforms.validate_url(url, "tiktok"), (True, None))

    def test_tiktok_extract_handle_strips_at(self):
        handle = platforms.extract_handle("https://www.tiktok.com/@ahormozi", "tiktok")
        self.assertEqual(handle, "ahormozi")

    def test_linkedin_regional_profile_url_is_valid(self):
        url = platforms.normalize_url("https://ca.linkedin.com/in/dmartell?trk=public_profile", "linkedin")
        self.assertEqual(url, "https://ca.linkedin.com/in/dmartell")
        self.assertEqual(platforms.validate_url(url, "linkedin"), (True, None))

    def test_linkedin_company_url_is_valid(self):
        url = platforms.normalize_url("https://www.linkedin.com/company/acme-inc/", "linkedin")
        self.assertEqual(platforms.validate_url(url, "linkedin"), (True, None))


class TikTokRouterTests(unittest.TestCase):
    def test_tiktok_route_applies_time_filter(self):
        now = datetime.now(timezone.utc)
        items = [
            {"published_at": (now - timedelta(days=1)).isoformat()},
            {"published_at": (now - timedelta(days=30)).isoformat()},
        ]
        with patch.object(scraper_router, "search_tiktok_posts", return_value=[dict(item) for item in items]):
            result = scraper_router._map_tiktok({
                "url": "https://www.tiktok.com/@ahormozi",
                "handle": "ahormozi",
                "creator_handle": "ahormozi",
                "max_items": 20,
                "time_filter": {"mode": "last_days", "days": 7},
            })
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["matched_time_filter"])


class _FakeDataset:
    def __init__(self, items):
        self._items = items

    def iterate_items(self):
        for item in self._items:
            yield item


class _FakeActorProxy:
    def __init__(self, dataset_id: str):
        self._dataset_id = dataset_id

    def call(self, run_input=None, timeout_secs=None):
        return {"defaultDatasetId": self._dataset_id}


class _FakeClient:
    def __init__(self, token):
        self._items = [{
            "webVideoUrl": "https://www.tiktok.com/@ahormozi/video/123",
            "text": "short caption",
            "authorMeta": {"name": "Alex", "nickName": "Alex Hormozi", "id": "1"},
            "id": "123",
            "createTimeISO": "2026-03-11T00:00:00+00:00",
        }]

    def actor(self, _actor_name):
        return _FakeActorProxy("dataset_1")

    def dataset(self, _dataset_id):
        return _FakeDataset(self._items)


class TikTokApifyTests(unittest.TestCase):
    def test_tiktok_caption_is_preserved_when_transcripts_are_deferred(self):
        with patch.object(apify_service, "APIFY_AVAILABLE", True), \
     patch.object(apify_service, "ApifyClient", _FakeClient, create=True), \
     patch.object(apify_service, "get_apify_token", return_value="token"):
            items = apify_service.scrape_tiktok_posts(
                "https://www.tiktok.com/@ahormozi",
                "ahormozi",
                limit=1,
                skip_transcripts=True,
            )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["caption"], "short caption")
        self.assertEqual(items[0]["transcript"], "")
        self.assertEqual(items[0]["transcript_status"], "pending")


if __name__ == "__main__":
    unittest.main()
