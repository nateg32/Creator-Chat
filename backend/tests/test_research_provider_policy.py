import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_research_provider_module():
    module_path = BASE_DIR / "services" / "research_provider.py"
    spec = importlib.util.spec_from_file_location("test_research_provider_module", module_path)
    module = importlib.util.module_from_spec(spec)

    fake_db = types.ModuleType("backend.db")
    fake_db.db = types.SimpleNamespace(
        execute_one=lambda *args, **kwargs: None,
        execute_update=lambda *args, **kwargs: None,
    )

    fake_settings = types.ModuleType("backend.settings")
    fake_settings.settings = types.SimpleNamespace(
        OPENAI_API_KEY="",
        MODEL_VERIFY="test-model",
        GOOGLE_API_KEY="",
        SEARCH_API_KEY="",
        LIVE_SEARCH_PROVIDER="openai",
        GEMINI_GROUNDING_MODEL="test-gemini",
    )

    fake_rag = types.ModuleType("backend.rag")
    fake_requests = types.ModuleType("requests")
    fake_live_rules = types.ModuleType("backend.services.live_search_rules")
    fake_live_rules.needs_fresh_public_web_search = lambda *args, **kwargs: False

    with patch.dict(
        sys.modules,
        {
            "backend.db": fake_db,
            "backend.settings": fake_settings,
            "backend.rag": fake_rag,
            "requests": fake_requests,
            "backend.services.live_search_rules": fake_live_rules,
        },
    ):
        spec.loader.exec_module(module)

    return module


research_provider_module = load_research_provider_module()
OpenAIResearchProvider = research_provider_module.OpenAIResearchProvider


class ResearchProviderPolicyTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAIResearchProvider()

    def test_direct_video_url_requires_real_content_path(self):
        self.assertFalse(self.provider._is_direct_video_url("https://www.tiktok.com/@blakefakhoury/"))
        self.assertFalse(self.provider._is_direct_video_url("https://x.com/blakefakhoury"))
        self.assertFalse(self.provider._is_direct_video_url("https://www.instagram.com/blakefakhoury/"))

        self.assertTrue(self.provider._is_direct_video_url("https://www.tiktok.com/@blakefakhoury/video/1234567890"))
        self.assertTrue(self.provider._is_direct_video_url("https://x.com/blakefakhoury/status/1234567890"))
        self.assertTrue(self.provider._is_direct_video_url("https://www.instagram.com/reel/REALVIDEO01/"))

    def test_score_relevance_keeps_generic_watch_first_queries_viable(self):
        results = [
            {
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "relation": "SELF",
                "confidence": 0.95,
                "ownership_score": 1.0,
                "_domain": "youtube.com",
                "snippet": "Foundation video for the creator's strategy",
            }
        ]

        scored = self.provider._score_relevance(results, "what video should I watch first", "VIDEO")

        self.assertEqual(len(scored), 1)
        self.assertGreaterEqual(scored[0]["query_fidelity_score"], 0.5)
        self.assertGreaterEqual(scored[0]["_relevance_score"], 1.0)

    def test_score_relevance_prefers_closer_topic_match(self):
        results = [
            {
                "title": "Ultra Long Form Is the Future",
                "url": "https://www.youtube.com/watch?v=REALVIDEO01",
                "relation": "SELF",
                "confidence": 0.95,
                "ownership_score": 1.0,
                "_domain": "youtube.com",
                "snippet": "Long form strategy and automation.",
            },
            {
                "title": "A Day in My Life",
                "url": "https://www.youtube.com/watch?v=DAYINLIFE01",
                "relation": "SELF",
                "confidence": 0.95,
                "ownership_score": 1.0,
                "_domain": "youtube.com",
                "snippet": "Personal vlog.",
            },
        ]

        scored = self.provider._score_relevance(results, "ultra long form automation", "VIDEO")

        self.assertEqual(scored[0]["title"], "Ultra Long Form Is the Future")
        self.assertGreater(scored[0]["query_fidelity_score"], scored[1]["query_fidelity_score"])


if __name__ == "__main__":
    unittest.main()
