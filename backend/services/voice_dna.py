"""
VoiceDNA Engine — Multi-layer persona priming system.

Architecture (bottom-up, each layer compounds the one below):

┌──────────────────────────────────────┐
│  Layer 6: VOCABULARY RESONANCE       │  Post-gen word-level alignment
├──────────────────────────────────────┤
│  Layer 5: ANTI-VOICE GUARD           │  What this creator NEVER sounds like
├──────────────────────────────────────┤
│  Layer 4: MODE ADAPTATION            │  Context-specific voice shifting
├──────────────────────────────────────┤
│  Layer 3: RESPONSE SCAFFOLD          │  Structural template for responses
├──────────────────────────────────────┤
│  Layer 2: VOICE EQUATION             │  Single-line voice formula
├──────────────────────────────────────┤
│  Layer 1: VOICE IMPRINT              │  Few-shot primer (highest impact)
└──────────────────────────────────────┘

Key insight: few-shot demonstrations are 10x more effective at style transfer
than instructions.  This engine concentrates the creator's voice into a
maximally potent signal that primes the LLM before the detailed instructions.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generic vocabulary that should be replaced with creator-specific words
# ---------------------------------------------------------------------------
_GENERIC_SWAPS: Dict[str, List[str]] = {
    # generic word -> list of creator-signal replacements (selected per-creator)
    "certainly": ["for sure", "absolutely", "yeah", "100%", "no doubt"],
    "additionally": ["and look", "on top of that", "plus", "also"],
    "furthermore": ["and look", "on top of that", "plus", "here is the thing"],
    "however": ["but", "look", "here is the thing", "now"],
    "therefore": ["so", "that is why", "which is why", "bottom line"],
    "utilize": ["use", "leverage", "put to work"],
    "individuals": ["people", "humans", "folks"],
    "commence": ["start", "kick off", "begin", "get going"],
    "sufficient": ["enough"],
    "numerous": ["a lot of", "tons of", "many"],
    "assist": ["help", "support"],
    "obtain": ["get", "grab", "earn"],
    "demonstrate": ["show", "prove"],
    "implement": ["do", "execute", "run", "build"],
    "regarding": ["about", "on", "around"],
    "approximately": ["about", "around", "roughly"],
    "endeavor": ["try", "push", "go for"],
    "subsequently": ["then", "after that", "next"],
    "facilitate": ["make easier", "help with", "enable"],
    "leverage": ["use", "lean on", "tap into"],
    "delve": ["dig into", "look at", "explore", "break down"],
    "navigate": ["handle", "deal with", "figure out", "work through"],
    "landscape": ["space", "world", "game", "arena"],
    "paradigm": ["model", "frame", "approach"],
    "synergy": ["alignment", "connection", "fit"],
    "holistic": ["full", "complete", "whole"],
    "optimize": ["improve", "sharpen", "level up", "dial in"],
    "comprehensive": ["full", "complete", "thorough"],
    "innovative": ["new", "fresh", "different", "creative"],
    "strategize": ["plan", "map out", "think through"],
    "impactful": ["powerful", "real", "meaningful"],
    "empower": ["push", "help", "give the tools", "equip"],
    "cultivate": ["build", "grow", "develop"],
    "resonate": ["hit", "land", "connect", "click"],
    "overarching": ["big", "main", "core"],
    "actionable": ["practical", "real", "concrete", "usable"],
    "here to help": [],  # just remove
    "i can assist": [],
    "hope this helps": [],
    "let me know if you": [],
    "feel free to": [],
    "happy to chat": [],
    "happy to help": [],
    "feel free to ask": [],
    "don't hesitate to": [],
    "do not hesitate to": [],
    "not my lane": [],
    "not really my lane": [],
    "out of my lane": [],
    "not my core focus": [],
    "not really my core focus": [],
    "not my main focus": [],
    "not really my main focus": [],
    "right up my alley": [],
    "those are right up my alley": [],
    "you might want to check out": [],
    "you may want to check out": [],
    "what sparked your interest": [],
}

# Banned AI-assistant phrases (remove entirely, no replacement)
_AI_PURGE = [
    "as an ai",
    "as a language model",
    "i'm an ai",
    "i am an ai",
    "as your ai",
    "i don't have personal",
    "i don't have feelings",
    "i'm here to help",
    "i am here to help",
    "i'm just an ai",
    "based on the information provided",
    "based on the content",
    "from the context provided",
    "according to the information",
    "according to the content",
    "great question",
    "that's a great question",
    "excellent question",
    "wonderful question",
    "what a great question",
    "hey there!",
    "hey there,",
    "hey there.",
    "feel free to ask",
    "feel free to reach out",
    "don't hesitate to ask",
    "don't hesitate to reach out",
    "for more insights",
    "narrow down what you're looking for",
    "narrow down what you are looking for",
    "hope this helps",
    "hope that helps",
    "i hope this helps",
    "i hope that helps",
    "let me know if you have any other questions",
    "let me know if you have any questions",
]

# Regex-based purge patterns (for variable product/program names)
_AI_PURGE_PATTERNS = [
    re.compile(r"join\s+the\s+\S+(?:\s+\S+){0,3}\s+for\s+more\s+\w+\.?", re.IGNORECASE),
    re.compile(r"check\s+out\s+(?:my|the)\s+\S+(?:\s+\S+){0,3}\s+for\s+more\s+\w+\.?", re.IGNORECASE),
]


def _coerce_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _safe_list(value: Any, limit: int = 20) -> List[str]:
    """Coerce value to a list of non-empty strings."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item or "").strip()
        if text and len(text) > 2:
            result.append(text)
        if len(result) >= limit:
            break
    return result


