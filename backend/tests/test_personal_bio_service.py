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


def _load_module(name: str, relative_path: str):
    module_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_personal_bio_service(search_results, grounded_results=None):
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
            return {
                "response_text": "Buy Back Your Time was published in September 2023.",
                "citations": [],
                "search_entry_point": {"rendered_content": ""},
                "query_plan": [query],
                "results": list(grounded_results if grounded_results is not None else self.results),
                "sources": [],
                "packages": [],
            }

    provider = _Provider(search_results)

    fake_rag = types.SimpleNamespace(
        create_embedding=lambda *args, **kwargs: [0.0],
        retrieve_chunks=lambda *args, **kwargs: [],
        generate_chat_completion=lambda *args, **kwargs: '{"answer": "fallback"}',
    )
    fake_settings = types.SimpleNamespace(FINAL_RESPONSE_MODEL="test-model")

    _stub_module("backend.db", db=types.SimpleNamespace(execute_query=lambda *args, **kwargs: []))
    _stub_module("backend.rag", **fake_rag.__dict__)
    _stub_module("backend.settings", settings=fake_settings)
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}), get_research_provider=lambda: provider)
    _stub_module("backend.services.decision_service", decision_service=types.SimpleNamespace(
        classify_question=lambda *args, **kwargs: ("personal_bio", "general", 3),
        choose_move=lambda *args, **kwargs: "ANSWER_DIRECTLY",
    ))

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

    def test_public_book_question_falls_back_to_official_sources_honestly(self):
        service, provider = _load_personal_bio_service([])

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
