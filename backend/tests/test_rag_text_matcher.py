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


class _FakeDb:
    def __init__(self):
        self.calls = []

    def execute_query(self, query, params):
        self.calls.append((query, params))
        return [{
            "doc_id": 7,
            "title": "Pain tolerance beats IQ.",
            "content": "Pain tolerance beats IQ.",
            "source": "twitter",
            "source_id": "1888",
            "metadata": {
                "platform": "twitter",
                "content_id": "1888",
                "canonical_url": "https://x.com/AlexHormozi/status/1888",
                "likes": 120,
            },
            "source_url": "https://x.com/AlexHormozi/status/1888",
            "lexical_score": 10,
        }]


fake_db = _FakeDb()
sys.modules["backend.db"] = SimpleNamespace(db=fake_db)
matcher = _load_module("rag_text_matcher_tests", "services/rag_text_matcher.py")


class RagTextMatcherTests(unittest.TestCase):
    def test_detects_platform_hints_for_x(self):
        hints = matcher.detect_platform_hints("what was your favourite quote you posted on x?")
        self.assertIn("twitter", hints)

    def test_exact_social_request_detection(self):
        self.assertTrue(matcher.wants_exact_social_post("what was your favourite quote you posted on x?"))
        self.assertFalse(matcher.wants_exact_social_post("how do you think about business systems?"))

    def test_retrieve_exact_text_matches_returns_chunk_shape(self):
        results = matcher.retrieve_exact_text_matches(
            creator_id=1,
            question='What was your favourite quote you posted on X?',
            limit=3,
            enabled_platforms=["twitter"],
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_ref"]["platform"], "twitter")
        self.assertEqual(results[0]["source_ref"]["canonical_url"], "https://x.com/AlexHormozi/status/1888")

    def test_named_video_reference_triggers_exact_match(self):
        results = matcher.retrieve_exact_text_matches(
            creator_id=1,
            question="what about from your video 26 harsh lessons i learned in 2025, what are some harsh lessons?",
            limit=3,
            enabled_platforms=None,
        )
        self.assertEqual(len(results), 1)
        self.assertIn("source_ref", results[0])

    def test_talk_about_in_title_extracts_resource_fragment(self):
        fragments = matcher.extract_named_resource_fragments(
            "what did u talk about in the night in the life of a rich trader in miami"
        )

        self.assertEqual(fragments, ["night in the life of a rich trader in miami"])

    def test_deep_breakdown_watch_followup_is_summary_request(self):
        self.assertTrue(
            matcher.is_content_summary_request("give me a deep breakdown, i dont wanna watch the video")
        )

    def test_rewritten_video_breakdown_extracts_title(self):
        fragments = matcher.extract_named_resource_fragments(
            'Give me a detailed breakdown of your video "How to Actually Use AI in 2026".'
        )

        self.assertEqual(fragments, ["How to Actually Use AI in 2026"])

    def test_merge_support_sets_prefers_supplemental(self):
        primary = [{
            "chunk_id": "vec_1",
            "document_id": 1,
            "source_ref": {"canonical_url": "https://example.com/a", "content_id": "a"},
        }]
        supplemental = [{
            "chunk_id": "lex_1",
            "document_id": 2,
            "source_ref": {"canonical_url": "https://x.com/example/status/2", "content_id": "2"},
        }]
        merged = matcher.merge_support_sets(primary, supplemental, limit=2)
        self.assertEqual(merged[0]["chunk_id"], "lex_1")
        self.assertEqual(merged[1]["chunk_id"], "vec_1")


if __name__ == "__main__":
    unittest.main()
