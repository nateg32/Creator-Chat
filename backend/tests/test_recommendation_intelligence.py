"""Tests for the upgraded recommendation intelligence stack."""

import importlib.util
import sys
import types
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


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


def _load_module(name: str, relative_path: str):
    module_path = BACKEND_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_grounded_rag():
    _stub_package("backend.prompts")
    _stub_package("backend.services")
    _stub_package("backend.core")

    db_stub = types.SimpleNamespace(
        execute_one=lambda *args, **kwargs: None,
        execute_query=lambda *args, **kwargs: [],
        execute_update=lambda *args, **kwargs: None,
        execute_insert=lambda *args, **kwargs: 1,
    )
    _stub_module("backend.db", db=db_stub)
    _stub_module("backend.settings", settings=types.SimpleNamespace(
        EMBEDDING_MODEL="test-embed",
        ROUTER_MODEL="test-router",
        RERANK_MODEL="test-rerank",
        MODEL_CLASSIFICATION="test-classify",
        REWRITE_MODEL="test-rewrite",
    ))
    _stub_module(
        "backend.rag",
        create_embedding=lambda *args, **kwargs: [0.0],
        generate_chat_completion=lambda *args, **kwargs: '{"winner_id": "cand_b", "reason": "better fit"}',
        get_client=lambda: types.SimpleNamespace(
            embeddings=types.SimpleNamespace(
                create=lambda **kwargs: types.SimpleNamespace(data=[types.SimpleNamespace(embedding=[0.0])])
            )
        ),
    )
    _stub_module("backend.prompts.creator_base_prompt", CREATOR_BASE_SYSTEM_PROMPT="")
    _stub_module("backend.services.style_distiller", StyleDistiller=type("StyleDistiller", (), {}))
    _stub_module("backend.services.style_scorer", StyleScorer=type("StyleScorer", (), {}))
    _stub_module("backend.services.content_finder", ContentFinder=type("ContentFinder", (), {}))
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}))
    _stub_module("backend.services.memory_service", memory_service=types.SimpleNamespace())
    _stub_module("backend.services.greeting_service", greeting_service=types.SimpleNamespace(), is_greeting=lambda *args, **kwargs: False)
    _stub_module("backend.services.personal_bio_service", personal_bio_service=types.SimpleNamespace())
    _stub_module("backend.services.persona_filter", apply_persona_surface_filter=lambda *args, **kwargs: "")
    _stub_module("backend.services.curiosity_service", curiosity_service=types.SimpleNamespace())
    _stub_module("backend.services.rhythm_shaper", rhythm_shaper=types.SimpleNamespace())
    _stub_module("backend.services.user_priority_service", user_priority_service=types.SimpleNamespace())
    _stub_module("backend.services.decision_service", decision_service=types.SimpleNamespace(resolve_followup_question=lambda q, h: q))
    _stub_module("backend.services.memory_loop_service", memory_loop_service=types.SimpleNamespace())
    _stub_module("backend.services.steering_service", steering_service=types.SimpleNamespace())
    _stub_module("backend.services.classifiers", classifiers=types.SimpleNamespace())
    _stub_module("backend.services.stronghold_guard", stronghold_guard=types.SimpleNamespace())
    _stub_module("backend.core.interaction_engine", interaction_engine=types.SimpleNamespace(), InteractionPlan=type("InteractionPlan", (), {}), strip_all_markdown=lambda text, allow_links=False: text)
    _stub_module("backend.services.web_verify", web_verify=types.SimpleNamespace())
    _stub_module("backend.services.formatting", clean_response=lambda text, **kwargs: text, should_strip_hyphens=lambda config: False)
    _stub_module("backend.services.assumption_blocker", assumption_blocker=types.SimpleNamespace())
    _stub_module("backend.services.image_identity_service", image_identity_service=types.SimpleNamespace())
    _stub_module("backend.services.live_search_rules", build_live_search_query=lambda *args, **kwargs: "", extract_requested_platforms=lambda *args, **kwargs: [], needs_fresh_public_web_search=lambda *args, **kwargs: False)
    _stub_module("backend.services.creator_entity_service", creator_entity_service=types.SimpleNamespace(resolve_entity=lambda *args, **kwargs: None))
    _stub_module("backend.services.evidence_router", EvidenceRouter=type("EvidenceRouter", (), {"__init__": lambda self, creator: None, "build_plan": lambda self, *args, **kwargs: None}), EvidencePlan=type("EvidencePlan", (), {}), detect_evidence_contradiction=lambda *args, **kwargs: {"has_contradiction": False}, log_evidence_plan=lambda *args, **kwargs: None)
    _stub_module("backend.services.search_decision_engine", SearchDecision=type("SearchDecision", (), {}), SearchDecisionEngine=type("SearchDecisionEngine", (), {}), log_search_decision=lambda *args, **kwargs: None)
    _stub_module(
        "backend.services.rag_text_matcher",
        extract_named_resource_fragments=lambda text: ["26 harsh lessons i learned in 2025"] if "26 harsh lessons" in text.lower() else [],
        merge_support_sets=lambda primary, secondary, limit=4: (primary or []) + (secondary or []),
        retrieve_sparse_text_matches=lambda *args, **kwargs: [],
        retrieve_exact_text_matches=lambda *args, **kwargs: [],
    )
    _stub_module("backend.services.out_of_domain_rules", default_bridge_question=lambda *args, **kwargs: "", detect_external_live_fact_topic=lambda *args, **kwargs: False, recent_bridge_topic=lambda *args, **kwargs: "", should_soft_decline_external_live_fact=lambda *args, **kwargs: False)
    regurgitation_guard = _load_module("backend.services.regurgitation_guard", "services/regurgitation_guard.py")
    sys.modules["backend.services.regurgitation_guard"] = regurgitation_guard
    recommendation_asset = _load_module("backend.services.recommendation_asset_service", "services/recommendation_asset_service.py")
    recommendation_feedback = _load_module("backend.services.recommendation_feedback_service", "services/recommendation_feedback_service.py")
    sys.modules["backend.services.recommendation_asset_service"] = recommendation_asset
    sys.modules["backend.services.recommendation_feedback_service"] = recommendation_feedback

    module_path = BACKEND_ROOT / "grounded_rag.py"
    spec = importlib.util.spec_from_file_location("grounded_rag_recommendation_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class RecommendationIntelligenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grounded_rag = _load_grounded_rag()
        cls.asset_service = _load_module("recommendation_asset_service_tests", "services/recommendation_asset_service.py")
        cls.eval_service = _load_module("recommendation_eval_service_tests", "services/recommendation_eval_service.py")

    def test_query_variants_expand_named_resource_and_medium(self):
        variants = self.grounded_rag._build_recommendation_query_variants(
            "what about from your video 26 harsh lessons i learned in 2025",
            {
                "resource_type": "video",
                "intent_type": "recommend_content",
                "implicit_goal": "find the exact resource",
            },
            creator_profile={"name": "Alex Hormozi"},
            history=[],
            allow_llm=False,
            limit=3,
        )

        self.assertGreaterEqual(len(variants), 2)
        self.assertTrue(any("26 harsh lessons" in item.lower() for item in variants))
        self.assertTrue(any("video" in item.lower() for item in variants))

    def test_asset_profile_prefers_tactical_fit_for_how_to(self):
        candidate = {
            "document_id": 11,
            "title": "5 Steps To Fix Your Sales Process",
            "content": "Step 1 fix the script. Step 2 tighten the follow up. Here is the process.",
            "source_ref": {"content_type": "video", "platform": "youtube"},
        }
        profile = self.asset_service.recommendation_asset_service.get_profile(1, candidate)
        fit = self.asset_service.recommendation_asset_service.score_fit(
            profile,
            "what video should I watch to fix my sales process",
            resource_intent={"learning_phase": "execution", "specificity": "recommendation"},
            context_features={"wants_tactical": True, "wants_video": True},
        )

        self.assertEqual(profile["content_mode"], "tactical")
        self.assertGreater(fit, 0.6)

    def test_rerank_candidates_uses_asset_fit_and_query_coverage(self):
        candidates = [
            {
                "title": "Sales Script Fixes",
                "content": "step by step sales script process",
                "source_ref": {"content_type": "video", "platform": "youtube", "title": "Sales Script Fixes", "canonical_url": "https://youtube.com/watch?v=a"},
                "distance": 0.3,
                "evidence_metrics": {"max_sim": 0.74, "density": 3},
                "retrieval_signals": {"query_coverage": 2, "rrf_total": 0.04, "sparse_hits": 1, "dense_hits": 2},
                "asset_profile": {"summary": "Tactical sales script walkthrough.", "problem_solved": "Helps fix sales scripts.", "audience_level": "general", "content_mode": "tactical", "format_label": "video", "actionability_score": 0.9, "primary_topic": "sales", "secondary_topics": ["script"]},
            },
            {
                "title": "Sales Mindset",
                "content": "confidence and belief in sales",
                "source_ref": {"content_type": "video", "platform": "youtube", "title": "Sales Mindset", "canonical_url": "https://youtube.com/watch?v=b"},
                "distance": 0.28,
                "evidence_metrics": {"max_sim": 0.76, "density": 2},
                "retrieval_signals": {"query_coverage": 1, "rrf_total": 0.02, "sparse_hits": 0, "dense_hits": 1},
                "asset_profile": {"summary": "Mindset content.", "problem_solved": "Helps with confidence.", "audience_level": "general", "content_mode": "mindset", "format_label": "video", "actionability_score": 0.4, "primary_topic": "sales", "secondary_topics": ["confidence"]},
            },
        ]

        reranked = self.grounded_rag.rerank_candidates(
            candidates,
            "sales script fix",
            {"intent_type": "how_to", "specificity": "recommendation", "learning_phase": "execution"},
            context_features={"wants_tactical": True, "wants_video": True},
        )

        self.assertEqual(reranked[0]["title"], "Sales Script Fixes")

    def test_pairwise_rerank_can_flip_close_candidates(self):
        original_compare = self.grounded_rag._pairwise_compare_candidates
        try:
            self.grounded_rag._pairwise_compare_candidates = lambda left, right, intent: "cand_b"
            reranked = self.grounded_rag.pairwise_rerank_if_ambiguous(
                [
                    {"id": "cand_a", "title": "A", "rerank_score": 0.71},
                    {"id": "cand_b", "title": "B", "rerank_score": 0.70},
                ],
                {"intent_type": "recommend_content"},
            )
            self.assertEqual(reranked[0]["id"], "cand_b")
        finally:
            self.grounded_rag._pairwise_compare_candidates = original_compare

    def test_eval_service_loads_cases_and_scores_ndcg(self):
        cases_path = BACKEND_ROOT / "evals" / "recommendation_eval_cases.jsonl"
        cases = self.eval_service.load_eval_cases(cases_path)
        self.assertGreaterEqual(len(cases), 1)
        score = self.eval_service.ndcg_at_k([cases[0].ideal_titles[0]], cases[0], k=5)
        self.assertGreater(score, 0.75)


if __name__ == "__main__":
    unittest.main()
