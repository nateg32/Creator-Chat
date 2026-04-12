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

    fake_backend = types.ModuleType("backend")
    fake_backend.__path__ = []  # type: ignore[attr-defined]
    fake_backend_services = types.ModuleType("backend.services")
    fake_backend_services.__path__ = []  # type: ignore[attr-defined]

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
    fake_creator_fact_policy = types.ModuleType("backend.services.creator_fact_policy")
    fake_creator_fact_policy.classify_creator_fact_query = lambda *args, **kwargs: types.SimpleNamespace(kind="general", fact_field="public_fact")

    with patch.dict(
        sys.modules,
        {
            "backend": fake_backend,
            "backend.services": fake_backend_services,
            "backend.db": fake_db,
            "backend.settings": fake_settings,
            "backend.rag": fake_rag,
            "requests": fake_requests,
            "backend.services.live_search_rules": fake_live_rules,
            "backend.services.creator_fact_policy": fake_creator_fact_policy,
        },
    ):
        spec.loader.exec_module(module)

    return module


research_provider_module = load_research_provider_module()
OpenAIResearchProvider = research_provider_module.OpenAIResearchProvider
GeminiResearchProvider = research_provider_module.GeminiResearchProvider


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

    def test_grounded_overview_preserves_query_order_and_adjusts_citations(self):
        provider = GeminiResearchProvider()
        provider.enabled = True
        provider._build_grounding_query_plan = lambda *args, **kwargs: ["alpha query", "beta query"]

        def fake_call(prompt, search_enabled=True):
            if "alpha query" in prompt:
                return {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Alpha facts."}]},
                            "groundingMetadata": {
                                "groundingChunks": [
                                    {"web": {"uri": "https://creator.com/about", "title": "About Creator"}}
                                ],
                                "groundingSupports": [
                                    {
                                        "segment": {"text": "Alpha", "startIndex": 0, "endIndex": 5},
                                        "groundingChunkIndices": [0],
                                    }
                                ],
                            },
                        }
                    ]
                }
            return {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Beta facts."}]},
                        "groundingMetadata": {
                            "groundingChunks": [
                                {"web": {"uri": "https://creator.com/offers", "title": "Offers"}}
                            ],
                            "groundingSupports": [
                                {
                                    "segment": {"text": "Beta", "startIndex": 0, "endIndex": 4},
                                    "groundingChunkIndices": [0],
                                }
                            ],
                        },
                    }
                ]
            }

        with patch.object(provider, "_call_gemini_rest", side_effect=fake_call):
            overview = provider.grounded_overview(
                "Tell me about the creator",
                {"name": "Creator", "official_domains": ["creator.com"], "platform_configs": {}},
                max_queries=2,
            )

        self.assertEqual(overview["response_text"], "Alpha facts.\n\nBeta facts.")
        self.assertEqual([pkg["subquery"] for pkg in overview["packages"]], ["alpha query", "beta query"])
        beta_citation = next(c for c in overview["citations"] if c["text"] == "Beta")
        self.assertEqual(beta_citation["start_index"], len("Alpha facts.\n\n"))
        self.assertEqual(beta_citation["end_index"], len("Alpha facts.\n\n") + 4)
        self.assertEqual(overview["sources"][0]["url"], "https://creator.com/about")
        self.assertEqual(overview["sources"][1]["url"], "https://creator.com/offers")

    def test_extract_grounding_package_unwraps_redirect_urls(self):
        provider = GeminiResearchProvider()
        package = provider._extract_grounding_package(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Alpha facts."}]},
                        "groundingMetadata": {
                            "groundingChunks": [
                                {
                                    "web": {
                                        "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect?url=https%3A%2F%2Fjointjrtrades.com%2Fabout",
                                        "title": "jointjrtrades.com",
                                    }
                                }
                            ],
                            "groundingSupports": [
                                {
                                    "segment": {"text": "Alpha", "startIndex": 0, "endIndex": 5},
                                    "groundingChunkIndices": [0],
                                }
                            ],
                        },
                    }
                ]
            }
        )

        self.assertEqual(package["grounded_results"][0]["url"], "https://jointjrtrades.com/about")
        self.assertEqual(package["citations"][0]["url"], "https://jointjrtrades.com/about")

    def test_extract_grounding_package_falls_back_to_title_domain_for_wrapper_urls(self):
        provider = GeminiResearchProvider()
        package = provider._extract_grounding_package(
            {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Alpha facts."}]},
                        "groundingMetadata": {
                            "groundingChunks": [
                                {
                                    "web": {
                                        "uri": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ-test",
                                        "title": "jointjrtrades.com",
                                    }
                                }
                            ],
                            "groundingSupports": [
                                {
                                    "segment": {"text": "Alpha", "startIndex": 0, "endIndex": 5},
                                    "groundingChunkIndices": [0],
                                }
                            ],
                        },
                    }
                ]
            }
        )

        self.assertEqual(package["grounded_results"][0]["url"], "https://jointjrtrades.com")
        self.assertEqual(package["citations"][0]["url"], "https://jointjrtrades.com")


if __name__ == "__main__":
    unittest.main()
