import asyncio
from types import SimpleNamespace

import pytest

import backend.rag as rag
from backend.services.chat_prompt import (
    build_creator_style_disclosure_prompt,
    build_memory_association_prompt,
    build_personality_filter_prompt,
    build_universal_human_engine_prompt,
)
from backend.services.llm_provider import GeminiLLMProvider, selected_chat_provider
from backend.services.gemini_context_cache import GeminiContextCacheService
from backend.services.persona_prompts import (
    CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION,
    SOUL_MD_GENERATOR_SYSTEM_INSTRUCTION,
    PersonaSynthesisResult,
    SoulCompilationResult,
    build_creator_content_analysis_prompt,
    build_soul_compiler_prompt,
)
from backend.settings import settings


def _persona_payload():
    return {
        "analysis_markdown": "# Creator Persona Analysis\n\nFinding: Direct.\nEvidence: Do the work.\nConfidence: CONFIRMED",
        "creator_persona": {
            "creator_name": "Creator",
            "voice_summary": "Direct, practical, and grounded.",
            "sentence_style": "Short sentences mixed with blunt framing.",
            "cadence": "Fast, punchy, then explanatory.",
            "slang_list": ["look"],
            "repeated_phrases": ["do the work"],
            "metaphor_domains": ["fitness", "business"],
            "worldview": "Action beats vague motivation.",
            "core_beliefs": ["Evidence matters"],
            "advice_style": "Concrete next steps.",
            "emotional_baseline": "Calm intensity.",
            "humor_style": "Dry and sparse.",
            "taboo_phrases": ["hope this helps"],
            "topics_to_avoid": ["private family details"],
            "no_fly_zone": ["hateful content"],
            "example_quotes": ["Do the work first."],
            "response_rules": ["Answer directly"],
            "confidence_score": 0.82,
            "source_coverage_summary": "8 approved samples with transcript coverage.",
        },
        "style_fingerprint": {
            "schema_version": 3,
            "traits": ["Creator favors direct action."],
        },
    }


class FakeGeminiModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.responses.pop(0))

    def generate_content_stream(self, **kwargs):
        self.calls.append(kwargs)
        for text in self.responses:
            yield SimpleNamespace(text=text)


class FakeGeminiClient:
    def __init__(self, responses):
        self.models = FakeGeminiModels(responses)


