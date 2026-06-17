import json

from backend.services.chat_prompt import build_personality_filter_prompt
from backend.core.interaction_engine import build_voice_card, format_voice_card_for_prompt
from backend.services.style_signal_sanitizer import (
    looks_like_raw_content_hook,
    sanitize_style_fingerprint_for_runtime,
    sanitize_style_fingerprint_for_storage,
)


def test_broadcast_hooks_are_not_runtime_voice_signals():
    raw = "Business owners: Want to scale faster?"

    assert looks_like_raw_content_hook(raw)

    style = {
        "signature_phrases": [raw, "Keep doing the work"],
        "lexical_rules": {"signature_phrases": [raw], "high_signal_words": ["leverage"]},
        "speech_mechanics": {"signature_openings": [raw]},
        "mode_matrix": {"greeting": {"opening_move": raw}},
        "golden_examples": {"greeting": [raw]},
        "creator_persona": {
            "repeated_phrases": [raw],
            "example_quotes": [raw],
            "voice_summary": "Direct, practical pressure with business language.",
        },
    }

    storage_style = sanitize_style_fingerprint_for_storage(style)
    assert raw not in json.dumps(storage_style)

    runtime_style = sanitize_style_fingerprint_for_runtime(style)
    assert raw not in json.dumps(runtime_style)
    assert runtime_style["lexical_rules"]["signature_phrases"] == []
    assert runtime_style["speech_mechanics"]["signature_openings"] == []


def test_personality_filter_prompt_uses_patterns_not_phrase_bank():
    raw = "Business owners: Want to scale faster?"
    creator_profile = {
        "style_fingerprint": {
            "creator_persona": {
                "creator_name": "Alex Hormozi",
                "voice_summary": "Direct, high-pressure business coaching.",
                "sentence_style": "Short, declarative, practical.",
                "cadence": "Fast and blunt.",
                "slang_list": [],
                "repeated_phrases": [raw],
                "example_quotes": [raw],
                "response_rules": ["Challenge the premise before giving advice."],
                "confidence_score": 0.8,
                "source_coverage_summary": "Approved business content.",
            },
            "lexical_rules": {"signature_phrases": [raw], "high_signal_words": ["leverage"]},
            "speech_mechanics": {"signature_openings": [raw]},
        },
        "voice_profile": {"signature_phrases": [raw]},
    }

    prompt = build_personality_filter_prompt(creator_profile, "Alex Hormozi", mode="greeting")

    assert raw not in prompt
    assert "transcript hooks" in prompt
    assert "leverage" in prompt


def test_voice_card_does_not_expose_raw_transcript_lines():
    raw = "Business owners: Want to scale faster?"
    creator_profile = {
        "style_fingerprint": {
            "evidence_snippets": [raw],
            "golden_examples": {"greeting": [raw]},
            "lexical_rules": {
                "signature_phrases": [raw],
                "high_signal_words": ["leverage"],
            },
            "value_model": {"decision_heuristics": ["Make decisions from leverage, not emotion."]},
        },
        "voice_profile": {"signature_phrases": [raw]},
    }

    rendered = format_voice_card_for_prompt(build_voice_card(creator_profile), "Alex Hormozi")

    assert raw not in rendered
    assert "A real line of yours" not in rendered
    assert "leverage" in rendered
