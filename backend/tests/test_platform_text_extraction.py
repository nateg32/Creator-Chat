import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


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
apify_service = _load_module("apify_service_text_tests", "apify_service.py")


class PlatformTextExtractionTests(unittest.TestCase):
    def test_instagram_caption_prefers_nested_caption_edges(self):
        item = {
            "edgeMediaToCaption": {
                "edges": [
                    {"node": {"text": "Instagram caption text"}},
                ],
            },
        }

        caption = apify_service._extract_platform_caption(item, "instagram")
        self.assertEqual(caption, "Instagram caption text")

    def test_youtube_transcript_candidate_reads_subtitles(self):
        item = {
            "subtitles": [
                {"text": "Hello"},
                {"text": "world"},
            ],
        }

        transcript = apify_service._extract_platform_transcript_candidate(item, "youtube")
        self.assertEqual(transcript, "Hello world")

    def test_twitter_caption_reads_legacy_full_text(self):
        item = {
            "legacy": {
                "full_text": "Tweet body text",
            },
        }

        caption = apify_service._extract_platform_caption(item, "twitter")
        self.assertEqual(caption, "Tweet body text")

    def test_twitter_title_strips_leading_mentions(self):
        item = {
            "legacy": {
                "full_text": "@dom_lucre They said the same thing about index funds and ETFs",
            },
        }

        title = apify_service.extract_title_from_metadata(
            item,
            "twitter",
            "https://twitter.com/alexhormozi/status/123",
        )
        self.assertEqual(title, "They said the same thing about index funds and ETFs")

    def test_linkedin_caption_reads_commentary_text(self):
        item = {
            "commentary": {
                "text": {
                    "text": "LinkedIn post copy",
                },
            },
        }

        caption = apify_service._extract_platform_caption(item, "linkedin")
        self.assertEqual(caption, "LinkedIn post copy")

    def test_has_meaningful_text_handles_nested_values(self):
        self.assertFalse(apify_service._has_meaningful_text("", None, "   "))
        self.assertTrue(apify_service._has_meaningful_text({"text": "Some text"}))


if __name__ == "__main__":
    unittest.main()
