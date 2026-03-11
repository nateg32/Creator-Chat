import importlib.util
import unittest
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[1] / "services" / "preview_cards.py"
    spec = importlib.util.spec_from_file_location("preview_cards", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


preview_cards = _load_module()


class PreviewCardTests(unittest.TestCase):
    def test_extracts_markdown_link_card(self):
        cards = preview_cards.extract_preview_cards("Watch [this clip](https://example.com/watch?v=1) now.")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], "https://example.com/watch?v=1")
        self.assertEqual(cards[0]["title"], "this clip")

    def test_extracts_bare_domain_card(self):
        cards = preview_cards.extract_preview_cards("Check 2819Church.org for updates.")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], "https://2819Church.org")

    def test_extracts_youtube_thumbnail(self):
        cards = preview_cards.extract_preview_cards("Watch https://youtu.be/abc123XYZ")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["thumbnail_url"], "https://img.youtube.com/vi/abc123XYZ/mqdefault.jpg")

    def test_merge_deduplicates_urls(self):
        merged = preview_cards.merge_preview_cards(
            [{"url": "https://example.com", "title": "One", "thumbnail_url": ""}],
            [{"url": "example.com", "title": "Two", "thumbnail_url": ""}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["title"], "One")


    def test_extract_limits_generic_duplicate_domains(self):
        cards = preview_cards.extract_preview_cards(
            "acquisition.com\nacquisition.com/free-trial\nskool.com"
        )
        self.assertEqual(len(cards), 2)
        self.assertEqual(cards[0]["url"], "https://acquisition.com")
        self.assertEqual(cards[1]["url"], "https://skool.com")

    def test_merge_prefers_more_specific_non_generic_card(self):
        merged = preview_cards.merge_preview_cards(
            [{"url": "https://acquisition.com", "title": "External Resource", "thumbnail_url": ""}],
            [{"url": "https://acquisition.com/free-trial", "title": "Free trial", "thumbnail_url": ""}],
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], "https://acquisition.com/free-trial")
        self.assertEqual(merged[0]["title"], "Free trial")


if __name__ == "__main__":
    unittest.main()
