"""Regression tests for public creator-fact handling in PersonalBioService.

These tests cover the failure mode where creator-public questions about books,
products, or release dates were being answered with private-life fallback
language instead of forcing web evidence and answering directly or honestly
redirecting to official sources.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _stub_package(name: str):
    module = types.ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = module
    return module


def _load_module(name: str, relative_path: str):
    module_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_personal_bio_service(
    search_results,
    grounded_results=None,
    grounded_response_text="Buy Back Your Time was published in September 2023.",
    grounded_overview_callback=None,
):
    _stub_package("backend")
    _stub_package("backend.services")
    _stub_module(
        "backend.db",
        db=types.SimpleNamespace(
            execute_query=lambda *args, **kwargs: [],
            execute_one=lambda *args, **kwargs: None,
            execute_update=lambda *args, **kwargs: None,
        ),
    )
    _stub_module("backend.rag", create_embedding=lambda *args, **kwargs: [0.0], retrieve_chunks=lambda *args, **kwargs: [], generate_chat_completion=lambda *args, **kwargs: '{"answer": "fallback"}')
    _stub_module("backend.settings", settings=types.SimpleNamespace(FINAL_RESPONSE_MODEL="test-model"))
    _stub_module(
        "backend.services.live_search_rules",
        build_live_search_query=lambda question, history=None, creator_name=None, preferred_platforms=None, require_video=False: question,
    )
    decision_service_module = _load_module(
        "backend.services.decision_service",
        pathlib.Path("services") / "decision_service.py",
    )
    _load_module(
        "backend.services.creator_entity_service",
        pathlib.Path("services") / "creator_entity_service.py",
    )
    _load_module(
        "backend.services.fact_registry",
        pathlib.Path("services") / "fact_registry.py",
    )
    _load_module(
        "backend.services.evidence_router",
        pathlib.Path("services") / "evidence_router.py",
    )
    search_engine_module = _load_module(
        "backend.services.search_decision_engine",
        pathlib.Path("services") / "search_decision_engine.py",
    )

    class _Provider:
        def __init__(self, results):
            self.results = list(results)
            self.calls = []
            self.grounded_calls = []

        def search(self, query, creator_profile, **kwargs):
            self.calls.append((query, creator_profile.get("name")))
            return list(self.results)

        def grounded_overview(self, query, creator_profile, conversation_history=None, max_queries=4):
            self.grounded_calls.append((query, creator_profile.get("name")))
            if callable(grounded_overview_callback):
                return grounded_overview_callback(query, creator_profile)
            return {
                "response_text": grounded_response_text,
                "citations": [],
                "search_entry_point": {"rendered_content": ""},
                "query_plan": [query],
                "results": list(grounded_results if grounded_results is not None else self.results),
                "sources": [],
                "packages": [],
            }

    provider = _Provider(search_results)
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}), get_research_provider=lambda: provider)
    sys.modules["backend.services.decision_service"] = decision_service_module

    module = _load_module(
        "backend.services.personal_bio_service",
        pathlib.Path("services") / "personal_bio_service.py",
    )
    return module.personal_bio_service, provider


class PersonalBioServiceTests(unittest.TestCase):
    def test_public_book_question_answers_from_web_evidence(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Buy Back Your Time",
                    "url": "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/",
                    "snippet": "Buy Back Your Time was published in September 2023.",
                }
            ]
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when was your book published?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls or provider.calls)
        self.assertTrue("2023" in answer or "September" in answer, answer)
        self.assertNotIn("I haven't really talked about that publicly", answer)
        self.assertNotIn("wouldn't want to guess", answer)

    def test_public_book_question_forces_web_when_caller_disables_it(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Buy Back Your Time",
                    "url": "https://www.amazon.com/Buy-Back-Your-Time/dp/example",
                    "snippet": "Buy Back Your Time was published on September 26, 2023.",
                }
            ]
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did your first book come out?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=False,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls or provider.calls)
        self.assertTrue("September" in answer or "2023" in answer, answer)
        self.assertNotIn("I haven't really talked about that publicly", answer)

    def test_public_book_question_prefers_gemini_grounded_overview(self):
        grounded_results = [
            {
                "title": "Buy Back Your Time",
                "url": "https://www.amazon.com/Buy-Back-Your-Time/dp/example",
                "snippet": "Buy Back Your Time was published on September 26, 2023.",
            }
        ]
        service, provider = _load_personal_bio_service([], grounded_results=grounded_results)

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when was your book published?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertFalse(provider.calls)
        self.assertTrue("September" in answer or "2023" in answer, answer)

    def test_grounded_response_text_only_still_answers_public_fact(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="Buy Back Your Time was published on September 26, 2023.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when was your book published?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertIn("September 26, 2023", answer)

    def test_public_book_followup_uses_conversation_context_before_web_search(self):
        grounded_results = [
            {
                "title": "Buy Back Your Time",
                "url": "https://www.amazon.com/Buy-Back-Your-Time/dp/example",
                "snippet": "Buy Back Your Time was published on September 26, 2023.",
            }
        ]
        service, provider = _load_personal_bio_service([], grounded_results=grounded_results)

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u write it?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            conversation_history=[
                {"role": "user", "content": "do you have a book?"},
                {"role": "assistant", "content": "Yeah. I wrote a book called Buy Back Your Time."},
            ],
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        query = provider.grounded_calls[0][0]
        self.assertIn("Buy Back Your Time", query)
        self.assertNotIn("when did u write it", query.lower())
        self.assertTrue("September" in answer or "2023" in answer, answer)

    def test_public_book_question_keeps_searching_until_date_evidence_found(self):
        def grounded_callback(query, creator_profile):
            lowered = query.lower()
            if "publication date" in lowered or "release date" in lowered:
                return {
                    "response_text": "Buy Back Your Time was published in September 2023.",
                    "citations": [],
                    "search_entry_point": {"rendered_content": ""},
                    "query_plan": [query],
                    "results": [],
                    "sources": [],
                    "packages": [],
                }
            return {
                "response_text": "Buy Back Your Time is a book by Dan Martell.",
                "citations": [],
                "search_entry_point": {"rendered_content": ""},
                "query_plan": [query],
                "results": [],
                "sources": [],
                "packages": [],
            }

        service, provider = _load_personal_bio_service(
            [],
            grounded_overview_callback=grounded_callback,
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="wait when did your write buy back your time",
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
        self.assertGreaterEqual(len(provider.grounded_calls), 2)
        self.assertIn("2023", answer)
        self.assertTrue(
            any("publication date" in call[0].lower() or "release date" in call[0].lower() for call in provider.grounded_calls),
            provider.grounded_calls,
        )

    def test_entity_confirmation_uses_creator_entity_graph_before_web_search(self):
        service, provider = _load_personal_bio_service([])

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="do you know the book buy your time",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
                "official_domains": ["danmartell.com"],
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertFalse(provider.grounded_calls)
        self.assertFalse(provider.calls)
        self.assertIn("Buy Back Your Time", answer)
        self.assertEqual(result.get("move"), "ANSWER_ENTITY_GRAPH_CONFIRMATION")

    def test_public_book_question_falls_back_to_official_sources_honestly(self):
        service, provider = _load_personal_bio_service([], grounded_response_text="")

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when was your book published?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls or provider.calls)
        self.assertNotIn("I haven't really talked about that publicly", answer)
        self.assertNotIn("wouldn't want to guess", answer)
        self.assertNotIn("Dan Martell's", answer)
        self.assertTrue("amazon" in answer.lower() or "publisher" in answer.lower() or "official" in answer.lower(), answer)


if __name__ == "__main__":
    unittest.main()
