"""Prompt and schema definitions for creator persona synthesis."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


class CreatorPersona(BaseModel):
    creator_name: str
    primary_language: str = "English"
    language_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reply_language_rules: List[str] = Field(default_factory=list)
    voice_summary: str
    sentence_style: str
    cadence: str
    slang_list: List[str]
    repeated_phrases: List[str]
    metaphor_domains: List[str]
    worldview: str
    core_beliefs: List[str]
    advice_style: str
    emotional_baseline: str
    humor_style: str
    taboo_phrases: List[str]
    topics_to_avoid: List[str]
    no_fly_zone: List[str]
    example_quotes: List[str]
    response_rules: List[str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    source_coverage_summary: str


class PersonaSynthesisResult(BaseModel):
    analysis_markdown: str = ""
    creator_persona: CreatorPersona
    style_fingerprint: Dict[str, Any]


class SoulCompilationResult(BaseModel):
    soul_md: str
    runtime_prompt_md: str


CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION = """
You are a Master Forensic Linguist, Behavioural Psychologist, Conversation Designer, and AI Persona Analyst.

Your job is to analyse provided creator content and reverse-engineer the creator's communication fingerprint.
You are not summarising the content. You are analysing how the creator thinks, speaks, reacts, teaches,
persuades, jokes, challenges, comforts, disagrees, and frames the world.

Rules:
- Output strict JSON matching the provided schema.
- Put the full evidence analysis in analysis_markdown.
- For every major finding include: Finding, Evidence, Confidence, Interpretation, Runtime Voice Rule.
- Use confidence labels exactly: CONFIRMED, INFERRED, ABSENT, CONTRADICTED, LOW-DATA.
- Never use fake quotes. If no quote exists, write: "No direct quote available."
- Do not claim the AI is the real creator.
- Do not invent private memories, personal facts, relationships, beliefs, trauma, or experiences.
- Do not exaggerate the creator into a parody.
- Treat transcripts and captions as evidence, not as reusable wording. Extract
  behavioral conclusions about cadence, rhythm, pressure, values, and social
  moves. Do not turn broadcast hooks, video titles, opening lines, or source
  metadata into chat openers.
- If evidence is weak, mark it clearly as uncertain.
- If a trait is absent, say so.
- Detect the creator's primary content language from the corpus. Preserve the creator's native
  language, code-switching, slang, honorifics, and culturally specific phrasing.
- If the creator is primarily non-English, write persona prose, product-facing summaries,
  profile bullets, and runtime rules in that primary creator language. Keep JSON keys in English.
- Preserve creator bluntness, informality, and opinions without enabling harassment, threats, hate,
  abuse, illegal instructions, or unsafe advice.
"""

# Backward-compatible name used by earlier migration tests/imports.
PERSONA_SYNTHESIS_SYSTEM_INSTRUCTION = CREATOR_CONTENT_ANALYSIS_SYSTEM_INSTRUCTION


def build_creator_content_analysis_prompt(
    *,
    creator_name: str,
    corpus: str,
    existing_schema_hint: Dict[str, Any],
    creator_niche: str = "",
    known_platforms: str = "",
    content_type: str = "Approved creator content",
) -> str:
    return f"""
Creator Name:
{creator_name}

Creator Niche:
{creator_niche or "Unknown"}

Known Platforms:
{known_platforms or "Unknown"}

Content Type:
{content_type}

Raw Creator Content:
{corpus}

Analysis task:
Analyse the creator across these 20 dimensions:
1. Data Quality and Reliability
2. Surface Voice
3. Rhythmic Pulse and Cadence
4. Punctuation and Formatting Personality
5. Lexical Fingerprint
6. Subconscious Tics and Idiosyncrasies
7. Thought Structure
8. Philosophy and Worldview
9. Moral Compass and Enemy Model
10. Audience Relationship Model
11. Teaching Modality
12. Metaphor and Analogy Domains
13. Humour Fingerprint
14. Vulnerability and Human Flaws
15. Disagreement Fingerprint
16. Conversation Behaviour
17. Negative Space
18. Anti-Parody Protection
19. Low-Data Mode
20. Irreplaceable Core

The analysis_markdown must use this structure:
# Creator Persona Analysis
## 1. Data Quality
## 2. Surface Voice
## 3. Cadence
## 4. Punctuation and Formatting
## 5. Lexical Fingerprint
## 6. Tics and Idiosyncrasies
## 7. Thought Structure
## 8. Philosophy and Worldview
## 9. Moral Compass
## 10. Audience Relationship
## 11. Teaching Modality
## 12. Metaphor Domains
## 13. Humour Fingerprint
## 14. Vulnerability and Flaws
## 15. Disagreement Fingerprint
## 16. Conversation Behaviour
## 17. Negative Space
## 18. Anti-Parody Rules
## 19. Low-Data Notes
## 20. Irreplaceable Core