# ---------------------------------------------------------------------------
# Layer 1 — VOICE IMPRINT (few-shot primer)
# ---------------------------------------------------------------------------

def build_voice_imprint(
    creator_profile: Dict[str, Any],
    mode: str = "task",
    max_examples: int = 6,
) -> str:
    """
    Extract the most potent voice examples from the creator's fingerprint.
    Returns an annotated few-shot block that primes the LLM into the
    creator's exact voice, cadence, and structure.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    golden = _coerce_dict(sfp.get("golden_examples"))
    golden_replies = _coerce_dict(sfp.get("golden_replies"))

    mode_key = {
        "task": "teaching",
        "small_talk": "comfort",
        "greeting": "greeting",
        "sales": "sales",
        "story": "story",
        "rebuke": "rebuke",
        "boundary": "boundary",
        "uncertainty": "uncertainty",
    }.get((mode or "task").lower(), "teaching")

    # Collect examples from multiple sources for richness
    primary_examples = _safe_list(golden.get(mode_key))
    reply_examples = _safe_list(golden_replies.get(mode_key))
    fallback_examples = _safe_list(golden.get("teaching")) if mode_key != "teaching" else []
    comfort_examples = _safe_list(golden.get("comfort")) if mode_key != "comfort" else []
    rebuke_examples = _safe_list(golden.get("rebuke")) if mode_key != "rebuke" else []

    # Build a diverse set: primary mode first, then other modes for range
    all_examples: List[Tuple[str, str]] = []
    for ex in primary_examples:
        all_examples.append((ex, mode_key))
    for ex in reply_examples:
        all_examples.append((ex, f"{mode_key}_reply"))

    # Add diversity from other modes (shows voice range)
    _diversity_modes = [
        ("teaching", fallback_examples),
        ("comfort", comfort_examples),
        ("rebuke", rebuke_examples),
    ]
    for dmode, dexamples in _diversity_modes:
        for ex in dexamples[:1]:  # max 1 from each diversity mode
            all_examples.append((ex, dmode))

    if not all_examples:
        return ""

    # Deduplicate and select best examples
    seen: set = set()
    selected: List[Tuple[str, str]] = []
    for text, ex_mode in all_examples:
        text = text.strip()
        if len(text) < 15:
            continue
        key = text[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        selected.append((text, ex_mode))
        if len(selected) >= max_examples:
            break

    if not selected:
        return ""

    # Build annotated imprint
    lines = [
        "VOICE IMPRINT (these are REAL examples of how you actually talk — "
        "absorb the rhythm, word choices, energy, and structure):"
    ]
    for i, (text, ex_mode) in enumerate(selected, 1):
        # Annotate the example with its structural pattern
        annotation = _annotate_example(text)
        truncated = text if len(text) <= 400 else text[:397] + "..."
        lines.append(f'  [{ex_mode.upper()}] "{truncated}"')
        if annotation:
            lines.append(f"    ^ Pattern: {annotation}")

    return "\n".join(lines)


def _annotate_example(text: str) -> str:
    """Detect the structural pattern of an example."""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    if not sentences:
        return ""

    parts = []
    first = sentences[0]

    # Detect opening pattern
    if first.endswith("?"):
        parts.append("question-opener")
    elif re.match(r'^(look|listen|here|stop|let me|real talk|truth|fact)', first.lower()):
        parts.append("direct-hook")
    elif len(first.split()) <= 6:
        parts.append("punch-opener")
    elif re.match(r'^(i |my |when i |back when)', first.lower()):
        parts.append("story-opener")
    else:
        parts.append("declarative-opener")

    # Detect body pattern
    if len(sentences) > 2:
        has_story = any(
            re.search(r'\b(i |my |when |back when|one time|years ago|remember)', s.lower())
            for s in sentences[1:-1]
        )
        has_imperative = any(
            re.match(r'^(do |don\'t |stop |start |go |get |make |take |build |focus )', s.lower())
            for s in sentences[1:]
        )
        if has_story:
            parts.append("story-evidence")
        if has_imperative:
            parts.append("command-drive")

    # Detect closing pattern
    if len(sentences) > 1:
        last = sentences[-1]
        if last.endswith("?"):
            parts.append("question-close")
        elif re.match(r'^(do |don\'t |stop |start |go |get |make |take |that\'s )', last.lower()):
            parts.append("imperative-close")
        elif len(last.split()) <= 8:
            parts.append("punch-close")

    return " → ".join(parts) if parts else ""


# ---------------------------------------------------------------------------
# Layer 2 — VOICE EQUATION (single-line formula)
# ---------------------------------------------------------------------------

def build_voice_equation(creator_profile: Dict[str, Any]) -> str:
    """
    Compress the creator's entire voice identity into a single-line formula.
    This is the most token-efficient voice primer possible.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    identity = _coerce_dict(sfp.get("identity_signature"))
    dna = _coerce_dict(sfp.get("linguistic_dna"))
    cadence = _coerce_dict(sfp.get("cadence_rules"))
    behavioral = _coerce_dict(sfp.get("behavioral_patterns"))
    emotional = _coerce_dict(sfp.get("emotional_signature"))

    # Energy
    energy_raw = dna.get("energy") or ""
    energy = str(energy_raw).upper().strip() if energy_raw else "MEDIUM"
    if energy not in ("HIGH", "LOW", "CALM", "INTENSE", "MEDIUM"):
        energy = "MEDIUM"

    # Power position
    power = str(identity.get("power_position") or "peer").strip()

    # Sentence shape
    shape = str(cadence.get("sentence_shape") or dna.get("sentence_structure") or "varied").strip()

    # Evidence style
    evidence = str(dna.get("evidence_style") or "stories and examples").strip()

    # Emotional register
    temp = str(emotional.get("temperature") or "warm").strip()
    confidence = str(behavioral.get("confidence_level") or "high").strip()
    validation = str(emotional.get("validation_style") or "direct").strip()

    # Build equation
    parts = [
        f"{energy} ENERGY",
        power.upper() if power else "PEER",
        shape.upper() if shape else "VARIED SENTENCES",
        evidence,
        f"{temp}/{confidence} confidence/{validation} validation",
    ]

    return f"VOICE EQUATION: {' + '.join(parts)}"


