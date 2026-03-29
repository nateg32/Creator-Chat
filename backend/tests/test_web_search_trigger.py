"""Regression tests for smart web-search triggering in Creator Bot.

These tests cover two layers:
1. The query/retrieval decision engine that decides when live web search is
   required for creator-own facts and low-confidence RAG cases.
2. The grounded-RAG integration path that should trigger live search for
   creator-public facts, use the result in the answer, and fall back honestly
   when the search provider returns nothing.
"""

import importlib.util
import pathlib
import sys
import types
import unittest


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def _load_search_decision_engine():
    module_path = BASE_DIR / "services" / "search_decision_engine.py"
    spec = importlib.util.spec_from_file_location("search_decision_engine_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["search_decision_engine_test"] = module
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


def _load_grounded_rag(search_results, retrieved_chunks=None, search_mode="hybrid"):
    _stub_package("backend.prompts")
    _stub_package("backend.services")
    _stub_package("backend.core")
    search_engine_module = _load_search_decision_engine()
    sys.modules["backend.services.search_decision_engine"] = search_engine_module

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
    )

    class _Provider:
        def __init__(self):
            self.calls = []

        def search(self, query, creator_profile, **kwargs):
            self.calls.append((query, creator_profile.get("name")))
            return list(search_results)

    provider = _Provider()

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
    )
    _stub_module("backend.services.memory_service", memory_service=types.SimpleNamespace(update_memory=lambda *args, **kwargs: None))
    _stub_module(
        "backend.services.greeting_service",
        greeting_service=types.SimpleNamespace(generate_greeting=lambda *args, **kwargs: "Hi."),
        is_greeting=lambda *args, **kwargs: False,
    )
    _stub_module("backend.services.personal_bio_service", personal_bio_service=types.SimpleNamespace(answer=lambda *args, **kwargs: None))
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
        merge_support_sets=lambda primary, secondary, limit=4: (primary or []) + (secondary or []),
        retrieve_exact_text_matches=lambda *args, **kwargs: [],
    )
    _stub_module(
        "backend.services.out_of_domain_rules",
        default_bridge_question=lambda *args, **kwargs: "",
        detect_external_live_fact_topic=lambda *args, **kwargs: False,
        recent_bridge_topic=lambda *args, **kwargs: "",
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
        self.assertTrue(
            "check" in answer.lower() or "website" in answer.lower() or "amazon" in answer.lower(),
            answer,
        )


if __name__ == "__main__":
    unittest.main()