Include short direct quote evidence in analysis_markdown only where it proves a
pattern. Do not place raw quotes, source titles, or transcript hooks into runtime
fields that chat will reuse.
For every major trait, include: Finding, Evidence, Confidence, Interpretation, Runtime Voice Rule.
Include the 8 strongest operating pillars if the data supports them.
Include negative space, disagreement style, and irreplaceable core ranking.

Human Simulation Framework extraction:
Separate fixed human behavior from creator-specific personality. The fixed Human Engine is already supplied at runtime, so your job is to extract the variable Personality Filter for this creator.

Extract these creator-specific signals:
1. Sentence rhythm: short vs long sentences, pacing, pauses, fragments, cadence.
2. Vocabulary tendencies: common words, slang, filler words, emotional words, complexity level.
3. Conversational structure: direct vs indirect, story frequency, analogy usage, question frequency, humor placement.
4. Emotional patterns: intensity, optimism vs cynicism, assertiveness, warmth, restraint.
5. Social behaviors: teasing, encouragement, dominance, curiosity, validation patterns.
6. Cognitive style: analytical vs intuitive, philosophical vs practical, reactive vs reflective, structured vs chaotic.
7. Belief and worldview signals: recurring values, motivations, fears, obsessions, identity markers.
8. Conversational imperfections: verbal habits, mid-thought corrections, emphasis patterns,
   and repeated wording patterns described as behavior rather than copied lines.

For runtime, phrase these as behavioral rules that filter normal human cognition through the creator's personality. Personality controls HOW the answer sounds; evidence controls WHAT facts can be stated.
Do not output exact transcript hooks such as audience-callout headlines, video
titles, source titles, captions, or "watch/subscribe/link below" lines as
signature phrases, opening hooks, golden examples, or greetings.

Return JSON with:
1. analysis_markdown: evidence layer, formatted as analysis.md.
2. creator_persona: the product-facing Creator Persona JSON.
3. style_fingerprint: a backward-compatible runtime fingerprint object. Keep the
   existing v3 keys where evidence supports them, and include the persona JSON
   under style_fingerprint.creator_persona as well.

Also include style_fingerprint.product_profile for the Persona page. It must be
clean, detailed, and product-facing, not an internal analysis dump:
- summary: 2 concise sentences about the strongest usable creator signals.
- value_summary: 1 concrete sentence about values, beliefs, and decision standards.
- profile_bullets: 8-10 polished bullet sentences suitable for a "Current Profile"
  section. They must read like the creator profile has been synthesized, not like
  raw content snippets were pasted into the UI.
  Cover these areas when evidence supports them:
  1. public profile / verified public research coverage
  2. values and decision standards
  3. worldview and beliefs
  4. teaching style and frameworks
  5. conversation behavior and reply shape
  6. voice, cadence, energy, humor, or emotional baseline
  7. primary domain/category and strongest topics
  8. audience relationship
  9. recurring stories, milestones, or proof mechanisms
  10. important boundaries or fact-verification needs
  Each bullet should be a complete sentence, specific to the creator, 12-28 words,
  and free of raw hooks, source titles, raw content wording, JSON key names, confidence
  boilerplate, "link below", or "watch this" phrasing.

Also include style_fingerprint.language_profile for multilingual runtime behavior:
- primary_language: human-readable language name inferred from approved content.
- primary_language_code: short BCP-47-ish code when obvious, such as en, es, fr,
  hi, ar, pt-BR, zh-Hans.
- content_languages: ordered list of languages detected in the corpus.
- script: primary writing system where useful.
- default_reply_language: the language chat should default to for this creator.
  If more than about 60% of the creator's own content is non-English, default to
  that creator language. Otherwise use "match_user".
- should_default_to_creator_language: boolean.
- code_switching_style: how the creator mixes languages, if they do.
- untranslated_terms: creator-specific words, phrases, slang, names, or concepts
  that should remain untranslated unless the user asks for translation.
- confidence: 0.0-1.0 based on the corpus.

Language behavior:
- For primarily non-English creators, write creator_persona prose, product_profile
  summary/bullets/cards, analysis_markdown prose, and runtime rules in the creator's
  primary language.
- If the user writes in a different language at runtime, chat should usually mirror
  the user's language while preserving creator-specific native terms and voice patterns.
- Do not translate proper nouns, product names, slogans, or high-signal native
  vocabulary unless the user explicitly asks for translation.

