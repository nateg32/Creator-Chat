"""Fingerprint pipeline policy derived from creator archetype.

This is the brain that says "for a musician, skip web research and lyrics-as-
voice extraction; for a podcaster, weight transcripts heavily; for a
documentarian, separate narrator voice from on-screen subjects."

The policy is intentionally a plain dataclass with documented fields so the
downstream `FingerprintService` can consult it without coupling to archetype
strings everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class FingerprintPolicy:
    """Per-creator policy controlling how the style fingerprint is built.

    Each field has a sensible default tuned for a generic talking-head vlogger
    creator (the most common case). Specific archetypes override fields below.
    """
    # ── Web research / external grounding ──
    enable_link_research: bool = True
    """Walk public links + domains for identity clues (Phase 1)."""

    enable_google_expansion: bool = True
    """Run targeted Google research to fill identity gaps (Phase 3)."""

    enable_persona_agent: bool = True
    """Run the persona research agent over public sources (Phase 3 deep)."""

    # ── Voice extraction ──
    enable_voice_extraction: bool = True
    """Mine cadence / signature moves / lexicon from transcripts."""

    voice_signal_weight: float = 1.0
    """Multiplier on the voice analyzer's contribution. Set <1 when transcripts
    are not the primary voice surface (e.g. music — lyrics are written, not spoken)."""

    transcript_is_voice: bool = True
    """Treat transcript text as authentic spoken voice. False for music
    (lyrics) and documentary (often narrator-as-actor, not the creator)."""

    extract_lexicon: bool = True
    """Build a personal lexicon from word frequency. Skip for music (rhyme-
    constrained vocabulary doesn't reflect prose voice)."""

    # ── Persona shape ──
    voice_register: str = "personal"
    """One of: personal, conversational, narrative, performative, journalistic,
    instructional, lyrical. Tells downstream prompts what voice to emulate."""

    primary_format: str = "video"
    """Hint for output: video, audio, text, mixed."""

    expects_multi_speaker: bool = False
    """If True, isolate the creator's turns from co-hosts/guests before
    voice analysis. Critical for podcasts and interviews."""

    # ── Prompt shaping ──
    persona_prompt_modifier: str = ""
    """Sentence appended to the synthesis prompt to steer tone. Empty by default."""

    # ── Bookkeeping ──
    archetype: str = "vlogger"
    confidence: float = 0.0
    distribution: Dict[str, float] = field(default_factory=dict)
    rationale: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Per-archetype overrides. Only specify fields that DIFFER from the default.
_PRESETS: Dict[str, Dict[str, Any]] = {
    "podcaster": {
        "voice_register": "conversational",
        "primary_format": "audio",
        "expects_multi_speaker": True,
        "voice_signal_weight": 1.3,  # transcripts are everything
        "persona_prompt_modifier": (
            "Voice should read as relaxed, conversational, and unrehearsed — "
            "the way someone speaks when they're thinking out loud across long "
            "form audio. Use natural pauses, mid-thought corrections, and "
            "callbacks to earlier points."
        ),
    },
    "musician": {
        # Musicians' "voice" lives in interviews and posts, not lyrics. Block
        # the lyric stream from polluting the spoken-voice fingerprint.
        "enable_google_expansion": True,  # bio info still useful
        "enable_voice_extraction": True,
        "voice_signal_weight": 0.5,  # weight transcripts (lyrics) less
        "transcript_is_voice": False,
        "extract_lexicon": False,
        "voice_register": "lyrical",
        "primary_format": "audio",
        "persona_prompt_modifier": (
            "This creator's primary output is music. Build voice from spoken "
            "interviews, captions, and social posts — not from song lyrics. "
            "Lyrics reflect artistic persona, not conversational voice."
        ),
    },
    "documentarian": {
        # Narrator voice may not be the creator. Skip Google noise; lean on
        # the creator's actual social/interview material instead.
        "enable_google_expansion": False,
        "enable_persona_agent": False,
        "voice_signal_weight": 0.7,
        "transcript_is_voice": False,  # narrator could be hired voice
        "voice_register": "narrative",
        "primary_format": "video",
        "persona_prompt_modifier": (
            "Documentary work uses narrator voice that may not match the "
            "creator's personal voice. Prioritize director/creator interview "
            "snippets and behind-the-scenes commentary over film narration."
        ),
    },
    "educator": {
        "voice_register": "instructional",
        "voice_signal_weight": 1.1,
        "persona_prompt_modifier": (
            "Voice should read as patient, clarity-first, and example-driven. "
            "Favor concrete examples over abstractions; explain assumed "
            "concepts before using jargon."
        ),
    },
    "commentator": {
        "voice_register": "journalistic",
        "voice_signal_weight": 1.2,
        "persona_prompt_modifier": (
            "Voice should be analytical and current-events aware. Anchor "
            "opinions to specific recent facts; avoid evergreen platitudes."
        ),
    },
    "comedian": {
        "voice_register": "performative",
        "voice_signal_weight": 1.0,
        "expects_multi_speaker": False,
        "persona_prompt_modifier": (
            "Voice should preserve comedic timing — short setups, hard "
            "punchlines, callbacks. Bits are scripted but voice should still "
            "read as spoken stage material, not written essay."
        ),
    },
    "vlogger": {
        # Defaults already match.
        "voice_register": "personal",
    },
    "journalist": {
        "voice_register": "journalistic",
        "transcript_is_voice": True,
        "voice_signal_weight": 1.1,
        "persona_prompt_modifier": (
            "Voice should be sourced, neutral in tone, and citation-friendly. "
            "Lead with facts; reserve interpretation for clearly-marked analysis."
        ),
    },
    "streamer": {
        "voice_register": "conversational",
        "expects_multi_speaker": True,  # chat interaction
        "voice_signal_weight": 1.1,
        "persona_prompt_modifier": (
            "Voice is live, reactive, and chat-aware. Keep it loose and "
            "spontaneous; avoid over-polished sentence structure."
        ),
    },
    "writer": {
        # Twitter/LinkedIn-only creators. No transcripts, voice == prose.
        "enable_voice_extraction": True,
        "transcript_is_voice": False,
        "voice_register": "personal",
        "primary_format": "text",
        "voice_signal_weight": 1.0,
        "persona_prompt_modifier": (
            "This creator works primarily in written posts. Voice should "
            "match the cadence of their existing posts — sentence length, "
            "punchline placement, and recurring hooks."
        ),
    },
    "mixed": {
        # Mixed creators get the conservative middle ground.
        "voice_signal_weight": 1.0,
        "voice_register": "personal",
        "persona_prompt_modifier": (
            "This creator works across multiple content formats. Treat each "
            "format on its own terms; do not let any single format dominate "
            "the voice profile."
        ),
    },
}


def _weighted_blend(blend: Dict[str, float]) -> Dict[str, Any]:
    """Compute a true weighted blend of policy fields across the supplied
    archetype distribution.

    Numeric fields (voice_signal_weight) become weighted averages.
    Boolean fields (enable_*) AND together — any archetype that disables
    a phase wins, because we'd rather skip web research for a half-musician
    than do it for a half-vlogger we'd never have searched for anyway.
    String fields (voice_register, primary_format, persona_prompt_modifier)
    take the highest-weighted archetype's value.
    """
    bools = ["enable_link_research", "enable_google_expansion", "enable_persona_agent",
             "enable_voice_extraction", "transcript_is_voice", "extract_lexicon",
             "expects_multi_speaker"]
    nums = ["voice_signal_weight"]
    strings = ["voice_register", "primary_format", "persona_prompt_modifier"]

    out: Dict[str, Any] = {}

    # Booleans: AND-merge — restrictive wins.
    for b in bools:
        out[b] = True
        for label, weight in blend.items():
            preset = _PRESETS.get(label) or _PRESETS["vlogger"]
            val = preset.get(b, getattr(FingerprintPolicy, b))
            out[b] = out[b] and val

    # Numerics: weighted average.
    for n in nums:
        total_w = 0.0
        weighted = 0.0
        for label, weight in blend.items():
            preset = _PRESETS.get(label) or _PRESETS["vlogger"]
            val = float(preset.get(n, getattr(FingerprintPolicy, n)))
            weighted += val * weight
            total_w += weight
        out[n] = round(weighted / total_w, 2) if total_w > 0 else getattr(FingerprintPolicy, n)

    # Strings: heaviest archetype wins.
    if blend:
        top_label = max(blend.items(), key=lambda kv: kv[1])[0]
        top_preset = _PRESETS.get(top_label) or _PRESETS["vlogger"]
        for s in strings:
            out[s] = top_preset.get(s, getattr(FingerprintPolicy, s))

    return out


def get_policy(
    creator_archetype: str,
    confidence: float = 0.0,
    distribution: Optional[Dict[str, float]] = None,
    llm_profile: Optional[Dict[str, Any]] = None,
) -> FingerprintPolicy:
    """Return the FingerprintPolicy for a creator.

    Sources, in priority order:
      1. LLM profile.format_blend  → true weighted blend of preset fields
      2. distribution               → fallback weighted blend (rule-derived)
      3. canonical preset for `creator_archetype`
      4. LLM profile.policy_overrides applied last (highest precedence)
    """
    archetype = (creator_archetype or "vlogger").lower()
    distribution = distribution or {}
    rationale: List[str] = []

    # Pick the blend source. Prefer the LLM's free-form blend because it
    # actually read the content; fall back to the rule distribution.
    blend: Dict[str, float] = {}
    if llm_profile and isinstance(llm_profile, dict):
        raw = llm_profile.get("format_blend") or {}
        blend = {k.lower(): float(v) for k, v in raw.items() if isinstance(v, (int, float)) and v > 0}
        if blend:
            rationale.append(f"LLM blend: {blend}")
    if not blend and distribution:
        blend = {k: float(v) for k, v in distribution.items() if k != "mixed" and v > 0}
        if blend:
            rationale.append(f"rule distribution: {blend}")

    # Build the policy. If we have a real blend, use it. Otherwise just take
    # the canonical preset for the named archetype.
    policy = FingerprintPolicy(
        archetype=archetype,
        confidence=float(confidence or 0.0),
        distribution=dict(distribution),
    )

    if blend:
        merged = _weighted_blend(blend)
        for key, value in merged.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        rationale.append("applied weighted blend across archetypes")
    else:
        preset = _PRESETS.get(archetype, _PRESETS["vlogger"])
        for key, value in preset.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
        rationale.append(f"base preset only: {archetype}")

    # LLM-supplied per-creator overrides win over the blend. The LLM has
    # eyes on the actual content, so its judgment trumps the preset math.
    if llm_profile and isinstance(llm_profile, dict):
        overrides = llm_profile.get("policy_overrides") or {}
        applied = []
        for key, value in overrides.items():
            if hasattr(policy, key):
                setattr(policy, key, value)
                applied.append(key)
        if applied:
            rationale.append(f"LLM overrides: {applied}")
        if llm_profile.get("descriptive_label"):
            rationale.append(f"label: {llm_profile['descriptive_label']!r}")

    # Low-confidence guardrail: if the classifier is unsure, never disable web
    # research — better to do extra work than miss identity context.
    if confidence < 0.45 and not policy.enable_google_expansion:
        policy.enable_google_expansion = True
        policy.enable_persona_agent = True
        rationale.append("low confidence override: re-enabled web research")

    policy.rationale = rationale
    return policy

