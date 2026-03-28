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


def _load_grounded_rag():
    _stub_package("backend.prompts")
    _stub_package("backend.services")
    _stub_package("backend.core")

    db_stub = types.SimpleNamespace(
        execute_one=lambda *args, **kwargs: None,
        execute_query=lambda *args, **kwargs: [],
    )
    settings_stub = types.SimpleNamespace(
        EMBEDDING_MODEL="test-embed",
        ROUTER_MODEL="test-router",
        RERANK_MODEL="test-rerank",
        MODEL_CLASSIFICATION="test-classify",
    )
    rag_stub = types.SimpleNamespace(
        create_embedding=lambda *args, **kwargs: [0.0],
        generate_chat_completion=lambda *args, **kwargs: '{"classification": "SUFFICIENT"}',
    )

    _stub_module("backend.db", db=db_stub)
    _stub_module("backend.settings", settings=settings_stub)
    _stub_module("backend.rag", **rag_stub.__dict__)
    _stub_module("backend.prompts.creator_base_prompt", CREATOR_BASE_SYSTEM_PROMPT="")
    _stub_module("backend.services.style_distiller", StyleDistiller=type("StyleDistiller", (), {}))
    _stub_module("backend.services.style_scorer", StyleScorer=type("StyleScorer", (), {}))
    _stub_module("backend.services.content_finder", ContentFinder=type("ContentFinder", (), {}))
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}))
    _stub_module("backend.services.memory_service", memory_service=types.SimpleNamespace())
    _stub_module("backend.services.greeting_service", greeting_service=types.SimpleNamespace())
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
    _stub_module(
        "backend.core.interaction_engine",
        interaction_engine=types.SimpleNamespace(),
        InteractionPlan=type("InteractionPlan", (), {}),
        strip_all_markdown=lambda text, allow_links=False: text,
    )
    _stub_module("backend.services.web_verify", web_verify=types.SimpleNamespace())
    _stub_module("backend.services.grammar_normalizer", grammar_normalizer=types.SimpleNamespace())
    _stub_module("backend.services.text_sanitizer", strip_mid_sentence_hyphens=lambda text: text)
    _stub_module("backend.services.assumption_blocker", assumption_blocker=types.SimpleNamespace())
    _stub_module("backend.services.image_identity_service", image_identity_service=types.SimpleNamespace())
    _stub_module(
        "backend.services.live_search_rules",
        build_live_search_query=lambda *args, **kwargs: "",
        extract_requested_platforms=lambda *args, **kwargs: [],
        needs_fresh_public_web_search=lambda *args, **kwargs: False,
    )
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

    module_path = BACKEND_ROOT / "grounded_rag.py"
    spec = importlib.util.spec_from_file_location("grounded_rag_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


grounded_rag = _load_grounded_rag()


class ResourceLinkPolicyTests(unittest.TestCase):
    def test_build_response_cards_prefers_ingested_support_over_live_web(self):
        rec_result = {
            "best_candidate": {
                "title": "Watch this",
                "url": "https://www.youtube.com/watch?v=abc123XYZ89",
                "rerank_score": 0.05,
            },
            "resource_intent": {"resource_type": "video"},
            "card_limit": 1,
        }
        support_set = [
            {
                "content": "Chunk from ingested content",
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "source_ref": {
                    "title": "Ultra Long Form Is the Future",
                    "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01",
                },
            },
            {
                "content": "[LIVE WEB SEARCH RESULT]\nFresh result",
                "title": "Wrong Instagram Result",
                "url": "https://www.instagram.com/reel/WRONG123/",
                "source_ref": {
                    "title": "Wrong Instagram Result",
                    "canonical_url": "https://www.instagram.com/reel/WRONG123/",
                },
            },
        ]

        cards = grounded_rag._build_response_cards(rec_result, support_set, preferred_platforms=["youtube"])

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], "https://www.youtube.com/watch?v=REALVIDEO01")
        self.assertEqual(cards[0]["title"], "Ultra Long Form Is the Future")

    def test_build_response_cards_prefers_support_that_matches_answer_text(self):
        rec_result = {
            "best_candidate": {
                "title": "Different Recommendation",
                "url": "https://www.youtube.com/watch?v=DIFFERENT02",
                "rerank_score": 0.91,
            },
            "resource_intent": {"resource_type": "video"},
            "card_limit": 1,
        }
        support_set = [
            {
                "content": "This is the core long form foundation and the main recommendation.",
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "source_ref": {
                    "title": "Ultra Long Form Is the Future",
                    "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01",
                },
            },
            {
                "content": "This one is related but not the main answer.",
                "title": "YouTube Automation Is Getting Out of Hand",
                "url": "https://www.youtube.com/watch?v=REALVIDEO02",
                "source_ref": {
                    "title": "YouTube Automation Is Getting Out of Hand",
                    "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO02",
                },
            },
        ]

        cards = grounded_rag._build_response_cards(
            rec_result,
            support_set,
            preferred_platforms=["youtube"],
            question="what should I watch first",
            answer_text="Start with the ultra long form foundation because that is the core strategy.",
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], "https://www.youtube.com/watch?v=REALVIDEO01")

    def test_linkable_ingested_resource_blocks_web_fallback_for_video_request(self):
        support_set = [
            {
                "content": "Chunk from ingested content",
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "source_ref": {
                    "title": "Ultra Long Form Is the Future",
                    "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01",
                },
            }
        ]

        should_fallback = grounded_rag._should_block_on_web_fallback(
            "what video should I watch first?",
            [],
            wants_link=True,
            is_video_request=True,
            support_set=support_set,
            has_recommendable_ingested_resource=False,
            has_linkable_ingested_resource=True,
            search_mode="hybrid",
        )

        self.assertFalse(should_fallback)

    def test_filter_live_web_results_rejects_profile_pages_for_video_requests(self):
        results = [
            {
                "title": "Blake on Instagram",
                "url": "https://www.instagram.com/blakefakhoury/",
                "platform": "instagram",
                "relation": "SELF",
                "confidence": 0.95,
                "snippet": "Instagram profile",
            },
            {
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "platform": "youtube",
                "relation": "SELF",
                "confidence": 0.95,
                "snippet": "Foundation video",
            },
        ]

        filtered = grounded_rag._filter_live_web_results(
            results,
            "what video should I watch first",
            require_video=True,
        )

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["url"], "https://www.youtube.com/watch?v=REALVIDEO01")

    def test_inline_citations_rank_sources_closest_to_answer(self):
        support_set = [
            {
                "content": "Consumer apps need retention and habit loops.",
                "snippet": "Consumer apps need retention, not features.",
                "title": "Consumer Apps Need Retention",
                "url": "https://www.youtube.com/watch?v=APPRETENTION1",
                "source_ref": {
                    "title": "Consumer Apps Need Retention",
                    "canonical_url": "https://www.youtube.com/watch?v=APPRETENTION1",
                    "platform": "youtube",
                },
            },
            {
                "content": "Pick one buyer with money and urgency, then pre sell before you build.",
                "snippet": "Pre sell before you build.",
                "title": "Pre Sell Before You Build",
                "url": "https://www.youtube.com/watch?v=PRESell01",
                "source_ref": {
                    "title": "Pre Sell Before You Build",
                    "canonical_url": "https://www.youtube.com/watch?v=PRESell01",
                    "platform": "youtube",
                },
            },
        ]

        citations = grounded_rag.build_inline_citations(
            support_set,
            question="how do I start a software business",
            answer_text="Pre sell before you build and start with one buyer with money and urgency.",
        )

        self.assertEqual(citations[0]["url"], "https://www.youtube.com/watch?v=PRESell01")
        self.assertEqual(citations[0]["platform"], "youtube")
        self.assertIn("Pre sell", citations[0]["snippet"])


if __name__ == "__main__":
    unittest.main()
