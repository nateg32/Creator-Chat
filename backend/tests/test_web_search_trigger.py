"""Regression tests for smart web-search triggering in Creator Bot.

These tests cover two layers:
1. The query/retrieval decision engine that decides when live web search is
   required for creator-own facts and low-confidence RAG cases.
2. The grounded-RAG integration path that should trigger live search for
   creator-public facts, use the result in the answer, and fall back honestly
   when the search provider returns nothing.
"""

import asyncio
import importlib.util
import pathlib
import sys
import types
import unittest


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def _load_search_decision_engine():
    _stub_package("backend")
    _stub_package("backend.services")
    creator_fact_policy = _load_module("backend.services.creator_fact_policy", pathlib.Path("services") / "creator_fact_policy.py")

    class _StubEvidenceRouter:
        def __init__(self, creator):
            self.creator = creator or {}

        def build_plan(self, query, conversation_history=None, top_score=None, retrieved_chunks=None, web_results=None):
            lowered = str(query or "").lower()
            policy = creator_fact_policy.classify_creator_fact_query(query)
            if policy.kind == "availability":
                query_goal = "availability_lookup"
            elif policy.kind in {"publication_timeline", "creator_start_timeline"}:
                query_goal = "timeline_lookup"
            elif policy.kind == "creator_journey":
                query_goal = "journey_lookup"
            elif policy.kind == "price":
                query_goal = "price_lookup"
            elif policy.kind == "stats":
                query_goal = "stat_lookup"
            elif "do you have a book" in lowered or "is buy back your time your book" in lowered or "tell me about buy back your time" in lowered:
                query_goal = "entity_confirmation"
            else:
                query_goal = "general"

            should_search_web = bool(policy.requires_web)
            primary_world = "creator_world" if should_search_web else "creator_memory"
            return types.SimpleNamespace(
                query_goal=query_goal,
                entity_subject="buy back your time" if "buy back your time" in lowered else "",
                should_search_web=should_search_web,
                primary_world=primary_world,
                answer_mode="hybrid",
                risk_flags=["public_fact"] if should_search_web else [],
            )

    _stub_module("backend.services.evidence_router", EvidenceRouter=_StubEvidenceRouter)
    module_path = BASE_DIR / "services" / "search_decision_engine.py"
    spec = importlib.util.spec_from_file_location("search_decision_engine_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["search_decision_engine_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_module(name: str, relative_path):
    module_path = BASE_DIR / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stub_package(name: str):
    module = types.ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = module
    return module


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


class _DummyPlan:
    def __init__(self):
        self.route = "ROUTE_2_TASK"
        self.routing = "IN_DOMAIN"
        self.mode = "TASK"
        self.stage = "TASK"
        self.grounding = types.SimpleNamespace(video_policy="none", requires_sources=False)

    def dict(self):
        return {
            "route": self.route,
            "routing": self.routing,
                "mode": self.mode,
                "stage": self.stage,
                "grounding": {"video_policy": "none", "requires_sources": False},
            }


class _FakeInteractionEngine:
    def build_interaction_plan(self, *args, **kwargs):
        return _DummyPlan()

    def render_response(self, plan, creator_row, support_set, *args, **kwargs):
        for chunk in support_set or []:
            content = str(chunk.get("content") or "")
            snippet = str(chunk.get("snippet") or "")
            combined = f"{content} {snippet}"
            if "2023" in combined or "September" in combined:
                return "Buy Back Your Time was published in 2023."
        return "I don't have that."

    def log_turn(self, *args, **kwargs):
        return None

    def store_interaction(self, *args, **kwargs):
        return None


class _FakeDecisionService:
    def resolve_followup_question(self, question, history):
        return question

    def classify_question(self, question, intent, history=None):
        return ("domain_advice", "general", 3)

    def get_policy(self, creator_row):
        return {}

    def choose_move(self, *args, **kwargs):
        return "ANSWER_DIRECTLY"


class _FakePriorityService:
    def calculate_mvc_score(self, *args, **kwargs):
        return 0


def _load_grounded_rag(
    search_results,
    retrieved_chunks=None,
    search_mode="hybrid",
    grounded_results=None,
    grounded_overview_payload=None,
    fallback_search_results=None,
):
    _stub_package("backend.prompts")
    _stub_package("backend.services")
    _stub_package("backend.core")
    _stub_package("backend.utils")
    search_engine_module = _load_search_decision_engine()
    sys.modules["backend.services.search_decision_engine"] = search_engine_module
    _stub_module("backend.services.decision_service", decision_service=_FakeDecisionService())
    creator_entity_module = _load_module(
        "backend.services.creator_entity_service",
        pathlib.Path("services") / "creator_entity_service.py",
    )
    fact_registry_module = _load_module(
        "backend.services.fact_registry",
        pathlib.Path("services") / "fact_registry.py",
    )
    evidence_router_module = _load_module(
        "backend.services.evidence_router",
        pathlib.Path("services") / "evidence_router.py",
    )
    sys.modules["backend.services.evidence_router"] = evidence_router_module
    sys.modules["backend.services.creator_entity_service"] = creator_entity_module
    sys.modules["backend.services.fact_registry"] = fact_registry_module

    creator_row = {
        "id": 1,
        "name": "Dan Martell",
        "handle": "danmartell",
        "search_mode": search_mode,
        "soul_md": "",
        "platform_configs": {},
        "creator_category": "business",
        "rhythm_profile_json": {},
        "style_fingerprint": {},
        "voice_profile": {},
    }

    def execute_one(query, params=None):
        text = str(query or "").lower()
        if "information_schema.columns" in text:
            return {"exists": 1}
        if "from creators" in text:
            return creator_row
        return None

    def execute_query(*args, **kwargs):
        return []

    fake_db = types.SimpleNamespace(
        execute_one=execute_one,
        execute_query=execute_query,
        execute_update=lambda *args, **kwargs: None,
    )
    fake_settings = types.SimpleNamespace(
        EMBEDDING_MODEL="test-embed",
        ROUTER_MODEL="test-router",
        RERANK_MODEL="test-rerank",
        MODEL_CLASSIFICATION="test-classify",
        MODEL_MAIN_REPLY="test-main",
        MODEL_MEMORY="test-memory",
        MODEL_VERIFY="test-verify",
        REWRITE_MODEL="test-rewrite",
    )
    fake_rag = types.SimpleNamespace(
        create_embedding=lambda *args, **kwargs: [0.0],
        generate_chat_completion=lambda *args, **kwargs: '{"classification": "SUFFICIENT"}',
        get_persona=lambda *args, **kwargs: "",
        # Provided so that other test files which load ``grounded_rag`` and
        # later call into deferred ``from backend.rag import get_client`` paths
        # still find the symbol when this module's stub happens to win the
        # ``sys.modules`` slot during collection ordering.
        get_client=lambda *args, **kwargs: None,
        get_async_client=lambda *args, **kwargs: None,
        get_chat_client=lambda *args, **kwargs: None,
        get_async_chat_client=lambda *args, **kwargs: None,
    )

    class _Provider:
        def __init__(self):
            self.calls = []
            self.search_calls = []
            self.grounded_calls = []

        def search(self, query, creator_profile, **kwargs):
            self.search_calls.append((query, creator_profile.get("name")))
            self.calls.append(("search", query, creator_profile.get("name")))
            return list(search_results)

        def grounded_overview(self, query, creator_profile, conversation_history=None, max_queries=4):
            self.grounded_calls.append((query, creator_profile.get("name")))
            self.calls.append(("grounded_overview", query, creator_profile.get("name")))
            if grounded_overview_payload is not None:
                return {
                    "response_text": grounded_overview_payload.get("response_text", ""),
                    "citations": list(grounded_overview_payload.get("citations") or []),
                    "search_entry_point": grounded_overview_payload.get("search_entry_point") or {"rendered_content": ""},
                    "query_plan": list(grounded_overview_payload.get("query_plan") or [query]),
                    "results": list(grounded_overview_payload.get("results") or []),
                    "sources": list(grounded_overview_payload.get("sources") or []),
                    "packages": list(grounded_overview_payload.get("packages") or []),
                }
            results = list(grounded_results if grounded_results is not None else search_results)
            return {
                "response_text": "Buy Back Your Time was published in September 2023." if results else "",
                "citations": [],
                "search_entry_point": {"rendered_content": ""},
                "query_plan": [query],
                "results": results,
                "sources": [],
                "packages": [],
            }

    class _FallbackProvider:
        def __init__(self):
            self.search_calls = []

        def search(self, query, creator_profile, **kwargs):
            self.search_calls.append((query, creator_profile.get("name")))
            return list(fallback_search_results or [])

    class _FakePersonalBioService:
        def handle_personal_question(
            self,
            user_id,
            creator_id,
            question,
            voice_profile,
            creator_name,
            decision_policy,
            creator_profile=None,
            conversation_history=None,
            allow_web=True,
        ):
            profile = dict(creator_profile or {})
            profile.setdefault("name", creator_name)
            if not allow_web:
                return {
                    "answer": "I don't have that in my ingested content right now.",
                    "confidence": "LOW",
                    "sources": [],
                    "move": "NO_WEB_INGESTED_ONLY",
                }

            if callable(getattr(provider, "grounded_overview", None)):
                overview = provider.grounded_overview(question, profile, conversation_history=None)
                results = list(overview.get("results") or [])
            else:
                results = provider.search(question, profile)

            if results:
                snippet = str(results[0].get("snippet") or "")
                return {
                    "answer": snippet or "Buy Back Your Time was published in 2023.",
                    "confidence": "HIGH",
                    "sources": results,
                    "move": "ANSWER_PUBLIC_FACT",
                }

            return {
                "answer": "I want to give you the right date on that. Check my Amazon listing or my website for the exact publication info.",
                "confidence": "LOW",
                "sources": [],
                "move": "DIRECT_TO_OFFICIAL_SOURCE",
            }

    provider = _Provider()
    fallback_provider = _FallbackProvider() if fallback_search_results is not None else None

    _stub_module("backend.db", db=fake_db)
    _stub_module("backend.settings", settings=fake_settings)
    _stub_module("backend.rag", **fake_rag.__dict__)
    _stub_module("backend.prompts.creator_base_prompt", CREATOR_BASE_SYSTEM_PROMPT="")
    _stub_module("backend.services.style_distiller", StyleDistiller=type("StyleDistiller", (), {}))
    _stub_module("backend.services.style_scorer", StyleScorer=type("StyleScorer", (), {}))
    _stub_module("backend.services.content_finder", ContentFinder=type("ContentFinder", (), {}))
    _stub_module(
        "backend.services.research_provider",
        GeminiResearchProvider=type("GeminiResearchProvider", (), {}),
        get_research_provider=lambda: provider,
        get_fallback_research_provider=lambda: fallback_provider,
    )
    _stub_module("backend.services.memory_service", memory_service=types.SimpleNamespace(update_memory=lambda *args, **kwargs: None))
    _stub_module(
        "backend.services.greeting_service",
        greeting_service=types.SimpleNamespace(generate_greeting=lambda *args, **kwargs: "Hi."),
        is_greeting=lambda *args, **kwargs: False,
    )
    _stub_module("backend.services.personal_bio_service", personal_bio_service=_FakePersonalBioService())
    _stub_module("backend.services.persona_filter", apply_persona_surface_filter=lambda text, *args, **kwargs: text)
    _stub_module("backend.services.curiosity_service", curiosity_service=types.SimpleNamespace())
    _stub_module("backend.services.rhythm_shaper", rhythm_shaper=types.SimpleNamespace(apply_rhythm=lambda text, *args, **kwargs: text))
    _stub_module("backend.services.user_priority_service", user_priority_service=_FakePriorityService())
    _stub_module("backend.services.decision_service", decision_service=_FakeDecisionService())
    _stub_module(
        "backend.services.memory_loop_service",
        memory_loop_service=types.SimpleNamespace(extract_memory_updates=lambda *args, **kwargs: []),
    )
    _stub_module("backend.services.steering_service", steering_service=types.SimpleNamespace())
    _stub_module(
        "backend.services.classifiers",
        classifiers=types.SimpleNamespace(
            classify_all=lambda *args, **kwargs: {
                "intent": "domain_advice",
                "flags": {},
                "request_type": "question",
                "primary_domain": "business",
            }
        ),
    )
    _stub_module(
        "backend.services.stronghold_guard",
        stronghold_guard=types.SimpleNamespace(
            calculate_domain_match=lambda *args, **kwargs: "GENERAL_CHAT",
            generate_boundary_message=lambda *args, **kwargs: "Let's keep it in my lane.",
        ),
    )
    _stub_module(
        "backend.services.conversation_state_manager",
        ConversationStateManager=type(
            "ConversationStateManager",
            (),
            {
                "__init__": lambda self, *args, **kwargs: setattr(self, "state", {}),
                "save_state": lambda self: None,
            },
        ),
    )
    _stub_module(
        "backend.core.interaction_engine",
        interaction_engine=_FakeInteractionEngine(),
        InteractionPlan=_DummyPlan,
        strip_all_markdown=lambda text, **kwargs: text,
    )
    _stub_module("backend.services.web_verify", web_verify=types.SimpleNamespace(verify_fact=lambda *args, **kwargs: {"confidence": 1.0}))
    _stub_module("backend.services.grammar_normalizer", grammar_normalizer=types.SimpleNamespace())
    _stub_module(
        "backend.services.formatting",
        clean_response=lambda text, **kwargs: text,
        clean_for_stream_chunk=lambda text: text,
        should_strip_hyphens=lambda config: False,
    )
    _stub_module("backend.services.assumption_blocker", assumption_blocker=types.SimpleNamespace())
    _stub_module("backend.services.image_identity_service", image_identity_service=types.SimpleNamespace(maybe_answer_from_image=lambda *args, **kwargs: None))
    _stub_module("backend.services.voice_dna", build_voice_echo_block=lambda *args, **kwargs: "")
    _stub_module("backend.services.conversation_closure", get_bridge_question=lambda *args, **kwargs: "")
    _stub_module("backend.utils.url_health", check_url_alive_sync=lambda *args, **kwargs: True, is_url_known_dead=lambda *args, **kwargs: False)

    live_rules_path = BASE_DIR / "services" / "live_search_rules.py"
    live_rules_spec = importlib.util.spec_from_file_location(
        "backend.services.live_search_rules",
        live_rules_path,
    )
    live_rules_module = importlib.util.module_from_spec(live_rules_spec)
    assert live_rules_spec.loader is not None
    sys.modules["backend.services.live_search_rules"] = live_rules_module
    live_rules_spec.loader.exec_module(live_rules_module)

    regurgitation_guard_path = BASE_DIR / "services" / "regurgitation_guard.py"
    regurgitation_guard_spec = importlib.util.spec_from_file_location(
        "backend.services.regurgitation_guard",
        regurgitation_guard_path,
    )
    regurgitation_guard_module = importlib.util.module_from_spec(regurgitation_guard_spec)
    assert regurgitation_guard_spec.loader is not None
    sys.modules["backend.services.regurgitation_guard"] = regurgitation_guard_module
    regurgitation_guard_spec.loader.exec_module(regurgitation_guard_module)

    _stub_module(
        "backend.services.rag_text_matcher",
        extract_named_resource_fragments=lambda *args, **kwargs: [],
        merge_support_sets=lambda primary, secondary, limit=4: (primary or []) + (secondary or []),
        retrieve_sparse_text_matches=lambda *args, **kwargs: [],
        retrieve_exact_text_matches=lambda *args, **kwargs: [],
    )
    _stub_module(
        "backend.services.recommendation_asset_service",
        recommendation_asset_service=types.SimpleNamespace(
            get_profile=lambda *args, **kwargs: {},
            score_fit=lambda *args, **kwargs: 0.5,
        ),
    )
    _stub_module(
        "backend.services.recommendation_feedback_service",
        recommendation_feedback_service=types.SimpleNamespace(
            log_impression=lambda *args, **kwargs: 1,
            log_event=lambda *args, **kwargs: 1,
        ),
    )
    _stub_module(
        "backend.services.out_of_domain_rules",
        default_bridge_question=lambda *args, **kwargs: "",
        detect_general_knowledge_topic=lambda *args, **kwargs: False,
        detect_external_live_fact_topic=lambda *args, **kwargs: False,
        recent_bridge_topic=lambda *args, **kwargs: "",
        should_redirect_general_knowledge=lambda *args, **kwargs: False,
        should_soft_decline_external_live_fact=lambda *args, **kwargs: False,
    )

    module_path = BASE_DIR / "grounded_rag.py"
    spec = importlib.util.spec_from_file_location("grounded_rag_web_search_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    module.retrieve_candidates = lambda *args, **kwargs: list(retrieved_chunks or [])
    module.recommend_one_content = lambda *args, **kwargs: {
        "best_candidate": None,
        "q_emb": [0.0],
        "confidence": 0.0,
        "resource_intent": {"preferred_platforms": []},
    }
    module._should_run_resource_recommender = lambda *args, **kwargs: False
    module.needs_links = lambda *args, **kwargs: False
    module.get_enabled_platforms_for_creator = lambda *args, **kwargs: []
    module._test_creator_row = creator_row
    module._test_provider = provider
    module._test_fallback_provider = fallback_provider
    return module, provider


class WebSearchTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.search_engine_module = _load_search_decision_engine()
        cls.SearchDecisionEngine = cls.search_engine_module.SearchDecisionEngine

    def setUp(self):
        self.creator = {
            "id": 1,
            "name": "Dan Martell",
            "soul_md": 'Author of "Buy Back Your Time".',
            "identity_fingerprint": 'Built SaaS companies and wrote "Buy Back Your Time".',
        }
        self.engine = self.SearchDecisionEngine(self.creator)

    def test_creator_own_facts_trigger_web_search(self):
        queries = [
            "when was your book published",
            "how many followers does Dan Martell have",
            "what is Dan Martell's latest podcast episode",
            "where can I buy Dan Martell's course",
            "what is Dan Martell's net worth",
            "why did you start trading",
        ]
        for query in queries:
            decision = self.engine.pre_retrieval_decision(query)
            self.assertTrue(decision.should_search, query)
            self.assertEqual(decision.phase, "pre_retrieval")

    def test_low_rag_confidence_triggers_web_search(self):
        decision = self.engine.post_retrieval_decision(
            "how do you build leverage",
            chunks=[{"content": "some weak hit"}],
            top_score=0.45,
        )
        self.assertTrue(decision.should_search)
        self.assertEqual(decision.reason, "low_rag_confidence")

    def test_high_rag_confidence_skips_web_search(self):
        queries = [
            "what's your morning routine",
            "how do you deal with failure",
            "what advice would you give a new entrepreneur",
        ]
        for query in queries:
            decision = self.engine.post_retrieval_decision(
                query,
                chunks=[{"content": "strong experiential hit"}],
                top_score=0.83,
            )
            self.assertFalse(decision.should_search, query)
            self.assertEqual(decision.reason, "high_rag_confidence")

    def test_verifiable_fact_queries_trigger_web_search(self):
        queries = [
            "when did you launch the book",
            "what year did it come out",
            "how much does the course cost",
            "what is the price of your program",
            "what date was it released",
            "which month did you publish it",
        ]
        for query in queries:
            decision = self.engine.pre_retrieval_decision(query)
            self.assertTrue(decision.should_search, query)

    def test_entity_confirmation_skips_web_search(self):
        queries = [
            "do you know the book buy your time",
            "do you have a book",
            "tell me about buy back your time",
            "is buy back your time your book?",
        ]
        for query in queries:
            decision = self.engine.pre_retrieval_decision(query)
            self.assertFalse(decision.should_search, query)

    def test_entity_confirmation_with_no_rag_chunks_triggers_web_search(self):
        decision = self.engine.post_retrieval_decision(
            "is buy back your time your book?",
            chunks=[],
            top_score=None,
        )
        self.assertTrue(decision.should_search)
        self.assertEqual(decision.reason, "no_entity_support")

    def test_web_search_result_used_in_response(self):
        search_results = [
            {
                "title": "Buy Back Your Time",
                "url": "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/",
                "snippet": "Buy Back Your Time was published in September 2023.",
                "platform": "web",
            }
        ]
        module, provider = _load_grounded_rag(search_results=search_results, retrieved_chunks=[])

        result = module.grounded_rag_ask(
            1,
            "when was Buy Back Your Time published",
            user_id=1,
            thread_id="test-thread",
            conversation_history=[],
            user_name="Nathan",
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.calls, "Expected live search to be called")
        self.assertTrue("2023" in answer or "September" in answer, answer)
        self.assertNotIn("I don't have", answer)
        self.assertNotIn("I'm not sure", answer)
        self.assertNotIn("I wouldn't want to guess", answer)

    def test_gemini_grounded_overview_used_for_creator_public_facts(self):
        grounded_results = [
            {
                "title": "Buy Back Your Time",
                "url": "https://www.amazon.com/dp/059342297X",
                "snippet": "Buy Back Your Time was published in September 2023.",
                "platform": "web",
                "confidence": 0.9,
                "relation": "PUBLIC_FACTS",
            }
        ]
        module, provider = _load_grounded_rag(search_results=[], grounded_results=grounded_results, retrieved_chunks=[])

        result = module.grounded_rag_ask(
            1,
            "when was your book published",
            user_id=1,
            thread_id="test-thread",
            conversation_history=[],
            user_name="Nathan",
        )

        self.assertTrue(provider.grounded_calls, "Expected grounded_overview to be called")
        self.assertFalse(provider.search_calls, "Expected factual creator query to use grounded_overview instead of generic search")
        answer = result.get("answer", "")
        self.assertTrue("2023" in answer or "September" in answer, answer)

    def test_gemini_grounded_overview_sources_without_results_still_surface_web_results(self):
        module, provider = _load_grounded_rag(
            search_results=[],
            grounded_overview_payload={
                "response_text": "Buy Back Your Time was published in September 2023.",
                "results": [],
                "sources": [
                    {
                        "title": "Buy Back Your Time",
                        "url": "https://www.amazon.com/dp/059342297X",
                        "platform": "web",
                    }
                ],
                "citations": [
                    {
                        "title": "Buy Back Your Time",
                        "url": "https://www.amazon.com/dp/059342297X",
                        "snippet": "Buy Back Your Time was published in September 2023.",
                        "platform": "web",
                        "score": 0.92,
                    }
                ],
            },
            retrieved_chunks=[],
        )

        results = module._run_live_web_search(
            "when was your book published",
            module._test_creator_row,
            conversation_history=[],
            intent_metadata={"intent": "PUBLIC_CREATOR_FACT"},
        )

        self.assertTrue(provider.grounded_calls, "Expected grounded_overview to be called")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.amazon.com/dp/059342297X")

    def test_fallback_search_provider_used_when_primary_returns_no_results(self):
        module, provider = _load_grounded_rag(
            search_results=[],
            grounded_results=[],
            fallback_search_results=[
                {
                    "title": "Buy Back Your Time",
                    "url": "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/",
                    "snippet": "Buy Back Your Time was published in September 2023.",
                    "platform": "web",
                    "confidence": 0.91,
                    "relation": "PUBLIC_FACT_VERIFIED",
                }
            ],
            retrieved_chunks=[],
        )

        results = module._run_live_web_search(
            "when was your book published",
            module._test_creator_row,
            conversation_history=[],
            intent_metadata={"intent": "PUBLIC_CREATOR_FACT"},
        )

        self.assertTrue(provider.grounded_calls, "Expected primary provider to run first")
        self.assertTrue(module._test_fallback_provider.search_calls, "Expected fallback provider to be used")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/")

    def test_no_hallucination_when_search_fails(self):
        module, provider = _load_grounded_rag(search_results=[], retrieved_chunks=[])

        result = module.grounded_rag_ask(
            1,
            "when was Buy Back Your Time published",
            user_id=1,
            thread_id="test-thread",
            conversation_history=[],
            user_name="Nathan",
        )

        answer = result.get("answer", "")
        self.assertTrue(provider.calls, "Expected live search to be called")
        self.assertFalse("2023" in answer or "September" in answer, answer)
        self.assertNotIn("I haven't really talked about that publicly", answer)
        self.assertNotIn("I don't have that information", answer)
        self.assertNotIn("Dan Martell's", answer)
        self.assertTrue(
            "check" in answer.lower() or "website" in answer.lower() or "amazon" in answer.lower(),
            answer,
        )

    def test_buy_in_book_title_does_not_trigger_pricing_fallback(self):
        module, provider = _load_grounded_rag(search_results=[], grounded_results=[], retrieved_chunks=[])

        result = module.grounded_rag_ask(
            1,
            "when did u publish buy your time",
            user_id=1,
            thread_id="test-thread",
            conversation_history=[],
            user_name="Nathan",
        )

        answer = result.get("answer", "")
        self.assertNotIn("pricing info", answer.lower(), answer)
        self.assertNotIn("checkout page", answer.lower(), answer)
        self.assertTrue("date" in answer.lower() or "publication" in answer.lower() or "amazon" in answer.lower(), answer)

    def test_ingested_only_mode_never_calls_web_search(self):
        search_results = [
            {
                "title": "Buy Back Your Time",
                "url": "https://www.penguinrandomhouse.com/books/123456/buy-back-your-time/",
                "snippet": "Buy Back Your Time was published in September 2023.",
                "platform": "web",
            }
        ]
        module, provider = _load_grounded_rag(
            search_results=search_results,
            retrieved_chunks=[],
            search_mode="ingested",
        )

        result = module.grounded_rag_ask(
            1,
            "when was Buy Back Your Time published",
            user_id=1,
            thread_id="test-thread",
            conversation_history=[],
            user_name="Nathan",
        )

        self.assertFalse(provider.calls, "Web search should stay off in ingested-only mode")
        self.assertEqual(result.get("answer"), "I don't have that in my ingested content right now.")

    def test_streaming_falls_back_to_render_response_when_stream_empty(self):
        module, _ = _load_grounded_rag(search_results=[], retrieved_chunks=[])
        sys.modules["backend.services.out_of_domain_rules"].detect_general_knowledge_topic = lambda *args, **kwargs: False

        async def _create_embedding(*args, **kwargs):
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0])])

        async def _search_with_embedding_async(*args, **kwargs):
            return []

        async def _fake_stream_gen():
            if False:
                yield None

        async def _fake_stream(*args, **kwargs):
            return _fake_stream_gen()

        module.rag.get_async_client = lambda: types.SimpleNamespace(
            embeddings=types.SimpleNamespace(create=_create_embedding)
        )
        module.fetch_all_document_titles = lambda *args, **kwargs: []
        module.interaction_engine.classify_route = lambda *args, **kwargs: "ROUTE_2_TASK"
        module.interaction_engine.memory = types.SimpleNamespace(
            search_with_embedding_async=_search_with_embedding_async
        )
        module.interaction_engine.render_combined_pass_stream_async = _fake_stream

        async def _collect():
            parts = []
            async for item in module.grounded_rag_stream(
                creator_id=1,
                question="help me build leverage",
                thread_id="thread-1",
                conversation_history=[],
                user_preferences=None,
                user_name="Nathan",
                user_id=1,
            ):
                parts.append(item)
            return parts

        output = asyncio.run(_collect())
        joined = " ".join(str(item) for item in output)
        self.assertIn("I don't have that.", joined)

    def test_streaming_identity_path_falls_back_when_personal_answer_empty(self):
        module, _ = _load_grounded_rag(search_results=[], retrieved_chunks=[])
        sys.modules["backend.services.out_of_domain_rules"].detect_general_knowledge_topic = lambda *args, **kwargs: False

        async def _create_embedding(*args, **kwargs):
            return types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0])])

        module.rag.get_async_client = lambda: types.SimpleNamespace(
            embeddings=types.SimpleNamespace(create=_create_embedding)
        )
        module.interaction_engine.classify_route = lambda *args, **kwargs: "ROUTE_2_TASK"
        module.decision_service.classify_question = lambda *args, **kwargs: ("personal_bio", "identity", 3)
        module.personal_bio_service.handle_personal_question = lambda *args, **kwargs: {
            "answer": "",
            "confidence": "LOW",
            "sources": [],
            "move": "ANSWER_DIRECTLY",
        }

        async def _collect():
            parts = []
            async for item in module.grounded_rag_stream(
                creator_id=1,
                question="who are you",
                thread_id="thread-1",
                conversation_history=[],
                user_preferences=None,
                user_name="Nathan",
                user_id=1,
            ):
                parts.append(item)
            return parts

        output = asyncio.run(_collect())
        joined = " ".join(str(item) for item in output)
        self.assertIn("I'm Dan Martell", joined)


if __name__ == "__main__":
    unittest.main()
