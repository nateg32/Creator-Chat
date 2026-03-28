import asyncio
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


BASE_DIR = pathlib.Path(__file__).resolve().parents[1]


def load_fingerprint_service_module(fake_async_client):
    module_path = BASE_DIR / "services" / "fingerprint_service.py"
    spec = importlib.util.spec_from_file_location("test_fingerprint_service_module", module_path)
    module = importlib.util.module_from_spec(spec)

    fake_db = types.ModuleType("backend.db")
    fake_db.db = types.SimpleNamespace(
        execute_one=lambda *args, **kwargs: None,
        execute_query=lambda *args, **kwargs: [],
        execute_update=lambda *args, **kwargs: None,
    )

    class FakePersonalityAnalyzer:
        @staticmethod
        def _load_corpus(*args, **kwargs):
            return [
                {
                    "content": "Pick one buyer with money and urgency. Pre sell before you build.",
                    "metadata": {
                        "platform": "youtube",
                        "canonical_url": "https://www.youtube.com/watch?v=REALVIDEO01",
                    },
                    "source": "youtube",
                    "source_id": "REALVIDEO01",
                    "title": "How to Start Fast",
                }
            ]

        @staticmethod
        def analyze_creator(*args, **kwargs):
            return {}

    fake_personality = types.ModuleType("backend.personality_analyzer")
    fake_personality.PersonalityAnalyzer = FakePersonalityAnalyzer

    class FakeResearchProvider:
        def grounded_overview(self, query, creator_profile, conversation_history=None, max_queries=4):
            return {
                "response_text": "Creator says to start with one buyer and pre sell the offer.",
                "query_plan": ["creator official about", "creator interview product strategy"],
                "results": [
                    {
                        "title": "About Creator",
                        "url": "https://creator.com/about",
                        "snippet": "Background and offer framework.",
                    }
                ],
                "citations": [
                    {
                        "text": "one buyer",
                        "url": "https://creator.com/about",
                        "title": "About Creator",
                        "start_index": 29,
                        "end_index": 38,
                    }
                ],
                "sources": [
                    {
                        "title": "About Creator",
                        "url": "https://creator.com/about",
                        "resource_type": "web",
                        "platform": "web",
                        "subquery": "creator official about",
                    }
                ],
                "packages": [],
            }

        def research_links(self, *args, **kwargs):
            return {}

        def research_dossier(self, *args, **kwargs):
            return {}

    fake_research_provider = types.ModuleType("backend.services.research_provider")
    fake_research_provider.GeminiResearchProvider = FakeResearchProvider
    fake_research_provider.get_research_provider = lambda: FakeResearchProvider()
    fake_services_package = types.ModuleType("backend.services")
    fake_services_package.research_provider = fake_research_provider

    fake_corpus_state = types.ModuleType("backend.services.corpus_state")
    fake_corpus_state.compute_creator_corpus_checksum = lambda *args, **kwargs: "checksum"

    fake_settings = types.ModuleType("backend.settings")
    fake_settings.settings = types.SimpleNamespace(
        OPENAI_API_KEY="test-key",
        GOOGLE_API_KEY="test-gemini",
        MODEL_CLASSIFICATION="test-classify",
        MODEL_VERIFY="test-verify",
        CHAT_MODEL="test-chat",
    )

    fake_rag = types.ModuleType("backend.rag")
    fake_rag.get_client = lambda: types.SimpleNamespace()
    fake_rag.get_async_client = lambda: fake_async_client

    with patch.dict(
        sys.modules,
        {
            "backend.db": fake_db,
            "backend.personality_analyzer": fake_personality,
            "backend.services": fake_services_package,
            "backend.services.research_provider": fake_research_provider,
            "backend.services.corpus_state": fake_corpus_state,
            "backend.settings": fake_settings,
            "backend.rag": fake_rag,
        },
    ):
        spec.loader.exec_module(module)

    module.__fake_modules__ = {
        "backend.services": fake_services_package,
        "backend.services.research_provider": fake_research_provider,
    }
    return module


def _tool_call(call_id, name, args):
    return types.SimpleNamespace(
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _tool_response(*tool_calls):
    message = types.SimpleNamespace(content="", tool_calls=list(tool_calls))
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeAsyncCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake responses left for async client")
        return self.responses.pop(0)


class FingerprintAgentTests(unittest.TestCase):
    def test_agent_waits_for_evidence_then_synthesizes_bundle(self):
        fake_chat = FakeAsyncCompletions(
            [
                _tool_response(_tool_call("call-1", "analyze_content_style", {"focus": "voice_and_tone"})),
                _tool_response(_tool_call("call-2", "search_web", {"query": "Creator official about", "intent": "identity"})),
                _tool_response(
                    _tool_call(
                        "call-3",
                        "record_finding",
                        {
                            "category": "values",
                            "finding": "Pre sell before you build is a repeated rule.",
                            "confidence": "high",
                            "source_refs": ["https://creator.com/about"],
                        },
                    )
                ),
                _tool_response(_tool_call("call-4", "synthesize_persona", {"ready": True, "gaps": ""})),
            ]
        )
        fake_async_client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=fake_chat))
        module = load_fingerprint_service_module(fake_async_client)
        with patch.dict(sys.modules, module.__fake_modules__):
            service = module.FingerprintService()

        async def fake_bundle(context, gaps=""):
            return {
                "identity_patch": {"identity": {"full_name": "Creator Name"}},
                "dossier_patch": {"biography": {"early_life": "Built offers from first principles."}},
                "creator_claims": ["Start with one buyer."],
                "unknown_fields": [],
                "fact_registry": list(context.get("gathered_facts") or []),
                "style_summary": "Direct, imperative, and anti-fluff.",
                "identity_summary": "Operator-teacher who prioritizes proof over polish.",
                "runtime_anchor_points": ["pre sell before you build", "one buyer with money and urgency"],
                "verified_beliefs": ["Pre sell before you build."],
                "audience_contract": ["Give practical moves, not motivational filler."],
                "lexical_markers": {
                    "signature_phrases": ["pre sell before you build"],
                    "high_signal_words": ["buyer", "urgency", "offer"],
                    "banned_generic_phrases": ["follow your dreams"],
                },
                "soul_seed_markdown": "You are direct and proof-driven.",
            }

        service._synthesize_persona_bundle = fake_bundle

        result = asyncio.run(
            service._run_fingerprint_agent(
                creator_id=1,
                creator_name="Creator",
                creator_profile={"id": 1, "name": "Creator", "official_domains": ["creator.com"], "platform_configs": {}},
                link_identity={},
                voice_fingerprint={
                    "signature_phrases": ["cut the fluff"],
                    "evidence_snippets": ["one buyer with money and urgency"],
                },
                creator_claims=[],
                unknown_fields=[],
            )
        )

        self.assertEqual(result["research_quality"], "agentic_grounded")
        self.assertEqual(result["style_summary"], "Direct, imperative, and anti-fluff.")
        self.assertEqual(
            [step["tool"] for step in result["tool_trace"]],
            ["analyze_content_style", "search_web", "record_finding", "synthesize_persona"],
        )
        self.assertEqual(result["grounding_packets"][0]["query_plan"][0], "creator official about")
        self.assertEqual(result["fact_registry"][0]["finding"], "Pre sell before you build is a repeated rule.")
        self.assertEqual(fake_chat.calls[0]["tools"][0]["function"]["name"], "analyze_content_style")


if __name__ == "__main__":
    unittest.main()
