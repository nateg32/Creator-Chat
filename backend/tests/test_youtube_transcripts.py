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
apify_service = _load_module("apify_service_youtube_tests", "apify_service.py")


class YouTubeTranscriptBatchTests(unittest.TestCase):
    def test_batch_prefers_native_youtube_before_actor_fallback(self):
        items = [
            {
                "platform": "youtube",
                "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
                "transcript_status": "missing",
                "transcript": "",
            },
            {
                "platform": "tiktok",
                "source_url": "https://www.tiktok.com/@creator/video/123",
                "transcript_status": "missing",
                "transcript": "",
            },
        ]

        with patch.object(apify_service, "get_apify_token", return_value="token"),              patch.object(apify_service, "_extract_youtube_native_transcripts", return_value={
                 "https://www.youtube.com/watch?v=abcdefghijk": "native youtube transcript"
             }) as native_mock,              patch.object(apify_service, "_extract_transcripts_invideoiq", return_value={
                 "https://www.tiktok.com/@creator/video/123": "tiktok transcript"
             }) as actor_mock:
            result = apify_service.batch_extract_all_transcripts(items)

        self.assertEqual(result[0]["transcript"], "native youtube transcript")
        self.assertEqual(result[0]["transcript_status"], "present")
        self.assertEqual(result[1]["transcript"], "tiktok transcript")
        self.assertEqual(result[1]["transcript_status"], "present")
        native_mock.assert_called_once()
        actor_mock.assert_called_once_with(["https://www.tiktok.com/@creator/video/123"], "token")

    def test_youtube_transcripts_skip_actor_when_native_covers_all(self):
        urls = [
            "https://www.youtube.com/watch?v=abcdefghijk",
            "https://www.youtube.com/shorts/lmnopqrstuv",
        ]

        with patch.object(apify_service, "_extract_youtube_native_transcripts", return_value={
            urls[0]: "native one",
            urls[1]: "native two",
        }) as native_mock,              patch.object(apify_service, "_extract_transcripts_invideoiq") as actor_mock:
            result = apify_service._extract_youtube_transcripts(urls, "token")

        self.assertEqual(result[urls[0]], "native one")
        self.assertEqual(result[urls[1]], "native two")
        native_mock.assert_called_once_with(urls)
        actor_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