# ---------------------------------------------------------------------------
# Layer 3 — RESPONSE SCAFFOLD (structural template)
# ---------------------------------------------------------------------------

def build_response_scaffold(
    creator_profile: Dict[str, Any],
    mode: str = "task",
) -> str:
    """
    Generate a structural template showing how this creator builds responses.
    Based on their speech mechanics, cadence rules, and golden examples.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    mechanics = _coerce_dict(sfp.get("speech_mechanics"))
    cadence = _coerce_dict(sfp.get("cadence_rules"))
    dna = _coerce_dict(sfp.get("linguistic_dna"))
    reasoning = _coerce_dict(sfp.get("reasoning_profile"))
    mode_matrix = _coerce_dict(sfp.get("mode_matrix"))

    # Detect opening style
    openings = _safe_list(mechanics.get("signature_openings"), limit=5)
    opening_style = "declarative hook"
    if openings:
        q_count = sum(1 for o in openings if "?" in o)
        if q_count > len(openings) / 2:
            opening_style = "question hook"
        elif any(len(o.split()) <= 4 for o in openings):
            opening_style = "short punch opener"

    # Detect body style
    story_vs_list = str(cadence.get("story_vs_list") or dna.get("evidence_style") or "balanced")
    framework_vs_story = str(reasoning.get("framework_vs_story") or "balanced")
    body_style = "story + lesson"
    if "list" in story_vs_list.lower() or "framework" in framework_vs_story.lower():
        body_style = "framework + concrete example"
    elif "story" in story_vs_list.lower() or "story" in framework_vs_story.lower():
        body_style = "personal story + takeaway"

    # Detect closing style
    landings = _safe_list(mechanics.get("signature_landings"), limit=5)
    question_density = str(cadence.get("question_rate") or mechanics.get("question_density") or "moderate")
    closing_style = "actionable takeaway"
    if "high" in question_density.lower():
        closing_style = "follow-up question"
    elif landings:
        if any("?" in l for l in landings):
            closing_style = "challenge question"
        elif any(len(l.split()) <= 6 for l in landings):
            closing_style = "punch landing"

    # Get mode-specific rules
    mode_key = {
        "task": "teaching", "small_talk": "comfort",
        "greeting": "greeting", "rebuke": "rebuke",
    }.get((mode or "task").lower(), "teaching")
    mode_rules = _coerce_dict(mode_matrix.get(mode_key))
    mode_note = ""
    if mode_rules:
        tone = mode_rules.get("tone") or mode_rules.get("energy") or ""
        move = mode_rules.get("default_move") or mode_rules.get("opening") or ""
        if tone:
            mode_note = f" (mode tone: {tone})"
        if move:
            mode_note += f" (lead with: {move})"

    scaffold = (
        f"RESPONSE ARCHITECTURE{mode_note}:\n"
        f"  OPEN → {opening_style}\n"
        f"  BODY → {body_style}\n"
        f"  CLOSE → {closing_style}"
    )

    # Sentence length guidance
    sentence_shape = cadence.get("sentence_shape") or dna.get("sentence_structure") or ""
    if sentence_shape:
        scaffold += f"\n  RHYTHM → {sentence_shape}"

    return scaffold


# ---------------------------------------------------------------------------
# Layer 4 — MODE ADAPTATION (context-specific voice shifting)
# ---------------------------------------------------------------------------

def build_mode_voice_shift(
    creator_profile: Dict[str, Any],
    mode: str = "task",
) -> str:
    """
    Generate mode-specific voice adaptation rules.
    Different modes require different energy, pacing, and moves.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    mode_matrix = _coerce_dict(sfp.get("mode_matrix"))
    pressure = _coerce_dict(sfp.get("pressure_engine"))

    mode_key = {
        "task": "teaching", "small_talk": "comfort",
        "greeting": "greeting", "rebuke": "rebuke",
        "sales": "sales", "boundary": "boundary",
        "uncertainty": "uncertainty", "story": "story",
    }.get((mode or "task").lower(), "teaching")

    mode_rules = _coerce_dict(mode_matrix.get(mode_key))
    if not mode_rules:
        return ""

    lines = [f"MODE SHIFT ({mode_key.upper()}):"]
    for key in ("tone", "energy", "default_move", "opening", "pacing",
                "evidence_preference", "question_style", "empathy_level",
                "bluntness", "closer"):
        val = mode_rules.get(key)
        if val and str(val).strip():
            lines.append(f"  {key}: {val}")

    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Layer 5 — ANTI-VOICE GUARD (what this creator NEVER sounds like)
