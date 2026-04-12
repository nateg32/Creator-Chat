"""Tests for EvidencePlan routing across creator memory, creator world, and live world."""

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


def _load_evidence_router():
    _stub_module(
        "backend.db",
        db=types.SimpleNamespace(
            execute_one=lambda *args, **kwargs: None,
            execute_query=lambda *args, **kwargs: [],
            execute_update=lambda *args, **kwargs: None,
        ),
    )
    _load_module("backend.services.decision_service", pathlib.Path("services") / "decision_service.py")
    _load_module("backend.services.creator_entity_service", pathlib.Path("services") / "creator_entity_service.py")
    _load_module("backend.services.creator_fact_policy", pathlib.Path("services") / "creator_fact_policy.py")
    module = _load_module("backend.services.evidence_router", pathlib.Path("services") / "evidence_router.py")
    return module


class EvidenceRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        module = _load_evidence_router()
        cls.EvidenceRouter = module.EvidenceRouter
        cls.detect_evidence_contradiction = staticmethod(module.detect_evidence_contradiction)

    def setUp(self):
        self.creator = {
            "id": 1,
            "name": "Dan Martell",
            "identity_fingerprint": 'Author of "Buy Back Your Time". Creator of a course called "High Performance CEO".',
            "soul_md": 'You built SaaS companies and wrote "Buy Back Your Time".',
            "platform_configs": {"youtube": {"handle": "danmartell"}},
            "official_domains": ["danmartell.com"],
        }
        self.router = self.EvidenceRouter(self.creator)

    def test_creator_world_plan_for_public_book_fact(self):
        plan = self.router.build_plan("when was your book published")
        self.assertEqual(plan.primary_world, "creator_world")
        self.assertTrue(plan.should_search_web)
        self.assertTrue(plan.should_verify)
        self.assertIn(plan.answer_mode, {"direct_fact", "hybrid"})
        self.assertEqual(plan.entity_subject, "Buy Back Your Time")

    def test_live_world_plan_for_current_stat(self):
        plan = self.router.build_plan("how many followers do you have right now")
        self.assertEqual(plan.primary_world, "live_world")
        self.assertTrue(plan.should_search_web)
        self.assertEqual(plan.freshness_required, "high")
        self.assertIn("stats", plan.risk_flags)

    def test_creator_memory_plan_skips_web_when_rag_is_strong(self):
        plan = self.router.build_plan(
            "what's your best advice for a new entrepreneur",
            top_score=0.86,
        )
        self.assertEqual(plan.primary_world, "creator_memory")
        self.assertTrue(plan.should_search_corpus)
        self.assertFalse(plan.should_search_web)

    def test_followup_resolution_updates_resolved_query(self):
        plan = self.router.build_plan(
            "when did u write it?",
            conversation_history=[
                {"role": "user", "content": "do you have a book?"},
                {"role": "assistant", "content": "Yeah. I wrote a book called Buy Back Your Time."},
            ],
        )
        self.assertTrue(plan.user_is_followup)
        self.assertIn("Buy Back Your Time", plan.resolved_query)
        self.assertEqual(plan.entity_subject, "Buy Back Your Time")

    def test_entity_confirmation_uses_entity_graph_before_web(self):
        plan = self.router.build_plan("do you know the book buy your time")
        self.assertEqual(plan.query_goal, "entity_confirmation")
        self.assertEqual(plan.primary_world, "creator_memory")
        self.assertFalse(plan.should_search_web)
        self.assertEqual(plan.search_strategy, "entity_graph_first")
        self.assertEqual(plan.entity_subject, "Buy Back Your Time")

    def test_yes_no_book_identity_query_is_entity_confirmation(self):
        plan = self.router.build_plan("is buy back your time your book?")
        self.assertEqual(plan.query_goal, "entity_confirmation")
        self.assertEqual(plan.primary_world, "creator_memory")
        self.assertFalse(plan.should_search_web)
        self.assertEqual(plan.entity_subject, "Buy Back Your Time")

    def test_plural_book_query_routes_to_entity_catalog_lookup(self):
        plan = self.router.build_plan("have u written any books?")
        self.assertEqual(plan.query_goal, "entity_catalog_lookup")
        self.assertEqual(plan.primary_world, "creator_world")
        self.assertTrue(plan.should_search_web)
        self.assertEqual(plan.entity_type, "book")

    def test_how_many_books_query_routes_to_entity_catalog_lookup(self):
        plan = self.router.build_plan("how many books u written?")
        self.assertEqual(plan.query_goal, "entity_catalog_lookup")
        self.assertEqual(plan.primary_world, "creator_world")
        self.assertTrue(plan.should_search_web)
        self.assertEqual(plan.entity_type, "book")

    def test_user_partner_business_question_stays_creator_memory(self):
        plan = self.router.build_plan(
            "what would u reccomend if i have a partner who doesnt like my business?"
        )
        self.assertEqual(plan.primary_world, "creator_memory")
        self.assertEqual(plan.query_goal, "creator_take")
        self.assertEqual(plan.answer_mode, "creator_take")
        self.assertFalse(plan.should_search_web)

    def test_wdym_followup_resolves_to_contextual_clarification(self):
        plan = self.router.build_plan(
            "wdymean?",
            conversation_history=[
                {
                    "role": "user",
                    "content": "what would u reccomend if i have a partner who doesnt like my business?",
                },
                {"role": "assistant", "content": "I keep that side of my life private."},
            ],
        )
        self.assertTrue(plan.user_is_followup)
        self.assertIn("clarify what you meant", plan.resolved_query.lower())
        self.assertIn("partner who doesnt like my business", plan.resolved_query.lower())

    def test_availability_lookup_prefers_official_urls_before_search(self):
        plan = self.router.build_plan("where can i buy your book")
        self.assertEqual(plan.query_goal, "availability_lookup")
        self.assertEqual(plan.primary_world, "creator_world")
        self.assertFalse(plan.should_search_web)
        self.assertEqual(plan.search_strategy, "official_urls_first")

    def test_detect_evidence_contradiction_for_dates(self):
        report = self.detect_evidence_contradiction(
            "when was your book published",
            corpus_chunks=[{"content": "Buy Back Your Time was published in 2022."}],
            web_results=[{"snippet": "Buy Back Your Time was published in September 2023."}],
        )
        self.assertTrue(report.get("has_contradiction"))
        self.assertEqual(report.get("kind"), "date")


if __name__ == "__main__":
    unittest.main()
