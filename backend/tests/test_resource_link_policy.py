import importlib.util
import sys
import types
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _stub_package(name: str):
    # Preserve the real package's __path__ if the package exists on disk so
    # that submodules we did NOT explicitly stub can still be auto-imported by
    # other tests that run after this module is collected.
    real_path = []
    parts = name.split(".")
    candidate = BACKEND_ROOT.parent.joinpath(*parts)
    if candidate.is_dir():
        real_path = [str(candidate)]
    module = types.ModuleType(name)
    module.__path__ = real_path  # type: ignore[attr-defined]
    sys.modules[name] = module
    return module


def _stub_module(name: str, **attrs):
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _load_grounded_rag():
    # Snapshot sys.modules so we can restore real backend.* modules after the
    # heavyweight stub install. Otherwise these stubs leak into other test
    # modules that get collected after us.
    _modules_before = dict(sys.modules)

    backend_package = _stub_package("backend")
    prompts_package = _stub_package("backend.prompts")
    services_package = _stub_package("backend.services")
    core_package = _stub_package("backend.core")
    utils_package = _stub_package("backend.utils")

    backend_package.prompts = prompts_package
    backend_package.services = services_package
    backend_package.core = core_package
    backend_package.utils = utils_package

    db_stub = types.SimpleNamespace(
        execute_one=lambda *args, **kwargs: None,
        execute_query=lambda *args, **kwargs: [],
        execute_update=lambda *args, **kwargs: None,
    )
    _stub_module("backend.db", db=db_stub)
    _stub_module("backend.services.decision_service", decision_service=types.SimpleNamespace(resolve_followup_question=lambda q, h: q))
    _stub_module("backend.services.creator_entity_service", creator_entity_service=types.SimpleNamespace(resolve_entity=lambda *args, **kwargs: None))

    class _EvidenceRouter:
        def __init__(self, creator):
            self.creator = creator or {}

        def build_plan(self, query, conversation_history=None, top_score=None, retrieved_chunks=None, web_results=None, smart_decision=None):
            return types.SimpleNamespace(
                primary_world="creator_memory",
                secondary_worlds=[],
                should_search_web=False,
                should_search_corpus=True,
                should_verify=False,
                user_is_followup=False,
                resolved_query=query,
                entity_subject="",
                freshness_required="none",
                answer_mode="creator_take",
                risk_flags=[],
                entity_type="",
                top_score=top_score,
                contradiction_risk=False,
                to_dict=lambda: {
                    "primary_world": "creator_memory",
                    "secondary_worlds": [],
                    "should_search_web": False,
                    "should_search_corpus": True,
                    "should_verify": False,
                    "user_is_followup": False,
                    "resolved_query": query,
                    "entity_subject": "",
                    "freshness_required": "none",
                    "answer_mode": "creator_take",
                    "risk_flags": [],
                    "entity_type": "",
                    "top_score": top_score,
                    "contradiction_risk": False,
                },
            )

    _stub_module(
        "backend.services.evidence_router",
        EvidenceRouter=_EvidenceRouter,
        EvidencePlan=type("EvidencePlan", (), {}),
        detect_evidence_contradiction=lambda *args, **kwargs: {"has_contradiction": False, "kind": "none", "corpus_markers": [], "web_markers": []},
        log_evidence_plan=lambda *args, **kwargs: None,
    )

    search_decision_path = BACKEND_ROOT / "services" / "search_decision_engine.py"
    creator_fact_policy_path = BACKEND_ROOT / "services" / "creator_fact_policy.py"
    creator_fact_policy_spec = importlib.util.spec_from_file_location(
        "backend.services.creator_fact_policy",
        creator_fact_policy_path,
    )
    creator_fact_policy_module = importlib.util.module_from_spec(creator_fact_policy_spec)
    assert creator_fact_policy_spec.loader is not None
    sys.modules["backend.services.creator_fact_policy"] = creator_fact_policy_module
    creator_fact_policy_spec.loader.exec_module(creator_fact_policy_module)
    search_decision_spec = importlib.util.spec_from_file_location(
        "backend.services.search_decision_engine",
        search_decision_path,
    )
    search_decision_module = importlib.util.module_from_spec(search_decision_spec)
    assert search_decision_spec.loader is not None
    sys.modules["backend.services.search_decision_engine"] = search_decision_module
    search_decision_spec.loader.exec_module(search_decision_module)
    settings_stub = types.SimpleNamespace(
        EMBEDDING_MODEL="test-embed",
        ROUTER_MODEL="test-router",
        RERANK_MODEL="test-rerank",
        MODEL_CLASSIFICATION="test-classify",
        REWRITE_MODEL="test-rewrite",
    )
    rag_stub = types.SimpleNamespace(
        create_embedding=lambda *args, **kwargs: [0.0],
        generate_chat_completion=lambda *args, **kwargs: '{"classification": "SUFFICIENT"}',
        get_client=lambda *args, **kwargs: None,
    )

    _stub_module("backend.settings", settings=settings_stub)
    _stub_module("backend.rag", **rag_stub.__dict__)
    _stub_module("backend.prompts.creator_base_prompt", CREATOR_BASE_SYSTEM_PROMPT="")
    _stub_module("backend.services.style_distiller", StyleDistiller=type("StyleDistiller", (), {}))
    _stub_module("backend.services.style_scorer", StyleScorer=type("StyleScorer", (), {}))
    _stub_module("backend.services.content_finder", ContentFinder=type("ContentFinder", (), {}))
    _stub_module("backend.services.research_provider", GeminiResearchProvider=type("GeminiResearchProvider", (), {}))
    _stub_module("backend.services.memory_service", memory_service=types.SimpleNamespace())
    _stub_module(
        "backend.services.greeting_service",
        greeting_service=types.SimpleNamespace(),
        is_greeting=lambda *args, **kwargs: False,
    )
    _stub_module("backend.services.personal_bio_service", personal_bio_service=types.SimpleNamespace())
    _stub_module("backend.services.persona_filter", apply_persona_surface_filter=lambda *args, **kwargs: "")
    _stub_module("backend.services.curiosity_service", curiosity_service=types.SimpleNamespace())
    _stub_module("backend.services.rhythm_shaper", rhythm_shaper=types.SimpleNamespace())
    _stub_module("backend.services.user_priority_service", user_priority_service=types.SimpleNamespace())
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
    _stub_module("backend.services.formatting", clean_response=lambda text, **kwargs: text, clean_for_stream_chunk=lambda text: text, should_strip_hyphens=lambda config: False)
    _stub_module("backend.services.assumption_blocker", assumption_blocker=types.SimpleNamespace())
    _stub_module("backend.services.image_identity_service", image_identity_service=types.SimpleNamespace())
    _stub_module("backend.services.voice_dna", build_voice_echo_block=lambda *args, **kwargs: "")
    _stub_module("backend.services.conversation_closure", get_bridge_question=lambda *args, **kwargs: "")
    _stub_module(
        "backend.services.live_search_rules",
        build_live_search_query=lambda *args, **kwargs: "",
        extract_requested_platforms=lambda *args, **kwargs: [],
        needs_fresh_public_web_search=lambda *args, **kwargs: False,
    )
    _stub_module("backend.utils.url_health", check_url_alive_sync=lambda *args, **kwargs: True, is_url_known_dead=lambda *args, **kwargs: False)
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
        detect_external_live_fact_topic=lambda *args, **kwargs: False,
        recent_bridge_topic=lambda *args, **kwargs: "",
        should_redirect_general_knowledge=lambda *args, **kwargs: False,
        should_soft_decline_external_live_fact=lambda *args, **kwargs: False,
    )
    regurgitation_guard_path = BACKEND_ROOT / "services" / "regurgitation_guard.py"
    regurgitation_guard_spec = importlib.util.spec_from_file_location(
        "backend.services.regurgitation_guard",
        regurgitation_guard_path,
    )
    regurgitation_guard_module = importlib.util.module_from_spec(regurgitation_guard_spec)
    assert regurgitation_guard_spec.loader is not None
    sys.modules["backend.services.regurgitation_guard"] = regurgitation_guard_module
    regurgitation_guard_spec.loader.exec_module(regurgitation_guard_module)

    module_path = BACKEND_ROOT / "grounded_rag.py"
    spec = importlib.util.spec_from_file_location("grounded_rag_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    # Capture every sys.modules entry we introduced or replaced so the test
    # class can reinstate them in setUp (other test files reorder things
    # underneath us between collection and execution).
    _modules_after = set(sys.modules.keys())
    introduced = _modules_after - set(_modules_before.keys())
    replaced = {
        name for name in _modules_after & set(_modules_before.keys())
        if sys.modules.get(name) is not _modules_before.get(name)
    }
    snapshot_names = introduced | replaced
    module.__test_stub_snapshot__ = {
        name: sys.modules[name] for name in snapshot_names if name in sys.modules
    }
    module.__test_stub_replaced_originals__ = {
        name: _modules_before[name] for name in replaced
    }

    # Restore previously-loaded real modules so other test files don't inherit
    # our stubs. Anything we introduced that wasn't loaded before (e.g. small
    # helper modules) is left in place because the loaded `module` may still
    # reference them internally.
    for name, original in _modules_before.items():
        if sys.modules.get(name) is not original:
            sys.modules[name] = original
    return module


grounded_rag = _load_grounded_rag()


class ResourceLinkPolicyTests(unittest.TestCase):
    def setUp(self):
        # Other test modules reshuffle ``sys.modules`` between our collection
        # and execution. Reinstate the heavyweight stubs we set up in
        # ``_load_grounded_rag`` so deferred ``from backend.rag import …``
        # imports inside ``grounded_rag`` keep finding the symbols we
        # promised them.
        for name, module in getattr(grounded_rag, "__test_stub_snapshot__", {}).items():
            sys.modules[name] = module

    def test_retrieve_candidates_skips_missing_embedding(self):
        self.assertEqual(grounded_rag.retrieve_candidates(1, None), [])

    def test_force_resource_fallback_when_no_safe_link_exists(self):
        self.assertTrue(
            grounded_rag._should_force_resource_fallback(
                "I don't have a specific video I'd feel good sending you right now.",
                wants_link=True,
                has_linkable_ingested_resource=False,
                web_results=[],
            )
        )
        self.assertFalse(
            grounded_rag._should_force_resource_fallback(
                "I don't have a specific video I'd feel good sending you right now.",
                wants_link=True,
                has_linkable_ingested_resource=True,
                web_results=[],
            )
        )

    def test_detects_placeholder_link_artifacts(self):
        broken = (
            'For my best productivity videos, start on my official site at "" and head to the section '
            'with my video content. If you want my books too, my books page is ""/books.'
        )
        self.assertTrue(grounded_rag._contains_placeholder_link_artifacts(broken))
        self.assertFalse(grounded_rag._contains_placeholder_link_artifacts("Check the video I attached below."))

    def test_resource_prompt_context_marks_post_as_non_video_with_video_alternative(self):
        support_set = [
            {
                "title": "5 Steps to solving 99% of sales issues",
                "url": "https://x.com/AlexHormozi/status/123",
                "source_ref": {
                    "title": "5 Steps to solving 99% of sales issues",
                    "canonical_url": "https://x.com/AlexHormozi/status/123",
                    "platform": "twitter",
                    "content_type": "post",
                },
            },
            {
                "title": "DM Selling Breakdown",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "source_ref": {
                    "title": "DM Selling Breakdown",
                    "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01",
                    "platform": "youtube",
                    "content_type": "video",
                },
            },
        ]

        context = grounded_rag._resource_prompt_context(
            support_set,
            "what video should I watch about dm selling",
        )

        self.assertEqual(context["primary_label"], "post on X")
        self.assertFalse(context["primary_is_video"])
        self.assertEqual(context["closest_video_title"], "DM Selling Breakdown")

    def test_resource_prompt_context_labels_tiktok_direct_post_as_video(self):
        support_set = [
            {
                "title": "DM Selling In 30 Seconds",
                "url": "https://www.tiktok.com/@alexhormozi/video/1234567890",
                "source_ref": {
                    "title": "DM Selling In 30 Seconds",
                    "canonical_url": "https://www.tiktok.com/@alexhormozi/video/1234567890",
                    "platform": "tiktok",
                    "content_type": "post",
                },
            },
        ]

        context = grounded_rag._resource_prompt_context(
            support_set,
            "what should I watch on tiktok about dm selling",
        )

        self.assertEqual(context["primary_label"], "TikTok video")
        self.assertTrue(context["primary_is_video"])

    def test_response_length_instruction_uses_nonvideo_video_request_guidance(self):
        guidance = grounded_rag.response_length_instruction(
            "introduce_content",
            resource_context={
                "video_requested": True,
                "primary_is_video": False,
                "primary_label": "post on X",
                "closest_video_title": "DM Selling Breakdown",
            },
        )

        self.assertIn("did not find an exact video", guidance)
        self.assertIn("best direct match", guidance)
        self.assertIn("closest video", guidance)

    def test_resource_search_query_anchors_prior_prose_resource(self):
        history = [
            {"role": "user", "content": "do you have a misus?"},
            {
                "role": "assistant",
                "content": (
                    "Yes, I am married to Leila Hormozi. I've shared the story on "
                    "various platforms, including an episode of The Kim Constable Podcast "
                    "where I talk about our early days."
                ),
            },
        ]
        query = grounded_rag._build_resource_search_query(
            "can u link the video",
            {"needs_resource": True, "request_type": "explicit", "query": "relationship video"},
            history,
        )
        self.assertIn("The Kim Constable Podcast", query)
        self.assertIn("relationship video", query)

    def test_link_followup_to_prior_podcast_does_not_force_video_filter(self):
        assistant_text = (
            "I've shared the story on various platforms, including an episode of "
            "The Kim Constable Podcast where I talk about our early days."
        )
        self.assertFalse(
            grounded_rag._followup_resource_requires_video("can u link the video", assistant_text)
        )
        self.assertTrue(
            grounded_rag._followup_resource_requires_video(
                "send it",
                "I attached the video Sales Tactics below.",
            )
        )

    def test_recommend_one_content_keeps_post_as_best_match_but_surfaces_video_alternative(self):
        original_classify = grounded_rag.classify_resource_intent
        original_build_query = grounded_rag._build_resource_search_query
        original_get_enabled = grounded_rag.get_enabled_platforms_for_creator
        original_retrieve = grounded_rag.retrieve_candidates
        original_aggregate = grounded_rag._aggregate_document_evidence
        original_filter_platforms = grounded_rag._filter_candidates_for_requested_platforms
        original_dedup = grounded_rag._get_suggested_resources
        original_rerank = grounded_rag.rerank_candidates
        original_skip_llm = grounded_rag._can_skip_llm_rerank
        original_gate = grounded_rag.calculate_gate_confidence
        try:
            grounded_rag.classify_resource_intent = lambda *args, **kwargs: {
                "needs_resource": True,
                "request_type": "explicit",
                "intent_type": "recommend_content",
                "resource_type": "video",
                "query": "dm selling",
            }
            grounded_rag._build_resource_search_query = lambda *args, **kwargs: "dm selling"
            grounded_rag.get_enabled_platforms_for_creator = lambda *args, **kwargs: []
            grounded_rag.retrieve_candidates = lambda *args, **kwargs: [{"document_id": 1}, {"document_id": 2}]
            grounded_rag._aggregate_document_evidence = lambda *args, **kwargs: [
                {
                    "id": "post1",
                    "title": "5 Steps to solving 99% of sales issues",
                    "url": "https://x.com/AlexHormozi/status/123",
                    "platform": "twitter",
                    "source_ref": {"content_type": "post", "platform": "twitter"},
                    "rerank_score": 0.91,
                    "chunks": [{"source_ref": {"title": "5 Steps to solving 99% of sales issues", "canonical_url": "https://x.com/AlexHormozi/status/123", "content_type": "post", "platform": "twitter"}}],
                },
                {
                    "id": "vid1",
                    "title": "DM Selling Breakdown",
                    "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                    "platform": "youtube",
                    "source_ref": {"content_type": "video", "platform": "youtube"},
                    "rerank_score": 0.74,
                    "chunks": [{"source_ref": {"title": "DM Selling Breakdown", "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01", "content_type": "video", "platform": "youtube"}}],
                },
            ]
            grounded_rag._filter_candidates_for_requested_platforms = lambda candidates, preferred_platforms=None: candidates
            grounded_rag._get_suggested_resources = lambda *args, **kwargs: {"titles": set(), "urls": set()}
            grounded_rag.rerank_candidates = lambda candidates, *args, **kwargs: candidates
            grounded_rag._can_skip_llm_rerank = lambda *args, **kwargs: True
            grounded_rag.calculate_gate_confidence = lambda *args, **kwargs: 0.92

            result = grounded_rag.recommend_one_content(
                user_id=1,
                creator_id=1,
                user_message="what video should I watch about dm selling",
                conversation_history=[],
                creator_row={"platform_configs": {}},
                q_emb=[0.0],
            )

            self.assertEqual(result["best_candidate"]["title"], "5 Steps to solving 99% of sales issues")
            self.assertTrue(result["resource_intent"]["video_request_without_exact_video"])
            self.assertEqual(result["resource_intent"]["closest_video_title"], "DM Selling Breakdown")
            self.assertEqual(result["card_limit"], 2)
            self.assertEqual(result["alternate_candidates"][0]["title"], "DM Selling Breakdown")
        finally:
            grounded_rag.classify_resource_intent = original_classify
            grounded_rag._build_resource_search_query = original_build_query
            grounded_rag.get_enabled_platforms_for_creator = original_get_enabled
            grounded_rag.retrieve_candidates = original_retrieve
            grounded_rag._aggregate_document_evidence = original_aggregate
            grounded_rag._filter_candidates_for_requested_platforms = original_filter_platforms
            grounded_rag._get_suggested_resources = original_dedup
            grounded_rag.rerank_candidates = original_rerank
            grounded_rag._can_skip_llm_rerank = original_skip_llm
            grounded_rag.calculate_gate_confidence = original_gate

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

    def test_broad_grounded_creator_answer_keeps_citations_when_answer_overlap_is_strong(self):
        support_set = [
            {
                "content": (
                    "Start with market structure and execution. Learn higher highs, lower lows, breaks of structure, "
                    "support and resistance, then liquidity sweeps and traps."
                ),
                "snippet": "Market structure first, then support and resistance, then liquidity sweeps.",
                "title": "Trading Concepts Every Beginner Misses",
                "url": "https://www.youtube.com/watch?v=TRADING001",
                "source_ref": {
                    "title": "Trading Concepts Every Beginner Misses",
                    "canonical_url": "https://www.youtube.com/watch?v=TRADING001",
                    "platform": "youtube",
                },
            }
        ]

        citations = grounded_rag.build_inline_citations(
            support_set,
            question="in terms of concept what concepts like technical do i need to know?",
            answer_text=(
                "Start with market structure, execution, support and resistance, then learn liquidity sweeps and traps."
            ),
        )

        self.assertEqual(len(citations), 1)
        self.assertEqual(citations[0]["url"], "https://www.youtube.com/watch?v=TRADING001")

    def test_broad_grounded_creator_answer_keeps_cards_when_answer_overlap_is_strong(self):
        support_set = [
            {
                "content": (
                    "Start with market structure and execution. Learn higher highs, lower lows, breaks of structure, "
                    "support and resistance, then liquidity sweeps and traps."
                ),
                "snippet": "Market structure first, then support and resistance, then liquidity sweeps.",
                "title": "Trading Concepts Every Beginner Misses",
                "url": "https://www.youtube.com/watch?v=TRADING001",
                "source_ref": {
                    "title": "Trading Concepts Every Beginner Misses",
                    "canonical_url": "https://www.youtube.com/watch?v=TRADING001",
                    "platform": "youtube",
                },
            }
        ]

        cards = grounded_rag._build_response_cards(
            None,
            support_set,
            question="in terms of concept what concepts like technical do i need to know?",
            answer_text=(
                "Start with market structure, execution, support and resistance, then learn liquidity sweeps and traps."
            ),
        )

        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["url"], "https://www.youtube.com/watch?v=TRADING001")

    def test_source_policy_suppresses_random_advice_cards_and_citations(self):
        decision = types.SimpleNamespace(
            route="ROUTE_2_TASK",
            response_mode="answer",
            query_goal="general",
            source_policy="none",
            confidence=0.94,
        )

        self.assertFalse(
            grounded_rag._should_build_source_cards_for_turn(
                decision,
                answer_text="Let's diagnose the close rate first.",
                has_substantive_rag=True,
            )
        )
        self.assertFalse(
            grounded_rag._should_emit_citations_for_turn(
                decision,
                answer_text="Let's diagnose the close rate first.",
            )
        )

    def test_source_policy_keeps_citations_for_public_facts(self):
        decision = types.SimpleNamespace(
            route="ROUTE_2_TASK",
            response_mode="answer",
            query_goal="current_stat_lookup",
            source_policy="must_cite",
            confidence=0.95,
        )

        self.assertTrue(
            grounded_rag._should_emit_citations_for_turn(
                decision,
                answer_text="The portfolio revenue is public enough to verify.",
            )
        )
        self.assertFalse(
            grounded_rag._should_build_source_cards_for_turn(
                decision,
                answer_text="The portfolio revenue is public enough to verify.",
                has_substantive_rag=True,
            )
        )

    def test_support_set_shaping_prefers_document_diversity_for_title_match(self):
        shaped = grounded_rag.shape_support_set(
            "how the top 0.1% invest their money",
            [
                {
                    "chunk_id": "a1",
                    "document_id": 1,
                    "distance": 0.08,
                    "content": "Stage 1 cash engine. Stage 2 safety assets.",
                    "source_ref": {
                        "title": "How the Top 0.1% Invest Their Money",
                        "canonical_url": "https://www.youtube.com/watch?v=INVEST001",
                        "content_id": "INVEST001",
                    },
                },
                {
                    "chunk_id": "a2",
                    "document_id": 1,
                    "distance": 0.09,
                    "content": "Stage 3 tax efficiency. Stage 4 asymmetric bets.",
                    "source_ref": {
                        "title": "How the Top 0.1% Invest Their Money",
                        "canonical_url": "https://www.youtube.com/watch?v=INVEST001",
                        "content_id": "INVEST001",
                    },
                },
                {
                    "chunk_id": "b1",
                    "document_id": 2,
                    "distance": 0.16,
                    "content": "Wealth compounds when you earn before you allocate.",
                    "source_ref": {
                        "title": "The Real Order of Wealth Building",
                        "canonical_url": "https://www.youtube.com/watch?v=INVEST002",
                        "content_id": "INVEST002",
                    },
                },
            ],
            limit=3,
        )

        self.assertEqual(len(shaped), 2)
        self.assertEqual(shaped[0]["source_ref"]["content_id"], "INVEST001")
        self.assertEqual(shaped[1]["source_ref"]["content_id"], "INVEST002")

    def test_web_result_citation_survives_scoring_threshold(self):
        """Web search results with topic overlap must appear as citations."""
        support_set = [
            {
                "content": "[LIVE WEB SEARCH RESULT]\nDay Trading Basics for Beginners — Learn risk management, market structure, and a simple system.",
                "snippet": "Day Trading Basics for Beginners — Learn risk management, market structure.",
                "title": "Day Trading Basics for Beginners",
                "url": "https://www.youtube.com/watch?v=DAYTRADING01",
                "source_ref": {
                    "title": "Day Trading Basics for Beginners",
                    "canonical_url": "https://www.youtube.com/watch?v=DAYTRADING01",
                    "platform": "youtube",
                },
            }
        ]

        citations = grounded_rag.build_inline_citations(
            support_set,
            question="whats a key video youd recommend if i wanna start day trading from the basics",
            answer_text="Start with risk management and a simple system. I attached a video below on day trading basics.",
        )

        self.assertGreaterEqual(len(citations), 1)
        self.assertEqual(citations[0]["url"], "https://www.youtube.com/watch?v=DAYTRADING01")
        self.assertTrue(citations[0]["is_live_web"])


if __name__ == "__main__":
    unittest.main()
