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
        EXA_API_KEY="test-exa-key",
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
ExaSearchProvider = research_provider_module.ExaSearchProvider


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

    def test_grounded_overview_promotes_citation_urls_into_sources_when_results_are_empty(self):
        provider = GeminiResearchProvider()
        provider.enabled = True
        provider._build_grounding_query_plan = lambda *args, **kwargs: ["alpha query"]

        def fake_call(prompt, search_enabled=True):
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

        with patch.object(provider, "_call_gemini_rest", side_effect=fake_call), \
             patch.object(provider, "_extract_grounded_results", return_value=[]):
            overview = provider.grounded_overview(
                "Tell me about the creator",
                {"name": "Creator", "official_domains": ["creator.com"], "platform_configs": {}},
                max_queries=1,
            )

        self.assertEqual(overview["sources"][0]["url"], "https://creator.com/about")

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

    def test_exa_platform_query_specs_use_creator_owned_social_handles(self):
        provider = ExaSearchProvider()
        profile = {
            "name": "Alex G",
            "handle": "alexg",
            "platform_configs": {
                "youtube": {"enabled": True, "handle": "alexgtrades", "social_confidence": 0.92},
                "tiktok": {"enabled": True, "handle": "alexgtrades", "social_confidence": 0.91},
                "instagram": {"enabled": True, "handle": "alexgtrades", "social_confidence": 0.91},
            },
        }

        specs = provider._platform_query_specs("risk management for beginners", profile, max_specs=5)
        platforms = {spec["platform"] for spec in specs}
        queries = " ".join(spec["query"].lower() for spec in specs)

        self.assertIn("youtube", platforms)
        self.assertIn("tiktok", platforms)
        self.assertIn("instagram", platforms)
        self.assertIn("alex g", queries)
        self.assertIn("risk management", queries)

    def test_exa_affiliated_podcast_requires_clear_creator_feature(self):
        provider = ExaSearchProvider()
        profile = {
            "name": "Alex Hormozi",
            "handle": "hormozi",
            "platform_configs": {
                "youtube": {"enabled": True, "handle": "alexhormozi", "social_confidence": 0.95}
            },
        }

        accepted = provider._enforce_cog(
            [
                {
                    "title": "Alex Hormozi guest interview on scaling software",
                    "url": "https://open.spotify.com/episode/real",
                    "snippet": "A podcast conversation featuring Alex Hormozi as the guest.",
                    "platform": "spotify",
                    "confidence": 0.76,
                }
            ],
            profile,
        )

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["relation"], "AFFILIATED")
        self.assertEqual(accepted[0]["platform"], "spotify")
        self.assertGreaterEqual(accepted[0]["confidence"], 0.8)

    def test_exa_rejects_social_results_without_creator_identity(self):
        provider = ExaSearchProvider()
        profile = {
            "name": "Anabolic Gabe",
            "handle": "anabolicgabe",
            "platform_configs": {
                "tiktok": {"enabled": True, "handle": "anabolicgabe", "social_confidence": 0.94}
            },
        }

        accepted = provider._enforce_cog(
            [
                {
                    "title": "Best bulking food tips",
                    "url": "https://www.tiktok.com/@randomfitness/video/123",
                    "snippet": "A random creator talking about bulking.",
                    "platform": "tiktok",
                    "confidence": 0.9,
                }
            ],
            profile,
        )

        self.assertEqual(accepted, [])

    def test_enforce_cog_rejects_same_name_result_when_category_mismatches(self):
        provider = ExaSearchProvider()
        profile = {
            "name": "Matt Armstrong",
            "handle": "mattarmstrongbmx",
            "creator_category": "automotive rebuilds",
            "style_fingerprint": {
                "search_profile": {
                    "primary_category": "automotive rebuilds",
                    "search_identity_terms": ["cars", "wrecked cars", "Audi R8", "rebuilds"],
                    "disambiguation_terms": ["automotive", "car rebuild"],
                }
            },
            "platform_configs": {
                "youtube": {"enabled": True, "handle": "mattarmstrongbmx", "social_confidence": 0.95}
            },
        }

        accepted = provider._enforce_cog(
            [
                {
                    "title": "Matt Armstrong on Arming for the War We're In Podcast",
                    "url": "https://mountainrunner.substack.com/p/arming-for-the-war-were-in-podcast",
                    "snippet": "Taking non-military conflict as seriously as military conflict.",
                    "platform": "podcast",
                    "confidence": 0.91,
                }
            ],
            profile,
        )

        self.assertEqual(accepted, [])

    def test_enforce_cog_accepts_same_name_result_when_category_matches(self):
        provider = ExaSearchProvider()
        profile = {
            "name": "Matt Armstrong",
            "handle": "mattarmstrongbmx",
            "creator_category": "automotive rebuilds",
            "style_fingerprint": {
                "search_profile": {
                    "primary_category": "automotive rebuilds",
                    "search_identity_terms": ["cars", "wrecked cars", "Audi R8", "rebuilds"],
                    "disambiguation_terms": ["automotive", "car rebuild"],
                }
            },
        }

        accepted = provider._enforce_cog(
            [
                {
                    "title": "Matt Armstrong explains rebuilding a wrecked Audi R8",
                    "url": "https://open.spotify.com/episode/real",
                    "snippet": "A podcast interview about cars, automotive YouTube and rebuilds.",
                    "platform": "spotify",
                    "confidence": 0.84,
                }
            ],
            profile,
        )

        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["relation"], "AFFILIATED")


if __name__ == "__main__":
    unittest.main()