def test_selected_chat_provider(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_PROVIDER", "gemini")
    assert selected_chat_provider() == "gemini"
    monkeypatch.setattr(settings, "CHAT_PROVIDER", "bogus")
    assert selected_chat_provider() == "gemini"
    monkeypatch.setattr(settings, "CHAT_PROVIDER", "openai")
    assert selected_chat_provider() == "gemini"


def test_gemini_request_construction(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_SAFETY_THRESHOLD", "BLOCK_ONLY_HIGH")
    client = FakeGeminiClient(['{"ok": true}'])
    provider = GeminiLLMProvider(client=client)

    provider.generate_text(
        messages=[
            {"role": "system", "content": "System rules"},
            {"role": "user", "content": "Hello"},
        ],
        model="gemini-test",
        json_mode=True,
        temperature=0.3,
    )

    call = client.models.calls[0]
    assert call["model"] == "gemini-test"
    assert "USER:\nHello" in call["contents"]
    assert call["config"]["system_instruction"] == "System rules"
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["safety_settings"][0]["threshold"] == "BLOCK_ONLY_HIGH"


def test_gemini_async_stream_yields_provider_chunks():
    client = FakeGeminiClient(["one ", "two"])
    provider = GeminiLLMProvider(client=client)

    async def collect():
        chunks = []
        async for chunk in provider._stream_text_async(prompt="Hello", model="gemini-test"):
            chunks.append(chunk.text)
        return chunks

    assert asyncio.run(collect()) == ["one ", "two"]
    assert client.models.calls[0]["model"] == "gemini-test"


def test_persona_schema_validation():
    client = FakeGeminiClient([__import__("json").dumps(_persona_payload())])
    provider = GeminiLLMProvider(client=client)

    result = provider.generate_json(
        system_instruction="Return persona JSON",
        prompt="Analyze",
        schema_model=PersonaSynthesisResult,
        model="gemini-test",
    )

    assert result.creator_persona.creator_name == "Creator"
    assert "Creator Persona Analysis" in result.analysis_markdown
    assert result.creator_persona.confidence_score == pytest.approx(0.82)


def test_invalid_json_retries_once_and_repairs():
    client = FakeGeminiClient(["not json", __import__("json").dumps(_persona_payload())])
    provider = GeminiLLMProvider(client=client)

    result = provider.generate_json(
        system_instruction="Return persona JSON",
        prompt="Analyze",
        schema_model=PersonaSynthesisResult,
        model="gemini-test",
    )

    assert result.creator_persona.voice_summary.startswith("Direct")
    assert len(client.models.calls) == 2


def test_chat_prompt_uses_persona_json_without_ai_disclosure():
    prompt = build_creator_style_disclosure_prompt(
        {
            "style_fingerprint": {
                "creator_persona": {
                    "voice_summary": "Blunt and practical",
                    "response_rules": ["No fluff"],
                }
            }
        },
        "Creator",
    )

    assert "creator chat surface" in prompt.lower()
    assert "ai creator-style assistant" not in prompt.lower()
    assert "never say you are an ai" in prompt.lower()
    assert "Blunt and practical" in prompt
    assert "literally be the real person" in prompt


def test_chat_prompt_layers_separate_human_engine_and_personality_filter():
    creator_profile = {
        "style_fingerprint": {
            "creator_persona": {
                "voice_summary": "Blunt and practical",
                "cadence": "Short punchy lines",
            }
        }
    }

    human_prompt = build_universal_human_engine_prompt(mode="small_talk")
    personality_prompt = build_personality_filter_prompt(creator_profile, "Creator", mode="small_talk")
    memory_prompt = build_memory_association_prompt()

    assert "UNIVERSAL HUMAN ENGINE" in human_prompt
    assert "CREATOR PERSONALITY FILTER" in personality_prompt
    assert "Personality changes HOW things are said" in personality_prompt
    assert "Blunt and practical" in personality_prompt
    assert "HUMAN MEMORY ASSOCIATION" in memory_prompt


def test_analysis_prompt_keeps_evidence_and_confidence_layers():
    prompt = build_creator_content_analysis_prompt(
        creator_name="Creator",
        creator_niche="business",
        known_platforms="YouTube",
        content_type="Video transcripts",
        corpus="Look, do the work first.",
        existing_schema_hint={"schema_version": 3},
    )

    assert "CONFIRMED, INFERRED, ABSENT, CONTRADICTED, LOW-DATA" in CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION
    assert "Negative Space" in prompt
    assert "Irreplaceable Core" in prompt
    assert "Human Simulation Framework extraction" in prompt
    assert "Sentence rhythm" in prompt
    assert "Direct quote evidence" not in prompt
    assert "Finding, Evidence, Confidence, Interpretation, Runtime Voice Rule" in prompt


def test_soul_compiler_returns_runtime_prompt_artifact():
    client = FakeGeminiClient([
        __import__("json").dumps({
            "soul_md": "# soul.md\n\n## 0. Identity Boundary\nDo not claim to be real.",
            "runtime_prompt_md": "# Runtime\nReason normally. Speak through the creator voice.",
        })
    ])
    provider = GeminiLLMProvider(client=client)

    prompt = build_soul_compiler_prompt(
        creator_name="Creator",
        creator_niche="business",
        analysis_markdown="# Creator Persona Analysis",
        research_summary={},
        style_fingerprint={},
    )
    result = provider.generate_json(
        system_instruction=SOUL_MD_GENERATOR_SYSTEM_INSTRUCTION,
        prompt=prompt,
        schema_model=SoulCompilationResult,
        model="gemini-test",
    )

    assert "Identity Boundary" in result.soul_md
    assert "Speak through the creator voice" in result.runtime_prompt_md


def test_chat_completion_is_gemini_only(monkeypatch):
    monkeypatch.setattr(settings, "CHAT_PROVIDER", "openai")
    monkeypatch.setattr(settings, "GEMINI_CHAT_MODEL", "gemini-test")

    class FakeProvider:
        def __init__(self):
            self.calls = []

        def generate_text(self, **kwargs):
            self.calls.append(kwargs)
            return "gemini ok"

    provider = FakeProvider()
    monkeypatch.setattr(rag, "get_gemini_provider", lambda: provider)

    result = rag.generate_chat_completion(
        messages=[{"role": "user", "content": "hello"}],
        model="legacy-model",
    )

    assert result == "gemini ok"
    assert provider.calls[0]["model"] == "gemini-test"


def test_gemini_cache_router_detects_specific_reference(monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_DYNAMIC_RAG_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    client = FakeGeminiClient([
        '{"requires_lookup": true, "reason": "video reference", "query": "4 stages of burnout", "reference_type": "video"}'
    ])
    service = GeminiContextCacheService(provider=GeminiLLMProvider(client=client))

    result = service.should_lookup("Remember that part in the video where he talks about the 4 stages of burnout?")

    assert result["requires_lookup"] is True
    assert result["query"] == "4 stages of burnout"


def test_gemini_cache_support_chunk_shapes_fact_block():
    chunk = GeminiContextCacheService.as_support_chunk({
        "source_title": "Burnout video",
        "source_url": "https://example.com/video",
        "timestamp": "12:02",
        "evidence_quote": "Burnout isn't about working too hard.",
        "retrieved_fact": "The creator defines burnout as misaligned work.",
        "confidence": 0.91,
    })

    assert chunk["is_gemini_cache"] is True
    assert "GEMINI CACHED CORPUS RESULT" in chunk["content"]
    assert "Burnout isn't about working too hard" in chunk["content"]
    assert chunk["source_ref"]["timestamp"] == "12:02"


def test_creator_memory_v2_migration_defines_transcript_persona_cache_tables():
    from pathlib import Path

    sql = Path("backend/migrations/013_creator_memory_v2.sql").read_text(encoding="utf-8")

    for table_name in [
        "content_documents",
        "transcript_segments",
        "content_chunks",
        "content_embeddings",
        "persona_analyses",
        "soul_versions",
        "runtime_prompts",
        "gemini_context_caches",
        "retrieval_events",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table_name}" in sql