# ---------------------------------------------------------------------------

def build_anti_voice(creator_profile: Dict[str, Any]) -> str:
    """
    Generate concrete examples of how this creator does NOT sound.
    Anti-examples are powerful negative primers that prevent generic drift.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    anti = _coerce_dict(sfp.get("anti_persona"))
    contrastive = _coerce_dict(sfp.get("contrastive_identity"))

    lines = []

    forbidden_lines = _safe_list(
        anti.get("forbidden_generic_coach_lines"), limit=5
    )
    forbidden_postures = _safe_list(
        anti.get("forbidden_emotional_postures"), limit=4
    )
    sounds_fake_if = _safe_list(
        anti.get("sounds_like_someone_else_if"), limit=4
    )
    confusion_risks = _safe_list(
        contrastive.get("confusion_risks"), limit=3
    )
    nearest_neighbors = _safe_list(
        contrastive.get("nearest_neighbor_creators")
        or _coerce_dict(sfp.get("disambiguation_markers")).get("closest_neighbor_creators"),
        limit=3,
    )

    if forbidden_lines:
        lines.append(
            "NEVER SAY (these are generic filler that kills your voice): "
            + " | ".join(f'"{fl}"' for fl in forbidden_lines[:5])
        )

    if forbidden_postures:
        lines.append(
            "FORBIDDEN POSTURES (emotional stances you never take): "
            + ", ".join(forbidden_postures[:4])
        )

    if sounds_fake_if:
        lines.append(
            "YOU SOUND FAKE IF: " + "; ".join(sounds_fake_if[:4])
        )

    if nearest_neighbors:
        lines.append(
            f"YOU ARE NOT: {', '.join(nearest_neighbors[:3])}. "
            "Do not drift toward their cadence or catchphrases."
        )

    if confusion_risks:
        lines.append(
            "CONFUSION RISKS: " + "; ".join(confusion_risks[:3])
        )

    if not lines:
        return ""

    return "ANTI-VOICE GUARD:\n" + "\n".join(f"  {l}" for l in lines)


# ---------------------------------------------------------------------------
# Layer 6 — VOCABULARY RESONANCE (post-generation word alignment)
# ---------------------------------------------------------------------------

def build_vocabulary_map(
    creator_profile: Dict[str, Any],
) -> Dict[str, str]:
    """
    Build a mapping of generic words → creator-preferred replacements.
    Used for fast post-processing without an LLM call.
    """
    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    lexical = _coerce_dict(sfp.get("lexical_rules"))
    high_words = _safe_list(lexical.get("high_signal_words"), limit=20)
    banned = _safe_list(lexical.get("banned_words"), limit=20)
    banned_frames = _safe_list(lexical.get("banned_frames"), limit=10)

    # Start with the generic swaps
    vocab_map: Dict[str, str] = {}

    # Map banned words to empty (will be caught by the replacer)
    for word in banned:
        vocab_map[word.lower()] = ""

    # Map generic AI words to creator's preferred vocabulary
    # Use the creator's high-signal words to pick the best replacement
    high_set = set(w.lower() for w in high_words)
    for generic, options in _GENERIC_SWAPS.items():
        if not options:
            vocab_map[generic.lower()] = ""
            continue
        # See if any of the creator's high-signal words match a replacement
        preferred = None
        for opt in options:
            if opt.lower() in high_set:
                preferred = opt
                break
        if preferred:
            vocab_map[generic.lower()] = preferred
        else:
            vocab_map[generic.lower()] = options[0]  # default first option

    return vocab_map


def apply_vocabulary_resonance(
    text: str,
    creator_profile: Dict[str, Any],
) -> str:
    """
    Fast post-processing pass that swaps generic/AI vocabulary
    for creator-specific words. No LLM call needed.
    """
    if not text:
        return text

    vocab_map = build_vocabulary_map(creator_profile)
    if not vocab_map:
        return text

    result = text

    # First pass: remove AI-assistant phrases entirely
    lower = result.lower()
    for phrase in _AI_PURGE:
        idx = lower.find(phrase)
        if idx != -1:
            # Remove the phrase and clean up surrounding whitespace
            before = result[:idx]
            after = result[idx + len(phrase):]
            # Clean up: remove leading punctuation/whitespace from after
            after = re.sub(r'^[\s,.:;]+', ' ', after)
            result = before.rstrip() + " " + after.lstrip()
            result = re.sub(r'\s+', ' ', result).strip()
            lower = result.lower()

    # First pass (b): regex-based AI filler removal
    for pat in _AI_PURGE_PATTERNS:
        result = pat.sub('', result)
    result = re.sub(r'\s+', ' ', result).strip()

    # Second pass: word-level swaps (whole-word only, preserve case)
    for generic, replacement in vocab_map.items():
        if not generic or generic in (p.lower() for p in _AI_PURGE):
            continue
        # Only swap standalone words/phrases, not substrings
        pattern = re.compile(r'\b' + re.escape(generic) + r'\b', re.IGNORECASE)
        if pattern.search(result):
            if not replacement:
                # Remove the word entirely and clean up
                result = pattern.sub("", result)
                result = re.sub(r'\s+', ' ', result).strip()
            else:

                def _match_case(match: re.Match) -> str:
                    original = match.group(0)
                    if original[0].isupper():
                        return replacement[0].upper() + replacement[1:]
                    return replacement

                result = pattern.sub(_match_case, result)

    # Clean up any resulting double spaces or weird punctuation
    result = re.sub(r'\s+', ' ', result).strip()
    result = re.sub(r'\s+([.,!?;:])', r'\1', result)
    result = re.sub(r'([.,!?;:])\s*([.,!?;:])', r'\1', result)

    return result


# ---------------------------------------------------------------------------
# Voice Fidelity Scoring (fast, no LLM)
# ---------------------------------------------------------------------------

def score_voice_fidelity(
    text: str,
    creator_profile: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Score how well a response matches the creator's voice fingerprint.
    Returns a score 0-1 and specific gaps found.
    Fast computation, no LLM call.
    """
    if not text:
        return {"score": 0.0, "gaps": ["empty_response"]}

    sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
    lexical = _coerce_dict(sfp.get("lexical_rules"))
    cadence = _coerce_dict(sfp.get("cadence_rules"))
    dna = _coerce_dict(sfp.get("linguistic_dna"))
    anti = _coerce_dict(sfp.get("anti_persona"))

    lower = text.lower()
    words = text.split()
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    word_count = len(words)

    score = 1.0
    gaps: List[str] = []

    # Check for AI phrases
    for phrase in _AI_PURGE:
        if phrase in lower:
            score -= 0.15
            gaps.append(f"ai_phrase:{phrase}")

    # Check signature phrase presence (at least one in substantive responses)
    sig_phrases = _safe_list(lexical.get("signature_phrases"), limit=10)
    if word_count >= 30 and sig_phrases:
        sig_hits = sum(1 for p in sig_phrases if p.lower() in lower)
        if sig_hits == 0:
            score -= 0.05
            gaps.append("no_signature_phrases")

    # Check high-signal vocabulary usage
    high_words = _safe_list(lexical.get("high_signal_words"), limit=15)
    if word_count >= 20 and high_words:
        hw_hits = sum(1 for w in high_words if w.lower() in lower)
        if hw_hits == 0:
            score -= 0.05
            gaps.append("no_high_signal_words")

    # Check banned words
    banned = _safe_list(lexical.get("banned_words"), limit=15)
    for bw in banned:
        if bw.lower() in lower:
            score -= 0.08
            gaps.append(f"banned_word:{bw}")

    # Check forbidden generic lines
    forbidden_lines = _safe_list(anti.get("forbidden_generic_coach_lines"), limit=10)
    for fl in forbidden_lines:
        if fl.lower() in lower:
            score -= 0.1
            gaps.append(f"forbidden_line:{fl}")

    # Check sentence length alignment
    target_shape = str(cadence.get("sentence_shape") or dna.get("sentence_structure") or "").lower()
    if sentences and word_count >= 15:
        avg_sentence_len = word_count / max(len(sentences), 1)
        if "short" in target_shape and avg_sentence_len > 18:
            score -= 0.05
            gaps.append("sentences_too_long")
        elif "long" in target_shape and avg_sentence_len < 8:
            score -= 0.05
            gaps.append("sentences_too_short")

    # Check question density
    question_rate = str(cadence.get("question_rate") or "").lower()
    q_count = text.count("?")
    if "high" in question_rate and word_count >= 30 and q_count == 0:
        score -= 0.03
        gaps.append("missing_questions")
    elif "low" in question_rate and q_count > 2:
        score -= 0.03
        gaps.append("too_many_questions")

    return {
        "score": max(0.0, min(1.0, score)),
        "gaps": gaps,
        "word_count": word_count,
        "sentence_count": len(sentences),
    }


