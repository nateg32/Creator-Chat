"""Tests for evidence-first web fact extraction and fast cached reuse."""

import importlib.util
from pathlib import Path
import unittest


def _load_personal_bio_test_module():
    module_path = Path(__file__).resolve().parent / "test_personal_bio_service.py"
    spec = importlib.util.spec_from_file_location("test_personal_bio_service_shared", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


shared_module = _load_personal_bio_test_module()
_load_personal_bio_service = shared_module._load_personal_bio_service


class WebSearchResultInjectionTests(unittest.TestCase):
    def test_extract_search_text_handles_common_shapes(self):
        _service, _provider = _load_personal_bio_service([], grounded_results=[])
        personal_bio_module = __import__("backend.services.personal_bio_service", fromlist=["extract_search_text"])
        extract_search_text = personal_bio_module.extract_search_text
        self.assertEqual(extract_search_text(" September 2023 "), "September 2023")
        self.assertIn(
            "September 2023",
            extract_search_text({"response_text": "Buy Back Your Time was published in September 2023."}),
        )
        self.assertIn(
            "September 2023",
            extract_search_text(
                [
                    {"title": "Buy Back Your Time", "snippet": "Published in September 2023."},
                    {"text": "Portfolio/Penguin release"},
                ]
            ),
        )

    def test_structured_fact_answer_beats_fallback(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="Buy Back Your Time was published in September 2023 by Portfolio/Penguin.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u write buy back your time",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertIn("2023", answer)
        self.assertNotIn("check my", answer.lower())
        self.assertEqual(result.get("move"), "ANSWER_STRUCTURED_FACT")
        self.assertTrue(provider.grounded_calls)

    def test_hot_cache_skips_second_search_for_same_fact(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="Buy Back Your Time was published in September 2023 by Portfolio/Penguin.",
        )

        kwargs = dict(
            user_id=1,
            creator_id=1,
            question="when did u write buy back your time",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
            },
            allow_web=True,
        )

        first = service.handle_personal_question(**kwargs)
        first_call_count = len(provider.grounded_calls)
        second = service.handle_personal_question(**kwargs)
        second_call_count = len(provider.grounded_calls)

        self.assertIn("2023", first.get("answer", ""))
        self.assertIn("2023", second.get("answer", ""))
        self.assertEqual(first_call_count, second_call_count)

    def test_structured_fact_lookup_payload_short_circuits_fallback(self):
        service, provider = _load_personal_bio_service([], grounded_results=[], grounded_response_text="")
        provider.fact_calls = []

        def lookup_public_fact(query, creator_profile, **kwargs):
            provider.fact_calls.append((query, kwargs.get("fact_field"), kwargs.get("entity_subject")))
            return {
                "found": True,
                "fact_field": "publication_date",
                "value": "September 2023",
                "answer_text": "Buy Back Your Time was published in September 2023.",
                "confidence": 0.97,
                "source_url": "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/",
                "source_title": "Penguin Random House",
                "source_snippet": "Buy Back Your Time was published in September 2023.",
                "results": [],
                "sources": [],
                "response_text": "Buy Back Your Time was published in September 2023.",
            }

        provider.lookup_public_fact = lookup_public_fact
        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u write buy back your time",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
            },
            allow_web=True,
        )

        self.assertTrue(provider.fact_calls)
        self.assertIn("2023", result.get("answer", ""))
        self.assertEqual(result.get("move"), "ANSWER_STRUCTURED_FACT")
        self.assertNotIn("check my", result.get("answer", "").lower())
        self.assertNotIn("Dan Martell's", result.get("answer", ""))
        self.assertTrue(result.get("answer", "").startswith("I "), result.get("answer", ""))


if __name__ == "__main__":
    unittest.main()
