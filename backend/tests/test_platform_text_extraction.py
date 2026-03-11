import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("backend.settings", SimpleNamespace(settings=SimpleNamespace(APIFY_TOKEN="test-token")))
sys.modules.setdefault("backend.db", SimpleNamespace(db=SimpleNamespace(execute_update=lambda *args, **kwargs: None, execute_query=lambda *args, **kwargs: [], execute_one=lambda *args, **kwargs: None)))
sys.modules.setdefault("backend.lib.transcription", SimpleNamespace(transcribe_video=lambda url: None))
apify_service = _load_module("apify_service_platform_tests", "apify_service.py")
transcript_worker = _load_module("transcript_worker_platform_tests", "services/transcript_worker.py")


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
    def __init__(self, token, items=None):
        self._items = items or []

    def actor(self, _actor_name):
        return _FakeActorProxy("dataset_1")

    def dataset(self, _dataset_id):
        return _FakeDataset(self._items)


class PlatformTextExtractionTests(unittest.TestCase):
    def test_twitter_prefers_full_note_tweet_text(self):
        item = {
            "text": "short teaser",
            "noteTweet": {
                "noteTweetResults": {
                    "result": {
                        "text": "This is the full note tweet text with the complete thought included."
                    }
                }
            },
        }
        self.assertEqual(
            apify_service._extract_platform_caption(item, "twitter"),
            "This is the full note tweet text with the complete thought included.",
        )

    def test_linkedin_prefers_nested_commentary_text(self):
        item = {
            "text": "short preview",
            "commentary": {"text": {"text": "This is the full LinkedIn commentary body with the actual post text."}},
        }
        self.assertEqual(
            apify_service._extract_platform_caption(item, "linkedin"),
            "This is the full LinkedIn commentary body with the actual post text.",
        )

    def test_instagram_transcript_candidate_uses_subtitles(self):
        item = {
            "caption": "A short caption",
            "subtitles": [{"text": "First line"}, {"text": "Second line"}],
        }
        self.assertEqual(
            apify_service._extract_platform_transcript_candidate(item, "instagram"),
            "First line Second line",
        )

    def test_tiktok_skip_transcripts_keeps_caption_but_marks_pending(self):
        fake_items = [{
            "webVideoUrl": "https://www.tiktok.com/@ahormozi/video/123",
            "text": "caption text",
            "authorMeta": {"name": "Alex"},
            "id": "123",
        }]

        class ClientFactory(_FakeClient):
            def __init__(self, token):
                super().__init__(token, items=fake_items)

        with patch.object(apify_service, "APIFY_AVAILABLE", True),              patch.object(apify_service, "ApifyClient", ClientFactory, create=True),              patch.object(apify_service, "get_apify_token", return_value="token"):
            items = apify_service.scrape_tiktok_posts(
                "https://www.tiktok.com/@ahormozi",
                "ahormozi",
                limit=1,
                skip_transcripts=True,
            )

        self.assertEqual(items[0]["caption"], "caption text")
        self.assertEqual(items[0]["transcript"], "")
        self.assertEqual(items[0]["transcript_status"], "pending")

    def test_youtube_skip_transcripts_preserves_existing_subtitles(self):
        transcript = apify_service._extract_platform_transcript_candidate(
            {"subtitles": [{"text": "Line one"}, {"text": "Line two"}]},
            "youtube",
        )
        self.assertEqual(transcript, "Line one Line two")

    def test_youtube_shorts_use_youtube_caption_path_in_worker(self):
        with patch.dict(sys.modules, {"backend.apify_service": SimpleNamespace(_extract_youtube_native_transcripts=lambda urls: {urls[0]: "native short transcript"})}),              patch.object(transcript_worker.db, "execute_update") as update_mock:
            transcript_worker.process_transcript_job(
                "item-1",
                "https://www.youtube.com/shorts/abcdefghijk",
                "youtube_shorts",
                "caption",
            )

        first_call = update_mock.call_args_list[0]
        self.assertIn("transcript_status = 'present'", first_call.args[0])
        self.assertEqual(first_call.args[1][0], "native short transcript")


if __name__ == "__main__":
    unittest.main()