# ---------------------------------------------------------------------------
# Conversation Persona Memory (tracks used phrases across turns)
# ---------------------------------------------------------------------------

class ConversationVoiceTracker:
    """
    Tracks which voice elements have been used in the current conversation
    to prevent repetition and maintain natural variety.
    """

    def __init__(self) -> None:
        self._used_phrases: List[str] = []
        self._used_openers: List[str] = []
        self._turn_count: int = 0

    def record_turn(self, response: str, sig_phrases: List[str]) -> None:
        self._turn_count += 1
        lower = response.lower()
        for phrase in sig_phrases:
            if phrase.lower() in lower:
                self._used_phrases.append(phrase)
        # Record opening pattern
        first_sentence = re.split(r'(?<=[.!?])\s+', response)[0] if response else ""
        if first_sentence:
            self._used_openers.append(first_sentence[:50])

    def get_avoidance_notes(self, sig_phrases: List[str]) -> str:
        if not self._used_phrases:
            return ""
        recent = self._used_phrases[-4:]
        available = [p for p in sig_phrases if p not in recent]
        lines = []
        if recent:
            lines.append(
                f"RECENTLY USED (do NOT repeat): {', '.join(recent)}"
            )
        if available:
            lines.append(
                f"AVAILABLE PHRASES: {', '.join(available[:4])}"
            )
        return "\n".join(lines)

    @property
    def turn_count(self) -> int:
        return self._turn_count


