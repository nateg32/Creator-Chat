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
    entity_lookup_result=None,
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
        "backend.services.creator_fact_policy",
        pathlib.Path("services") / "creator_fact_policy.py",
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
            self.entity_calls = []

        def search(self, query, creator_profile, **kwargs):
            self.calls.append((query, creator_profile.get("name")))
            return list(self.results)

        def grounded_overview(self, query, creator_profile, conversation_history=None, max_queries=4):
            self.grounded_calls.append((query, creator_profile.get("name"), max_queries))
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

        def lookup_creator_entities(self, query, creator_profile, entity_type="", conversation_history=None):
            self.entity_calls.append((query, creator_profile.get("name"), entity_type))
            return entity_lookup_result or {"entities": [], "response_text": "", "sources": []}

    provider = _Provider(search_results)
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}), get_research_provider=lambda: provider)
    sys.modules["backend.services.decision_service"] = decision_service_module

    module = _load_module(
        "backend.services.personal_bio_service",
        pathlib.Path("services") / "personal_bio_service.py",
    )
    return module.personal_bio_service, provider


class PersonalBioServiceTests(unittest.TestCase):
    def test_private_religion_question_declines_before_search(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Irrelevant public fact",
                    "url": "https://example.com",
                    "snippet": "The creator is 22 years old.",
                }
            ]
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="do u believe in God",
            voice_profile={},
            creator_name="Anabolic Gabe",
            decision_policy={},
            creator_profile={"name": "Anabolic Gabe", "creator_category": "fitness"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertEqual(result.get("move"), "DECLINE_PRIVATE")
        self.assertFalse(provider.grounded_calls)
        self.assertFalse(provider.calls)
        self.assertIn("keep that side", answer.lower())
        self.assertIn("training", answer.lower())
        self.assertNotIn("22", answer)

    def test_private_religion_boundary_uses_creator_lane_when_available(self):
        service, _ = _load_personal_bio_service([])

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="do you believe in God?",
            voice_profile={},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={
                "name": "Alex Hormozi",
                "creator_category": "business",
                "style_fingerprint": {
                    "domain_map": {"creator_lane": "business constraints and scaling systems"}
                },
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertEqual(result.get("move"), "DECLINE_PRIVATE")
        self.assertIn("public operating system", answer.lower())
        self.assertIn("business constraints and scaling systems", answer)

    def test_verifiable_public_profile_fact_does_not_decline_before_search(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text="Alex Hormozi is married to Leila Hormozi, his co-founder and business partner.",
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "I am married to Leila Hormozi, who is also my co-founder and business partner."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="are you married?",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "").lower()
        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertNotIn("keep that side", answer)
        self.assertIn("leila", answer)

    def test_misus_slang_searches_relationship_fact_without_random_source_card(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Alex and Leila Hormozi",
                    "url": "https://www.acquisition.com/about",
                    "snippet": "Alex Hormozi is married to Leila Hormozi.",
                }
            ],
            grounded_response_text="Alex Hormozi is married to Leila Hormozi, his co-founder and business partner.",
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "I am married to Leila Hormozi, who is also my co-founder and business partner."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="do you have a misus",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi", "official_domains": ["acquisition.com"]},
            allow_web=True,
        )

        answer = result.get("answer", "").lower()
        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertIn("leila", answer)
        self.assertEqual(result.get("sources"), [])

    def test_public_profile_search_queries_match_requested_fact(self):
        service, _ = _load_personal_bio_service([])

        queries = service._build_public_fact_search_queries(
            "are you married?",
            "Alex Hormozi",
            evidence_plan=types.SimpleNamespace(query_goal="identity_lookup"),
        )

        joined = " ".join(queries).lower()
        self.assertIn("married", joined)
        self.assertIn("spouse", joined)
        self.assertNotIn("full name", joined)

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
        self.assertNotIn("Dan Martell's", answer)
        self.assertTrue(answer.startswith("I "), answer)

    def test_role_question_answers_from_profile_role_fact_before_web(self):
        service, provider = _load_personal_bio_service([])

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="what do u manage again?",
            voice_profile={},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={
                "id": 1,
                "name": "Alex Hormozi",
                "identity_fingerprint": {
                    "job_titles": ["Founder and Managing Partner of Acquisition AI"],
                },
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertIn("Acquisition AI", answer)
        self.assertIn("Managing Partner", answer)
        self.assertFalse(provider.grounded_calls or provider.calls)
        self.assertNotIn("partner of,", answer)

    def test_public_book_question_does_not_use_web_when_caller_disables_it(self):
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
        self.assertFalse(provider.grounded_calls or provider.calls)
        self.assertFalse("September" in answer or "2023" in answer, answer)

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
        self.assertNotIn("Dan Martell's", answer)
        self.assertTrue(answer.startswith("I "), answer)

    def test_public_identity_question_answers_from_web_evidence(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="TJR Trades (Tyler J. Riches) is a trading creator who documents his journey online.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="what's your full name?",
            voice_profile={},
            creator_name="Tjr",
            decision_policy={},
            creator_profile={"name": "Tjr"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertIn("Tyler J. Riches", answer)
        self.assertEqual(answer, "My full name is Tyler J. Riches.")
        self.assertTrue(result.get("sources"))

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

    def test_direct_write_question_answers_from_publication_evidence(self):
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
        self.assertTrue(provider.grounded_calls)
        self.assertTrue("September" in answer or "2023" in answer, answer)
        self.assertNotIn("check my", answer.lower())
        self.assertNotIn("Dan Martell's", answer)
        self.assertTrue(answer.startswith("I "), answer)

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
        self.assertGreaterEqual(len(provider.grounded_calls), 1)
        self.assertTrue(
            "publication date" in provider.grounded_calls[0][0].lower()
            or "release date" in provider.grounded_calls[0][0].lower(),
            provider.grounded_calls,
        )
        self.assertLessEqual(len(provider.grounded_calls), 2, provider.grounded_calls)
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

    def test_book_catalog_question_uses_entity_lookup_to_return_full_list(self):
        service, provider = _load_personal_bio_service(
            [],
            entity_lookup_result={
                "entities": [
                    {"name": "$100M Offers", "type": "book", "official_urls": ["https://www.amazon.com/offers"]},
                    {"name": "$100M Leads", "type": "book", "official_urls": ["https://www.amazon.com/leads"]},
                    {"name": "$100M Money Models", "type": "book", "official_urls": ["https://www.amazon.com/money-models"]},
                ],
                "response_text": "",
                "sources": [],
            },
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="have u written any books?",
            voice_profile={},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.entity_calls)
        self.assertIn("$100M Offers", answer)
        self.assertIn("$100M Leads", answer)
        self.assertIn("$100M Money Models", answer)
        self.assertEqual(result.get("move"), "ANSWER_ENTITY_CATALOG")

    def test_how_many_books_query_returns_count_and_titles_instead_of_date_fallback(self):
        service, provider = _load_personal_bio_service(
            [],
            entity_lookup_result={
                "entities": [
                    {"name": "$100M Offers", "type": "book", "official_urls": ["https://www.amazon.com/offers"]},
                    {"name": "$100M Leads", "type": "book", "official_urls": ["https://www.amazon.com/leads"]},
                    {"name": "$100M Money Models", "type": "book", "official_urls": ["https://www.amazon.com/money-models"]},
                ],
                "response_text": "",
                "sources": [],
            },
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="how many books u written?",
            voice_profile={},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.entity_calls)
        self.assertIn("3", answer)
        self.assertIn("$100M Offers", answer)
        self.assertIn("$100M Leads", answer)
        self.assertIn("$100M Money Models", answer)
        self.assertNotIn("right date", answer.lower())
        self.assertNotIn("amazon listing", answer.lower())
        self.assertEqual(result.get("move"), "ANSWER_ENTITY_CATALOG")

    def test_book_catalog_fallback_does_not_turn_into_publication_date_prompt(self):
        service, provider = _load_personal_bio_service(
            [],
            entity_lookup_result={"entities": [], "response_text": "", "sources": []},
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="how many books u written?",
            voice_profile={},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertNotIn("right date", answer.lower())
        self.assertNotIn("publication info", answer.lower())
        self.assertNotIn("amazon listing", answer.lower())

    def test_followup_write_question_uses_recent_book_title_not_creator_name(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="$100M Money Models was published on August 16, 2025.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u write it?",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            conversation_history=[
                {"role": "user", "content": "what about 100m money models?"},
                {"role": "assistant", "content": "Yeah, that's mine too. $100M Money Models is the third book I put out in the $100M series."},
            ],
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertIn("$100M Money Models", provider.grounded_calls[0][0])
        self.assertIn("$100M Money Models", answer)
        self.assertNotIn("Alex Hormozi was published", answer)
        self.assertTrue(answer.startswith("I "), answer)

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
        self.assertIn("verified publication date", answer.lower())
        self.assertIn("not going to guess", answer.lower())

    def test_creator_start_question_uses_grounded_timeline_not_publication_language(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_results=[],
            grounded_response_text="Alex Hormozi started trading in 2014.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u start trading?",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls or provider.calls)
        self.assertIn("2014", answer)
        self.assertIn("started trading", answer.lower())
        self.assertNotIn("published", answer.lower())
        self.assertNotIn("amazon", answer.lower())

    def test_creator_start_question_fallback_is_not_publication_specific(self):
        service, provider = _load_personal_bio_service([], grounded_response_text="")

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="when did u start trading?",
            voice_profile={},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={"name": "Dan Martell"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls or provider.calls)
        self.assertNotIn("publication info", answer.lower())
        self.assertNotIn("amazon listing", answer.lower())
        self.assertNotIn("audible", answer.lower())
        self.assertNotIn("publisher page", answer.lower())
        self.assertIn("not going to make up", answer.lower())
        self.assertIn("timeline", answer.lower())

    def test_creator_start_subject_normalizes_trading_variants(self):
        service, _provider = _load_personal_bio_service([], grounded_response_text="")
        plan = types.SimpleNamespace(query_goal="timeline_lookup", entity_type="", entity_subject="")

        self.assertEqual(
            service._derive_entity_subject("when did u start day trading?", "Tjr", evidence_plan=plan),
            "trading",
        )
        self.assertEqual(
            service._derive_entity_subject("when did u start trading?", "Tjr", evidence_plan=plan),
            "trading",
        )

    def test_creator_start_question_keeps_searching_past_two_queries(self):
        def grounded_callback(query, creator_profile):
            if " since" in query.lower():
                return {
                    "response_text": "Tjr started trading in 2018.",
                    "citations": [],
                    "search_entry_point": {"rendered_content": ""},
                    "query_plan": [query],
                    "results": [],
                    "sources": [],
                    "packages": [],
                }
            return {
                "response_text": "Tjr talks a lot about trading.",
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
            question="when did u start day trading?",
            voice_profile={"energy": "direct"},
            creator_name="Tjr",
            decision_policy={},
            creator_profile={"name": "Tjr"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertGreaterEqual(len(provider.grounded_calls), 4, provider.grounded_calls)
        self.assertTrue(any(" since" in call[0].lower() for call in provider.grounded_calls), provider.grounded_calls)
        self.assertIn("2018", answer)

    def test_fact_lookup_timeline_sentence_does_not_double_render(self):
        service, _provider = _load_personal_bio_service([], grounded_response_text="")

        candidate = service._candidate_from_fact_lookup(
            {
                "found": True,
                "fact_field": "start_date",
                "answer_text": "I started trading in 2017.",
                "value": "",
                "confidence": 0.92,
            },
            fact_field="start_date",
            entity_subject="trading",
        )

        answer = service._render_structured_fact_answer(
            candidate,
            "when did u start day trading?",
            "Tjr",
            {"energy": "direct"},
        )

        self.assertEqual(answer, "I started day trading in 2017.")

    def test_creator_journey_question_triggers_grounded_search(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text="He said he got into trading because he wanted more control over his future.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="why did u start trading man?",
            voice_profile={"energy": "direct"},
            creator_name="Tjr",
            decision_policy={},
            creator_profile={"name": "Tjr"},
            allow_web=True,
        )

        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertIn("wanted more control over my future", result.get("answer", "").lower())

    def test_creator_journey_question_prefers_reason_over_year_fact(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text="He started trading in 2017 because he was tired of being broke and stuck.",
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="why did u start trading?",
            voice_profile={"energy": "direct"},
            creator_name="Tjr",
            decision_policy={},
            creator_profile={"name": "Tjr"},
            allow_web=True,
        )

        answer = result.get("answer", "").lower()
        self.assertTrue(provider.grounded_calls)
        self.assertIn("tired of being broke", answer)
        self.assertNotEqual(answer.strip(), "2017")
        self.assertNotIn("i started trading in 2017", answer)

    def test_creator_journey_question_does_not_render_timeline_candidate(self):
        service, _provider = _load_personal_bio_service([], grounded_response_text="")

        candidate = service._candidate_from_fact_lookup(
            {
                "found": True,
                "fact_field": "public_fact",
                "answer_text": "I started trading in 2017.",
                "value": "2017",
                "confidence": 0.9,
            },
            fact_field="public_fact",
            entity_subject="trading",
        )

        answer = service._render_structured_fact_answer(
            candidate,
            "why did u start trading?",
            "Tjr",
            {"energy": "direct"},
        )

        self.assertEqual(answer, "")

    def test_turning_point_followup_does_not_reuse_broad_journey_cache(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text=(
                "Dan Martell says hitting rock bottom and spending time in jail forced him to choose a different path."
            ),
        )
        service_module = sys.modules["backend.services.personal_bio_service"]
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "Hitting rock bottom and sitting in jail was the mirror for me. That was the point where I decided the old path was done and I was going to build something different."}'
        )

        original_lookup = service_module.fact_registry.lookup_fact
        lookup_fields = []

        def fake_lookup(creator_id, entity_subject, fact_field, freshness_required="low"):
            lookup_fields.append(fact_field)
            if fact_field == "public_fact":
                return types.SimpleNamespace(
                    creator_id=str(creator_id),
                    entity_subject=entity_subject,
                    entity_type="",
                    fact_field="public_fact",
                    fact_value=(
                        "My journey started in a dark place, going from stolen cars to becoming a 5 time SaaS entrepreneur."
                    ),
                    source_url="https://example.com/broad-story",
                    source_domain="example.com",
                    source_title="Broad Story",
                    source_snippet="Dan Martell broad biography.",
                    confidence=0.9,
                    freshness="low",
                    verified_at="",
                    metadata={},
                )
            return None

        service_module.fact_registry.lookup_fact = fake_lookup
        try:
            result = service.handle_personal_question(
                user_id=1,
                creator_id=1,
                question="what made u turn it around?",
                voice_profile={"energy": "direct"},
                creator_name="Dan Martell",
                decision_policy={},
                creator_profile={"name": "Dan Martell"},
                conversation_history=[
                    {
                        "role": "user",
                        "content": "tell me about your background, whats your story/journey, how did u get rich?",
                    },
                    {
                        "role": "assistant",
                        "content": "My journey started in a dark place, going from stolen cars to becoming a 5 time SaaS entrepreneur.",
                    },
                ],
                allow_web=True,
            )
        finally:
            service_module.fact_registry.lookup_fact = original_lookup

        answer = result.get("answer", "").lower()
        self.assertTrue(provider.grounded_calls)
        self.assertIn("journey_turning_point", lookup_fields)
        self.assertNotIn("public_fact", lookup_fields)
        self.assertIn("jail", answer)
        self.assertNotIn("5 time saas", answer)
        joined_queries = " ".join(call[0] for call in provider.grounded_calls).lower()
        self.assertTrue("turning point" in joined_queries or "rock bottom" in joined_queries, provider.grounded_calls)

    def test_public_wealth_journey_does_not_decline_as_private_finance(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text="Alex Hormozi built wealth by scaling Gym Launch, selling a majority stake, and then building Acquisition.com.",
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "I got there by scaling Gym Launch, selling a majority stake, and then using those skills to build Acquisition.com."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="how did u get rich?",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "").lower()
        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertNotIn("personal finances", answer)
        self.assertNotIn("net worth private", answer)
        self.assertIn("gym launch", answer)

    def test_creator_journey_uses_web_evidence_even_without_regex_reason(self):
        service, provider = _load_personal_bio_service(
            [],
            grounded_response_text="Alex Hormozi: What is Acquisition.com & Why I Started It.",
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "I did not retire because the next game was using what I learned from Gym Launch to build and buy better companies through Acquisition.com."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="why didnt u just retire after selling gym launch, why did u start acquisition next",
            voice_profile={"energy": "direct"},
            creator_name="Alex Hormozi",
            decision_policy={},
            creator_profile={"name": "Alex Hormozi"},
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertIn("Acquisition.com", answer)
        self.assertNotIn("couldn't pin down one clean public quote", answer)

    def test_why_write_book_uses_synthesis_not_publication_date_shortcut(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Dan Martell | Buy Back Your Time",
                    "url": "https://example.com/buy-back-your-time",
                    "snippet": "Buy Back Your Time was published on January 17, 2023.",
                }
            ],
            grounded_response_text=(
                "Buy Back Your Time was published on January 17, 2023. "
                "Dan Martell says the book helps entrepreneurs stop building businesses that trap them."
            ),
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "I wrote Buy Back Your Time because I kept seeing founders build companies that owned them instead of giving them freedom."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="Why did you write Buy Back Your Time?",
            voice_profile={"energy": "direct"},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "id": 1,
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
                "soul_md": 'You teach founders to buy back time and build leverage.',
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertEqual(result.get("move"), "ANSWER_PUBLIC_FACT")
        self.assertIn("founders build companies", answer)
        self.assertNotIn("published on January 17, 2023", answer)

    def test_compound_application_and_availability_answers_both_parts(self):
        service, provider = _load_personal_bio_service(
            [
                {
                    "title": "Buy Back Your Time",
                    "url": "https://example.com/buy-back-your-time",
                    "snippet": "Buy Back Your Time is available on Amazon and Audible and teaches founders to audit low-value tasks.",
                }
            ],
            grounded_response_text=(
                "Buy Back Your Time is available on Amazon and Audible. "
                "The book teaches founders to audit low-value tasks and buy back their calendar."
            ),
        )
        rag_module = sys.modules["backend.rag"]
        rag_module.generate_chat_completion = lambda *args, **kwargs: (
            '{"answer": "Apply it by auditing your week, deleting or delegating the lowest-value tasks, then buying back one block of time first. You can get Buy Back Your Time on Amazon, Audible, or the publisher page."}'
        )

        result = service.handle_personal_question(
            user_id=1,
            creator_id=1,
            question="How can I apply Buy Back Your Time in my life, also where can I get it?",
            voice_profile={"energy": "direct"},
            creator_name="Dan Martell",
            decision_policy={},
            creator_profile={
                "id": 1,
                "name": "Dan Martell",
                "identity_fingerprint": 'Author of "Buy Back Your Time".',
                "soul_md": 'You teach founders to audit low-value tasks and buy back their time.',
            },
            allow_web=True,
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.grounded_calls)
        self.assertIn("auditing your week", answer)
        self.assertIn("Amazon", answer)
        self.assertNotEqual(answer.strip(), "You can get it on Amazon, Audible, or the publisher page.")

    def test_journey_direct_answer_rejects_source_metadata_as_reason(self):
        service, _provider = _load_personal_bio_service([], grounded_response_text="")

        answer = service._answer_public_creator_fact(
            "What was your journey, how did you get wealthy/rich",
            [
                {
                    "source": "web",
                    "title": "If I Wanted to Go From $0 to $1M in 12 Months",
                    "text": "Wanted to Go From $0 to $1M in 12 Months, Channel: I, Length:, Views: 1.",
                }
            ],
            "Dan Martell",
            voice_profile={"energy": "direct"},
        )

        self.assertEqual(answer, "")


if __name__ == "__main__":
    unittest.main()