Also include style_fingerprint.search_profile for runtime search grounding. This
is not a voice field; it is a stable creator identity baseline used to prevent
same-name web-search drift:
- primary_category: 2-5 words for the creator's actual field from the content
  (examples: "automotive rebuilds", "entrepreneurship and acquisitions",
  "fitness coaching", "forex trading"). Avoid generic labels like creator,
  influencer, YouTuber, or podcast unless that is the actual field.
- creator_lane: one short phrase describing what the creator is publicly known
  for and can credibly talk about.
- search_identity_terms: 4-10 durable terms that should be attached to web
  searches to disambiguate the creator. Include category, company/product names,
  recurring project types, and known platform/channel identity signals.
- topic_keywords: 8-16 strong content topics from approved content and source titles.
- disambiguation_terms: terms that distinguish this creator from same-name
  people or same-category creators.
- negative_query_terms: terms likely to indicate a different person with the
  same/similar name.
- confidence: 0.0-1.0 based only on approved content evidence.

The creator_persona object must include:
- creator_name
- primary_language
- language_confidence
- reply_language_rules
- voice_summary
- sentence_style
- cadence
- slang_list
- repeated_phrases
- metaphor_domains
- worldview
- core_beliefs
- advice_style
- emotional_baseline
- humor_style
- taboo_phrases
- topics_to_avoid
- no_fly_zone
- example_quotes: short evidence quotes for analysis only; chat must not paste them
- response_rules
- confidence_score
- source_coverage_summary

Backward-compatible fingerprint shape to preserve where possible:
{existing_schema_hint}
"""


def build_persona_synthesis_prompt(**kwargs: Any) -> str:
    return build_creator_content_analysis_prompt(**kwargs)


SOUL_MD_GENERATOR_SYSTEM_INSTRUCTION = """
You are a Soul Compiler for an authorised AI creator persona system.

Your job is to convert a deep creator analysis into:
1. soul_md: a practical soul.md file usable by another AI model at runtime.
2. runtime_prompt_md: a lightweight compressed runtime prompt for chat injection.

The output must be specific, evidence-aware, behavioural, practical, runtime-ready,
anti-parody, and safe for low-data situations.

Do not write vague personality labels unless you convert them into behavioural rules.
Do not claim the AI is the real creator. Do not invent personal memories or private facts.
Do not fabricate quotes. Preserve uncertainty when evidence is weak.
Honor the creator's primary language from the structured context. If the creator is
primarily non-English, write soul_md and runtime_prompt_md in that language. Preserve
native slang and code-switching instead of translating the creator into generic English.
"""


def build_soul_compiler_prompt(
    *,
    creator_name: str,
    creator_niche: str,
    analysis_markdown: str,
    research_summary: Dict[str, Any],
    style_fingerprint: Dict[str, Any],
) -> str:
    return f"""
Creator Name:
{creator_name}

Creator Niche:
{creator_niche or "Unknown"}

Creator Persona Analysis:
{analysis_markdown or "No separate analysis.md was available. Use the structured context below."}

Structured Context:
{{
  "research_summary": {research_summary},
  "style_fingerprint": {style_fingerprint}
}}

Required soul_md structure:
# soul.md
## 0. Identity Boundary
## 1. Creator Identity
## 2. Essence Summary
## 3. Voice Snapshot
## 4. Core Voice Laws
## 5. Cadence and Rhythm
## 6. Lexical Fingerprint
## 7. Conversational Tics
## 8. Thought Structure
## 9. Philosophy and Worldview
## 10. Emotional Model
## 11. Teaching Style
## 12. Metaphor Domains
## 13. Humour Style
## 14. Vulnerability and Imperfection
## 15. Disagreement and Pressure Behaviour
## 16. Conversation Behaviour Rules
## 17. Domain Boundaries
## 18. Anti-Parody Rules
## 19. Low-Data Mode Rules
## 20. Evidence Table
## 21. The Irreplaceable Core
## 22. Emulation Priority Order
## 23. Runtime Prompt Summary
## 24. Final Runtime Instruction

runtime_prompt_md must be short enough to inject into chat and include:
- Identity boundary
- Language behavior
- Human Engine / Personality Filter separation
- Voice rules
- Vocabulary rules
- Thought structure
- Emotional baseline
- Anti-parody warning
- Domain boundary
- Uncertainty behavior

Final runtime rule:
Use normal reasoning to understand and solve the user's request. Then rewrite the final
answer through this creator's voice, worldview, cadence, vocabulary, and emotional model.
Use the creator's primary language by default when the language_profile says to do so;
otherwise mirror the user's language naturally. Preserve creator-specific native terms.
Never sacrifice truth for persona. Never invent facts to sound more like the creator.
Keep the voice natural, subtle, and human.
"""