# ---------------------------------------------------------------------------
# Layer 7 — VOICE ECHO (real-time vocabulary from current RAG chunks)
# ---------------------------------------------------------------------------

# Common filler / generic sentences to skip when extracting echoes
_ECHO_SKIP_STARTS = frozenset([
    "subscribe", "like and subscribe", "hit the bell", "check out",
    "click the link", "follow me", "leave a comment", "thanks for watching",
    "welcome back", "what's up", "hey guys", "hey everyone",
])


def extract_voice_echoes(
    rag_chunks: Optional[List[Dict[str, Any]]],
    max_phrases: int = 10,
) -> List[str]:
    """
    Mine the retrieved RAG chunks for short, distinctive creator sentences.
    These are the creator's ACTUAL words on the current topic — injected
    into the prompt so the LLM echoes their real vocabulary and cadence
    instead of paraphrasing into generic language.

    Selection criteria:
      - 4-20 words (short enough to be distinctive, long enough to carry voice)
      - Contains first-person or direct address (signals authentic creator speech)
      - Not YouTube filler ("subscribe", "check out", etc.)
      - Deduplicated by first 40 chars
    """
    if not rag_chunks:
        return []

    phrases: List[str] = []
    for chunk in rag_chunks:
        content = (chunk.get("content") or "").strip()
        if not content or content.startswith("[LIVE WEB SEARCH"):
            continue
        sentences = re.split(r'(?<=[.!?])\s+', content)
        for s in sentences:
            s = s.strip()
            words = s.split()
            wc = len(words)
            if wc < 4 or wc > 20:
                continue
            lower = s.lower()
            # Skip YouTube filler
            if any(lower.startswith(skip) for skip in _ECHO_SKIP_STARTS):
                continue
            # Prefer sentences with first-person or direct address
            if re.search(r"\b(i |i'[a-z]|my |you |your |we |our )\b", lower):
                phrases.append(s)

    # Deduplicate by first 40 chars, preserve order
    seen: set = set()
    unique: List[str] = []
    for p in phrases:
        key = p.lower()[:40]
        if key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) >= max_phrases:
            break

    return unique


def build_voice_echo_block(
    rag_chunks: Optional[List[Dict[str, Any]]],
    max_phrases: int = 8,
) -> str:
    """
    Build the VOICE ECHO prompt section from current RAG chunks.
    Returns empty string if no echoes found.
    """
    echoes = extract_voice_echoes(rag_chunks, max_phrases=max_phrases)
    if not echoes:
        return ""

    lines = [
        "VOICE ECHOES (your REAL words from your content on this topic — "
        "absorb and echo this exact vocabulary, slang, and energy):"
    ]
    for echo in echoes:
        truncated = echo if len(echo) <= 200 else echo[:197] + "..."
        lines.append(f'  - "{truncated}"')
    lines.append(
        "When you can reuse a phrase, word, or sentence structure from "
        "above, do so. Prefer YOUR real vocabulary over any generic alternative."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API — Build complete voice DNA block
# ---------------------------------------------------------------------------

def build_voice_dna_block(
    creator_profile: Dict[str, Any],
    mode: str = "task",
    conversation_tracker: Optional[ConversationVoiceTracker] = None,
) -> str:
    """
    Build the complete Voice DNA block for injection into the system prompt.
    This is the concentrated voice signal that goes BEFORE the detailed
    voice instructions for maximum priming effect.
    """
    if not creator_profile:
        return ""

    parts: List[str] = []

    # Layer 2: Voice Equation (compact, goes first)
    equation = build_voice_equation(creator_profile)
    if equation:
        parts.append(equation)

    # Layer 1: Voice Imprint (most powerful layer)
    imprint = build_voice_imprint(creator_profile, mode=mode)
    if imprint:
        parts.append(imprint)

    # Layer 3: Response Scaffold
    scaffold = build_response_scaffold(creator_profile, mode=mode)
    if scaffold:
        parts.append(scaffold)

    # Layer 4: Mode Adaptation
    mode_shift = build_mode_voice_shift(creator_profile, mode=mode)
    if mode_shift:
        parts.append(mode_shift)

    # Layer 5: Anti-Voice Guard
    anti = build_anti_voice(creator_profile)
    if anti:
        parts.append(anti)

    # Conversation tracking
    if conversation_tracker:
        sfp = _coerce_dict(creator_profile.get("style_fingerprint"))
        lexical = _coerce_dict(sfp.get("lexical_rules"))
        sig = _safe_list(lexical.get("signature_phrases"), limit=10)
        avoidance = conversation_tracker.get_avoidance_notes(sig)
        if avoidance:
            parts.append(avoidance)

    if not parts:
        return ""

    return "\n\n".join(parts)
